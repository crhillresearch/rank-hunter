import pytest

from rank42.coverings import validate_covering


def valid():
    return {
        "schema": "rank42.covering.v1",
        "curve_id": 7,
        "lattice_id": 2,
        "hole_id": 3,
        "quartic": {"coefficients": ["1", "0", "0", "0", "1"], "height": 1000},
        "map": {"x": "u", "y": "v"},
    }


def test_covering_schema_accepts_exact_handoff():
    assert validate_covering(valid())["curve_id"] == 7


def test_covering_schema_rejects_missing_map():
    d = valid()
    del d["map"]
    with pytest.raises(ValueError):
        validate_covering(d)
