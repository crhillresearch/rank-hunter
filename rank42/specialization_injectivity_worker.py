"""Isolated Gusic-Tadic specialization injectivity criterion worker."""
from __future__ import annotations

import itertools
import json
import sys
from datetime import datetime, timezone

from sage.all import QQ, ZZ, EllipticCurve, PolynomialRing, sage_eval

RESULT_MARKER = "RANK42_SPECIALIZATION_INJECTIVITY_RESULT="


def _sage_version():
    try:
        from sage.version import version
        return str(version)
    except Exception:
        return None


def _squarefree_constant_divisors(content):
    content = abs(ZZ(content))
    primes = list(content.prime_divisors()) if content not in (0, 1) else []
    values = [ZZ(1)]
    for p in primes:
        values += [v * p for v in list(values)]
    return sorted(set(values))


def _criterion_divisors(poly, *, max_divisors):
    if poly == 0:
        raise ValueError("criterion polynomial must be nonzero")
    content = ZZ(poly.content())
    # Sage's dense ZZ[t] polynomial type does not expose primitive_part().
    # Factoring the integral polynomial directly is exact: its integer content
    # is retained as the factorization unit, while iteration yields the
    # positive-degree irreducible factors. Constant square-free divisors are
    # handled separately from poly.content() below.
    factors = [factor for factor, _exp in poly.factor()]
    polynomial_factors = [factor for factor in factors if factor.degree() > 0]
    if not polynomial_factors:
        return []

    constants = _squarefree_constant_divisors(content)
    out = {}
    for size in range(1, len(polynomial_factors) + 1):
        for subset in itertools.combinations(polynomial_factors, size):
            base = poly.parent()(1)
            for factor in subset:
                base *= factor
            for constant in constants:
                for sign in (1, -1):
                    h = poly.parent()(sign * constant) * base
                    key = str(h)
                    out[key] = h
                    if len(out) > int(max_divisors):
                        raise OverflowError(
                            "square-free divisor enumeration exceeds configured maximum"
                        )
    return [out[key] for key in sorted(out)]


def _coerce_integral_polynomial(expr, parameter, R, K):
    t = K(R.gen())
    value = K(sage_eval(str(expr), locals={parameter: t}))
    if value.denominator() != 1:
        raise ValueError("coefficient is not a polynomial")
    numerator = value.numerator()
    try:
        return R(numerator)
    except Exception as exc:
        raise ValueError("coefficient is not in Z[t]") from exc


def compute_certificate(payload):
    parameter = str(payload.get("parameter_variable") or "t")
    t0 = QQ(str(payload["specialization_parameter"]))
    ainvs = list(payload.get("a_invariants") or [])
    if len(ainvs) != 5:
        return {"status": "unsupported", "reason": "requires_five_a_invariants"}

    R = PolynomialRing(ZZ, parameter)
    K = R.fraction_field()
    try:
        a1 = _coerce_integral_polynomial(ainvs[0], parameter, R, K)
        A = _coerce_integral_polynomial(ainvs[1], parameter, R, K)
        a3 = _coerce_integral_polynomial(ainvs[2], parameter, R, K)
        B = _coerce_integral_polynomial(ainvs[3], parameter, R, K)
        a6 = _coerce_integral_polynomial(ainvs[4], parameter, R, K)
    except ValueError as exc:
        return {
            "status": "unsupported",
            "reason": "criterion_requires_integral_polynomial_coefficients",
            "detail": str(exc),
        }

    if any(poly != 0 for poly in (a1, a3, a6)):
        return {
            "status": "unsupported",
            "reason": "criterion_requires_y2_eq_x3_plus_Ax2_plus_Bx",
        }
    if B == 0:
        return {"status": "unsupported", "reason": "B_polynomial_is_zero"}

    delta2 = A * A - 4 * B
    if delta2 == 0:
        return {"status": "unsupported", "reason": "quadratic_2_torsion_factor_degenerate"}

    # Exactly one nontrivial rational 2-torsion point means the quadratic
    # factor x^2 + A*x + B is irreducible over Q(t), equivalently its
    # discriminant is not a square in Q(t).
    if bool(K(delta2).is_square()):
        return {
            "status": "unsupported",
            "reason": "family_has_more_than_one_nontrivial_rational_2_torsion_point",
        }

    try:
        A0 = QQ(A(t0))
        B0 = QQ(B(t0))
        E0 = EllipticCurve(QQ, [0, A0, 0, B0, 0])
    except Exception as exc:
        return {
            "status": "inconclusive",
            "reason": "specialized_curve_construction_failed",
            "error": repr(exc),
        }
    if E0.discriminant() == 0:
        return {
            "status": "rejected",
            "reason": "specialized_fiber_is_singular",
            "specialization_parameter": str(t0),
        }

    max_divisors = max(1, int(payload.get("max_divisors") or 4096))
    checks = []
    try:
        sources = (("B", B), ("A2_minus_4B", delta2))
        for source_name, poly in sources:
            for h in _criterion_divisors(poly, max_divisors=max_divisors):
                value = QQ(h(t0))
                square = bool(value.is_square())
                rec = {
                    "source": source_name,
                    "divisor": str(h),
                    "value": str(value),
                    "is_square_in_Q": square,
                }
                checks.append(rec)
                if square:
                    return {
                        "status": "rejected",
                        "reason": "squarefree_divisor_specializes_to_square",
                        "specialization_parameter": str(t0),
                        "failed_check": rec,
                        "checks_completed": len(checks),
                        "A": str(A),
                        "B": str(B),
                        "A2_minus_4B": str(delta2),
                    }
    except OverflowError as exc:
        return {
            "status": "inconclusive",
            "reason": "criterion_divisor_budget_exceeded",
            "error": str(exc),
            "checks_completed": len(checks),
        }

    return {
        "status": "completed",
        "injective": True,
        "rigorous": True,
        "criterion": "Gusic-Tadic Theorem 1.3",
        "parameter_variable": parameter,
        "specialization_parameter": str(t0),
        "A": str(A),
        "B": str(B),
        "A2_minus_4B": str(delta2),
        "specialized_a_invariants": [str(x) for x in E0.a_invariants()],
        "specialized_discriminant": str(E0.discriminant()),
        "checks": checks,
        "checks_completed": len(checks),
        "assumptions": [],
        "sage_version": _sage_version(),
    }


def main():
    payload = json.loads(sys.stdin.read())
    started = datetime.now(timezone.utc).isoformat()
    result = compute_certificate(payload)
    result["started_at"] = started
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    print(RESULT_MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
