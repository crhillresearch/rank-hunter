"""Exact short-model coordinates for fixed elliptic curves.

For a general Weierstrass equation

    y^2 + a1*x*y + a3*y = x^3 + a2*x^2 + a4*x + a6,

the classical integral short model used by the ICARM certificate is

    Y^2 = X^3 - 27*c4*X - 54*c6

with

    X = 36*x + 3*b2,
    Y = 108*(2*y + a1*x + a3).

All maps below use exact rational arithmetic.  The short model is usually much
larger than a minimal model; it is a search representation, not a claim that it
is reduced for point finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


def Q(x):
    return Fraction(str(x))


@dataclass(frozen=True)
class ShortModel:
    a_invariants: tuple[Fraction, ...]
    b2: Fraction
    c4: Fraction
    c6: Fraction
    A: Fraction
    B: Fraction

    @property
    def polynomial_coefficients(self):
        # a0,a1,a2,a3 for Y^2 = B + A*X + X^3
        return (self.B, self.A, Fraction(0), Fraction(1))

    def forward(self, x, y):
        a1, _a2, a3, _a4, _a6 = self.a_invariants
        x = Q(x)
        y = Q(y)
        X = 36 * x + 3 * self.b2
        Y = 108 * (2 * y + a1 * x + a3)
        return X, Y

    def inverse(self, X, Y):
        a1, _a2, a3, _a4, _a6 = self.a_invariants
        X = Q(X)
        Y = Q(Y)
        x = (X - 3 * self.b2) / 36
        y = (Y / 108 - a1 * x - a3) / 2
        return x, y

    def verify_short(self, X, Y):
        X = Q(X)
        Y = Q(Y)
        return Y * Y == X * X * X + self.A * X + self.B


def short_model_from_ainvs(ainvs):
    a = tuple(Q(x) for x in ainvs)
    if len(a) == 2:
        a = (Fraction(0), Fraction(0), Fraction(0), a[0], a[1])
    if len(a) != 5:
        raise ValueError("a-invariants must have length 2 or 5")
    a1, a2, a3, a4, a6 = a
    b2 = a1 * a1 + 4 * a2
    b4 = 2 * a4 + a1 * a3
    b6 = a3 * a3 + 4 * a6
    c4 = b2 * b2 - 24 * b4
    c6 = -(b2 ** 3) + 36 * b2 * b4 - 216 * b6
    A = -27 * c4
    B = -54 * c6
    return ShortModel(a, b2, c4, c6, A, B)
