from fractions import Fraction

from rank42.mobius_quartic import (
    chart_from_three_points, evaluate, forward_x, inverse_point, inverse_x,
    rank_charts, rank_exploratory_geometry_charts,
)


def test_three_anchor_map_and_polynomial_identity():
    coeffs = [1, 2, 3, 4, 5]
    chart = chart_from_three_points(coeffs, 0, 2, 5, known_x=[0, 1, 2, 5, 7])
    assert forward_x(chart.A, chart.B, chart.C, chart.D, 0) == 0
    assert forward_x(chart.A, chart.B, chart.C, chart.D, 2) is None
    assert forward_x(chart.A, chart.B, chart.C, chart.D, 5) == 1
    assert inverse_x(chart.A, chart.B, chart.C, chart.D, 0) == 0
    assert inverse_x(chart.A, chart.B, chart.C, chart.D, 1) == 5

    for z in [Fraction(-3, 2), Fraction(1, 3), Fraction(7, 4)]:
        den = chart.C * z + chart.D
        if den == 0:
            continue
        x = inverse_x(chart.A, chart.B, chart.C, chart.D, z)
        lhs = evaluate(chart.coefficients, z)
        rhs = den**4 * evaluate(coeffs, x)
        assert lhs == rhs


def test_point_round_trip():
    # y^2=x^4+1 has (0,1).
    chart = chart_from_three_points([1, 0, 0, 0, 1], 0, 1, 2, known_x=[0, 1, 2])
    z = forward_x(chart.A, chart.B, chart.C, chart.D, 0)
    den = chart.C * z + chart.D
    Y = den**2
    assert inverse_point(chart, z, Y) == (Fraction(0), Fraction(1))


def test_rank_charts_is_deterministic():
    coeffs = [1, -2, 3, 4, 1]
    xs = [-5, -2, 0, 1, 3, 9]
    a = rank_charts(coeffs, xs, anchor_pool=6, limit=5)
    b = rank_charts(coeffs, xs, anchor_pool=6, limit=5)
    assert [c.chart_id for c in a] == [c.chart_id for c in b]
    assert len(a) == 5



def test_exploratory_geometry_charts_are_deterministic_and_geometry_only():
    coeffs = [1, -2, 3, 4, 1]
    base = [-5, -2, 0, 1, 3, 9]
    rigorous = [Fraction(11, 3), Fraction(17, 5), Fraction(23, 7)]
    exploratory = [
        Fraction(101, 97),
        Fraction(211, 199),
        Fraction(307, 293),
        Fraction(401, 389),
    ]

    a = rank_exploratory_geometry_charts(
        coeffs,
        base,
        rigorous,
        exploratory,
        rigorous_anchor_pool=9,
        exploratory_anchor_pool=4,
        mix="balanced",
        limit=12,
    )
    b = rank_exploratory_geometry_charts(
        coeffs,
        base,
        rigorous,
        exploratory,
        rigorous_anchor_pool=9,
        exploratory_anchor_pool=4,
        mix="balanced",
        limit=12,
    )

    assert [chart.chart_id for chart in a] == [chart.chart_id for chart in b]
    assert len(a) == 12
    assert all(chart.source == "exploratory" for chart in a)
    assert all("geometry-only" in str(chart.source_detail) for chart in a)
    used = {
        anchor
        for chart in a
        for anchor in (chart.alpha, chart.beta, chart.gamma)
        if anchor in set(exploratory)
    }
    assert used == set(exploratory)
