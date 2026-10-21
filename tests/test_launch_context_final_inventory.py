import json
import types

from rank42 import launch_context
from rank42.ui_pages import auto_search, geometry_search_page, jobs_page


def test_auto_page_has_no_legacy_native_launch_reconstruction():
    assert not hasattr(auto_search, "_campaign_command")
    assert not hasattr(auto_search, "_launch_campaign")
    assert not hasattr(auto_search, "_render_history")


def test_auto_search_campaign_reader_prefers_new_key_and_supports_legacy():
    assert auto_search._search_campaign_id({
        "search_campaign_id": 7,
        "campaign_id": 99,
    }) == 7
    assert auto_search._search_campaign_id({"campaign_id": 99}) == 99
    assert auto_search._search_campaign_id({}) is None


def test_geometry_native_launch_keeps_search_campaign_identity_separate(monkeypatch):
    campaign = {
        "id": 52,
        "search_mode": "geometry",
        "family_name": "Geometry Family",
        "plugin_id": "family.geometry",
        "variant_id": "v3",
        "torsion_group": None,
        "target_rank": 20,
    }
    captured = {}

    monkeypatch.setattr(
        geometry_search_page,
        "_command",
        lambda db, ctx, row: ["python", "-V"],
    )
    monkeypatch.setattr(
        geometry_search_page,
        "update_campaign",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        geometry_search_page,
        "launch",
        lambda ctx, db, **kwargs: captured.update(kwargs) or 502,
    )

    jid = geometry_search_page._launch(object(), object(), campaign)

    assert jid == 502
    metadata = captured["metadata"]
    assert metadata["search_campaign_id"] == 52
    assert metadata["search_campaign_kind"] == "geometry"
    assert "campaign_id" not in metadata
    assert metadata["plugin_id"] == "family.geometry"
    assert metadata["plugin_variant"] == "v3"


def test_geometry_search_campaign_reader_prefers_new_key_and_supports_legacy():
    assert geometry_search_page._search_campaign_id({
        "search_campaign_id": 8,
        "campaign_id": 101,
    }) == 8
    assert geometry_search_page._search_campaign_id({"campaign_id": 101}) == 101
    assert geometry_search_page._search_campaign_id({}) is None


def test_jobs_resume_reuses_frozen_launch_context_without_resolving_active_campaign(monkeypatch):
    captured = {}
    ctx = types.SimpleNamespace(
        db_path="rank42.db",
        project_root=".",
        detected_science_python=lambda: "python",
    )
    selected_run = {
        "id": 77,
        "pipeline_id": 9,
        "pipeline_name": "Frozen Pipeline",
        "target_mode": "family",
        "run_config_json": json.dumps({
            "campaign_id": 31,
            "campaign_name": "Original Research Campaign",
            "campaign_created_at": "2026-09-01T00:00:00",
            "launch_surface": "pipeline_builder",
            "plugin_id": "family.demo",
            "plugin_variant": "v2",
            "feature_plugin_ids": ["feat.a"],
            "source_pool_id": 44,
            "ratpoints_backend": "GPU",
        }),
    }

    monkeypatch.setattr(
        jobs_page,
        "setting_value",
        lambda *_args, **_kwargs: "python",
    )
    monkeypatch.setattr(
        jobs_page,
        "launch_resolved",
        lambda ctx, db, **kwargs: captured.update(kwargs) or 601,
    )

    jid = jobs_page._launch_pipeline_run_resume_attempt(
        object(),
        ctx,
        selected_run,
    )

    assert jid == 601
    metadata = captured["metadata"]
    assert metadata["campaign_id"] == 31
    assert metadata["campaign_name"] == "Original Research Campaign"
    assert metadata["campaign_created_at"] == "2026-09-01T00:00:00"
    assert metadata["launch_surface"] == "pipeline_builder"
    assert metadata["pipeline_run_id"] == 77
    assert metadata["pipeline_id"] == 9
    assert metadata["pipeline_name"] == "Frozen Pipeline"
    assert metadata["plugin_id"] == "family.demo"
    assert metadata["plugin_variant"] == "v2"
    assert metadata["feature_plugin_ids"] == ["feat.a"]
    assert metadata["source_pool_id"] == 44
    assert metadata["ratpoints_backend"] == "GPU"
    assert captured["campaign_failure"] is None


def test_jobs_resume_preserves_frozen_campaign_lookup_failure(monkeypatch):
    captured = {}
    ctx = types.SimpleNamespace(
        db_path="rank42.db",
        project_root=".",
        detected_science_python=lambda: "python",
    )
    failure = {
        "level": "warning",
        "message": "Active campaign temporarily unavailable: database is locked",
    }
    selected_run = {
        "id": 78,
        "pipeline_id": None,
        "pipeline_name": "Legacy Run",
        "target_mode": "general",
        "run_config_json": json.dumps({
            "campaign_context_error": failure,
            "feature_plugin_ids": [],
            "ratpoints_backend": "CPU",
        }),
    }

    monkeypatch.setattr(
        jobs_page,
        "setting_value",
        lambda *_args, **_kwargs: "python",
    )
    monkeypatch.setattr(
        jobs_page,
        "launch_resolved",
        lambda ctx, db, **kwargs: captured.update(kwargs) or 602,
    )

    jid = jobs_page._launch_pipeline_run_resume_attempt(
        object(),
        ctx,
        selected_run,
    )

    assert jid == 602
    assert captured["campaign_failure"] == failure
    assert captured["metadata"]["campaign_context_error"] == failure
    assert captured["metadata"]["launch_surface"] == "pipeline_resume"
    assert "campaign_id" not in captured["metadata"]


def test_search_and_research_campaign_identities_can_coexist():
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class DB:
        def execute(self, sql, params=()):
            if "FROM ui_application_state" in sql:
                return Result({"value_json": json.dumps(17)})
            if "FROM research_campaigns" in sql:
                return Result({
                    "id": 17,
                    "name": "Research Campaign",
                    "created_at": "2026-09-25T12:00:00",
                    "status": "active",
                })
            raise AssertionError(sql)

    resolved = launch_context.resolve_launch_context(
        DB(),
        metadata={
            "search_campaign_id": 41,
            "search_campaign_kind": "auto",
        },
        launch_surface="auto_search",
    )

    assert resolved.metadata["search_campaign_id"] == 41
    assert resolved.metadata["search_campaign_kind"] == "auto"
    assert resolved.metadata["campaign_id"] == 17
    assert resolved.metadata["campaign_name"] == "Research Campaign"
