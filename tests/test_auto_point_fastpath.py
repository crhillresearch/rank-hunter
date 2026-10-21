import json

import rank42.auto_point_search as point_search
from rank42.model_prep import ModelPrepTimeout
from rank42.ratpoints import RatpointsTimeout


class _EmptyResult:
    def fetchall(self):
        return []


class _FakeDB:
    def execute(self, *args, **kwargs):
        return _EmptyResult()


def test_minimal_model_timeout_falls_back_to_exact_stored_search(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1/2", "1/3"]),
    }
    seen = {}

    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 18)

    def fail_minimal(*args, **kwargs):
        raise ModelPrepTimeout("global minimal model exceeded 1s")

    monkeypatch.setattr(point_search, "run_global_minimal_model", fail_minimal)

    def fake_ratpoints(coefficients, height, **kwargs):
        seen["coefficients"] = coefficients
        seen["height"] = height
        seen.update(kwargs)
        return {"points": [], "runtime": 0.01}

    monkeypatch.setattr(point_search, "run_ratpoints", fake_ratpoints)
    monkeypatch.setattr(
        point_search,
        "certify_ledger_growth",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("deferred seed search must not certify")
        ),
    )

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="fastpath-test",
        heights=(1000,),
        chart_budget=1,
        timeout=2,
        executable="/tmp/ratpoints_gpu",
        native_only=True,
        minimal_model=True,
        denominator_low=1,
        denominator_high=1,
        model_prep_timeout=1,
        certify_after_search=False,
    )

    assert result["search_model"] == "stored_exact_fallback"
    assert "global minimal model exceeded" in result["model_prep_error"]
    assert result["completed_searches"] == 1
    assert result["failures"] == 0
    assert result["planned_searches"] == 1
    assert result["search_outcome"] == "completed"
    assert result["search_coverage"]["coverage_fraction"] == 1.0
    assert result["search_coverage"]["complete"] is True
    assert result["certificate_attempts"] == 0
    assert result["rigorous_lower"] == 18
    assert seen["denominator_low"] == 1
    assert seen["denominator_high"] == 1


def test_point_tier_timeout_is_incomplete_not_zero_yield(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1", "0"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 4)
    monkeypatch.setattr(
        point_search,
        "run_ratpoints",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RatpointsTimeout("injected timeout")
        ),
    )

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="timeout-contract",
        heights=(1000,),
        chart_budget=1,
        timeout=2,
        native_only=True,
        minimal_model=False,
        denominator_low=1,
        denominator_high=1,
        certify_after_search=False,
    )

    assert result["planned_searches"] == 1
    assert result["completed_searches"] == 0
    assert result["timeouts"] == 1
    assert result["exact_points"] == 0
    assert result["search_outcome"] == "timeout"
    assert result["search_coverage"]["coverage_fraction"] == 0.0
    assert result["search_coverage"]["complete"] is False
    assert result["search_coverage"]["comparable"] is False


def test_pipeline_checkpoint_resumes_at_first_unfinished_attempt(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1", "0"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 4)

    def fake_get_attempt(db, **kwargs):
        if int(kwargs["height"]) == 100:
            return {
                "status": "completed",
                "exact_points": 1,
                "timeout_seconds": 2,
            }
        return None

    recorded = []
    monkeypatch.setattr(point_search, "get_pipeline_point_attempt", fake_get_attempt)
    monkeypatch.setattr(
        point_search,
        "upsert_pipeline_point_attempt",
        lambda db, **kwargs: recorded.append(dict(kwargs)) or kwargs,
    )
    called_heights = []

    def fake_ratpoints(coefficients, height, **kwargs):
        called_heights.append(int(height))
        return {"points": [], "runtime": 0.01}

    monkeypatch.setattr(point_search, "run_ratpoints", fake_ratpoints)

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="pipeline-checkpoint",
        heights=(100, 1000),
        chart_budget=1,
        timeout=2,
        native_only=True,
        minimal_model=False,
        denominator_low=1,
        denominator_high=1,
        certify_after_search=False,
        pipeline_checkpoint={
            "run_id": 7,
            "candidate_id": 11,
            "stage_index": 3,
            "stage_id": "integral_seed",
        },
        retry_policy="manual",
        retry_timeout=8,
    )

    assert called_heights == [1000]
    assert result["planned_searches"] == 2
    assert result["completed_searches"] == 1
    assert result["resume_completed"] == 1
    assert result["resume_skipped"] == 1
    assert result["search_outcome"] == "completed"
    assert result["pipeline_checkpointed"] is True
    assert [rec["status"] for rec in recorded] == ["running", "completed"]


def test_pipeline_point_retry_policy_is_explicit():
    timeout_row = {
        "status": "timeout",
        "timeout_seconds": 4,
    }
    skip, effective, reason = point_search._pipeline_retry_decision(
        timeout_row,
        retry_policy="manual",
        timeout_seconds=4,
        retry_timeout=16,
    )
    assert skip is True
    assert effective == 4
    assert reason == "manual"

    skip, effective, reason = point_search._pipeline_retry_decision(
        timeout_row,
        retry_policy="automatic",
        timeout_seconds=4,
        retry_timeout=16,
    )
    assert skip is False
    assert effective == 4
    assert reason == "automatic"

    skip, effective, reason = point_search._pipeline_retry_decision(
        timeout_row,
        retry_policy="escalated",
        timeout_seconds=4,
        retry_timeout=16,
    )
    assert skip is False
    assert effective == 16
    assert reason == "escalated"

    exhausted = {
        "status": "timeout",
        "timeout_seconds": 16,
    }
    skip, effective, reason = point_search._pipeline_retry_decision(
        exhausted,
        retry_policy="escalated",
        timeout_seconds=4,
        retry_timeout=16,
    )
    assert skip is True
    assert effective == 16
    assert reason == "escalated_budget_already_attempted"

    interrupted = {
        "status": "running",
        "timeout_seconds": 4,
    }
    skip, effective, reason = point_search._pipeline_retry_decision(
        interrupted,
        retry_policy="manual",
        timeout_seconds=4,
        retry_timeout=16,
    )
    assert skip is False
    assert effective == 4
    assert reason == "interrupted"



def test_point_tier_records_effective_denominator_region(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1", "0"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 4)
    monkeypatch.setattr(
        point_search,
        "run_ratpoints",
        lambda *args, **kwargs: {"points": [], "runtime": 0.01},
    )

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="effective-region",
        heights=(1000,),
        chart_budget=1,
        timeout=2,
        native_only=True,
        minimal_model=False,
        denominator_low=101,
        denominator_high=10000,
        certify_after_search=False,
    )

    assert result["planned_searches"] == 1
    assert result["search_outcome"] == "completed"
    assert result["attempted_regions"] == [{
        "chart_index": 1,
        "center": "0",
        "scale": "1",
        "height": 1000,
        "requested_denominator_low": 101,
        "requested_denominator_high": 10000,
        "effective_denominator_low": 101,
        "effective_denominator_high": 1000,
        "status": "completed",
        "resume": False,
    }]


def test_point_hit_records_chart_and_stored_x_denominators(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1", "0"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 0)
    monkeypatch.setattr(
        point_search,
        "search_charts",
        lambda db, curve_id, E, *, tier, budget: [
            (point_search.QQ("1/2"), point_search.QQ(1)),
        ],
    )

    class Hit:
        x = "-1/2"
        y = "0"

    monkeypatch.setattr(
        point_search,
        "run_ratpoints",
        lambda *args, **kwargs: {"points": [Hit()], "runtime": 0.01},
    )
    captured = []
    discoveries = []
    monkeypatch.setattr(
        point_search,
        "upsert_point",
        lambda db, **kwargs: captured.append(dict(kwargs)) or {"id": 77},
    )
    monkeypatch.setattr(
        point_search,
        "record_point_discovery",
        lambda db, **kwargs: discoveries.append(dict(kwargs)),
    )

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="coordinate-provenance",
        heights=(20,),
        chart_budget=1,
        timeout=2,
        native_only=False,
        minimal_model=False,
        denominator_low=1,
        denominator_high=20,
        certify_after_search=False,
    )

    assert result["exact_points"] == 1
    assert len(captured) == 1
    metadata = captured[0]["metadata"]
    assert metadata["center"] == "1/2"
    assert metadata["scale"] == "1"
    assert metadata["chart_x_denominator"] == 2
    assert metadata["stored_x_denominator"] == 1
    assert metadata["requested_denominator_low"] == 1
    assert metadata["requested_denominator_high"] == 20
    assert metadata["effective_denominator_low"] == 1
    assert metadata["effective_denominator_high"] == 20
    assert result["discovery_observations"] == 1
    assert result["map_back_failures"] == 0
    assert len(discoveries) == 1
    assert discoveries[0]["outcome"] == "mapped"
    assert discoveries[0]["point_id"] == 77
    assert discoveries[0]["chart_x"] == "-1/2"
    assert str(discoveries[0]["stored_x"]) == "0"



def test_prepared_point_model_reuses_one_fallback_coordinate_system(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1/2", "1/3"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 0)
    prep_calls = []

    def fail_minimal(*args, **kwargs):
        prep_calls.append(dict(kwargs))
        raise ModelPrepTimeout("fixed fallback")

    monkeypatch.setattr(point_search, "run_global_minimal_model", fail_minimal)
    monkeypatch.setattr(
        point_search,
        "run_ratpoints",
        lambda *args, **kwargs: {"points": [], "runtime": 0.01},
    )

    prepared = point_search.prepare_point_search_model(
        _FakeDB(),
        curve_id=1,
        minimal_model=True,
        model_prep_timeout=1,
    )
    first = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="prepared-model-H100",
        heights=(100,),
        chart_budget=1,
        timeout=2,
        native_only=True,
        minimal_model=True,
        denominator_low=1,
        denominator_high=1,
        certify_after_search=False,
        prepared_model=prepared,
    )
    second = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="prepared-model-H1000",
        heights=(1000,),
        chart_budget=1,
        timeout=2,
        native_only=True,
        minimal_model=True,
        denominator_low=1,
        denominator_high=1,
        certify_after_search=False,
        prepared_model=prepared,
    )

    assert len(prep_calls) == 1
    assert prepared["search_model"] == "stored_exact_fallback"
    assert prepared["model_prep_error"] == "fixed fallback"
    assert first["search_model"] == "stored_exact_fallback"
    assert second["search_model"] == "stored_exact_fallback"
    assert first["prepared_model_reused"] is True
    assert second["prepared_model_reused"] is True



def test_map_back_failure_is_visible_and_persisted(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1", "0"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 0)

    class BadHit:
        x = "not-a-rational"
        y = "0"

    monkeypatch.setattr(
        point_search,
        "run_ratpoints",
        lambda *args, **kwargs: {"points": [BadHit()], "runtime": 0.01},
    )
    discoveries = []
    monkeypatch.setattr(
        point_search,
        "record_point_discovery",
        lambda db, **kwargs: discoveries.append(dict(kwargs)),
    )

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="map-back-failure",
        heights=(100,),
        chart_budget=1,
        timeout=2,
        native_only=True,
        minimal_model=False,
        denominator_low=1,
        denominator_high=1,
        certify_after_search=False,
    )

    assert result["completed_searches"] == 1
    assert result["map_back_failures"] == 1
    assert result["discovery_observations"] == 1
    assert result["search_outcome"] == "partial"
    assert result["search_coverage"]["complete"] is False
    assert len(discoveries) == 1
    observation = discoveries[0]
    assert observation["outcome"] == "map_back_error"
    assert observation.get("point_id") is None
    assert observation["chart_x"] == "not-a-rational"
    assert observation["chart_w"] == "0"
    assert observation["error_class"]
    assert observation["error"]


def test_repeated_point_hits_keep_append_only_discovery_observations(monkeypatch):
    row = {
        "a_invariants_json": json.dumps(["0", "0", "0", "-1", "0"]),
    }
    monkeypatch.setattr(point_search, "get_curve", lambda db, curve_id: row)
    monkeypatch.setattr(point_search, "proven_lower", lambda curve: 0)
    monkeypatch.setattr(
        point_search,
        "search_charts",
        lambda db, curve_id, E, *, tier, budget: [
            (point_search.QQ(0), point_search.QQ(1)),
            (point_search.QQ(1), point_search.QQ(1)),
        ],
    )

    calls = []

    class Hit0:
        x = "0"
        y = "0"

    class Hit1:
        x = "-1"
        y = "0"

    def fake_ratpoints(*args, **kwargs):
        calls.append(1)
        hit = Hit0() if len(calls) == 1 else Hit1()
        return {
            "points": [hit],
            "runtime": 0.01,
            "engine_provenance": {
                "backend": "GPU",
                "executable": "/vendor/ratpoints_gpu",
                "version": "3.0.0",
                "source": "vendored",
                "vendored_revision": "c" * 40,
                "argv": ["/vendor/ratpoints_gpu", "poly", "20"],
                "timeout_seconds": 2.0,
                "runtime_seconds": 0.01,
                "status": "completed",
            },
        }

    monkeypatch.setattr(point_search, "run_ratpoints", fake_ratpoints)
    upserts = []
    discoveries = []
    monkeypatch.setattr(
        point_search,
        "upsert_point",
        lambda db, **kwargs: upserts.append(dict(kwargs)) or {"id": 91},
    )
    monkeypatch.setattr(
        point_search,
        "record_point_discovery",
        lambda db, **kwargs: discoveries.append(dict(kwargs)),
    )
    # This unit uses a deliberately tiny fake DB. Bypass durable Pipeline
    # checkpoint I/O while still exercising discovery-lineage propagation from
    # the supplied checkpoint identity.
    monkeypatch.setattr(
        point_search,
        "_pipeline_point_attempt",
        lambda db, **kwargs: None,
    )
    pipeline_attempts = []
    monkeypatch.setattr(
        point_search,
        "_record_pipeline_point_attempt",
        lambda db, **kwargs: pipeline_attempts.append(dict(kwargs)),
    )

    result = point_search.run_auto_point_tier(
        _FakeDB(),
        curve_id=1,
        tier="repeat-provenance",
        heights=(20,),
        chart_budget=2,
        timeout=2,
        native_only=False,
        minimal_model=False,
        denominator_low=1,
        denominator_high=20,
        certify_after_search=False,
        pipeline_checkpoint={
            "run_id": 17,
            "candidate_id": 44,
            "stage_index": 6,
            "stage_id": "affine_search",
        },
    )

    assert result["exact_points"] == 1
    assert result["discovery_observations"] == 2
    assert len(upserts) == 1
    assert len(discoveries) == 2
    assert [rec["point_id"] for rec in discoveries] == [91, 91]
    assert [rec["center"] for rec in discoveries] == ["0", "1"]
    assert [rec["chart_x"] for rec in discoveries] == ["0", "-1"]
    assert {rec["pipeline_run_id"] for rec in discoveries} == {17}
    assert {rec["pipeline_candidate_id"] for rec in discoveries} == {44}
    assert {rec["pipeline_stage_index"] for rec in discoveries} == {6}
    assert {rec["pipeline_stage_id"] for rec in discoveries} == {"affine_search"}
    assert result["ratpoints_engines"] == [{
        "backend": "GPU",
        "executable": "/vendor/ratpoints_gpu",
        "version": "3.0.0",
        "source": "vendored",
        "vendored_revision": "c" * 40,
    }]
    assert all(
        rec["metadata"]["ratpoints_engine"]["version"] == "3.0.0"
        for rec in discoveries
    )
    completed_attempts = [
        rec for rec in pipeline_attempts if rec.get("status") == "completed"
    ]
    assert len(completed_attempts) == 2
    assert all(
        rec["engine"]["argv"][0] == "/vendor/ratpoints_gpu"
        for rec in completed_attempts
    )
    assert all(
        rec["engine"]["timeout_seconds"] == 2.0
        for rec in completed_attempts
    )
