from fractions import Fraction

from rank42.reverse_engineer import (
    exact_weierstrass_invariants,
    point_on_curve,
    point_structure,
)


def test_exact_invariants_short_curve():
    # y^2 = x^3 - x has c4=48, c6=0, Delta=64, j=1728.
    inv = exact_weierstrass_invariants([0, 0, 0, -1, 0])
    assert inv["c4"] == "48"
    assert inv["c6"] == "0"
    assert inv["discriminant"] == "64"
    assert inv["j"] == "1728"


def test_point_verification_is_exact_rational_arithmetic():
    ainvs = [0, 0, 0, -1, 0]
    assert point_on_curve(ainvs, ["0", "0"])
    assert point_on_curve(ainvs, ["1", "0"])
    assert not point_on_curve(ainvs, ["2", "1"])


def test_point_structure_tracks_denominators_without_float_roundoff():
    # y^2 = x^3 - x; (0,0), (1,0), (-1,0) are exact rational points.
    info = point_structure([0, 0, 0, -1, 0], [["0", "0"], ["1", "0"], ["-1", "0"]])
    assert info["all_points_exactly_verified"]
    assert info["integral_x_count"] == 3
    assert info["unique_x_denominators"] == 1
    assert info["integral_x_fraction"] == 1.0
