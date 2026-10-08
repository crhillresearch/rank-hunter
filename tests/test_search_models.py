from fractions import Fraction

from rank42.search_models import affine_cubic_polynomial, completed_square_polynomial, recover_weierstrass_y, translate_cubic_polynomial


def _eval(coeffs, x):
    out = Fraction(0)
    for c in reversed(coeffs):
        out = out * x + Fraction(c)
    return out


def test_completed_square_identity_and_inverse():
    ainvs = [
        3319997657275182,
        4426725135347125255915385510976,
        4686376137099582083712032891033379962658470848,
        3716409621509293193468536779418034241559477775076815172174848,
        0,
    ]
    # Pure algebra check at arbitrary rational x,y; no point-on-curve assumption
    # is needed to verify W=2y+a1*x+a3 is recovered exactly.
    x = Fraction(7, 11)
    y = Fraction(-13, 17)
    a1, _a2, a3, _a4, _a6 = map(Fraction, ainvs)
    w = 2*y + a1*x + a3
    assert recover_weierstrass_y(ainvs, x, w) == y

    poly = completed_square_polynomial(ainvs)
    assert len(poly) == 4
    assert poly[3] == 4


def test_completed_square_maps_curve_point_exactly():
    # y^2 = x^3 - x has (0,0), and completed square is W^2=4x^3-4x.
    ainvs = [0, 0, 0, -1, 0]
    poly = completed_square_polynomial(ainvs)
    x = Fraction(0)
    y = Fraction(0)
    w = 2*y
    assert w*w == _eval(poly, x)
    assert recover_weierstrass_y(ainvs, x, w) == y


def test_exact_cubic_translation_preserves_values():
    poly = [Fraction(5, 7), Fraction(-3, 2), Fraction(11, 5), Fraction(4)]
    center = Fraction(13, 17)
    shifted = translate_cubic_polynomial(poly, center)
    for X in [Fraction(0), Fraction(2, 3), Fraction(-7, 11)]:
        assert _eval(shifted, X) == _eval(poly, X + center)


def test_cubic_translation_rejects_non_cubic_input():
    import pytest
    with pytest.raises(ValueError):
        translate_cubic_polynomial([1, 2, 3], Fraction(1))


def test_exact_affine_cubic_preserves_values():
    poly = [Fraction(5, 7), Fraction(-3, 2), Fraction(11, 5), Fraction(4)]
    center = Fraction(13, 17)
    scale = Fraction(5, 3)
    chart = affine_cubic_polynomial(poly, scale=scale, center=center)
    for X in [Fraction(0), Fraction(2, 3), Fraction(-7, 11)]:
        assert _eval(chart, X) == _eval(poly, scale * X + center)


def test_affine_cubic_rejects_zero_scale():
    import pytest
    with pytest.raises(ValueError):
        affine_cubic_polynomial([1, 2, 3, 4], scale=0, center=0)
