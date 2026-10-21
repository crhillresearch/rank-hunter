"""Project exact Genus 2 discoveries through a saved elliptic quotient."""
from __future__ import annotations

import json

from sage.all import EllipticCurve, NumberField, PolynomialRing, QQ

from rank42.db import get_curve
from rank42.genus2_registry import elliptic_quotient_rank_certificate
from rank42.independence_check import certify_stored_independence
from rank42.points import record_point_discovery, upsert_point


def _short_point(E, x, y):
    return E([x * x, y])


def _divisor_image(E, divisor):
    """Push a rational quadratic Mumford divisor to the elliptic quotient."""
    R = PolynomialRing(QQ, "z")
    z = R.gen()
    q = sum(QQ(str(value)) * z**i for i, value in enumerate(divisor["q_coefficients_ascending"]))
    v = sum(QQ(str(value)) * z**i for i, value in enumerate(divisor["v_coefficients_ascending"]))
    if q.degree() != 2 or q.gcd(q.derivative()).degree() != 0:
        raise ValueError("expected a squarefree quadratic divisor support")
    roots = q.roots(QQ)
    if len(roots) == 2 and all(multiplicity == 1 for _, multiplicity in roots):
        result = E(0)
        for root, _ in roots:
            result += _short_point(E, root, v(root))
        return None if result.is_zero() else result

    K = NumberField(q.monic(), "alpha")
    alpha = K.gen()
    conjugate = -QQ(q[1] / q[2]) - alpha
    E_K = E.change_ring(K)
    result = _short_point(E_K, alpha, v(alpha)) + _short_point(E_K, conjugate, v(conjugate))
    if result.is_zero():
        return None
    # Galois invariance makes both coordinates rational; conversion fails if not.
    return E([QQ(result[0]), QQ(result[1])])


def project_to_elliptic_source(
    db, *, model_id, coefficients, rational_points=(), divisors=(),
    run_id, stage_index, certificate_timeout=120, max_candidates=32,
):
    """Store exact images and certify any resulting source-curve rank growth."""
    if model_id is None:
        return {
            "status": "completed", "proof_status": "no_saved_elliptic_quotient",
            "projected_points": [], "rank_growth": 0,
        }
    quotient = elliptic_quotient_rank_certificate(
        db, model_id=model_id, coefficients=coefficients
    )
    source_id = quotient.get("source_curve_id")
    if source_id is None:
        return {
            "status": "completed", "proof_status": "no_elliptic_quotient",
            "projected_points": [], "rank_growth": 0,
        }
    source = get_curve(db, int(source_id))
    a1, a2, a3, a4, a6 = [
        QQ(str(value)) for value in json.loads(source["a_invariants_json"])
    ]
    E = EllipticCurve(QQ, [a1, a2, a3, a4, a6])
    b2 = a1 * a1 + 4 * a2
    b4 = a1 * a3 + 2 * a4
    b6 = a3 * a3 + 4 * a6
    c4 = b2 * b2 - 24 * b4
    c6 = -b2**3 + 36 * b2 * b4 - 216 * b6
    E_short = EllipticCurve(QQ, [0, 0, 0, -c4 / 48, -c6 / 864])

    found = []
    seen = set()
    errors = []
    for origin, items in (("rational_point", rational_points), ("quadratic_divisor", divisors)):
        for index, item in enumerate(items):
            try:
                if origin == "rational_point":
                    x, y = [QQ(str(value)) for value in item]
                    short_point = _short_point(E_short, x, y)
                else:
                    short_point = _divisor_image(E_short, item)
                if short_point is None:
                    continue
                original_x = QQ(short_point[0]) - b2 / 12
                original_y = QQ(short_point[1]) - (a1 * original_x + a3) / 2
                point = E([original_x, original_y])
                key = (str(point[0]), str(point[1]))
                if key in seen:
                    continue
                seen.add(key)
                row = upsert_point(
                    db, curve_id=int(source_id), x=point[0], y=point[1],
                    source="pipeline_genus2_elliptic_projection",
                    role="candidate_extra", exact_verified=True,
                    independence_status="unknown", rigorous_independent=False,
                    search_ref=f"pipeline:{run_id}:genus2-projection:{stage_index}",
                    metadata={"genus2_model_id": int(model_id), "genus2_origin": origin,
                              "genus2_origin_index": index, "map_degree": 2},
                )
                record_point_discovery(
                    db, curve_id=int(source_id), point_id=int(row["id"]),
                    source="pipeline_genus2_elliptic_projection",
                    outcome="exact_point_verified", exact_verified=True,
                    search_ref=f"pipeline:{run_id}:genus2-projection:{stage_index}",
                    pipeline_run_id=int(run_id), pipeline_stage_index=int(stage_index),
                    pipeline_stage_id="genus2_project_to_elliptic",
                    stored_x=key[0], stored_y=key[1],
                    metadata={"genus2_model_id": int(model_id), "origin": origin,
                              "origin_index": index, "map_degree": 2},
                )
                found.append({
                    "point_id": int(row["id"]), "x": key[0], "y": key[1],
                    "origin": origin, "origin_index": index,
                })
            except (ArithmeticError, TypeError, ValueError) as exc:
                errors.append({"origin": origin, "index": index, "reason": str(exc)})

    candidate_ids = sorted({item["point_id"] for item in found})
    certification = (
        certify_stored_independence(
            db, curve_id=int(source_id), E=E,
            source="pipeline_genus2_elliptic_projection",
            search_ref=f"pipeline:{run_id}:genus2-projection:{stage_index}:exact",
            certificate_timeout=max(1, int(certificate_timeout)),
            max_candidates=max(1, int(max_candidates)),
            candidate_point_ids=candidate_ids,
        )
        if candidate_ids else None
    )
    return {
        "status": "completed",
        "proof_status": "exact_projection_with_independence_check" if certification else "no_nonzero_image",
        "source_curve_id": int(source_id),
        "genus2_model_id": int(model_id),
        "map_to_elliptic_short_model": {"X": "x^2", "Y": "y"},
        "projected_points": found,
        "projection_errors": errors,
        "independence": certification,
        "rank_growth": int((certification or {}).get("rank_growth") or 0),
    }
