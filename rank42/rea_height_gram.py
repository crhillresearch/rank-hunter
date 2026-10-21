"""REA canonical-height Gram certification for arbitrary elliptic curves."""
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path


def certify_stored_point_rank(
    a_invariants,
    point_rows,
    *,
    dmt_root="/home/chris/dmt-rank-jumps",
    depths=(4, 5, 6, 7, 8, 9),
    maximum_points=32,
):
    """Certify the independent rank lower bound in exact verified point rows.

    A positive definite outward-rounded canonical-height Gram interval proves
    the selected points independent modulo torsion. This never returns an
    upper bound or exact-rank claim.
    """
    root = Path(str(dmt_root or "")).expanduser().resolve()
    if not (root / "z8" / "generic_multi1.py").is_file():
        return {
            "status": "unavailable",
            "reason": "rea_generic_height_gram_backend_not_found",
            "backend_root": str(root),
            "rank_lower_bound": None,
        }
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    from z8.generic_multi1 import adaptive_joint_gram_certificate
    from z8.rea_curve_adapter import GenericREACurve

    invariants = tuple(Fraction(str(value)) for value in a_invariants)
    if len(invariants) != 5:
        raise ValueError("expected five generalized Weierstrass invariants")
    curve = GenericREACurve(*invariants)
    if not curve.smooth:
        return {
            "status": "inconclusive",
            "reason": "singular_elliptic_model",
            "rank_lower_bound": None,
        }

    limit = max(1, int(maximum_points))
    candidates = []
    seen = set()
    for row in list(point_rows)[:limit]:
        point = (Fraction(str(row["x"])), Fraction(str(row["y"])))
        if not curve.on_curve(point):
            raise ArithmeticError("stored exact-verified point is off the selected curve")
        inverse = curve.neg(point)
        canonical = min(point, inverse)
        if canonical in seen:
            continue
        seen.add(canonical)
        candidates.append((canonical, row))

    if not candidates:
        return {
            "status": "inconclusive",
            "reason": "no_exact_verified_points_available",
            "points_examined": 0,
            "rank_lower_bound": None,
            "rank_upper_bound": None,
        }

    basis = []
    basis_rows = []
    certificates = []
    depth_values = tuple(sorted({int(depth) for depth in depths if int(depth) > 0}))
    if not depth_values:
        raise ValueError("at least one positive height depth is required")
    for point, row in candidates:
        proposal = (*basis, point)
        proof = adaptive_joint_gram_certificate(
            curve,
            proposal,
            start=depth_values[0],
            stop=depth_values[-1],
        )
        if not proof or not proof.get("independent"):
            continue
        basis.append(point)
        basis_rows.append(row)
        certificates.append({
            "point_id": int(row["id"]),
            "point": [str(point[0]), str(point[1])],
            "height_depth": int(proof["depth"]),
            "gram_proof": proof["proof"],
        })

    lower = len(basis)
    return {
        "status": "completed" if lower else "inconclusive",
        "certificate_kind": "rea_interval_canonical_height_gram",
        "algorithm": "REA BOUND1/MULTI1 outward-rounded interval canonical-height Gram",
        "points_examined": len(candidates),
        "independent_points": certificates,
        "rank_lower_bound": lower,
        "rank_upper_bound": None,
        "exact_rank": None,
        "rigorous": bool(lower),
        "proof_status": "positive_definite_gram_certified" if lower else "no_positive_definite_gram_found",
        "theorem": (
            "A positive-definite canonical-height Gram matrix proves the listed "
            "rational points independent modulo torsion, so the Jacobian/Mordell-Weil rank is at least its dimension."
        ),
        "height_depths_attempted": list(depth_values),
        "backend": "REA generic MULTI1",
    }
