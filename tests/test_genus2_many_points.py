"""Exact point-count and resume controls for the genus-two record hunt."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from rank42.genus2_many_points import point_census, search_point_bands
from rank42.pipeline_catalog import pipeline_preset_options, template_stages, validate_pipeline


MODEL = [1, 0, 0, 0, -1, 0, 1]  # x=0 and x=±1 yield exact rational points.


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    return db


def test_many_points_preset_is_a_valid_genus_two_pipeline():
    stages = template_stages("genus2_many_points", "genus2_jacobian")
    assert [stage["id"] for stage in stages] == [
        "genus2_model_audit", "genus2_many_points_hunt", "genus2_point_census",
    ]
    assert validate_pipeline("genus2_jacobian", stages) == []
    assert "genus2_many_points" in {
        option["id"] for option in pipeline_preset_options("genus2_jacobian")
    }


def test_exact_orbit_count_and_next_band_resume():
    db = _db()
    with patch("rank42.genus2_many_points.run_ratpoints") as ratpoints:
        ratpoints.side_effect = [
            {"points": [SimpleNamespace(x=1, y=1)], "runtime": 0.1},
            {"points": [SimpleNamespace(x=0, y=1)], "runtime": 0.1},
        ]
        first = search_point_bands(
            db, MODEL, height=2, band_width=1, bands_per_run=1, timeout=10,
        )
        assert first["new_points"] == 4  # (±1, ±1)
        assert first["verified_point_lower_bound"] == 6  # plus two at infinity
        assert first["remaining_bands_at_height"] == 1

        second = search_point_bands(
            db, MODEL, height=2, band_width=1, bands_per_run=1, timeout=10,
        )
        assert second["new_points"] == 2  # (0, ±1)
        assert second["verified_point_lower_bound"] == 8
        assert second["remaining_bands_at_height"] == 0
        assert ratpoints.call_count == 2
        assert ratpoints.call_args_list[0].kwargs["denominator_low"] == 1
        assert ratpoints.call_args_list[1].kwargs["denominator_low"] == 2

        third = search_point_bands(
            db, MODEL, height=2, band_width=1, bands_per_run=1, timeout=10,
        )
        assert third["new_points"] == 0
        assert ratpoints.call_count == 2
        assert point_census(db, MODEL, recheck=True)["verified_point_lower_bound"] == 8


def test_failed_band_does_not_claim_coverage_and_retries():
    db = _db()
    with patch("rank42.genus2_many_points.run_ratpoints") as ratpoints:
        ratpoints.side_effect = [ValueError("backend unavailable"),
                                 {"points": [], "runtime": 0.1}]
        first = search_point_bands(db, MODEL, height=1, band_width=1,
                                   bands_per_run=1, timeout=10)
        assert first["status"] == "inconclusive"
        assert first["bands_completed"] == 0
        second = search_point_bands(db, MODEL, height=1, band_width=1,
                                    bands_per_run=1, timeout=10)
        assert second["status"] == "completed"
        assert second["bands_completed"] == 1


def test_unverified_backend_point_is_rejected():
    db = _db()
    with patch("rank42.genus2_many_points.run_ratpoints", return_value={
        "points": [SimpleNamespace(x=1, y=2)], "runtime": 0.1,
    }):
        result = search_point_bands(db, MODEL, height=1, band_width=1,
                                    bands_per_run=1, timeout=10)
    assert result["status"] == "inconclusive"
    assert result["bands_completed"] == 0
    assert point_census(db, MODEL, recheck=True)["finite_points"] == 0
