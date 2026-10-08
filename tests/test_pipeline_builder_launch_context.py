import json
import sqlite3
import types

from rank42.ui_pages import build_your_own


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeLaunchDB:
    def __init__(self, *, campaign=None, error=None):
        self.campaign = campaign
        self.error = error

    def execute(self, sql, params=()):
        if self.error is not None:
            raise self.error
        if "FROM ui_application_state" in sql:
            if self.campaign is None:
                return _FakeResult(None)
            return _FakeResult({
                "value_json": json.dumps(int(self.campaign["id"])),
            })
        if "FROM ui_settings" in sql:
            if self.campaign is None:
                return _FakeResult(None)
            return _FakeResult({
                "value_json": json.dumps(int(self.campaign["id"])),
            })
        if "FROM research_campaigns" in sql:
            if self.campaign is None:
                return _FakeResult(None)
            return _FakeResult(self.campaign)
        raise AssertionError(f"unexpected Launch Context SQL: {sql}")


def _campaign():
    return {
        "id": 23,
        "name": "Pipeline Research",
        "created_at": "2026-09-25T13:00:00",
        "status": "active",
    }


def _ctx():
    return types.SimpleNamespace(
        db_path="rank42.db",
        project_root=".",
        detected_science_python=lambda: "python",
    )


def test_pipeline_builder_freezes_shared_context_into_run_and_job(monkeypatch):
    captured = {}

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 101

    def launch_resolved(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 202

    monkeypatch.setattr(build_your_own, "create_pipeline_run", create_run)
    monkeypatch.setattr(build_your_own, "launch_resolved", launch_resolved)
    monkeypatch.setattr(
        build_your_own,
        "setting",
        lambda *_args, **_kwargs: "python",
    )

    plugin = types.SimpleNamespace(id="family.demo")
    variant = types.SimpleNamespace(id="v2")
    run_id, job_id = build_your_own._launch_pipeline_builder_search(
        _FakeLaunchDB(campaign=_campaign()),
        _ctx(),
        pipeline_id=7,
        pipeline_name="Rank Push",
        mode="family",
        target={
            "plugin_id": "family.demo",
            "variant_id": "v2",
            "candidate_pool_id": 77,
            "only_unsearched": True,
        },
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={
            "target_rank": 18,
            "retention_floor": 10,
            "certificate_timeout": 120,
            "exact_candidates": 64,
            "ratpoints_backend": "GPU",
            "ratpoints": "/tmp/ratpoints",
            "feature_plugin_ids": ["feat.a"],
        },
        selected_plugin=plugin,
        selected_variant=variant,
    )

    assert (run_id, job_id) == (101, 202)
    run_config = captured["run"]["run_config"]
    job = captured["job"]
    metadata = job["metadata"]

    assert captured["run"]["pipeline_id"] == 7
    assert captured["run"]["pipeline_name"] == "Rank Push"
    assert captured["run"]["target_mode"] == "family"
    assert captured["run"]["target"]["candidate_pool_id"] == 77

    expected_shared = {
        "campaign_id": 23,
        "campaign_name": "Pipeline Research",
        "campaign_created_at": "2026-09-25T13:00:00",
        "launch_surface": "pipeline_builder",
        "plugin_id": "family.demo",
        "plugin_variant": "v2",
        "target_mode": "family",
        "pipeline_id": 7,
        "pipeline_name": "Rank Push",
        "feature_plugin_ids": ["feat.a"],
        "source_pool_id": 77,
    }
    for key, value in expected_shared.items():
        assert run_config[key] == value
        assert metadata[key] == value

    assert metadata["pipeline_run_id"] == 101
    assert metadata["ratpoints_backend"] == "GPU"
    assert job["campaign_failure"] is None
    assert job["kind"] == "pipeline_search"
    assert job["label"] == "Pipeline · Rank Push"
    assert job["command"] == [
        "python",
        "-m",
        "rank42.pipeline_runner",
        "--project-root",
        ".",
        "--db",
        "rank42.db",
        "--run-id",
        "101",
    ]


def test_pipeline_builder_passes_campaign_failure_without_second_lookup(monkeypatch):
    captured = {}

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 303

    def launch_resolved(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 404

    monkeypatch.setattr(build_your_own, "create_pipeline_run", create_run)
    monkeypatch.setattr(build_your_own, "launch_resolved", launch_resolved)
    monkeypatch.setattr(
        build_your_own,
        "setting",
        lambda *_args, **_kwargs: "python",
    )

    run_id, job_id = build_your_own._launch_pipeline_builder_search(
        _FakeLaunchDB(error=sqlite3.OperationalError("database is locked")),
        _ctx(),
        pipeline_id=None,
        pipeline_name="Unsaved Pipeline",
        mode="general",
        target={"pool_mode": "random", "pool_size": 5000},
        stages=[],
        run_config={
            "target_rank": 8,
            "ratpoints_backend": "CPU",
            "feature_plugin_ids": [],
        },
    )

    assert (run_id, job_id) == (303, 404)
    expected = {
        "level": "warning",
        "message": "Active campaign temporarily unavailable: database is locked",
    }
    assert captured["run"]["run_config"]["campaign_context_error"] == expected
    assert captured["job"]["metadata"]["campaign_context_error"] == expected
    assert captured["job"]["campaign_failure"] == expected
    assert "campaign_id" not in captured["run"]["run_config"]
    assert "campaign_id" not in captured["job"]["metadata"]
    assert captured["run"]["run_config"]["launch_surface"] == "pipeline_builder"
    assert captured["job"]["metadata"]["launch_surface"] == "pipeline_builder"


def test_pipeline_builder_explicit_campaign_skips_active_lookup(monkeypatch):
    captured = {}

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 505

    def launch_resolved(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 606

    monkeypatch.setattr(build_your_own, "create_pipeline_run", create_run)
    monkeypatch.setattr(build_your_own, "launch_resolved", launch_resolved)
    monkeypatch.setattr(
        build_your_own,
        "setting",
        lambda *_args, **_kwargs: "python",
    )

    run_id, job_id = build_your_own._launch_pipeline_builder_search(
        _FakeLaunchDB(error=AssertionError("active lookup should be skipped")),
        _ctx(),
        pipeline_id=11,
        pipeline_name="Explicit Campaign",
        mode="torsion",
        target={"torsion_group": "C2"},
        stages=[],
        run_config={
            "target_rank": 6,
            "feature_plugin_ids": [],
            "campaign_id": 88,
            "campaign_name": "Explicit",
            "campaign_created_at": "2026-01-01T00:00:00",
        },
    )

    assert (run_id, job_id) == (505, 606)
    assert captured["run"]["run_config"]["campaign_id"] == 88
    assert captured["job"]["metadata"]["campaign_id"] == 88
    assert captured["job"]["campaign_failure"] is None

def test_pipeline_builder_dirty_launch_detaches_saved_identity_and_freezes_derivation(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        build_your_own,
        "_pipeline_builder_lineage",
        lambda *args, **kwargs: {
            "pipeline_id": None,
            "dirty": True,
            "source_pipeline_id": 12,
            "source_pipeline_revision": 3,
            "source_pipeline_hash": "savedhash",
            "provenance": {
                "derived_from_pipeline_id": 12,
                "derived_from_pipeline_revision": 3,
                "derived_from_pipeline_hash": "savedhash",
                "pipeline_source_relation": "modified",
            },
        },
    )

    def create_run(db, **kwargs):
        captured["run"] = kwargs
        return 707

    def launch_resolved(ctx, db, **kwargs):
        captured["job"] = kwargs
        return 808

    monkeypatch.setattr(build_your_own, "create_pipeline_run", create_run)
    monkeypatch.setattr(build_your_own, "launch_resolved", launch_resolved)
    monkeypatch.setattr(
        build_your_own,
        "setting",
        lambda *_args, **_kwargs: "python",
    )

    run_id, job_id = build_your_own._launch_pipeline_builder_search(
        _FakeLaunchDB(campaign=None),
        _ctx(),
        pipeline_id=12,
        pipeline_name="Saved but edited",
        mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {"prime_bound": 997}}],
        run_config={
            "target_rank": 9,
            "ratpoints_backend": "CPU",
            "feature_plugin_ids": [],
        },
        definition_config={
            "target_rank": 9,
            "retention_floor": 0,
            "certificate_timeout": 120,
            "exact_candidates": 64,
            "ratpoints_backend": "CPU",
            "feature_plugin_ids": [],
            "target": {"pool_mode": "open"},
        },
    )

    assert (run_id, job_id) == (707, 808)
    assert captured["run"]["pipeline_id"] is None
    run_config = captured["run"]["run_config"]
    metadata = captured["job"]["metadata"]
    for key, value in {
        "derived_from_pipeline_id": 12,
        "derived_from_pipeline_revision": 3,
        "derived_from_pipeline_hash": "savedhash",
        "pipeline_source_relation": "modified",
    }.items():
        assert run_config[key] == value
        assert metadata[key] == value
    assert run_config.get("pipeline_id") is None
    assert metadata.get("pipeline_id") is None

