"""Exact minimization/reduction of stored affine quartic coverings.

``rank42.covering.v1`` stores a square quartic ``v^2=f(u)`` together with an
exact rational map to its target elliptic curve.  This module clears rational
denominators, delegates minimal-discriminant modeling and Cremona--Stoll
reduction to PARI in an isolated worker, completes the returned generalized
model back to a square quartic, and composes PARI's inverse change of variables
into the stored map.

The operation preserves covering geometry only.  It does not test local
solubility and does not create rank or Selmer evidence.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from sage.all import PolynomialRing, QQ, sage_eval

from rank42.coverings import SCHEMA as COVERING_SCHEMA, validate_covering
from rank42.lattice_store import store_covering, update_covering
from rank42.pointed_quartic import completed_square_polynomial, quartic_complexity


REDUCTION_WORKER = Path(__file__).with_name("pointed_quartic_reduce_worker.py")
REDUCTION_MARKER = "RANK42_POINTED_REDUCTION="


class CoveringReductionFailure(RuntimeError):
    pass


class CoveringReductionTimeout(CoveringReductionFailure):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


def _q(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _sage_q(value):
    value = _q(value)
    return QQ(value.numerator) / QQ(value.denominator)


def _trim(values):
    values = list(values)
    while len(values) > 2 and values[-1] == 0:
        values.pop()
    return tuple(values)


def _parse_worker_record(stdout, *, key):
    record = None
    for line in str(stdout or "").splitlines():
        if not line.startswith(REDUCTION_MARKER):
            continue
        try:
            candidate = json.loads(line[len(REDUCTION_MARKER):])
        except Exception:
            continue
        if str(candidate.get("key") or "") == str(key):
            record = candidate
    if record is None:
        raise CoveringReductionFailure("quartic reduction worker returned no result")
    if str(record.get("status") or "") != "completed":
        raise CoveringReductionFailure(
            str(record.get("error") or "quartic reduction worker failed")
        )
    return record


def _compose_map(expression, *, old_u, old_v, field):
    try:
        value = sage_eval(
            str(expression),
            locals={"u": old_u, "v": old_v},
        )
        return str(field(value))
    except Exception as exc:
        raise CoveringReductionFailure(
            f"could not compose exact covering map: {exc}"
        ) from exc


def validate_reduction_record(coefficients, record):
    """Validate and normalize one worker result over ``QQ``.

    PARI may return ``y^2+q(x)y=f(x)``.  With ``w=2y+q(x)`` the square model is
    ``w^2=q(x)^2+4f(x)``.  The worker's checked identities imply the inverse
    map to the input square model

        u_old=(a*u+b)/(c*u+d),
        v_old=e*v/(2*scale*(c*u+d)^2).

    The identity is independently checked here before any database mutation.
    """
    source = tuple(_q(x) for x in coefficients)
    if not 2 <= len(source) <= 5:
        raise CoveringReductionFailure("covering reduction requires degree <= 4")
    try:
        scale = int(record["scale"])
        f = tuple(_q(x) for x in record["f"])
        q = tuple(_q(x) for x in record["q"])
        transform = dict(record["transform"])
        e = _q(transform["e"])
        a, b, c, d = (_q(x) for x in transform["matrix"])
        h = tuple(_q(x) for x in transform["h"])
    except Exception as exc:
        raise CoveringReductionFailure(
            f"malformed quartic reduction result: {exc}"
        ) from exc
    if scale <= 0 or e == 0 or len(f) > 5 or len(q) > 3:
        raise CoveringReductionFailure("invalid quartic reduction dimensions")

    reduced = _trim(completed_square_polynomial(f, q))
    ring = PolynomialRing(QQ, "u")
    u = ring.gen()
    field = ring.fraction_field()
    u = field(u)
    e_q, a_q, b_q, c_q, d_q = (
        _sage_q(value) for value in (e, a, b, c, d)
    )
    denominator = c_q * u + d_q
    if denominator == 0:
        raise CoveringReductionFailure("singular quartic reduction transform")
    old_u = (a_q * u + b_q) / denominator
    source_polynomial = sum(
        QQ(value.numerator) / QQ(value.denominator) * old_u**index
        for index, value in enumerate(source)
    )
    reduced_polynomial = sum(
        QQ(value.numerator) / QQ(value.denominator) * u**index
        for index, value in enumerate(reduced)
    )
    identity = (
        e_q**2 * reduced_polynomial
        - 4 * scale**2 * denominator**4 * source_polynomial
    )
    if identity != 0:
        raise CoveringReductionFailure(
            "quartic reduction inverse-map identity failed"
        )

    return {
        "scale": scale,
        "f": f,
        "q": q,
        "coefficients": reduced,
        "e": e,
        "matrix": (a, b, c, d),
        "h": h,
        "equivalence_verified": True,
    }


def reduce_covering_model(coefficients, mapping, *, timeout=20):
    """Return an exactly equivalent reduced square quartic and composed map."""
    timeout = max(1, int(timeout))
    key = "covering"
    payload = {
        "models": [{
            "key": key,
            "coefficients": [str(_q(x)) for x in coefficients],
            "minimize": True,
        }]
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, str(REDUCTION_WORKER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CoveringReductionTimeout(
            f"covering reduction exceeded {timeout}s",
            runtime=time.monotonic() - started,
        ) from exc
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout or "").strip()[-2000:]
        raise CoveringReductionFailure(
            f"covering reduction worker exited {cp.returncode}: {tail}"
        )

    raw = _parse_worker_record(cp.stdout, key=key)
    normalized = validate_reduction_record(coefficients, raw)
    e = normalized["e"]
    a, b, c, d = normalized["matrix"]
    scale = normalized["scale"]

    target_ring = PolynomialRing(QQ, names=("u", "v"))
    target_field = target_ring.fraction_field()
    u, v = (target_field(x) for x in target_ring.gens())
    e_q, a_q, b_q, c_q, d_q = (
        _sage_q(value) for value in (e, a, b, c, d)
    )
    denominator = c_q * u + d_q
    old_u = (a_q * u + b_q) / denominator
    old_v = e_q * v / (2 * scale * denominator**2)
    composed = {
        "x": _compose_map(
            mapping["x"], old_u=old_u, old_v=old_v, field=target_field
        ),
        "y": _compose_map(
            mapping["y"], old_u=old_u, old_v=old_v, field=target_field
        ),
    }
    return {
        "status": "completed",
        "engine": "PARI hyperellred",
        "algorithm": (
            "PARI minimal-discriminant model, Cremona-Stoll reduction, "
            "and square completion"
        ),
        "coefficients": [str(x) for x in normalized["coefficients"]],
        "map": composed,
        "transform": {
            "scale": str(scale),
            "minimalized": bool(raw.get("minimalized")),
            "e": str(e),
            "matrix": [str(x) for x in normalized["matrix"]],
            "h": [str(x) for x in normalized["h"]],
            "generalized_f": [str(x) for x in normalized["f"]],
            "generalized_q": [str(x) for x in normalized["q"]],
            "inverse_u": str(old_u),
            "inverse_v": str(old_v),
        },
        "equivalence_verified": True,
        "local_solubility_checked": False,
        "elapsed_seconds": time.monotonic() - started,
    }


def _metadata(row):
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def minimize_reduce_coverings(
    db,
    *,
    curve_id,
    max_coverings=16,
    timeout=20,
    require_improvement=True,
):
    """Reduce active root coverings and persist exact child models.

    A source is marked ``reduced`` only after its child has been stored.  A
    timeout or error therefore leaves the original covering available to the
    existing fan-out search.
    """
    max_coverings = max(1, int(max_coverings))
    timeout = max(1, int(timeout))
    rows = db.execute(
        """SELECT * FROM coverings
           WHERE curve_id=? AND status IN ('ready','searched','mapped')
           ORDER BY id""",
        (int(curve_id),),
    ).fetchall()
    active_roots = [
        row for row in rows
        if not isinstance(_metadata(row).get("covering_reduction"), dict)
    ][:max_coverings]
    all_rows = db.execute(
        "SELECT * FROM coverings WHERE curve_id=? ORDER BY id",
        (int(curve_id),),
    ).fetchall()
    child_by_parent = {}
    for row in all_rows:
        reduction = _metadata(row).get("covering_reduction")
        if isinstance(reduction, dict) and reduction.get("parent_covering_id") is not None:
            child_by_parent[int(reduction["parent_covering_id"])] = row

    branches = []
    for row in active_roots:
        covering_id = int(row["id"])
        prior_child = child_by_parent.get(covering_id)
        if prior_child is not None:
            update_covering(db, covering_id, status="reduced")
            branches.append({
                "covering_id": covering_id,
                "reduced_covering_id": int(prior_child["id"]),
                "status": "completed",
                "outcome": "resumed",
                "equivalence_verified": True,
            })
            continue

        quartic = json.loads(row["quartic_json"])
        source_coefficients = quartic["coefficients"]
        before = quartic_complexity(source_coefficients)
        try:
            reduction = reduce_covering_model(
                source_coefficients,
                {"x": row["map_x"], "y": row["map_y"]},
                timeout=timeout,
            )
            after = quartic_complexity(reduction["coefficients"])
            if require_improvement and after >= before:
                branches.append({
                    "covering_id": covering_id,
                    "status": "completed",
                    "outcome": "unchanged",
                    "complexity_before": list(before),
                    "complexity_after": list(after),
                    "equivalence_verified": True,
                    "elapsed_seconds": reduction["elapsed_seconds"],
                })
                continue

            metadata = _metadata(row)
            metadata["covering_reduction"] = {
                "parent_covering_id": covering_id,
                "parent_covering_key": str(row["covering_key"]),
                "parent_status": str(row["status"]),
                "engine": reduction["engine"],
                "algorithm": reduction["algorithm"],
                "transform": reduction["transform"],
                "complexity_before": list(before),
                "complexity_after": list(after),
                "equivalence_verified": True,
                "local_solubility_checked": False,
            }
            child_data = {
                "schema": COVERING_SCHEMA,
                "curve_id": int(row["curve_id"]),
                "lattice_id": row["lattice_id"],
                "hole_id": row["hole_id"],
                "quartic": {
                    **quartic,
                    "coefficients": reduction["coefficients"],
                },
                "map": reduction["map"],
                "metadata": metadata,
            }
            validate_covering(child_data)
            child = store_covering(db, child_data)
            update_covering(db, covering_id, status="reduced")
            branches.append({
                "covering_id": covering_id,
                "reduced_covering_id": int(child["id"]),
                "status": "completed",
                "outcome": "reduced",
                "complexity_before": list(before),
                "complexity_after": list(after),
                "equivalence_verified": True,
                "local_solubility_checked": False,
                "elapsed_seconds": reduction["elapsed_seconds"],
            })
        except CoveringReductionTimeout as exc:
            branches.append({
                "covering_id": covering_id,
                "status": "timeout",
                "outcome": "timeout",
                "error": str(exc),
                "elapsed_seconds": exc.runtime,
            })
        except Exception as exc:
            branches.append({
                "covering_id": covering_id,
                "status": "error",
                "outcome": "error",
                "error": repr(exc),
            })

    reduced_count = sum(branch.get("outcome") == "reduced" for branch in branches)
    unchanged_count = sum(branch.get("outcome") == "unchanged" for branch in branches)
    completed_count = sum(branch.get("status") == "completed" for branch in branches)
    timeout_count = sum(branch.get("status") == "timeout" for branch in branches)
    error_count = sum(branch.get("status") == "error" for branch in branches)
    if not active_roots:
        status = "completed" if rows else "inconclusive"
        reason = "no_unreduced_coverings" if rows else "no_exact_coverings_available"
    elif completed_count == len(branches):
        status, reason = "completed", None
    elif completed_count:
        status, reason = "partial", "covering_reduction_branch_coverage_partial"
    elif timeout_count and not error_count:
        status, reason = "timeout", "covering_reduction_timeout"
    else:
        status, reason = "error", "covering_reduction_failed"

    return {
        "status": status,
        "reason": reason,
        "module_label": "Covering Minimization & Reduction",
        "engine": "PARI hyperellred",
        "coverings_seen": len(active_roots),
        "coverings_reduced": reduced_count,
        "coverings_unchanged": unchanged_count,
        "completed_branches": completed_count,
        "timeout_branches": timeout_count,
        "error_branches": error_count,
        "branches": branches,
        "equivalence_scope": "exact_covering_geometry",
        "local_solubility_checked": False,
        "rank_evidence_created": False,
        "retryable": bool(timeout_count or error_count),
        "attempt_complete": True,
        "mathematical_outcome": status,
    }
