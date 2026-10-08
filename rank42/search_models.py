"""Exact completed-square search models for long Weierstrass equations.

For
    y^2 + a1*x*y + a3*y = x^3 + a2*x^2 + a4*x + a6
set
    W = 2*y + a1*x + a3.
Then
    W^2 = 4*x^3 + b2*x^2 + 2*b4*x + b6,
with b2=a1^2+4*a2, b4=2*a4+a1*a3, b6=a3^2+4*a6.

This preserves x exactly, which is preferable to an arbitrary short-model
isomorphism for bounded rational-point searches.
"""
from __future__ import annotations

from fractions import Fraction


def _q(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def completed_square_polynomial(a_invariants):
    a1, a2, a3, a4, a6 = (_q(x) for x in a_invariants)
    b2 = a1*a1 + 4*a2
    b4 = 2*a4 + a1*a3
    b6 = a3*a3 + 4*a6
    # ratpoints coefficient order: constant, x, x^2, x^3
    return [b6, 2*b4, b2, Fraction(4)]


def recover_weierstrass_y(a_invariants, x, w):
    a1, _a2, a3, _a4, _a6 = (_q(v) for v in a_invariants)
    x = _q(x)
    w = _q(w)
    return (w - a1*x - a3) / 2


def translate_cubic_polynomial(coefficients, center):
    """Return f(X+center) for exact cubic coefficients [c0,c1,c2,c3]."""
    if len(coefficients) != 4:
        raise ValueError("translate_cubic_polynomial requires four cubic coefficients")
    c0, c1, c2, c3 = (_q(x) for x in coefficients)
    r = _q(center)
    return [
        c0 + c1*r + c2*r*r + c3*r*r*r,
        c1 + 2*c2*r + 3*c3*r*r,
        c2 + 3*c3*r,
        c3,
    ]


def affine_cubic_polynomial(coefficients, *, scale, center):
    """Return f(scale*X + center) exactly for a nonzero rational scale."""
    s = _q(scale)
    if s == 0:
        raise ValueError("affine cubic scale must be nonzero")
    translated = translate_cubic_polynomial(coefficients, center)
    return [
        translated[0],
        translated[1] * s,
        translated[2] * s * s,
        translated[3] * s * s * s,
    ]
