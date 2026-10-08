"""Typed result contract for isolated plugin p-adic covering searches."""
from __future__ import annotations

SCHEMA = "rank42.padic_covering_search.v1"
STATUSES = frozenset({"completed", "partial", "inconclusive"})
COMPLETENESS = frozenset({"heuristic", "bounded", "exhaustive"})


def _required_text(result, key):
    value = result.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"p-adic result requires non-empty {key}")
    return value.strip()


def validate_padic_covering_result(
    result,
    *,
    requested_prime,
    requested_precision,
    allowed_covering_ids,
):
    """Validate and normalize a successful plugin p-adic search result.

    The contract describes what search actually ran. Exact point membership and
    rank promotion remain separate core-owned checks.
    """
    if not isinstance(result, dict):
        raise ValueError("p-adic result must be an object")
    if str(result.get("schema") or "") != SCHEMA:
        raise ValueError(
            f"unsupported p-adic result schema: {result.get('schema')!r}"
        )

    status = str(result.get("status") or "")
    if status not in STATUSES:
        raise ValueError(
            "p-adic result status must be completed, partial, or inconclusive"
        )

    engine = _required_text(result, "engine")
    engine_version = _required_text(result, "engine_version")
    algorithm = _required_text(result, "algorithm")
    precision_semantics = _required_text(result, "precision_semantics")

    try:
        prime = int(result["prime"])
    except Exception as exc:
        raise ValueError("p-adic result requires integer prime") from exc
    if prime != int(requested_prime):
        raise ValueError(
            f"p-adic result prime {prime} does not match requested "
            f"{int(requested_prime)}"
        )

    try:
        precision = int(result["precision"])
    except Exception as exc:
        raise ValueError("p-adic result requires integer precision") from exc
    if precision != int(requested_precision):
        raise ValueError(
            f"p-adic result precision {precision} does not match requested "
            f"{int(requested_precision)}"
        )

    completeness = str(result.get("completeness") or "")
    if completeness not in COMPLETENESS:
        raise ValueError(
            "p-adic result completeness must be heuristic, bounded, or exhaustive"
        )

    covering_ids = result.get("covering_ids")
    if not isinstance(covering_ids, list):
        raise ValueError("p-adic result covering_ids must be a list")
    try:
        normalized_coverings = [int(value) for value in covering_ids]
    except Exception as exc:
        raise ValueError("p-adic result covering_ids must contain integers") from exc
    if len(set(normalized_coverings)) != len(normalized_coverings):
        raise ValueError("p-adic result covering_ids must not contain duplicates")
    allowed = {int(value) for value in (allowed_covering_ids or [])}
    unknown = sorted(set(normalized_coverings) - allowed)
    if unknown:
        raise ValueError(
            f"p-adic result references unrequested covering ids: {unknown}"
        )

    lifting = result.get("local_lifting_bounds")
    if not isinstance(lifting, dict):
        raise ValueError("p-adic result local_lifting_bounds must be an object")

    points = result.get("points")
    if points is None:
        points = []
    if not isinstance(points, list):
        raise ValueError("p-adic result points must be a list")

    metadata = result.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("p-adic result metadata must be an object")

    normalized = dict(result)
    normalized.update({
        "schema": SCHEMA,
        "status": status,
        "engine": engine,
        "engine_version": engine_version,
        "algorithm": algorithm,
        "prime": prime,
        "precision": precision,
        "precision_semantics": precision_semantics,
        "covering_ids": normalized_coverings,
        "local_lifting_bounds": dict(lifting),
        "completeness": completeness,
        "points": list(points),
        "metadata": dict(metadata),
        "search_complete": bool(
            status == "completed" and completeness == "exhaustive"
        ),
    })
    return normalized
