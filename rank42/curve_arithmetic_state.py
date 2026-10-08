"""Local arithmetic authority plus provenance-aware display projection.

Rank Hunter's persisted/computed Curve Arithmetic values are the scientific
authority. Exact catalog matches remain external reference evidence. Read
projections may use one unambiguous catalog value as a labeled display fallback,
but scientific comparison/publication always targets strict local authority.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import reduce
from operator import mul

def _table_exists(db, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _parse_positive_int(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_nonzero_int(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed != 0 else None


def _parse_real_text(value):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite():
        return None
    if parsed == 0:
        return "0"
    return format(parsed.normalize(), "f")


def _parse_bad_primes(value):
    if value in (None, ""):
        return None
    try:
        raw = json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None
    if not isinstance(raw, list):
        return None
    out = []
    try:
        for item in raw:
            prime = int(str(item))
            if prime <= 1:
                return None
            out.append(prime)
    except (TypeError, ValueError):
        return None
    return sorted(set(out))


def _parse_torsion(row) -> tuple[dict | None, list[str]]:
    keys = set(row.keys())
    if "torsion_label" not in keys:
        return None, []
    label = row["torsion_label"]
    order = row["torsion_order"] if "torsion_order" in keys else None
    raw_invariants = row["torsion_invariants_json"] if "torsion_invariants_json" in keys else None
    if label is None and order is None and raw_invariants is None:
        return None, []

    issues = []
    invariants = None
    if raw_invariants not in (None, ""):
        try:
            decoded = json.loads(raw_invariants)
            if isinstance(decoded, list):
                invariants = [int(x) for x in decoded if int(x) > 1]
            else:
                issues.append("invalid torsion invariants")
        except Exception:
            issues.append("invalid torsion invariants")

    parsed_order = None
    if order is not None:
        try:
            parsed_order = int(order)
            if parsed_order <= 0:
                issues.append("invalid torsion order")
                parsed_order = None
        except (TypeError, ValueError):
            issues.append("invalid torsion order")

    if invariants is not None and parsed_order is not None:
        expected = reduce(mul, invariants, 1)
        if expected != parsed_order:
            issues.append("torsion order/invariants mismatch")

    return {
        "label": str(label) if label is not None else None,
        "order": parsed_order,
        "invariants": invariants,
    }, issues


def _size_metrics_row(db, curve_id: int):
    if not _table_exists(db, "curve_size_metrics"):
        return None
    row = db.execute(
        """SELECT naive_height,faltings_height,precision_bits,method,computed_at
           FROM curve_size_metrics
           WHERE curve_id=?""",
        (int(curve_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def _conductor_attempt(db, curve_id: int):
    if not _table_exists(db, "conductor_backfill_attempts"):
        return None
    row = db.execute(
        """SELECT status,method,detail,attempted_at
           FROM conductor_backfill_attempts
           WHERE curve_id=?""",
        (int(curve_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def _reference_rows(db, curve_id: int) -> list[dict]:
    if not (
        _table_exists(db, "curve_catalog_checks")
        and _table_exists(db, "external_curves")
    ):
        return []
    rows = db.execute(
        """
        SELECT cc.curve_id, cc.source, cc.source_label, cc.source_url, cc.checked_at,
               ec.conductor, ec.discriminant, ec.bad_primes_json,
               ec.naive_height, ec.faltings_height, ec.raw_json,
               ec.updated_at AS reference_updated_at
        FROM curve_catalog_checks cc
        JOIN external_curves ec
          ON ec.source=cc.source
         AND ec.source_id=cc.source_label
         AND ec.active=1
        WHERE cc.curve_id=?
          AND cc.status='known'
        ORDER BY cc.source
        """,
        (int(curve_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _reference_value(field: str, row: dict):
    if field == "conductor":
        return _parse_positive_int(row.get("conductor"))
    if field == "discriminant":
        return _parse_nonzero_int(row.get("discriminant"))
    if field == "bad_primes":
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except Exception:
            raw = {}
        if not isinstance(raw, dict) or "bad_primes" not in raw:
            return None
        return _parse_bad_primes(row.get("bad_primes_json"))
    if field == "naive_height":
        return _parse_real_text(row.get("naive_height"))
    if field == "faltings_height":
        return _parse_real_text(row.get("faltings_height"))
    return None


def _resolve_field(field: str, local_value, local_provenance: dict | None, references: list[dict]):
    ref_values = []
    for row in references:
        value = _reference_value(field, row)
        if value is None:
            continue
        ref_values.append(
            {
                "source": str(row["source"]),
                "source_label": row.get("source_label"),
                "source_url": row.get("source_url"),
                "checked_at": row.get("checked_at"),
                "reference_updated_at": row.get("reference_updated_at"),
                "value": value,
            }
        )

    normalized = {
        json.dumps(ref["value"], sort_keys=True, separators=(",", ":"))
        for ref in ref_values
    }
    external_conflict = len(normalized) > 1
    local_reference_conflict = False
    if local_value is not None and ref_values:
        local_key = json.dumps(local_value, sort_keys=True, separators=(",", ":"))
        local_reference_conflict = any(
            json.dumps(ref["value"], sort_keys=True, separators=(",", ":")) != local_key
            for ref in ref_values
        )

    if local_value is not None:
        value = local_value
        provenance = dict(local_provenance or {})
    elif ref_values and not external_conflict:
        value = ref_values[0]["value"]
        sources = sorted({ref["source"] for ref in ref_values})
        provenance = {
            "kind": "external_reference",
            "source": ",".join(sources),
            "method": "exact_catalog_match",
            "detail": "local invariant not computed",
        }
    else:
        value = None
        provenance = {
            "kind": "missing",
            "source": None,
            "method": None,
            "detail": None,
        }

    conflict = bool(external_conflict or local_reference_conflict)
    return value, provenance, ref_values, conflict


def _project_curve_arithmetic_state(
    curve: dict,
    *,
    references=None,
    conductor_attempt=None,
    size_metrics=None,
) -> dict:
    """Project one already-loaded curve plus provenance rows into authority state."""
    curve = dict(curve)
    curve_id = int(curve["id"])
    issues = []
    references = [dict(row) for row in (references or [])]

    conductor = _parse_positive_int(curve.get("conductor"))
    if curve.get("conductor") not in (None, "") and conductor is None:
        issues.append("invalid local conductor")

    discriminant = _parse_nonzero_int(curve.get("discriminant"))
    if curve.get("discriminant") not in (None, "") and discriminant is None:
        issues.append("invalid local discriminant")

    bad_primes = _parse_bad_primes(curve.get("bad_primes_json"))
    if curve.get("bad_primes_json") not in (None, "") and bad_primes is None:
        issues.append("invalid local bad-prime set")

    root_number = None
    if curve.get("root_number") is not None:
        try:
            candidate = int(curve["root_number"])
            if candidate in (-1, 1):
                root_number = candidate
            else:
                issues.append("invalid local root number")
        except (TypeError, ValueError):
            issues.append("invalid local root number")

    attempt = dict(conductor_attempt) if conductor_attempt is not None else None
    conductor_provenance = {
        "kind": "local_persisted",
        "source": "curves.conductor",
        "method": None,
        "detail": None,
    }
    if conductor is not None and attempt is not None and attempt.get("status") == "done":
        method = attempt.get("method")
        conductor_provenance.update(
            {
                "kind": (
                    "external_reference_cached_local"
                    if method == "icarm_exact_match"
                    else "local_computed"
                ),
                "source": "icarm" if method == "icarm_exact_match" else "curves.conductor",
                "method": method,
                "detail": attempt.get("detail"),
                "computed_at": attempt.get("attempted_at"),
            }
        )

    local_conductor = conductor
    local_discriminant = discriminant
    local_bad_primes = bad_primes
    local_conductor_provenance = (
        dict(conductor_provenance)
        if local_conductor is not None
        else {"kind": "missing", "source": None, "method": None, "detail": None}
    )
    local_discriminant_provenance = (
        {
            "kind": "local_persisted",
            "source": "curves.discriminant",
            "method": None,
            "detail": None,
        }
        if local_discriminant is not None
        else {"kind": "missing", "source": None, "method": None, "detail": None}
    )
    local_bad_primes_provenance = (
        {
            "kind": "local_persisted",
            "source": "curves.bad_primes_json",
            "method": None,
            "detail": None,
        }
        if local_bad_primes is not None
        else {"kind": "missing", "source": None, "method": None, "detail": None}
    )

    conductor, conductor_provenance, conductor_refs, conductor_conflict = _resolve_field(
        "conductor",
        local_conductor,
        local_conductor_provenance if local_conductor is not None else None,
        references,
    )
    discriminant, discriminant_provenance, discriminant_refs, discriminant_conflict = _resolve_field(
        "discriminant",
        local_discriminant,
        local_discriminant_provenance if local_discriminant is not None else None,
        references,
    )
    bad_primes, bad_primes_provenance, bad_prime_refs, bad_primes_conflict = _resolve_field(
        "bad_primes",
        local_bad_primes,
        local_bad_primes_provenance if local_bad_primes is not None else None,
        references,
    )

    size_metrics = dict(size_metrics) if size_metrics is not None else None
    local_naive = _parse_real_text(size_metrics.get("naive_height")) if size_metrics else None
    local_faltings = _parse_real_text(size_metrics.get("faltings_height")) if size_metrics else None
    if size_metrics and size_metrics.get("naive_height") not in (None, "") and local_naive is None:
        issues.append("invalid local naive height")
    if size_metrics and size_metrics.get("faltings_height") not in (None, "") and local_faltings is None:
        issues.append("invalid local Faltings height")

    size_provenance_base = None
    if size_metrics is not None:
        size_provenance_base = {
            "kind": "local_computed",
            "source": "curve_size_metrics",
            "method": size_metrics.get("method"),
            "precision_bits": size_metrics.get("precision_bits"),
            "computed_at": size_metrics.get("computed_at"),
            "detail": None,
        }
    local_naive_provenance = (
        dict(size_provenance_base)
        if local_naive is not None and size_provenance_base is not None
        else {"kind": "missing", "source": None, "method": None, "detail": None}
    )
    local_faltings_provenance = (
        dict(size_provenance_base)
        if local_faltings is not None and size_provenance_base is not None
        else {"kind": "missing", "source": None, "method": None, "detail": None}
    )
    naive_height, naive_provenance, naive_refs, naive_conflict = _resolve_field(
        "naive_height",
        local_naive,
        local_naive_provenance if local_naive is not None else None,
        references,
    )
    faltings_height, faltings_provenance, faltings_refs, faltings_conflict = _resolve_field(
        "faltings_height",
        local_faltings,
        local_faltings_provenance if local_faltings is not None else None,
        references,
    )

    torsion, torsion_issues = _parse_torsion(curve)
    issues.extend(torsion_issues)
    torsion_provenance = {
        "kind": "missing",
        "source": None,
        "method": None,
        "detail": None,
    }
    if torsion is not None:
        torsion_provenance = {
            "kind": "local_computed" if curve.get("torsion_computed_at") else "local_persisted",
            "source": "curves.torsion_*",
            "method": curve.get("torsion_algorithm"),
            "computed_at": curve.get("torsion_computed_at"),
            "detail": curve.get("torsion_error"),
        }

    root_provenance = {
        "kind": "local_persisted" if root_number is not None else "missing",
        "source": "curves.root_number" if root_number is not None else None,
        "method": None,
        "detail": None,
    }

    local_log_conductor = (
        math.log(local_conductor) if local_conductor is not None else None
    )
    local_log_provenance = {
        "kind": "derived" if local_log_conductor is not None else "missing",
        "source": "local.conductor" if local_log_conductor is not None else None,
        "method": "natural_log" if local_log_conductor is not None else None,
        "detail": None,
    }
    log_conductor = math.log(conductor) if conductor is not None else None
    log_provenance = {
        "kind": "derived" if log_conductor is not None else "missing",
        "source": "conductor" if log_conductor is not None else None,
        "method": "natural_log" if log_conductor is not None else None,
        "detail": None,
    }

    conflicts = []
    for field, conflict in (
        ("conductor", conductor_conflict),
        ("discriminant", discriminant_conflict),
        ("bad_primes", bad_primes_conflict),
        ("naive_height", naive_conflict),
        ("faltings_height", faltings_conflict),
    ):
        if conflict:
            conflicts.append(field)

    return {
        "curve_id": curve_id,
        # Top-level values remain the display projection for compatibility:
        # local authority first, then one unambiguous exact catalog fallback.
        "conductor": conductor,
        "log_conductor": log_conductor,
        "discriminant": discriminant,
        "bad_primes": bad_primes,
        "root_number": root_number,
        "torsion": torsion,
        "naive_height": naive_height,
        "faltings_height": faltings_height,
        "local": {
            "conductor": local_conductor,
            "log_conductor": local_log_conductor,
            "discriminant": local_discriminant,
            "bad_primes": local_bad_primes,
            "root_number": root_number,
            "torsion": torsion,
            "naive_height": local_naive,
            "faltings_height": local_faltings,
        },
        "display": {
            "conductor": conductor,
            "log_conductor": log_conductor,
            "discriminant": discriminant,
            "bad_primes": bad_primes,
            "root_number": root_number,
            "torsion": torsion,
            "naive_height": naive_height,
            "faltings_height": faltings_height,
        },
        "local_provenance": {
            "conductor": local_conductor_provenance,
            "log_conductor": local_log_provenance,
            "discriminant": local_discriminant_provenance,
            "bad_primes": local_bad_primes_provenance,
            "root_number": root_provenance,
            "torsion": torsion_provenance,
            "naive_height": local_naive_provenance,
            "faltings_height": local_faltings_provenance,
        },
        "provenance": {
            "conductor": conductor_provenance,
            "log_conductor": log_provenance,
            "discriminant": discriminant_provenance,
            "bad_primes": bad_primes_provenance,
            "root_number": root_provenance,
            "torsion": torsion_provenance,
            "naive_height": naive_provenance,
            "faltings_height": faltings_provenance,
        },
        "references": {
            "conductor": conductor_refs,
            "discriminant": discriminant_refs,
            "bad_primes": bad_prime_refs,
            "naive_height": naive_refs,
            "faltings_height": faltings_refs,
        },
        "conflicts": conflicts,
        "issues": issues,
        "complete": {
            "conductor": conductor is not None,
            "discriminant": discriminant is not None,
            "bad_primes": bad_primes is not None,
            "root_number": root_number is not None,
            "torsion": torsion is not None,
            "naive_height": naive_height is not None,
            "faltings_height": faltings_height is not None,
        },
        "local_complete": {
            "conductor": local_conductor is not None,
            "discriminant": local_discriminant is not None,
            "bad_primes": local_bad_primes is not None,
            "root_number": root_number is not None,
            "torsion": torsion is not None,
            "naive_height": local_naive is not None,
            "faltings_height": local_faltings is not None,
        },
        "curve": curve,
    }


def get_curve_arithmetic_state(db, curve_id: int) -> dict:
    """Return strict local authority plus a provenance-aware display projection."""
    row = db.execute("SELECT * FROM curves WHERE id=?", (int(curve_id),)).fetchone()
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    return _project_curve_arithmetic_state(
        dict(row),
        references=_reference_rows(db, int(curve_id)),
        conductor_attempt=_conductor_attempt(db, int(curve_id)),
        size_metrics=_size_metrics_row(db, int(curve_id)),
    )


def compare_curve_arithmetic_values(db, curve_id: int, proposed: dict) -> dict:
    """Compare exact proposed globals against strict local arithmetic authority.

    External catalog values remain reference evidence. They are reported
    separately and may disagree, but they never become the local authority that
    scientific producers compare against.
    """
    state = get_curve_arithmetic_state(db, int(curve_id))
    local = dict(state.get("local") or {})
    local_provenance = dict(state.get("local_provenance") or {})
    references = dict(state.get("references") or {})
    normalizers = {
        "conductor": _parse_positive_int,
        "discriminant": _parse_nonzero_int,
        "bad_primes": lambda value: (
            sorted({int(x) for x in value}) if value is not None else None
        ),
        "root_number": lambda value: (
            int(value) if value is not None and int(value) in (-1, 1) else None
        ),
    }
    checked = {}
    conflicts = []
    reference_conflicts = []
    missing = []
    matches = []
    for field, raw in dict(proposed or {}).items():
        if field not in normalizers:
            continue
        try:
            value = normalizers[field](raw)
        except Exception:
            value = None
        authority = local.get(field)
        ref_rows = list(references.get(field) or [])
        ref_values = [rec.get("value") for rec in ref_rows]
        ref_keys = {
            json.dumps(ref, sort_keys=True, separators=(",", ":"))
            for ref in ref_values
        }
        reference_status = "none"
        if ref_values:
            if len(ref_keys) > 1:
                reference_status = "reference_conflict"
                reference_conflicts.append(field)
            elif value is not None and ref_values[0] == value:
                reference_status = "match"
            elif value is not None:
                reference_status = "disagree"
                reference_conflicts.append(field)
            else:
                reference_status = "present"

        record = {
            "proposed": value,
            "authority": authority,
            "authority_provenance": dict(local_provenance.get(field) or {}),
            "display_value": state.get(field),
            "display_provenance": dict(
                (state.get("provenance") or {}).get(field) or {}
            ),
            "references": ref_rows,
            "reference_status": reference_status,
        }
        if value is None:
            record["status"] = "invalid_proposed"
        elif authority is None:
            record["status"] = "missing_authority"
            missing.append(field)
        elif authority == value:
            record["status"] = "match"
            matches.append(field)
        else:
            record["status"] = "conflict"
            conflicts.append(field)
        checked[field] = record
    return {
        "curve_id": int(curve_id),
        "fields": checked,
        "matches": sorted(matches),
        "missing_authority": sorted(missing),
        "conflicts": sorted(conflicts),
        "reference_conflicts": sorted(set(reference_conflicts)),
        "authority_consistent": not conflicts,
        "consistent": not conflicts and not reference_conflicts,
    }


def compute_curve_arithmetic_values(E) -> dict:
    """Compute the exact global arithmetic bundle used for curve materialization.

    This preserves the historical General Hunt semantics: the stored
    discriminant is the discriminant of the supplied model, while bad primes
    are derived from a global-minimal discriminant when that computation is
    available.
    """
    # Projection/read helpers in this module are imported by the lightweight
    # Streamlit UI process. Keep Sage confined to the exact-computation path,
    # which runs under the configured scientific interpreter.
    from sage.all import ZZ, prime_divisors

    disc = ZZ(E.discriminant())
    try:
        minimal_disc = ZZ(E.global_minimal_model().discriminant())
        bad_prime_source = minimal_disc
        bad_prime_method = "global_minimal_discriminant"
    except Exception:
        bad_prime_source = disc
        bad_prime_method = "model_discriminant_fallback"

    values = {
        "discriminant": int(disc),
        "bad_primes": [
            int(p) for p in prime_divisors(abs(bad_prime_source))
        ],
        "bad_primes_method": bad_prime_method,
    }
    try:
        values["conductor"] = int(E.conductor())
    except Exception as exc:
        values["conductor_error"] = repr(exc)
    try:
        values["root_number"] = int(E.root_number())
    except Exception as exc:
        values["root_number_error"] = repr(exc)
    return values


def publish_curve_arithmetic_values(
    db,
    curve_id: int,
    values: dict,
    *,
    source: str,
    method: str,
) -> dict:
    """Conservatively publish exact local arithmetic values for one curve.

    A valid exact local computation owns local arithmetic state. External
    catalog references may agree or disagree, but they never block publication
    into an otherwise-missing local field. Any disagreement remains visible in
    the projected conflict/provenance state.
    """
    curve_id = int(curve_id)
    values = dict(values or {})
    normalizers = {
        "conductor": _parse_positive_int,
        "discriminant": _parse_nonzero_int,
        "bad_primes": lambda value: (
            sorted({int(x) for x in value}) if value is not None else None
        ),
        "root_number": lambda value: (
            int(value) if value is not None and int(value) in (-1, 1) else None
        ),
    }
    columns = {
        "conductor": "conductor",
        "discriminant": "discriminant",
        "bad_primes": "bad_primes_json",
        "root_number": "root_number",
    }
    raw_row = db.execute(
        "SELECT conductor,discriminant,bad_primes_json,root_number "
        "FROM curves WHERE id=?",
        (curve_id,),
    ).fetchone()
    if raw_row is None:
        raise ValueError(f"curve #{curve_id} not found")

    state_before = get_curve_arithmetic_state(db, curve_id)
    local_before = dict(state_before.get("local") or {})
    updates = {}
    fields = {}
    local_conflicts = []

    for field, normalizer in normalizers.items():
        if field not in values:
            continue
        try:
            proposed = normalizer(values.get(field))
        except Exception:
            proposed = None

        column = columns[field]
        raw_local = raw_row[column]
        if field == "bad_primes":
            local = _parse_bad_primes(raw_local)
        else:
            local = normalizer(raw_local)

        record = {
            "proposed": proposed,
            "local_before": local,
            "authority_before": local_before.get(field),
            "display_before": state_before.get(field),
            "display_provenance_before": dict(
                (state_before.get("provenance") or {}).get(field) or {}
            ),
            "references_before": list(
                (state_before.get("references") or {}).get(field) or []
            ),
            "status": None,
        }
        if proposed is None:
            record["status"] = "invalid_proposed"
            local_conflicts.append(field)
        elif raw_local not in (None, "") and local is None:
            record["status"] = "invalid_local_authority"
            local_conflicts.append(field)
        elif local is not None:
            if local == proposed:
                record["status"] = "match_local"
            else:
                record["status"] = "conflict_local"
                local_conflicts.append(field)
        else:
            record["status"] = "promote_missing"
            updates[column] = (
                json.dumps(proposed)
                if field == "bad_primes"
                else str(proposed)
                if field in {"conductor", "discriminant"}
                else int(proposed)
            )
        fields[field] = record

    if updates:
        assignments = ", ".join(f"{column}=?" for column in updates)
        assignments += ", updated_at=?"
        params = list(updates.values()) + [
            datetime.now(timezone.utc).isoformat(),
            curve_id,
        ]
        db.execute(
            f"UPDATE curves SET {assignments} WHERE id=?",
            params,
        )
        db.commit()

    state_after = get_curve_arithmetic_state(db, curve_id)
    check = compare_curve_arithmetic_values(
        db,
        curve_id,
        {
            field: values[field]
            for field in normalizers
            if field in values
        },
    )
    reference_conflicts = sorted(
        field
        for field in normalizers
        if field in values and field in set(state_after.get("conflicts") or [])
    )
    authority_conflicts = sorted(
        set(local_conflicts) | set(check.get("conflicts") or [])
    )
    conflicts = sorted(set(authority_conflicts) | set(reference_conflicts))
    provenance = {
        "kind": "local_computed",
        "source": str(source),
        "method": str(method),
    }
    local_after = dict(state_after.get("local") or {})
    return {
        "curve_id": curve_id,
        "published_fields": sorted(updates),
        "conflicts": conflicts,
        "authority_conflicts": authority_conflicts,
        "reference_conflicts": reference_conflicts,
        "consistent": not conflicts,
        "fields": fields,
        "provenance": provenance,
        "authority": {
            field: local_after.get(field)
            for field in normalizers
            if field in values
        },
        "display": {
            field: state_after.get(field)
            for field in normalizers
            if field in values
        },
        "authority_check": check,
    }


def compute_and_publish_curve_arithmetic(
    db,
    curve_id: int,
    E,
    *,
    source: str,
    method: str,
) -> dict:
    """Compute and conservatively publish the shared curve-arithmetic bundle."""
    values = compute_curve_arithmetic_values(E)
    publication = publish_curve_arithmetic_values(
        db,
        curve_id,
        values,
        source=source,
        method=method,
    )
    return {
        "values": values,
        "publication": publication,
    }


def publish_curve_root_number(
    db,
    curve_id: int,
    root_number,
    *,
    source: str,
    method: str,
) -> dict:
    """Publish one exact global root number through the arithmetic authority.

    Missing authority is filled, matching authority is accepted, and a
    conflicting persisted value is never overwritten. The returned provenance
    is intended to be persisted by the calling scientific stage alongside its
    own result record.
    """
    try:
        proposed = int(root_number)
    except (TypeError, ValueError) as exc:
        raise ValueError("root number must be +1 or -1") from exc
    if proposed not in (-1, 1):
        raise ValueError("root number must be +1 or -1")

    before = get_curve_arithmetic_state(db, int(curve_id))
    authority = before.get("root_number")
    raw_authority = (before.get("curve") or {}).get("root_number")
    provenance = {
        "kind": "local_computed",
        "source": str(source),
        "method": str(method),
    }

    if raw_authority is not None and authority is None:
        return {
            "status": "invalid_authority",
            "published": False,
            "conflict": True,
            "proposed": proposed,
            "authority": None,
            "provenance": provenance,
            "authority_check": compare_curve_arithmetic_values(
                db, int(curve_id), {"root_number": proposed}
            ),
        }

    if authority is not None and int(authority) != proposed:
        return {
            "status": "conflict",
            "published": False,
            "conflict": True,
            "proposed": proposed,
            "authority": int(authority),
            "provenance": provenance,
            "authority_check": compare_curve_arithmetic_values(
                db, int(curve_id), {"root_number": proposed}
            ),
        }

    status = "match"
    if authority is None:
        db.execute(
            "UPDATE curves SET root_number=?, updated_at=? WHERE id=?",
            (
                proposed,
                datetime.now(timezone.utc).isoformat(),
                int(curve_id),
            ),
        )
        db.commit()
        status = "promoted"

    check = compare_curve_arithmetic_values(
        db, int(curve_id), {"root_number": proposed}
    )
    return {
        "status": status,
        "published": bool(
            not check.get("conflicts")
            and "root_number" in (check.get("matches") or [])
        ),
        "conflict": bool(check.get("conflicts")),
        "proposed": proposed,
        "authority": get_curve_arithmetic_state(
            db, int(curve_id)
        ).get("root_number"),
        "provenance": provenance,
        "authority_check": check,
    }


def curve_arithmetic_state_map(db, curve_ids) -> dict[int, dict]:
    """Bulk read projection for inventory callers without per-curve SQL queries."""
    ids = sorted({int(curve_id) for curve_id in curve_ids})
    if not ids:
        return {}

    curves = {}
    references = {}
    attempts = {}
    size_metrics = {}
    has_attempts = _table_exists(db, "conductor_backfill_attempts")
    has_size_metrics = _table_exists(db, "curve_size_metrics")

    # Stay comfortably below SQLite builds that retain the historical 999
    # host-parameter limit while still avoiding per-row inventory queries.
    batch_size = 500
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        placeholders = ",".join("?" for _ in batch)

        curve_rows = db.execute(
            f"SELECT * FROM curves WHERE id IN ({placeholders})",
            batch,
        ).fetchall()
        for row in curve_rows:
            curve_id = int(row["id"])
            curves[curve_id] = dict(row)
            references.setdefault(curve_id, [])

        reference_rows = db.execute(
            f"""
            SELECT cc.curve_id, cc.source, cc.source_label, cc.source_url, cc.checked_at,
                   ec.conductor, ec.discriminant, ec.bad_primes_json,
                   ec.naive_height, ec.faltings_height, ec.raw_json,
                   ec.updated_at AS reference_updated_at
            FROM curve_catalog_checks cc
            JOIN external_curves ec
              ON ec.source=cc.source
             AND ec.source_id=cc.source_label
             AND ec.active=1
            WHERE cc.curve_id IN ({placeholders})
              AND cc.status='known'
            ORDER BY cc.curve_id, cc.source
            """,
            batch,
        ).fetchall()
        for row in reference_rows:
            references.setdefault(int(row["curve_id"]), []).append(dict(row))

        if has_attempts:
            attempt_rows = db.execute(
                f"""SELECT curve_id,status,method,detail,attempted_at
                    FROM conductor_backfill_attempts
                    WHERE curve_id IN ({placeholders})""",
                batch,
            ).fetchall()
            attempts.update(
                {int(row["curve_id"]): dict(row) for row in attempt_rows}
            )

        if has_size_metrics:
            metric_rows = db.execute(
                f"""SELECT curve_id,naive_height,faltings_height,
                           precision_bits,method,computed_at
                    FROM curve_size_metrics
                    WHERE curve_id IN ({placeholders})""",
                batch,
            ).fetchall()
            size_metrics.update(
                {int(row["curve_id"]): dict(row) for row in metric_rows}
            )

    return {
        curve_id: _project_curve_arithmetic_state(
            curve,
            references=references.get(curve_id, []),
            conductor_attempt=attempts.get(curve_id),
            size_metrics=size_metrics.get(curve_id),
        )
        for curve_id, curve in curves.items()
    }
