"""Central persistence/projection for exact-certified Mordell-Weil subgroups."""
from __future__ import annotations

import hashlib
import json

from rank42.db import get_curve, update_curve
from rank42.points import upsert_point
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence


def _point_key(P):
    Q = -P
    return min(
        (str(P[0]), str(P[1])),
        (str(Q[0]), str(Q[1])),
    )


def _normalize_points(E, points):
    normalized = []
    seen = set()
    for raw in points:
        try:
            P = E(raw[0], raw[1])
        except Exception as exc:
            raise ValueError(f"invalid exact point for certified subgroup: {raw!r}") from exc
        if P.is_zero():
            raise ValueError("identity point cannot be a certified subgroup witness")
        key = _point_key(P)
        if key in seen:
            raise ValueError("certified subgroup contains duplicate/sign-equivalent witnesses")
        seen.add(key)
        normalized.append(P)
    if not normalized:
        raise ValueError("certified subgroup must contain at least one nonzero point")
    return normalized


def _generator_count(row):
    try:
        values = json.loads(row["generators_json"] or "[]")
    except Exception:
        return 0
    return len(values) if isinstance(values, list) else 0


def promote_certified_subgroup(
    db,
    *,
    curve_id,
    E,
    points,
    source,
    search_ref,
    certificate=None,
    metadata=None,
    point_metadata=None,
    engine="exact_independence",
):
    """Persist an already exact-certified independent subgroup and promote rank.

    This function deliberately does not run the independence certificate itself.
    Callers supply the certified independent point set. The service owns durable
    witness persistence, certified-subgroup rank evidence, conservative reduced
    compatibility projection, and certificate provenance.
    """
    curve_id = int(curve_id)
    row = get_curve(db, curve_id)
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    if certificate is None:
        raise ValueError("certified subgroup promotion requires certificate provenance")

    witnesses = _normalize_points(E, list(points))
    lower = len(witnesses)
    if point_metadata is None:
        per_witness_metadata = [{} for _ in witnesses]
    else:
        per_witness_metadata = list(point_metadata)
        if len(per_witness_metadata) != lower:
            raise ValueError("point_metadata must align one-to-one with certified subgroup witnesses")
        if not all(isinstance(item, dict) for item in per_witness_metadata):
            raise ValueError("point_metadata entries must be mappings")
    point_pairs = [[str(P[0]), str(P[1])] for P in witnesses]
    fingerprint_payload = json.dumps(point_pairs, sort_keys=True, separators=(",", ":"))
    witness_fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
    certificate_payload = json.dumps(certificate, sort_keys=True, default=str, separators=(",", ":"))
    certificate_fingerprint = hashlib.sha256(certificate_payload.encode("utf-8")).hexdigest()

    base_metadata = dict(metadata or {})
    base_metadata.update(
        {
            "promotion_service": "certified_subgroup",
            "certificate": certificate,
            "witness_fingerprint": witness_fingerprint,
            "certificate_fingerprint": certificate_fingerprint,
        }
    )

    for index, P in enumerate(witnesses):
        x_text, y_text = str(P[0]), str(P[1])
        existing = db.execute(
            """SELECT exact_verified,rigorous_independent,independence_status
               FROM points
               WHERE curve_id=? AND x=? AND y=?""",
            (curve_id, x_text, y_text),
        ).fetchone()
        if (
            existing is not None
            and int(existing["exact_verified"] or 0) == 1
            and int(existing["rigorous_independent"] or 0) == 1
            and str(existing["independence_status"]) == "rigorous_independent"
        ):
            continue
        witness_metadata = dict(per_witness_metadata[index])
        witness_metadata.update(base_metadata)
        witness_metadata["certificate_point_index"] = index + 1
        upsert_point(
            db,
            curve_id=curve_id,
            x=x_text,
            y=y_text,
            source=source,
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
            search_ref=search_ref,
            metadata=witness_metadata,
        )

    evidence_id = record_rank_evidence(
        db,
        curve_id=curve_id,
        model=E.a_invariants(),
        data={
            "engine": str(engine),
            "evidence_type": "certified_subgroup",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": None,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": point_pairs,
            "options": {
                "source": str(source),
                "search_ref": None if search_ref is None else str(search_ref),
                "witness_fingerprint": witness_fingerprint,
                "certificate_fingerprint": certificate_fingerprint,
            },
            "stdout_summary": json.dumps(
                {
                    "certificate": certificate,
                    "metadata": base_metadata,
                    "point_metadata": per_witness_metadata,
                    "witnesses": point_pairs,
                },
                sort_keys=True,
                default=str,
            ),
        },
    )
    state = apply_reduced_rank_state(db, curve_id)

    projected = False
    if not state["inconsistent"] and int(state["rigorous_lower"] or 0) >= lower:
        current = get_curve(db, curve_id)
        fields = {"error": None}
        if _generator_count(current) < lower:
            fields["generators_json"] = json.dumps(point_pairs)
        if state["exact_rank"] is None:
            fields["status"] = "proven_lower"
        update_curve(db, curve_id, **fields)
        projected = True

    return {
        "curve_id": curve_id,
        "evidence_id": int(evidence_id),
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "rank_inconsistent": bool(state["inconsistent"]),
        "projected": bool(projected),
        "witness_count": lower,
        "witness_fingerprint": witness_fingerprint,
        "certificate_fingerprint": certificate_fingerprint,
    }

def promote_exact_rank_certificate(
    db,
    *,
    curve_id,
    model,
    exact_rank,
    certificate,
    engine="exact_rank_certificate",
    source=None,
    metadata=None,
):
    """Persist one rigorous proof-mode exact-rank certificate.

    Exact-rank proof is distinct from point/subgroup certification.  The
    certificate becomes matching rigorous lower/upper evidence and compatibility
    columns are updated only through the shared reducer.
    """
    curve_id = int(curve_id)
    row = get_curve(db, curve_id)
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    if certificate is None:
        raise ValueError("exact-rank promotion requires certificate provenance")
    exact = int(exact_rank)
    if exact < 0:
        raise ValueError("exact rank must be non-negative")

    model_values = [str(x) for x in model]
    certificate_payload = json.dumps(
        certificate,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    certificate_fingerprint = hashlib.sha256(
        certificate_payload.encode("utf-8")
    ).hexdigest()
    summary = {
        "certificate": certificate,
        "metadata": dict(metadata or {}),
        "exact_rank": exact,
    }

    evidence_id = record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model_values,
        data={
            "engine": str(engine),
            "evidence_type": "exact_certificate",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": exact,
            "rigorous_upper": exact,
            "exact_rank": exact,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {
                "source": None if source is None else str(source),
                "certificate_fingerprint": certificate_fingerprint,
            },
            "stdout_summary": json.dumps(
                summary,
                sort_keys=True,
                default=str,
            ),
        },
    )
    state = apply_reduced_rank_state(db, curve_id)
    projected = (
        not state["inconsistent"]
        and state["exact_rank"] is not None
        and int(state["exact_rank"]) == exact
    )
    if projected:
        update_curve(db, curve_id, error=None)

    return {
        "curve_id": curve_id,
        "evidence_id": int(evidence_id),
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "rank_inconsistent": bool(state["inconsistent"]),
        "projected": bool(projected),
        "certificate_fingerprint": certificate_fingerprint,
    }

def promote_rigorous_rank_interval(
    db,
    *,
    curve_id,
    model,
    rigorous_lower,
    rigorous_upper,
    certificate,
    engine,
    evidence_type="rank_bounds",
    source=None,
    engine_version=None,
    sage_version=None,
    points_found=None,
    options=None,
    metadata=None,
    minimal_model_a_invariants=None,
    elapsed_seconds=None,
):
    """Persist one rigorous lower/upper certificate and reduce compatibility state."""
    curve_id = int(curve_id)
    row = get_curve(db, curve_id)
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    if certificate is None:
        raise ValueError("rank-interval promotion requires certificate provenance")

    lower = None if rigorous_lower is None else int(rigorous_lower)
    upper = None if rigorous_upper is None else int(rigorous_upper)
    if lower is None and upper is None:
        raise ValueError("rank-interval promotion requires a rigorous lower or upper bound")
    if lower is not None and lower < 0:
        raise ValueError("rigorous lower must be non-negative")
    if upper is not None and upper < 0:
        raise ValueError("rigorous upper must be non-negative")

    point_pairs = [
        [str(pair[0]), str(pair[1])]
        for pair in list(points_found or [])
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    ]
    exact = (
        lower
        if lower is not None and upper is not None and lower == upper
        else None
    )
    certificate_payload = json.dumps(
        certificate,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    certificate_fingerprint = hashlib.sha256(
        certificate_payload.encode("utf-8")
    ).hexdigest()
    evidence_options = dict(options or {})
    if source is not None:
        evidence_options["source"] = str(source)
    evidence_options["certificate_fingerprint"] = certificate_fingerprint

    evidence_id = record_rank_evidence(
        db,
        curve_id=curve_id,
        model=[str(x) for x in model],
        data={
            "engine": str(engine),
            "engine_version": engine_version,
            "sage_version": sage_version,
            "evidence_type": str(evidence_type),
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": exact,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": point_pairs,
            "options": evidence_options,
            "minimal_model_a_invariants": (
                None
                if minimal_model_a_invariants is None
                else [str(x) for x in minimal_model_a_invariants]
            ),
            "elapsed_seconds": elapsed_seconds,
            "stdout_summary": json.dumps(
                {
                    "certificate": certificate,
                    "metadata": dict(metadata or {}),
                    "rigorous_lower": lower,
                    "rigorous_upper": upper,
                    "points_found": point_pairs,
                },
                sort_keys=True,
                default=str,
            ),
        },
    )
    state = apply_reduced_rank_state(db, curve_id)
    return {
        "curve_id": curve_id,
        "evidence_id": int(evidence_id),
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "rank_inconsistent": bool(state["inconsistent"]),
        "projected": not bool(state["inconsistent"]),
        "certificate_fingerprint": certificate_fingerprint,
    }


def promote_mwrank_strong_result(
    db,
    *,
    curve_id,
    model,
    result,
    rigorous_lower,
    point_certificate,
    source,
    search_ref,
    options=None,
    metadata=None,
):
    """Persist one strong mwrank result through shared interval/subgroup promotion."""
    result = dict(result or {})
    lower = None if rigorous_lower is None else int(rigorous_lower)
    upper = int(result["upper"])
    points = list(result.get("points") or [])

    interval = promote_rigorous_rank_interval(
        db,
        curve_id=curve_id,
        model=model,
        rigorous_lower=lower,
        rigorous_upper=upper,
        certificate=result,
        engine="mwrank",
        evidence_type="descent",
        source=source,
        engine_version=result.get("engine_version"),
        sage_version=result.get("sage_version"),
        points_found=points if lower is not None else [],
        options=options,
        metadata=metadata,
        minimal_model_a_invariants=result.get("a_invariants") or model,
        elapsed_seconds=result.get("_runtime_seconds"),
    )

    subgroup = None
    if lower is not None and lower > 0 and len(points) == lower:
        certificate = None
        lower_source = None
        if isinstance(point_certificate, dict) and point_certificate.get("independent"):
            certificate = point_certificate
            lower_source = "rank_hunter_exact_point_certificate"
        elif bool(result.get("certain")) and int(result.get("lower") or 0) == upper == lower:
            certificate = {
                "kind": "mwrank_certain_subgroup",
                "mwrank_result": result,
            }
            lower_source = "mwrank_certain"
        if certificate is not None:
            from sage.all import EllipticCurve, QQ
            witness_model = result.get("a_invariants") or model
            E = EllipticCurve(QQ, [QQ(str(x)) for x in witness_model])
            subgroup = promote_certified_subgroup(
                db,
                curve_id=curve_id,
                E=E,
                points=points,
                source=str(source),
                search_ref=search_ref,
                certificate=certificate,
                metadata={
                    **dict(metadata or {}),
                    "lower_source": lower_source,
                    "mwrank_interval_evidence_id": int(interval["evidence_id"]),
                },
                engine="mwrank_certified_subgroup",
            )

    state = apply_reduced_rank_state(db, int(curve_id))
    return {
        "curve_id": int(curve_id),
        "interval_evidence_id": int(interval["evidence_id"]),
        "subgroup_evidence_id": (
            None if subgroup is None else int(subgroup["evidence_id"])
        ),
        "rigorous_lower": int(state["rigorous_lower"] or 0),
        "rigorous_upper": state["rigorous_upper"],
        "exact_rank": state["exact_rank"],
        "rank_inconsistent": bool(state["inconsistent"]),
        "subgroup_promoted": subgroup is not None,
    }

