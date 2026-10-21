from pathlib import Path

from rank42.ui_pages import common


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_ordinary_launch_uses_shared_resolver_and_retains_feature_hook_authority():
    source = _source("rank42/ui_pages/common.py")
    launch_source = source.split("def launch(ctx, db", 1)[1].split(
        "def search_pipeline_selector", 1
    )[0]
    enqueue_source = source.split("def _enqueue_resolved_launch(", 1)[1].split(
        "def launch(ctx, db", 1
    )[0]

    assert "resolve_launch_context(" in launch_source
    assert "launch_surface=str(kind)" in launch_source
    assert "launch_resolved(" in launch_source
    assert "_active_campaign_lookup(db)" not in launch_source

    assert "apply_search_command_features(" in enqueue_source
    assert 'metadata["feature_command_hooks"] = feature_audit' in enqueue_source
    assert "enqueue_job(" in enqueue_source


def test_pipeline_substitution_uses_shared_resolver_and_preserves_failure_provenance():
    source = _source("rank42/ui_pages/common.py")
    pipeline_source = source.split("def launch_with_pipeline(", 1)[1].split(
        "def _usable_executable", 1
    )[0]

    assert "resolve_launch_context(" in pipeline_source
    assert "normalize_launch_metadata(" in pipeline_source
    assert "launch_context_snapshot(native_metadata)" in pipeline_source
    assert "launch_context_snapshot(merged_run_config)" in pipeline_source
    assert "campaign_failure=run_context.failure" in pipeline_source
    assert "launch_resolved(" in pipeline_source
    assert "from rank42.manage_store import active_campaign" not in pipeline_source
    assert "campaign = active_campaign(db)" not in pipeline_source
    assert "except Exception:" not in pipeline_source

    assert 'pipeline_run_id=run_id' in pipeline_source
    assert 'pipeline_id=payload.get("id")' in pipeline_source
    assert 'pipeline_name=payload.get("name")' in pipeline_source
    assert 'target_mode=mode' in pipeline_source
    assert 'feature_plugin_ids=merged_run_config.get("feature_plugin_ids") or []' in pipeline_source
    assert 'metadata["builder_override"] = True' in pipeline_source


def test_pipeline_builder_uses_shared_resolver_and_resolved_launch_seam():
    source = _source("rank42/ui_pages/build_your_own.py")
    helper_source = source.split(
        "def _launch_pipeline_builder_search(", 1
    )[1].split("def _render_settings(", 1)[0]
    run_source = source.split('if run_col.button(', 1)[1].split(
        "def _render_pipeline_editor_actions", 1
    )[0]

    assert "resolve_launch_context(" in helper_source
    assert 'launch_surface="pipeline_builder"' in helper_source
    assert "launch_context_snapshot(" in helper_source
    assert "normalize_launch_metadata(" in helper_source
    assert "launch_resolved(" in helper_source
    assert "create_pipeline_run(" in helper_source
    assert "campaign_failure=run_context.failure" in helper_source

    assert "campaign = active_campaign(db)" not in run_source
    assert 'run_config["campaign_id"]' not in run_source
    assert 'metadata["campaign_id"]' not in run_source
    assert "_launch_pipeline_builder_search(" in run_source


def test_search_surfaces_route_pipeline_substitution_through_shared_helper():
    expected = {
        "rank42/ui_pages/auto_search.py": "launch_with_pipeline(",
        "rank42/ui_pages/family_search.py": "launch_with_pipeline(",
        "rank42/ui_pages/general_hunt.py": "launch_with_pipeline(",
        "rank42/ui_pages/target_curve.py": "launch_with_pipeline(",
        "rank42/ui_pages/geometry_search_page.py": "launch_with_pipeline(",
    }
    for relative_path, marker in expected.items():
        assert marker in _source(relative_path), relative_path


def test_launch_surface_is_normalized_in_common_and_pipeline_builder():
    common_source = _source("rank42/ui_pages/common.py")
    builder_source = _source("rank42/ui_pages/build_your_own.py")

    assert "launch_surface" in common_source
    assert 'launch_surface="pipeline_builder"' in builder_source


def test_launch_context_target_fields_are_explicitly_inventoried():
    # Keep the future service contract visible beside the characterization.
    target_fields = {
        "campaign_id",
        "campaign_name",
        "campaign_created_at",
        "launch_surface",
        "plugin_id",
        "plugin_variant",
        "target_mode",
        "pipeline_run_id",
        "pipeline_id",
        "pipeline_name",
        "feature_plugin_ids",
        "source_pool_id",
        "feature_command_hooks",
        "campaign_context_error",
    }

    authority = _source("rank42/launch_context.py")
    ordinary = _source("rank42/ui_pages/common.py")
    builder = _source("rank42/ui_pages/build_your_own.py")
    combined = authority + "\n" + ordinary + "\n" + builder

    # The shared authority owns normalized field names; common launch paths and
    # Pipeline Builder consume that resolver/snapshot contract.
    present = {field for field in target_fields if field in combined}
    assert {
        "campaign_id",
        "campaign_name",
        "campaign_created_at",
        "plugin_id",
        "plugin_variant",
        "target_mode",
        "pipeline_run_id",
        "pipeline_id",
        "pipeline_name",
        "feature_plugin_ids",
        "feature_command_hooks",
        "campaign_context_error",
    }.issubset(present)
    assert "launch_surface" in present
    assert "source_pool_id" in present


def test_legacy_search_campaign_identity_is_not_reused_as_research_campaign_identity():
    auto_source = _source("rank42/ui_pages/auto_search.py")
    geometry_source = _source("rank42/ui_pages/geometry_search_page.py")

    assert '"search_campaign_id": int(campaign["id"])' not in auto_source
    assert '"search_campaign_kind": "auto"' not in auto_source
    assert '"campaign_id": int(campaign["id"])' not in auto_source
    assert "def _launch_campaign(" not in auto_source
    assert '"rank42.auto_search"' not in auto_source

    assert '"search_campaign_id": int(campaign["id"])' in geometry_source
    assert '"search_campaign_kind": "geometry"' in geometry_source
    assert '"campaign_id": int(campaign["id"])' not in geometry_source

    assert 'value = metadata.get("search_campaign_id")' in auto_source
    assert 'value = metadata.get("campaign_id")' in auto_source
    assert 'value = metadata.get("search_campaign_id")' in geometry_source
    assert 'value = metadata.get("campaign_id")' in geometry_source


def test_pipeline_resume_reuses_frozen_launch_context_instead_of_manual_campaign_copy():
    source = _source("rank42/ui_pages/jobs_page.py")
    helper = source.split(
        "def _launch_pipeline_run_resume_attempt(", 1
    )[1].split("def _pipeline_run_handoff", 1)[0]
    handoff = source.split(
        "def _pipeline_run_handoff(", 1
    )[1].split("def page", 1)[0]

    assert "launch_context_snapshot(run_config)" in helper
    assert "normalize_launch_metadata(" in helper
    assert "launch_resolved(" in helper
    assert "campaign_failure=failure" in helper
    assert 'launch_surface=run_config.get("launch_surface") or "pipeline_resume"' in helper

    assert "_launch_pipeline_run_resume_attempt(" in handoff
    assert "resume_pipeline_run(" in handoff
    assert 'metadata={"pipeline_run_id"' not in handoff
    assert '"campaign_id": int(' not in handoff
    assert 'jid = launch(' not in handoff


def test_general_search_launch_is_pipeline_only_and_keeps_current_controls():
    source = _source("rank42/ui_pages/general_hunt.py")
    common = _source("rank42/ui_pages/common.py")

    assert 'builtin_label="Built-in Pipeline · Current General controls"' in source
    assert "general_search_stages(" in source
    assert "require_pipeline=True" in source
    assert "native_command=[]" in source
    assert '"rank42.general_hunt"' not in source
    assert "pipeline_target" in source
    for token in (
        '"pool_mode": mode',
        '"pool_size": pool',
        "u_min=umin",
        "a_min=amin",
        '"target_rank": target',
        '"ratpoints_backend": rp_backend',
    ):
        assert token in source

    pipeline_launch = common.split("def launch_with_pipeline(", 1)[1].split(
        "def _usable_executable", 1
    )[0]
    assert "if require_pipeline:" in pipeline_launch
    assert 'raise ValueError("this Search surface requires Pipeline execution")' in pipeline_launch
