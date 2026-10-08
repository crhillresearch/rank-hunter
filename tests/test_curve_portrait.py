from rank42.curve_portrait import curve_portrait_data, parse_a_invariants


def test_curve_portrait_parses_exact_rational_a_invariants():
    assert parse_a_invariants('["0","1/2","0","-1","3/2"]') == (
        0.0,
        0.5,
        0.0,
        -1.0,
        1.5,
    )


def test_curve_portrait_builds_real_locus_and_exact_point_overlay():
    portrait = curve_portrait_data(
        '["0","0","0","-1","0"]',
        [
            {
                "id": 1,
                "x": "0",
                "y": "0",
                "source": "test",
                "rigorous_independent": 1,
            },
            {
                "id": 2,
                "x": "1",
                "y": "0",
                "source": "test",
                "rigorous_independent": 0,
            },
        ],
        samples=180,
    )

    assert portrait is not None
    assert portrait["points_available"] == 2
    assert portrait["points_shown"] == 2
    curve_rows = [row for row in portrait["rows"] if row["kind"] == "curve"]
    point_rows = [row for row in portrait["rows"] if row["kind"] == "point"]
    assert curve_rows
    assert {row["status"] for row in point_rows} == {
        "Rigorous witness",
        "Exact point",
    }


def test_curve_portrait_rejects_missing_or_invalid_models():
    assert curve_portrait_data(None, []) is None
    assert curve_portrait_data('["0","0"]', []) is None
