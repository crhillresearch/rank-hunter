import inspect
from pathlib import Path
from types import SimpleNamespace

from rank42.db import connect
from rank42.pipeline_catalog import template_stages
from rank42.pipeline_state import save_pipeline
from rank42.ui_pages import auto_search, common, diagnostics_page
from rank42 import ui as shell_ui


ROOT = Path(__file__).resolve().parents[1]


def _family_plugin(pid="demo", variants=None):
    variants = tuple(variants or (
        SimpleNamespace(
            id="v1",
            name="Variant One",
            curve_family_name="Family One",
        ),
        SimpleNamespace(
            id="v2",
            name="Variant Two",
            curve_family_name="Family Two",
        ),
    ))
    return SimpleNamespace(
        id=pid,
        name="Demo Family",
        variants=variants,
        capabilities=frozenset({"candidate_generation"}),
    )


def test_active_campaign_resolves_exact_variant_before_family_label():
    plugin = _family_plugin()
    campaign = {
        "id": 7,
        "plugin_id": "demo",
        "variant_id": "v2",
        "family": "stale old family label",
        "target_rank": 31,
        "updated_at": "2026-09-22T12:00:00+00:00",
    }

    resolved = auto_search._campaign_family_target(campaign, [plugin])

    assert resolved == (plugin, plugin.variants[1])


def test_active_campaign_legacy_family_fallback_is_exact_not_guessy():
    plugin = _family_plugin()
    legacy = {
        "id": 8,
        "plugin_id": "demo",
        "family": "Family One",
    }
    ambiguous = {
        "id": 9,
        "plugin_id": "demo",
        "family": "Unknown old label",
    }

    assert auto_search._campaign_family_target(legacy, [plugin]) == (
        plugin,
        plugin.variants[0],
    )
    assert auto_search._campaign_family_target(ambiguous, [plugin]) is None


def test_active_campaign_context_tracks_target_and_variant_changes():
    campaign = {
        "id": 10,
        "plugin_id": "demo",
        "variant_id": "v1",
        "family": "Family One",
        "target_rank": 20,
        "updated_at": "2026-09-22T12:00:00+00:00",
    }
    first = auto_search._campaign_context_token(campaign)

    campaign["variant_id"] = "v2"
    second = auto_search._campaign_context_token(campaign)
    campaign["target_rank"] = 25
    third = auto_search._campaign_context_token(campaign)
    campaign["updated_at"] = "2026-09-22T13:30:00+00:00"
    note_only_update = auto_search._campaign_context_token(campaign)

    assert first != second
    assert second != third
    assert note_only_update == third


def test_paused_campaign_releases_inherited_auto_widgets():
    plugin = _family_plugin()
    campaign = {
        "id": 11,
        "plugin_id": "demo",
        "variant_id": "v2",
        "family": "Family Two",
        "target_rank": 31,
    }
    target = (plugin, plugin.variants[1])
    state = {}

    auto_search._sync_auto_campaign_context(
        [plugin],
        campaign,
        target,
        state=state,
    )
    state.update({
        "auto-search-mode": "Family",
        "auto-search-plugin": plugin,
        "auto-search-variant-demo": plugin.variants[1],
        "auto-search-target-family-demo-v2": 31,
        "auto-family-demo-v2-pipeline-strategy": "preset:frontiermath_record_breaker",
        "auto-search-retention-family-demo-v2": 8,
    })

    # A paused Campaign is no longer active, so active_campaign() feeds None here.
    auto_search._sync_auto_campaign_context(
        [plugin],
        None,
        None,
        state=state,
    )

    assert "auto_campaign_context" not in state
    assert "auto_campaign_control_key" not in state
    assert "auto-search-mode" not in state
    assert "auto-search-plugin" not in state
    assert "auto-search-variant-demo" not in state
    assert "auto-search-target-family-demo-v2" not in state
    assert "auto-family-demo-v2-pipeline-strategy" not in state
    # Non-Campaign manual controls are not erased.
    assert state["auto-search-retention-family-demo-v2"] == 8


def test_auto_recommends_high_baseline_pipeline_before_other_rules(monkeypatch):
    plugin = SimpleNamespace(capabilities=frozenset({"quartic_search"}))
    variant = SimpleNamespace()
    monkeypatch.setattr(
        auto_search,
        "family_rank_claim",
        lambda plugin, variant: {"effective_lower": 17},
    )

    assert auto_search._recommended_auto_preset(
        target_mode="family",
        target_rank=32,
        plugin=plugin,
        variant=variant,
        profile={"recommended_preset": "high_baseline_rank_hunter"},
    ) == "high_baseline_rank_hunter"


def test_auto_recommends_strategy_from_goal_and_geometry(monkeypatch):
    plugin = SimpleNamespace(capabilities=frozenset())
    variant = SimpleNamespace()
    monkeypatch.setattr(
        auto_search,
        "family_rank_claim",
        lambda plugin, variant: {"effective_lower": 4},
    )

    assert auto_search._recommended_auto_preset(
        target_mode="family",
        target_rank=30,
        plugin=plugin,
        variant=variant,
        profile={},
    ) == "frontiermath_record_breaker"

    geometry_plugin = SimpleNamespace(capabilities=frozenset({"quartic_search"}))
    assert auto_search._recommended_auto_preset(
        target_mode="family",
        target_rank=12,
        plugin=geometry_plugin,
        variant=variant,
        profile={},
    ) == "geometry_grinder"

    assert auto_search._recommended_auto_preset(
        target_mode="family",
        target_rank=20,
        plugin=plugin,
        variant=variant,
        profile={},
    ) == "aggressive_rank_hunter"

    assert auto_search._recommended_auto_preset(
        target_mode="torsion",
        target_rank=11,
        profile={},
    ) == "auto"


def test_auto_honors_valid_pipeline_native_plugin_recommendation(monkeypatch):
    plugin = SimpleNamespace(capabilities=frozenset())
    variant = SimpleNamespace()
    monkeypatch.setattr(
        auto_search,
        "family_rank_claim",
        lambda plugin, variant: {"effective_lower": 4},
    )

    assert auto_search._recommended_auto_preset(
        target_mode="family",
        target_rank=12,
        plugin=plugin,
        variant=variant,
        profile={"recommended_preset": "geometry_grinder"},
    ) == "geometry_grinder"


def test_auto_pipeline_options_always_include_builtins_and_saved_overrides(tmp_path):
    db = connect(tmp_path / "rank42.db")
    saved_id = save_pipeline(
        db,
        name="My Family Pipeline",
        target_mode="family",
        stages=template_stages("minimal", "family"),
        config={"target_rank": 12},
    )

    options = auto_search._auto_pipeline_options(
        db,
        target_mode="family",
        recommended_preset="auto",
    )

    assert options
    assert options[0]["key"] == "preset:auto"
    assert options[0]["recommended"] is True
    assert any(rec["key"] == f"saved:{saved_id}" for rec in options)
    assert all(rec["key"] != "native" for rec in options)


def test_auto_stop_toggle_changes_execution_copy_not_recipe():
    original_stages = template_stages("auto", "family")
    choice = {
        "pipeline": {
            "id": None,
            "name": "Auto",
            "target_mode": "family",
            "stages": original_stages,
            "config": {},
        },
        "target_rank": 12,
        "strategy_label": "Auto",
    }

    keep_running = auto_search._auto_apply_stop_policy(
        choice,
        stop_on_target=False,
    )
    stop_at_goal = auto_search._auto_apply_stop_policy(
        choice,
        stop_on_target=True,
    )

    assert all(
        rec["id"] != "stop_goal"
        for rec in keep_running["pipeline"]["stages"]
    )
    assert stop_at_goal["pipeline"]["stages"][-1]["id"] == "stop_goal"
    assert sum(
        rec["id"] == "stop_goal"
        for rec in stop_at_goal["pipeline"]["stages"]
    ) == 1
    assert choice["pipeline"]["stages"] == original_stages


def test_pipeline_launch_preparation_is_shared_by_immediate_and_scheduled_paths():
    prepare = inspect.getsource(common.prepare_pipeline_launch)
    launch = inspect.getsource(common.launch_with_pipeline)

    assert "create_pipeline_run(" in prepare
    assert "commit=commit" in prepare
    assert '"kind": "pipeline_search"' in prepare
    assert '"pipeline_run_id": int(run_id)' in prepare
    assert "normalize_launch_metadata(" in prepare
    assert "launch_context_snapshot(merged_run_config)" in prepare

    assert "prepare_pipeline_launch(" in launch
    assert "commit=True" in launch
    assert "create_pipeline_run(" not in launch
    assert "launch_resolved(" in launch


def test_auto_schedule_handoff_freezes_pipeline_and_uses_jobs_scheduler(monkeypatch):
    captured = {}

    class FakeDB:
        def __init__(self):
            self.updates = []
            self.rolled_back = False

        def execute(self, sql, params):
            self.updates.append((sql, params))
            return None

        def rollback(self):
            self.rolled_back = True

    db = FakeDB()
    ctx = SimpleNamespace(project_root="/repo", db_path="/repo/rank42.db")

    def fake_prepare(ctx, db, **kwargs):
        captured["prepare"] = kwargs
        metadata = dict(kwargs["native_metadata"])
        metadata.update({"pipeline_run_id": 41, "campaign_id": 9})
        return {
            "kind": "pipeline_search",
            "label": "Pipeline · Auto",
            "command": ["python", "-m", "rank42.pipeline_runner", "--run-id", "41"],
            "cwd": "/repo",
            "metadata": metadata,
            "pipeline_run_id": 41,
            "campaign_failure": None,
        }

    def fake_create_schedule(db, **kwargs):
        captured["schedule"] = kwargs
        return 73

    monkeypatch.setattr(auto_search, "prepare_pipeline_launch", fake_prepare)
    monkeypatch.setattr(auto_search, "create_schedule", fake_create_schedule)
    monkeypatch.setattr(auto_search, "now", lambda: "2026-10-01T12:00:00+00:00")

    schedule_id, template_run_id = auto_search._schedule_auto_pipeline(
        ctx,
        db,
        pipeline_choice={
            "pipeline": {"name": "Auto"},
            "target_rank": 25,
            "strategy_label": "Record breaker",
        },
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "v1"},
        run_config={"target_rank": 25, "auto_entrypoint": True},
        native_metadata={"auto_entrypoint": True, "search_mode": "family"},
        schedule={
            "enabled": True,
            "recurrence": "interval",
            "catch_up_policy": "latest",
            "interval_minutes": 360,
            "next_run_at": "2026-10-02T14:00:00+00:00",
        },
    )

    assert (schedule_id, template_run_id) == (73, 41)
    assert captured["prepare"]["commit"] is False
    assert captured["prepare"]["run_config"]["scheduled_entrypoint"] == "auto"
    assert captured["prepare"]["run_config"]["schedule_recurrence"] == "interval"
    assert captured["prepare"]["run_config"]["schedule_interval_minutes"] == 360
    assert captured["prepare"]["native_metadata"]["schedule_initial_next_run_at"] == (
        "2026-10-02T14:00:00+00:00"
    )
    assert any("status='scheduled'" in sql for sql, _params in db.updates)

    scheduled = captured["schedule"]
    assert scheduled["label"] == "Auto · Record breaker"
    assert scheduled["kind"] == "pipeline_search"
    assert scheduled["recurrence"] == "interval"
    assert scheduled["interval_minutes"] == 360
    assert scheduled["catch_up_policy"] == "latest"
    assert scheduled["campaign_id"] == 9
    assert scheduled["enabled"] is True
    assert scheduled["metadata"]["pipeline_run_id"] == 41
    assert scheduled["metadata"]["scheduled_entrypoint"] == "auto"
    assert db.rolled_back is False


def test_auto_schedule_ui_is_draft_only_and_main_action_chooses_owner_path():
    toggle_callback = inspect.getsource(auto_search._toggle_auto_schedule)
    toggle = inspect.getsource(auto_search._auto_schedule_enabled)
    settings = inspect.getsource(auto_search._auto_schedule_settings)
    render = inspect.getsource(auto_search._render_auto)

    assert '"auto-schedule-active"' in toggle_callback
    assert "not bool(st.session_state.get(state_key, False))" in toggle_callback

    assert 'with st.popover(' not in toggle
    assert 'st.button(' in toggle
    assert '"Schedule"' in toggle
    assert '"Schedule Auto"' not in toggle
    assert 'icon=":material/schedule:"' in toggle
    assert 'key="auto-schedule-toggle"' in toggle
    assert 'type="primary" if enabled else "secondary"' in toggle
    assert "on_click=_toggle_auto_schedule" in toggle

    assert 'with st.popover(' not in settings
    assert 'schedule_time_inputs("auto-schedule")' in settings
    assert "CATCH_UP_LABELS" in settings
    assert "create_schedule(" not in settings

    assert "scheduled = _auto_schedule_enabled()" in render
    assert 'key="auto-schedule-settings"' in render
    assert "if scheduled:" in render
    assert "schedule = _auto_schedule_settings()" in render
    assert '"Schedule Auto Search" if scheduled else "Start Auto Search"' in render
    assert "(bool(blocking_jobs) and not scheduled)" in render
    assert "_schedule_auto_pipeline(" in render
    assert "launch_with_pipeline(" in render
    assert 'st.session_state["manage_schedule_id"] = int(schedule_id)' in render


def test_auto_page_has_no_geometry_mode_and_new_launch_is_pipeline_only():
    page = inspect.getsource(auto_search.page)
    render = inspect.getsource(auto_search._render_auto)

    assert '"Pipeline-native automatic family and torsion hunting.' in page
    assert "tabs(" not in page
    assert '"History"' not in page
    assert '"Geometry"' not in page
    assert "geometry_search_page" not in page
    assert "_render_history(db, ctx)" not in page

    assert "active_campaign(db)" in render
    assert "_campaign_family_target(campaign, plugins)" in render
    assert "_sync_auto_campaign_context(" in render
    assert 'int(campaign["target_rank"])' in render
    assert "_auto_pipeline_selector(" in render
    assert "_auto_apply_stop_policy(" in render
    assert "launch_with_pipeline(" in render
    assert "native_command=[]" in render
    assert "create_campaign(" not in render
    assert '"auto_entrypoint": True' in render
    assert "pipeline_search_config(" in render
    assert "auto_search_policy(" not in render
    assert "default_target_rank(" not in render



def test_auto_s1_is_one_button_first_with_advanced_pipeline_override():
    render = inspect.getsource(auto_search._render_auto)

    assert "_, main_col, _ = st.columns([0.6, 2.8, 0.6])" in render
    assert '"Auto Search"' in render
    assert '"Start a hunt"' not in render
    assert '"Choose a family or torsion target and a rigorous rank goal."' in render
    assert "Auto selects a Pipeline strategy" not in render
    assert '["Family", "Torsion"]' in render

    assert "family_col, variant_col = st.columns(" in render
    family_pair = render[render.index("family_col, variant_col = st.columns("):]
    assert "[1.0, 1.0]" in family_pair[:220]
    assert "with family_col:" in render
    assert "with variant_col:" in render
    assert '"Family"' in render
    assert '"Variant"' in render

    assert "goal_col, floor_col = st.columns(" in render
    assert "with goal_col:" in render
    assert "with floor_col:" in render
    assert '"Goal rank"' in render
    assert '"Retention floor"' in render
    assert '"Stop when goal is reached"' in render
    assert "[4.8, 1.2]" in render
    assert '"Start Auto Search"' in render
    assert '"Schedule Auto Search"' in render

    advanced_marker = 'with st.expander("Advanced options", expanded=False):'
    assert advanced_marker in render
    advanced_start = render.index(advanced_marker)
    advanced_end = render.index('pipeline_choice = _auto_apply_stop_policy(', advanced_start)
    advanced = render[advanced_start:advanced_end]
    assert '"Retention floor"' not in advanced
    assert '"Exact certificate timeout"' in advanced
    assert '"Exact point candidates per certification pass"' in advanced
    assert "_auto_pipeline_selector(" in advanced
    assert '"After direct torsion families, try unrelated family specializations"' in advanced

    assert '"Use unrelated-family fallback"' in render[:advanced_start]
    assert "launch_with_pipeline(" in render
    assert "native_command=[]" in render


def test_auto_main_has_no_local_running_panel_and_diagnostics_owns_archive():
    module = inspect.getsource(auto_search)
    render = inspect.getsource(auto_search._render_auto)
    archive = inspect.getsource(diagnostics_page._render_legacy_auto_archive)

    assert "_render_active_controls" not in module
    assert '"Running Auto"' not in module
    assert '"Open in Pipelines"' not in module
    assert "pause_job(" not in module
    assert "stop_job(" not in module
    assert "_blocking_auto_jobs(db)" in render

    assert '"Legacy Auto campaigns"' not in render
    assert '"Read-only pre-Pipeline Auto history.' in archive
    assert "campaign_trials(db, selected_id" in archive
    assert "_launch_campaign" not in archive


def test_geometry_legacy_route_redirects_to_auto_without_geometry_tab_state():
    state = {
        "rh_page": "Geometry",
        "auto-main-tab": "Geometry",
    }

    canonical = shell_ui._normalize_route("Geometry", state)

    assert canonical == "Auto"
    assert state["rh_page"] == "Auto"
    assert "auto-main-tab" not in state
