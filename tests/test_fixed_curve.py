from fractions import Fraction

from rank42.fixed_curve import short_model_from_ainvs


def test_short_model_roundtrip_short_curve():
    M = short_model_from_ainvs([0, 0, 0, -1, 0])
    assert M.A == -1296
    assert M.B == 0
    X, Y = M.forward(1, 0)
    assert (X, Y) == (Fraction(36), Fraction(0))
    assert M.verify_short(X, Y)
    assert M.inverse(X, Y) == (Fraction(1), Fraction(0))


def test_short_model_roundtrip_general_weierstrass_point():
    # y^2 + x*y = x^3 - x has the rational point (0,0).
    M = short_model_from_ainvs([1, 0, 0, -1, 0])
    X, Y = M.forward(0, 0)
    assert M.verify_short(X, Y)
    assert M.inverse(X, Y) == (Fraction(0), Fraction(0))
