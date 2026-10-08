"""Isolated Sage worker for exact Mordell-Weil relation discovery.

Numerical canonical heights are used only to *guess* rational coefficients.
A dependent verdict is emitted only after an exact curve-group identity is
verified, modulo the exact rational torsion subgroup.
"""
from __future__ import annotations

import json
import math
import sys
from decimal import Decimal
from fractions import Fraction

from sage.all import QQ, ZZ, EllipticCurve, RealField, lcm, matrix, vector

MARKER = "RANK42_MW_RELATION_WORKER="


def _point(E, xy):
    if not isinstance(xy, (list, tuple)) or len(xy) < 2:
        raise ValueError("malformed point")
    return E(QQ(str(xy[0])), QQ(str(xy[1])))


def _pairing(P, Q, R):
    precision = int(R.precision())
    hp = R(P.height(precision=precision))
    hq = R(Q.height(precision=precision))
    hpq = R((P + Q).height(precision=precision))
    return (hpq - hp - hq) / R(2)


def _rational_approx(value, *, max_denominator, digits):
    text = str(value.n(digits=int(digits)))
    frac = Fraction(Decimal(text)).limit_denominator(int(max_denominator))
    return QQ(frac.numerator) / QQ(frac.denominator)


def main():
    payload = json.loads(sys.stdin.read())
    precision_bits = max(128, int(payload.get("precision_bits") or 256))
    max_denominator = max(1, int(payload.get("max_denominator") or 64))

    E = EllipticCurve(QQ, [QQ(str(x)) for x in payload["a_invariants"]])
    basis = [_point(E, xy) for xy in payload.get("basis") or []]
    P = _point(E, payload["candidate"])
    if not basis:
        raise ValueError("rigorous basis is empty")
    if P.is_zero():
        raise ValueError("candidate is the identity")

    R = RealField(precision_bits)
    gram = matrix(R, len(basis), len(basis))
    rhs = vector(R, len(basis))
    for i, Bi in enumerate(basis):
        rhs[i] = _pairing(Bi, P, R)
        for j in range(i, len(basis)):
            value = _pairing(Bi, basis[j], R)
            gram[i, j] = value
            gram[j, i] = value

    coeff_real = gram.solve_right(rhs)
    digits = max(30, int(math.floor(precision_bits * math.log10(2))) - 12)
    coeffs = [
        _rational_approx(
            c,
            max_denominator=max_denominator,
            digits=digits,
        )
        for c in coeff_real
    ]
    common_denominator = ZZ(1)
    for q in coeffs:
        common_denominator = lcm(common_denominator, ZZ(q.denominator()))

    combo = E(0)
    integer_coeffs = []
    for q, Bi in zip(coeffs, basis):
        m = ZZ(common_denominator * q)
        integer_coeffs.append(int(m))
        combo += m * Bi

    residual = common_denominator * P - combo
    torsion = E.torsion_subgroup()
    torsion_exponent = max(1, int(torsion.exponent()))
    verified = bool((torsion_exponent * residual).is_zero())

    approx_residual = gram * vector(R, coeffs) - rhs
    residual_norm = max(
        [abs(x) for x in approx_residual] or [R(0)]
    )

    result = {
        "status": "dependent" if verified else "inconclusive",
        "verified": verified,
        "basis_size": len(basis),
        "precision_bits": precision_bits,
        "max_denominator": max_denominator,
        "coefficients": [str(q) for q in coeffs],
        "integer_coefficients": integer_coeffs,
        "common_denominator": int(common_denominator),
        "torsion_exponent": torsion_exponent,
        "torsion_residual": None if residual.is_zero() else [str(residual[0]), str(residual[1])],
        "numerical_residual": str(residual_norm),
    }
    print(MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
