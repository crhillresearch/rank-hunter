import rank42.auto_point_search as point_search


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _DB:
    def execute(self, *args, **kwargs):
        return _Rows([
            {"x": "10", "rigorous_independent": 1, "role": "generic_section", "id": 1},
            {"x": "20", "rigorous_independent": 1, "role": "generic_section", "id": 2},
            {"x": "30", "rigorous_independent": 1, "role": "rigorous_witness", "id": 3},
            {"x": "40", "rigorous_independent": 1, "role": "rigorous_witness", "id": 4},
        ])


def test_small_chart_budget_reserves_point_centered_translations():
    charts = point_search.search_charts(
        _DB(),
        curve_id=1,
        E=object(),
        tier="deep",
        budget=8,
    )

    assert len(charts) == 8
    assert tuple(map(str, charts[0])) == ("0", "1")
    translated = [(center, scale) for center, scale in charts if center != 0]
    assert len(translated) >= 3
    assert all(scale == 1 for center, scale in translated[:3])
    assert len({(str(center), str(scale)) for center, scale in charts}) == len(charts)


def test_denominator_range_skips_impossible_height():
    assert point_search._denominator_range_possible(100000, 10001)
    assert point_search._denominator_range_possible(10001, 10001)
    assert not point_search._denominator_range_possible(10000, 10001)
    assert point_search._denominator_range_possible(10000, None)
