from fractions import Fraction
from types import SimpleNamespace

import pytest

from sage.all import QQ, EllipticCurve

from rank42.ratpoints import RatpointsFailure, RatpointsTimeout
from rank42.pipeline_catalog import geometry_search_budget
from rank42 import pointed_quartic as pq
from rank42.pointed_quartic import (
    build_anchor_pool,
    completed_square_polynomial,
    evaluate_polynomial,
    map_pointed_quartic_point,
    pointed_quartic_coefficients,
    pointed_quartic_coverage_summary,
    project_curve_point,
    run_pointed_quartic_escalation,
    _map_hits,
    _pointed_search_progress,
)


AINVS = ["0", "0", "0", "-25", "4"]
P = ["0", "2"]
Q = ["625/16", "15497/64"]


def test_pointed_quartic_formula_for_toy_curve():
    coeffs = pointed_quartic_coefficients(AINVS, P)
    assert coeffs == (
        Fraction(1600),
        Fraction(128),
        Fraction(0),
        Fraction(0),
        Fraction(1),
    )


def test_projection_and_inverse_map_are_exact():
    t, z = project_curve_point(AINVS, P, Q)
    coeffs = pointed_quartic_coefficients(AINVS, P)
    assert z * z == evaluate_polynomial(coeffs, t)
    assert (t, z) == (
        Fraction(15369, 1250),
        Fraction(252075089, 1562500),
    )
    assert map_pointed_quartic_point(AINVS, P, t, z) == (
        Fraction(625, 16),
        Fraction(15497, 64),
    )


def test_projection_rejects_anchor_vertical_fibre():
    with pytest.raises(ValueError, match="projection slope"):
        project_curve_point(AINVS, P, ["0", "-2"])


def test_inverse_rejects_non_quartic_point():
    with pytest.raises(ValueError, match="not on the pointed quartic"):
        map_pointed_quartic_point(AINVS, P, "0", "1")



def test_generalized_quartic_completion_is_exact():
    # y^2 + (1+x)y = 2 + 3x + x^4
    # W=2y+q gives W^2=q^2+4f.
    assert completed_square_polynomial(
        [2, 3, 0, 0, 1],
        [1, 1],
    ) == (
        Fraction(9),
        Fraction(14),
        Fraction(1),
        Fraction(0),
        Fraction(4),
    )


def test_family_preferred_anchor_vectors_enter_quartic_portfolio():
    E = EllipticCurve(QQ, [QQ(x) for x in AINVS])
    basis = [E(QQ(P[0]), QQ(P[1]))]
    rows = build_anchor_pool(
        E,
        basis,
        [],
        limit=2,
        pool_size=3,
        preferred_vectors=[{
            "vector": [2],
            "half_lattice_norm": 5.0,
            "source": "test_half_hole",
        }],
    )
    lattice = [row for row in rows if row["source"] == "lattice_hole"]
    assert lattice
    assert lattice[0]["vector"] == [2]
    assert lattice[0]["metadata"]["half_lattice_norm"] == 5.0



def test_pointed_quartic_progress_identifies_wave_position_and_budget():
    line = _pointed_search_progress(
        round_index=1,
        rounds=2,
        height_index=1,
        height_count=3,
        model_index=7,
        wave_count=24,
        height=1000000,
        anchor_key="abcdef0123456789",
        timeout=8,
    )
    assert "round=1/2" in line
    assert "height=1/3 H=1000000" in line
    assert "model=7/24" in line
    assert "anchor=abcdef0123" in line
    assert "timeout≤8s" in line



def test_pointed_quartic_coverage_summary_distinguishes_dry_and_incomplete():
    dry = pointed_quartic_coverage_summary(
        planned_searches=4,
        completed_searches=2,
        cached_completions=1,
        timeouts=0,
        errors=0,
        local_obstructions=1,
        new_exact_points=0,
        dry_round_min_coverage=1.0,
    )
    assert dry["status"] == "completed"
    assert dry["resolved_searches"] == 4
    assert dry["coverage_fraction"] == 1.0
    assert dry["dry_completed"] is True

    timeout = pointed_quartic_coverage_summary(
        planned_searches=4,
        completed_searches=0,
        cached_completions=0,
        timeouts=4,
        errors=0,
        local_obstructions=0,
        new_exact_points=0,
        dry_round_min_coverage=1.0,
    )
    assert timeout["status"] == "timeout"
    assert timeout["coverage_fraction"] == 0.0
    assert timeout["dry_completed"] is False

    partial = pointed_quartic_coverage_summary(
        planned_searches=4,
        completed_searches=2,
        cached_completions=0,
        timeouts=2,
        errors=0,
        local_obstructions=0,
        new_exact_points=0,
        dry_round_min_coverage=1.0,
    )
    assert partial["status"] == "partial"
    assert partial["coverage_fraction"] == 0.5
    assert partial["dry_completed"] is False


def _pointed_engine_fixture(monkeypatch, E, outcomes):
    P0 = E(0, 2)
    models = []
    for index in range(len(outcomes)):
        models.append({
            "anchor_key": f"anchor-{index}",
            "anchor": [str(P0[0]), str(P0[1])],
            "source": "fixture",
            "support": [0],
            "vector": [1],
            "complexity": [1, 1, 1],
            "search_coefficients": (1, 0, 0, 0, 1),
            "coefficients": (1, 0, 0, 0, 1),
            "reduction": None,
            "model_key": f"model-{index}",
        })

    monkeypatch.setattr(
        "rank42.points.rigorous_witness_basis",
        lambda db, curve_id, curve: ([P0], 1, True),
    )
    monkeypatch.setattr(
        "rank42.db.get_curve",
        lambda db, curve_id: {"family": "fixture"},
    )
    monkeypatch.setattr(
        "rank42.curve_research_state.get_curve_research_state",
        lambda db, curve_id: {"rigorous_lower": 0},
    )
    monkeypatch.setattr(
        "rank42.ratpoints.resolve_executable",
        lambda executable: "/tmp/ratpoints",
    )
    monkeypatch.setattr(
        pq,
        "_existing_exact_keys",
        lambda db, curve_id, curve: set(),
    )
    monkeypatch.setattr(
        pq,
        "_load_exact_observations",
        lambda db, curve_id, curve, limit=512: [],
    )
    monkeypatch.setattr(
        pq,
        "build_anchor_pool",
        lambda *args, **kwargs: list(models),
    )
    monkeypatch.setattr(
        pq,
        "prepare_reduced_models",
        lambda portfolio, timeout=20: list(portfolio),
    )
    monkeypatch.setattr(
        "rank42.prime_local.first_quartic_local_obstruction",
        lambda *args, **kwargs: None,
    )

    search_ids = iter(range(1, len(outcomes) + 1))
    monkeypatch.setattr(
        "rank42.quartic_store.create_or_get_search",
        lambda *args, **kwargs: {
            "id": next(search_ids),
            "status": "ready",
        },
    )
    monkeypatch.setattr(
        "rank42.quartic_store.mark_running",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "rank42.quartic_store.finish_search",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "rank42.quartic_store.store_points",
        lambda *args, **kwargs: None,
    )

    remaining = list(outcomes)

    def fake_ratpoints(*args, **kwargs):
        outcome = remaining.pop(0)
        if outcome == "timeout":
            raise RatpointsTimeout("fixture timeout")
        if outcome == "error":
            raise RatpointsFailure("fixture failure")
        return {"points": [], "runtime": 0.01}

    monkeypatch.setattr("rank42.ratpoints.run_ratpoints", fake_ratpoints)


def test_pointed_quartic_all_timeout_round_is_not_dry_completed(monkeypatch):
    E = EllipticCurve(QQ, [QQ(x) for x in AINVS])
    _pointed_engine_fixture(monkeypatch, E, ["timeout", "timeout"])

    result = run_pointed_quartic_escalation(
        object(),
        curve_id=1,
        E=E,
        heights=[100],
        anchors=2,
        pool_size=2,
        rounds=2,
        deep_keep=1,
        timeout=1,
        reduce_timeout=0,
        executable="/tmp/ratpoints",
        dry_round_min_coverage=1.0,
    )
    assert result["status"] == "timeout"
    assert result["stop_reason"] == "timeout_budget_exhausted"
    assert result["dry_completed"] is False
    budget = geometry_search_budget(
        "pointed_quartic",
        {
            "heights": [100],
            "anchors": 2,
            "rounds": 2,
            "deep_keep": 1,
            "timeout": 1,
            "reduce_timeout": 0,
        },
    )
    assert budget["searches_no_growth"] == result["planned_searches"] == 2
    assert result["completed_searches"] == 0
    assert result["timeouts"] == 2
    assert result["coverage_fraction"] == 0.0
    assert result["rounds"][0]["status"] == "timeout"
    assert result["rounds"][0]["dry_completed"] is False


def test_pointed_quartic_mixed_completed_timeout_round_is_partial(monkeypatch):
    E = EllipticCurve(QQ, [QQ(x) for x in AINVS])
    _pointed_engine_fixture(monkeypatch, E, ["completed", "timeout"])

    result = run_pointed_quartic_escalation(
        object(),
        curve_id=1,
        E=E,
        heights=[100],
        anchors=2,
        pool_size=2,
        rounds=2,
        deep_keep=1,
        timeout=1,
        reduce_timeout=0,
        executable="/tmp/ratpoints",
        dry_round_min_coverage=1.0,
    )
    assert result["status"] == "partial"
    assert result["stop_reason"] == "incomplete_round"
    assert result["dry_completed"] is False
    assert result["planned_searches"] == 2
    assert result["completed_searches"] == 1
    assert result["timeouts"] == 1
    assert result["coverage_fraction"] == 0.5
    assert result["rounds"][0]["status"] == "partial"
    assert result["rounds"][0]["dry_completed"] is False


def test_pointed_quartic_dry_threshold_validation():
    with pytest.raises(ValueError, match="dry_round_min_coverage"):
        pointed_quartic_coverage_summary(
            planned_searches=1,
            completed_searches=1,
            cached_completions=0,
            timeouts=0,
            errors=0,
            local_obstructions=0,
            dry_round_min_coverage=0,
        )



def test_pointed_quartic_map_failure_records_exact_debug_context():
    E = EllipticCurve(QQ, [QQ(x) for x in AINVS])
    model = {
        "anchor": list(P),
        "anchor_key": "anchor-debug",
        "model_key": "model-debug",
        "reduction": {
            "q": ["0"],
            "scale": "1",
            "transform": {
                "e": "1",
                "matrix": ["1", "0", "1", "0"],
                "h": ["0"],
            },
        },
    }
    mapped, failures = _map_hits(
        E,
        model,
        [SimpleNamespace(x=0, y=1)],
        search_id=77,
    )
    assert mapped == []
    assert len(failures) == 2
    sample = failures[0]
    assert sample["quartic_search_id"] == 77
    assert sample["anchor_key"] == "anchor-debug"
    assert sample["model_key"] == "model-debug"
    assert sample["hit"] == {"x": "0", "y": "1", "square_y": "1"}
    assert sample["reduction_transform"]["matrix"] == ["1", "0", "1", "0"]
    assert sample["error_class"] == "ZeroDivisionError"
    assert "denominator is zero" in sample["error"]


def test_pointed_quartic_result_surfaces_persisted_map_failure(
    monkeypatch,
):
    E = EllipticCurve(QQ, [QQ(x) for x in AINVS])
    _pointed_engine_fixture(monkeypatch, E, ["completed"])
    failure = {
        "quartic_search_id": 1,
        "anchor_key": "anchor-0",
        "model_key": "model-0",
        "hit": {"x": "0", "y": "1", "square_y": "1"},
        "reduction_transform": None,
        "error_class": "ValueError",
        "error": "fixture map-back failure",
    }
    monkeypatch.setattr(
        pq,
        "_map_hits",
        lambda E, model, hits, search_id=None: ([], [dict(failure)]),
    )
    captured = {}

    def fake_record(db, search_id, failures, sample_limit=16):
        captured["search_id"] = search_id
        captured["failures"] = [dict(item) for item in failures]
        return {
            "count": 1,
            "samples": [dict(failure, failure_key="abc")],
            "samples_truncated": False,
        }

    monkeypatch.setattr(
        "rank42.quartic_store.record_map_back_failures",
        fake_record,
    )
    result = run_pointed_quartic_escalation(
        object(),
        curve_id=1,
        E=E,
        heights=[100],
        anchors=1,
        pool_size=1,
        rounds=1,
        deep_keep=1,
        timeout=1,
        reduce_timeout=0,
        executable="/tmp/ratpoints",
        dry_round_min_coverage=1.0,
    )
    assert result["status"] == "completed"
    assert result["map_back_failures"] == 1
    assert len(result["map_back_failure_samples"]) == 1
    assert result["map_back_diagnostics_truncated"] is False
    assert result["rounds"][0]["map_back_failures"] == 1
    assert captured["search_id"] == 1
    assert captured["failures"][0]["error_class"] == "ValueError"
