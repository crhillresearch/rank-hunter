"""Exact rational torsion data and normalization for elliptic curves over Q.

Ordinary Rank Hunter workflows compute torsion as post-retention enrichment so
raw/pruned candidates do not pay for it. Torsion-target Auto Search is the
intentional exception: it computes the exact specialized torsion group before
deep rank-search work so a candidate can be filtered against the selected
Mazur group. Successful computations use the same persisted representation.
"""
from __future__ import annotations

import json
import subprocess
import sys
from functools import reduce
from operator import mul
from rank42.schema_manifest import (
    TORSION_SCHEMA_MANIFEST,
    inspect_schema,
    schema_issue_summary,
)


MAZUR_TORSION_INVARIANTS = {
    (),
    *((n,) for n in range(2, 11)),
    (12,),
    (2, 2), (2, 4), (2, 6), (2, 8),
}

MAZUR_TORSION_CHOICES = (
    "Trivial",
    "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C12",
    "C2 × C2", "C2 × C4", "C2 × C6", "C2 × C8",
)

_TORSION_LABEL_TO_INVARIANTS = {
    "Trivial": (),
    "C2": (2,), "C3": (3,), "C4": (4,), "C5": (5,), "C6": (6,),
    "C7": (7,), "C8": (8,), "C9": (9,), "C10": (10,), "C12": (12,),
    "C2 × C2": (2, 2), "C2 × C4": (2, 4), "C2 × C6": (2, 6), "C2 × C8": (2, 8),
}

TORSION_WORKER_MARKER = "RANK42_TORSION_RESULT="


class TorsionSchemaNotReady(RuntimeError):
    """Raised when exact-torsion runtime sees an unmigrated schema."""


class TorsionTimeout(RuntimeError):
    """Raised when isolated exact torsion exceeds its configured budget."""


class TorsionFailure(RuntimeError):
    """Raised when isolated exact torsion fails without a valid result."""


def torsion_schema_status(db):
    """Inspect torsion schema readiness without mutating the database."""
    return inspect_schema(db, TORSION_SCHEMA_MANIFEST)


def require_torsion_schema(db):
    """Require Migration Manager-owned torsion schema without repairing it."""
    status = torsion_schema_status(db)
    if not status["ready"]:
        reason = schema_issue_summary(status) or "schema is not ready"
        raise TorsionSchemaNotReady(
            "torsion schema is not ready; run the Migration Manager via "
            "rank42.migration_manager.open_database_with_migrations() "
            f"before scientific execution ({reason})"
        )
    return status


TORSION_COLUMNS = {
    "torsion_order": "INTEGER",
    "torsion_invariants_json": "TEXT",
    "torsion_label": "TEXT",
    "torsion_computed_at": "TEXT",
    "torsion_algorithm": "TEXT",
    "torsion_error": "TEXT",
}


def ensure_torsion_schema(db, *, commit=True):
    """Add torsion columns/index once; remain read-only after initialization."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(curves)")}
    changed = False
    for name, ddl in TORSION_COLUMNS.items():
        if name not in existing:
            db.execute(f"ALTER TABLE curves ADD COLUMN {name} {ddl}")
            changed = True
    index_exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_curves_torsion_label'"
    ).fetchone()
    if index_exists is None:
        db.execute("CREATE INDEX idx_curves_torsion_label ON curves(torsion_label)")
        changed = True
    if changed and commit:
        db.commit()
    return changed


def canonical_torsion_label(value):
    """Normalize a user/manifest torsion label to Rank Hunter's stored form."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("torsion group is required")
    compact = (
        raw.upper()
        .replace("ℤ", "Z")
        .replace("×", "X")
        .replace(" ", "")
        .replace("/", "")
        .replace("\\", "")
        .replace("_", "")
        .replace("-", "")
    )
    aliases = {
        "TRIVIAL": "Trivial",
        "NONE": "Trivial",
        "1": "Trivial",
        "C1": "Trivial",
        "Z1": "Trivial",
        "Z1Z": "Trivial",
    }
    for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 12):
        aliases[f"C{n}"] = f"C{n}"
        aliases[f"Z{n}"] = f"C{n}"
        aliases[f"Z{n}Z"] = f"C{n}"
    for n in (2, 4, 6, 8):
        canonical = f"C2 × C{n}"
        aliases[f"C2XC{n}"] = canonical
        aliases[f"C2X{n}"] = canonical
        aliases[f"Z2XZ{n}"] = canonical
        aliases[f"Z2ZXZ{n}Z"] = canonical
    label = aliases.get(compact)
    if label not in MAZUR_TORSION_CHOICES:
        raise ValueError(f"unsupported rational torsion group over Q: {value!r}")
    return label


def torsion_invariants_from_label(value):
    return _TORSION_LABEL_TO_INVARIANTS[canonical_torsion_label(value)]


def normalize_torsion_invariants(values):
    """Return canonical invariant factors, dropping trivial factors."""
    return tuple(int(x) for x in (values or ()) if int(x) > 1)


def torsion_label(invariants):
    invariants = normalize_torsion_invariants(invariants)
    if not invariants:
        return "Trivial"
    return " × ".join(f"C{x}" for x in invariants)


def validate_mazur_torsion(invariants):
    invariants = normalize_torsion_invariants(invariants)
    if invariants not in MAZUR_TORSION_INVARIANTS:
        raise ValueError(
            "rational torsion structure outside Mazur's theorem: "
            + (torsion_label(invariants) if invariants else "Trivial")
        )
    return invariants


def compute_torsion_data(E):
    """Compute and validate E(Q)_tors using Sage on a Q-model."""
    T = E.torsion_subgroup()
    invariants = validate_mazur_torsion(T.invariants())
    order = int(T.order())
    expected_order = reduce(mul, invariants, 1)
    if order != expected_order:
        raise ValueError(
            f"torsion order/invariants mismatch: order={order}, invariants={list(invariants)}"
        )
    return {
        "torsion_order": order,
        "torsion_invariants_json": json.dumps(list(invariants)),
        "torsion_label": torsion_label(invariants),
    }


def compute_torsion_data_bounded(E, *, timeout=30):
    """Compute exact torsion in an isolated Sage subprocess with a hard timeout."""
    timeout = max(1, int(timeout))
    payload = {
        "a_invariants": [str(value) for value in E.a_invariants()],
    }
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.torsion_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TorsionTimeout(
            f"exact rational torsion exceeded {timeout}s"
        ) from exc

    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(TORSION_WORKER_MARKER):
            marker = line[len(TORSION_WORKER_MARKER):]
            break
    if marker is None:
        tail = "\n".join(
            ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-12:]
        )
        raise TorsionFailure(
            f"exact torsion worker returned no result marker "
            f"(exit={cp.returncode})"
            + (f": {tail}" if tail else "")
        )
    try:
        result = json.loads(marker)
    except Exception as exc:
        raise TorsionFailure(
            f"exact torsion worker returned invalid JSON: {exc!r}"
        ) from exc
    if cp.returncode != 0 or result.get("status") != "completed":
        raise TorsionFailure(
            str(result.get("error") or f"worker exit={cp.returncode}")
        )
    return {
        "torsion_order": int(result["torsion_order"]),
        "torsion_invariants_json": str(result["torsion_invariants_json"]),
        "torsion_label": str(result["torsion_label"]),
    }


def persist_curve_torsion(
    db,
    curve_id,
    E,
    *,
    algorithm="sage.torsion_subgroup",
    timeout=30,
):
    """Compute and persist exact rational torsion without requiring retention.

    Auto Search uses this before expensive rank work so torsion-target campaigns
    can reject the wrong specialized torsion group early.
    """
    from rank42.db import now

    require_torsion_schema(db)
    data = compute_torsion_data_bounded(E, timeout=timeout)
    _write_torsion_state(
        db,
        int(curve_id),
        **data,
        torsion_computed_at=now(),
        torsion_algorithm=str(algorithm),
        torsion_error=None,
    )
    return data


def retained_research_curve(db, curve_id):
    """Whether a curve has crossed Rank Hunter's durable research boundary.

    Pruned rows are explicitly excluded. Otherwise retention requires an exact
    rank, a positive rigorous lower bound, a completed positive rigorous
    evidence record, or at least one rigorously independent point.
    """
    row = db.execute(
        """
        SELECT cv.id, cv.status, cv.exact_rank, cv.descent_lower, cv.generic_lower,
               EXISTS(
                   SELECT 1 FROM points p
                   WHERE p.curve_id=cv.id AND p.rigorous_independent=1
               ) AS has_rigorous_point,
               EXISTS(
                   SELECT 1 FROM rank_evidence re
                   WHERE re.curve_id=cv.id AND re.rigorous=1
                     AND re.status='completed'
                     AND COALESCE(re.rigorous_lower,0) > 0
               ) AS has_rigorous_evidence
        FROM curves cv
        WHERE cv.id=?
        """,
        (int(curve_id),),
    ).fetchone()
    if row is None or str(row["status"] or "") == "pruned":
        return False
    if row["exact_rank"] is not None:
        return True
    if max(int(row["descent_lower"] or 0), int(row["generic_lower"] or 0)) > 0:
        return True
    return bool(row["has_rigorous_point"] or row["has_rigorous_evidence"])


def _write_torsion_state(db, curve_id, **fields):
    from rank42.db import now

    require_torsion_schema(db)
    fields["updated_at"] = now()
    cols = ", ".join(f"{key}=?" for key in fields)
    db.execute(
        f"UPDATE curves SET {cols} WHERE id=?",
        list(fields.values()) + [int(curve_id)],
    )
    db.commit()


def enrich_retained_curve_torsion(
    db,
    curve_id,
    E=None,
    *,
    force=False,
    timeout=30,
    require_retained=True,
):
    """Persist torsion for a stored curve; failures are non-fatal.

    Normal callers retain the historical retained-research eligibility rule.
    The Curves batch backfill may set require_retained=False only after it has
    selected a durable Curves inventory row itself.

    Returns one of: ``stored``, ``cached``, ``ineligible``, ``missing_model``,
    ``timeout``, or ``error``.
    """
    from rank42.db import get_curve, log_event, now

    require_torsion_schema(db)
    curve_id = int(curve_id)
    row = get_curve(db, curve_id)
    if row is None:
        return "ineligible"
    if require_retained and not retained_research_curve(db, curve_id):
        return "ineligible"
    if not force and row["torsion_label"] is not None:
        return "cached"

    algorithm = "sage.torsion_subgroup"
    if E is None:
        try:
            ainvs = json.loads(row["a_invariants_json"] or "[]")
        except Exception:
            ainvs = []
        if len(ainvs) != 5:
            return "missing_model"
        try:
            from sage.all import QQ, EllipticCurve
            E = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
        except Exception as exc:
            _write_torsion_state(
                db, curve_id,
                torsion_order=None, torsion_invariants_json=None, torsion_label=None,
                torsion_computed_at=now(), torsion_algorithm=algorithm,
                torsion_error=repr(exc),
            )
            log_event(db, curve_id, "warn", f"torsion model construction failed: {exc!r}")
            return "error"

    try:
        data = compute_torsion_data_bounded(E, timeout=timeout)
    except TorsionTimeout as exc:
        _write_torsion_state(
            db, curve_id,
            torsion_order=None, torsion_invariants_json=None, torsion_label=None,
            torsion_computed_at=now(), torsion_algorithm=algorithm,
            torsion_error=repr(exc),
        )
        log_event(db, curve_id, "warn", f"rational torsion computation timed out: {exc!r}")
        return "timeout"
    except Exception as exc:
        _write_torsion_state(
            db, curve_id,
            torsion_order=None, torsion_invariants_json=None, torsion_label=None,
            torsion_computed_at=now(), torsion_algorithm=algorithm,
            torsion_error=repr(exc),
        )
        log_event(db, curve_id, "warn", f"rational torsion computation failed: {exc!r}")
        return "error"

    _write_torsion_state(
        db, curve_id, **data,
        torsion_computed_at=now(), torsion_algorithm=algorithm, torsion_error=None,
    )
    log_event(
        db, curve_id, "info",
        f"rational torsion {data['torsion_label']} (order {data['torsion_order']})",
    )
    return "stored"
