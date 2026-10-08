import copy
import inspect
import sqlite3
import types

from rank42.ui_pages import build_your_own
from rank42.ui_store import get_application_state


def _fake_streamlit(monkeypatch, state):
    fake = types.SimpleNamespace(session_state=state)
    monkeypatch.setattr(build_your_own, "st", fake)
    return fake


def _draft_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE ui_application_state(
               state_key TEXT PRIMARY KEY,
               value_json TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    return db


def test_geometry_budget_renderer_surfaces_worst_case_before_launch(monkeypatch):
    captions = []
    monkeypatch.setattr(
        build_your_own,
        "st",
        types.SimpleNamespace(caption=captions.append),
    )

    build_your_own._render_geometry_search_budget(
        "mw_growth_loop",
        {
            "heights": [10000, 100000, 1000000],
            "anchors": 32,
            "max_rounds": 8,
            "deep_keep": 12,
            "timeout": 8,
            "reduce_timeout": 20,
        },
    )
    assert captions
    assert "56 model/height branches" in captions[-1]
    assert "768 branches" in captions[-1]
    assert "1h 45m" in captions[-1]
    assert "not an ETA" in captions[-1]


def test_pipeline_draft_persists_and_recovers_after_session_restart(monkeypatch):
    db = _draft_db()
    original_state = {
        "byo_builder_state_version": 2,
        "byo_stage_mode": "general",
        "byo-target-mode": "General Curves",
        "byo_stages": [
            {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 5000}},
        ],
        "byo_pipeline_name_input": "Unsaved record hunt",
        "byo-general-mode": "open",
        "byo-general-pool": 7000,
        "byo-goal-general": 14,
        "byo-retention-general": 8,
        "byo-cert-timeout": 180,
        "byo-exact-candidates": 96,
        "byo-functional-features": ["feature.demo"],
        "rh-byo-ratpoints": "GPU",
        "byo-module-favorites-general": ["nagao_screen"],
        "byo-module-recent-general": ["nagao_screen"],
    }
    _fake_streamlit(monkeypatch, original_state)

    lineage = {
        "pipeline_id": None,
        "dirty": False,
        "source_pipeline_id": None,
    }
    assert build_your_own._persist_pipeline_draft(
        db,
        mode="general",
        target={"pool_mode": "open", "pool_size": 7000, "random_seed": 42},
        stages=original_state["byo_stages"],
        definition_config={
            "feature_plugin_ids": ["feature.demo"],
            "target_rank": 14,
            "retention_floor": 8,
            "certificate_timeout": 180,
            "exact_candidates": 96,
            "ratpoints_backend": "GPU",
            "target": {"pool_mode": "open", "pool_size": 7000, "random_seed": 42},
        },
        lineage=lineage,
    ) is True

    stored = get_application_state(db, build_your_own.PIPELINE_DRAFT_SETTING_KEY)
    assert stored["version"] == build_your_own.PIPELINE_DRAFT_VERSION
    assert stored["session"]["byo_pipeline_name_input"] == "Unsaved record hunt"
    assert stored["session"]["byo-goal-general"] == 14

    restarted_state = {}
    _fake_streamlit(monkeypatch, restarted_state)
    assert build_your_own._restore_pipeline_draft(db) is True
    assert restarted_state["byo_stage_mode"] == "general"
    assert restarted_state["byo-target-mode"] == "General Curves"
    assert restarted_state["byo_pipeline_name_input"] == "Unsaved record hunt"
    assert restarted_state["byo_stages"][0]["id"] == "nagao_screen"
    assert restarted_state["byo-goal-general"] == 14
    assert restarted_state["byo-functional-features"] == ["feature.demo"]
    assert restarted_state["byo_draft_recovered_notice"]["saved_at"]
    assert restarted_state["byo_draft_recovery_checked"] is True


def test_pipeline_draft_recovery_preserves_saved_and_duplicate_lineage(monkeypatch):
    db = _draft_db()
    state = {
        "byo_loaded_pipeline_id": None,
        "byo_duplicate_source_pipeline_id": 41,
        "byo_stage_mode": "family",
        "byo-target-mode": "Family",
        "byo-family": "family.demo:v2",
        "byo_stages": [{"id": "nagao_screen", "config": {}}],
        "byo_pipeline_name_input": "Saved strategy — Copy",
        "byo-goal-family": 18,
    }
    _fake_streamlit(monkeypatch, state)

    assert build_your_own._persist_pipeline_draft(
        db,
        mode="family",
        target={"plugin_id": "family.demo", "variant_id": "v2"},
        stages=state["byo_stages"],
        definition_config={
            "target_rank": 18,
            "target": {"plugin_id": "family.demo", "variant_id": "v2"},
        },
        lineage={
            "pipeline_id": None,
            "dirty": True,
            "source_pipeline_id": 41,
        },
    ) is True

    restarted = {}
    _fake_streamlit(monkeypatch, restarted)
    assert build_your_own._restore_pipeline_draft(db) is True
    assert restarted["byo_loaded_pipeline_id"] is None
    assert restarted["byo_duplicate_source_pipeline_id"] == 41
    assert restarted["byo-family"] == "family.demo:v2"
    assert restarted["byo_pipeline_name_input"] == "Saved strategy — Copy"
    assert restarted["byo_draft_recovered_notice"]["source_pipeline_id"] == 41


def test_pipeline_draft_discard_clears_durable_recovery(monkeypatch):
    db = _draft_db()
    state = {
        "byo_stage_mode": "general",
        "byo_stages": [{"id": "nagao_screen", "config": {}}],
    }
    _fake_streamlit(monkeypatch, state)
    assert build_your_own._persist_pipeline_draft(
        db,
        mode="general",
        target={"pool_mode": "open"},
        stages=state["byo_stages"],
        definition_config={"target": {"pool_mode": "open"}},
        lineage={
            "pipeline_id": None,
            "dirty": False,
            "source_pipeline_id": None,
        },
    ) is True

    build_your_own._discard_pipeline_draft(db)
    assert get_application_state(db, build_your_own.PIPELINE_DRAFT_SETTING_KEY) is None

    fresh_state = {}
    _fake_streamlit(monkeypatch, fresh_state)
    assert build_your_own._restore_pipeline_draft(db) is False
    assert "byo_stages" not in fresh_state


def test_exact_saved_pipeline_is_not_auto_saved_as_draft(monkeypatch):
    db = _draft_db()
    state = {
        "byo_loaded_pipeline_id": 4,
        "byo_stage_mode": "general",
        "byo_stages": [{"id": "nagao_screen", "config": {}}],
    }
    _fake_streamlit(monkeypatch, state)

    assert build_your_own._persist_pipeline_draft(
        db,
        mode="general",
        target={"pool_mode": "open"},
        stages=state["byo_stages"],
        definition_config={"target": {"pool_mode": "open"}},
        lineage={
            "pipeline_id": 4,
            "dirty": False,
            "source_pipeline_id": 4,
        },
    ) is False
    assert get_application_state(db, build_your_own.PIPELINE_DRAFT_SETTING_KEY) is None


def test_duplicate_loaded_pipeline_preserves_editor_state(monkeypatch):
    state = {
        "byo_loaded_pipeline_id": 41,
        "byo_pipeline_name_input": "ICARM302 R32 — Monster Funnel v1",
        "byo_stage_mode": "family",
        "byo-family": "icarm_rank31_302:recovered_mw17_s",
        "byo_stages": [
            {"id": "nagao_screen", "config": {"prime_bound": 523}},
            {"id": "generator_hunt", "config": {"mw_feedback_rounds": 5}},
        ],
        "byo-goal-family": 32,
        "byo-retention-family": 18,
        "byo-cert-timeout": 120,
        "byo-exact-candidates": 64,
        "byo-functional-features": [],
        "rh-byo-ratpoints": "GPU",
    }
    before = copy.deepcopy(state)
    _fake_streamlit(monkeypatch, state)

    source_id = build_your_own._duplicate_loaded_pipeline()

    assert source_id == 41
    assert state["byo_loaded_pipeline_id"] is None
    assert state["byo_duplicate_source_pipeline_id"] == 41
    assert state["byo_pipeline_name_input"] == "ICARM302 R32 — Monster Funnel v1 — Copy"

    for key, value in before.items():
        if key in {"byo_loaded_pipeline_id", "byo_pipeline_name_input"}:
            continue
        assert state[key] == value


def test_new_pipeline_clear_removes_duplicate_identity(monkeypatch):
    state = {
        "byo_loaded_pipeline_id": None,
        "byo_duplicate_source_pipeline_id": 41,
        "byo_pipeline_name_input": "Copy",
        "byo_stage_mode": "family",
        "byo_stages": [{"id": "nagao_screen", "config": {}}],
        "byo-functional-features": ["demo"],
    }
    fake = _fake_streamlit(monkeypatch, state)
    monkeypatch.setattr(build_your_own, "_bump_stage_revision", lambda: None)

    build_your_own._clear_loaded_pipeline("family")

    assert fake.session_state["byo_loaded_pipeline_id"] is None
    assert "byo_duplicate_source_pipeline_id" not in fake.session_state
    assert "byo_pipeline_name_input" not in fake.session_state
    assert fake.session_state["byo_stages"] == []
    assert fake.session_state["byo-functional-features"] == []


def test_pipeline_builder_marks_unsaved_edits_as_derived_from_saved_revision():
    from rank42.pipeline_state import ensure_pipeline_schema, save_pipeline

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    ensure_pipeline_schema(db)

    stages = [{"id": "nagao_screen", "config": {"prime_bound": 523}}]
    config = {
        "feature_plugin_ids": [],
        "target_rank": 8,
        "retention_floor": 0,
        "certificate_timeout": 120,
        "exact_candidates": 64,
        "ratpoints_backend": "GPU",
        "target": {"pool_mode": "open"},
    }
    pid = save_pipeline(
        db,
        name="Saved",
        target_mode="general",
        stages=stages,
        config=config,
    )

    exact = build_your_own._pipeline_builder_lineage(
        db,
        pipeline_id=pid,
        pipeline_name="Saved",
        target_mode="general",
        stages=stages,
        definition_config=config,
    )
    assert exact["dirty"] is False
    assert exact["pipeline_id"] == pid
    assert exact["provenance"]["source_pipeline_revision"] == 1
    assert exact["provenance"]["pipeline_source_relation"] == "exact"

    edited = build_your_own._pipeline_builder_lineage(
        db,
        pipeline_id=pid,
        pipeline_name="Saved",
        target_mode="general",
        stages=[{"id": "nagao_screen", "config": {"prime_bound": 997}}],
        definition_config=config,
    )
    assert edited["dirty"] is True
    assert edited["pipeline_id"] is None
    assert edited["provenance"]["derived_from_pipeline_id"] == pid
    assert edited["provenance"]["derived_from_pipeline_revision"] == 1
    assert len(edited["provenance"]["derived_from_pipeline_hash"]) == 64
    assert edited["provenance"]["pipeline_source_relation"] == "modified"


def test_saved_family_pool_settings_restore_explicit_population():
    payload = {
        "target_mode": "family",
        "config": {
            "target": {
                "plugin_id": "dmt_triad_rank4",
                "variant_id": "cef",
                "candidate_pool_id": 77,
                "only_unsearched": True,
            }
        },
    }
    settings = build_your_own._saved_family_pool_settings(payload)
    assert settings == {"candidate_pool_id": 77, "only_unsearched": True}


def test_compatible_family_pools_require_exact_family_spec(monkeypatch):
    rows = [
        {
            "id": 1,
            "status": "ready",
            "family_spec": "dmt_triad_cef",
            "generation_json": "{}",
            "name": "cef-corpus",
            "candidate_count": 60000,
        },
        {
            "id": 2,
            "status": "ready",
            "family_spec": "dmt_triad_bef",
            "generation_json": "{}",
            "name": "bef-corpus",
            "candidate_count": 10,
        },
        {
            "id": 3,
            "status": "importing",
            "family_spec": "dmt_triad_cef",
            "generation_json": "{}",
            "name": "not-ready",
            "candidate_count": 5,
        },
    ]
    monkeypatch.setattr(build_your_own, "list_pools", lambda *a, **k: rows)
    plugin = types.SimpleNamespace(id="dmt_triad_rank4")
    variant = types.SimpleNamespace(family_spec="dmt_triad_cef")
    found = build_your_own._compatible_family_pools(object(), plugin, variant)
    assert [row["id"] for row in found] == [1]


def test_stage_list_rows_are_one_record_per_pipeline_module():
    stages = [
        {"id": "nagao_screen", "config": {}},
        {"id": "select_survivors", "config": {"keep": 10}},
        {"id": "select_survivors", "config": {"keep": 5}},
        {"id": "family_baseline", "config": {}},
    ]
    rows = build_your_own._stage_list_rows(stages)
    assert [row["module"] for row in rows] == [
        "Nagao Screen",
        "Select Survivors",
        "Select Survivors",
        "Family Sections",
    ]
    assert len(rows) == len(stages)
    assert len({row["token"] for row in rows}) == len(stages)
    assert all(set(row) == {"token", "module", "detail"} for row in rows)


def test_apply_stage_list_order_preserves_stage_objects_and_settings():
    stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 523}},
        {"id": "select_survivors", "config": {"keep": 256}},
        {"id": "family_baseline", "config": {"certificate_timeout": 240}},
    ]
    tokens = [str(id(rec)) for rec in stages]
    ids_before = [id(rec) for rec in stages]
    changed = build_your_own._apply_stage_list_order(
        stages,
        [tokens[2], tokens[0], tokens[1]],
    )
    assert changed is True
    assert [rec["id"] for rec in stages] == [
        "family_baseline",
        "nagao_screen",
        "select_survivors",
    ]
    assert stages[0]["config"]["certificate_timeout"] == 240
    assert stages[2]["config"]["keep"] == 256
    assert sorted(id(rec) for rec in stages) == sorted(ids_before)


def test_apply_stage_list_order_rejects_stale_payload():
    stages = [
        {"id": "nagao_screen", "config": {}},
        {"id": "family_baseline", "config": {}},
    ]
    before = list(stages)
    assert build_your_own._apply_stage_list_order(
        stages,
        [str(id(stages[0])), "missing-token"],
    ) is False
    assert stages == before


def test_stage_index_for_token_tracks_repeatable_stage_instance():
    stages = [
        {"id": "select_survivors", "config": {"keep": 96}},
        {"id": "select_survivors", "config": {"keep": 32}},
    ]
    assert build_your_own._stage_index_for_token(
        stages, str(id(stages[1]))
    ) == 1


def test_pipeline_stage_component_is_packaged_and_uses_requested_fontawesome_icons():
    asset = build_your_own.PIPELINE_STAGE_COMPONENT_DIR / "index.html"
    assert asset.is_file()
    html = asset.read_text()
    assert '"grip-lines":' in html
    assert '"gear":' in html
    assert '"trash":' in html
    assert 'icon("grip-lines")' in html
    assert 'icon("gear")' in html
    assert 'icon("trash")' in html
    assert "streamlit:setComponentValue" in html
    assert "streamlit:componentReady" in html



def test_module_search_blob_covers_capabilities_and_description():
    spec = build_your_own.stage_spec("specialization_seeds")
    blob = build_your_own._module_search_blob(spec)
    assert "known specialization seeds" in blob
    assert "rigorous" in blob
    assert "point_source" in blob


def test_module_palette_suggested_only_returns_appendable_modules():
    stages = [
        {"id": "nagao_screen", "config": dict(
            build_your_own.stage_spec("nagao_screen").defaults
        )},
    ]
    visible = build_your_own._module_palette_specs(
        "family",
        stages,
        view="Suggested",
    )
    ids = {spec.id for spec in visible}
    assert "nagao_screen" not in ids
    assert "nagao_rescore" in ids
    assert "specialization_seeds" in ids


def test_module_palette_added_view_handles_repeatable_stage_counts():
    stages = [
        {"id": "nagao_screen", "config": {}},
        {"id": "select_survivors", "config": {"keep": 96}},
        {"id": "select_survivors", "config": {"keep": 32}},
    ]
    visible = build_your_own._module_palette_specs(
        "family",
        stages,
        view="Added",
    )
    ids = {spec.id for spec in visible}
    assert ids == {"nagao_screen", "select_survivors"}


def test_module_palette_filters_search_category_and_cost():
    visible = build_your_own._module_palette_specs(
        "family",
        [],
        query="nagao",
        view="All",
        category="Candidates",
        cost="Cheap",
    )
    assert [spec.id for spec in visible] == ["nagao_screen"]



def test_stage_settings_popup_has_no_persistent_open_token():
    source = build_your_own._render_stage_list.__code__
    assert "byo_stage_config_token" not in source.co_names
    assert "byo_stage_config_token" not in source.co_consts


def test_stage_config_dialog_does_not_persist_open_state():
    source = build_your_own._render_stage_config_dialog.__code__
    assert "byo_stage_config_token" not in source.co_names
    assert "byo_stage_config_token" not in source.co_consts



def test_module_browser_defaults_to_suggested_and_has_compact_views():
    import inspect
    source = inspect.getsource(build_your_own._render_stage_palette)
    assert '["Suggested", "All", "Favorites", "Recent", "Added"]' in source
    assert 'default="Suggested"' in source
    assert 'height=560' in source
    assert 'byo-module-view-v3-' in source


def test_pipeline_module_component_is_packaged_and_uses_fontawesome_icons():
    asset = build_your_own.PIPELINE_MODULE_ROW_COMPONENT_DIR / "index.html"
    assert asset.is_file()
    html = asset.read_text()

    assert '"star":' in html
    assert '"circle-info":' in html
    assert '"plus":' in html
    assert 'button.innerHTML = icon(iconName);' in html
    assert '"favorite",\n    "star"' in html
    assert 'actionButton("details", "circle-info"' in html
    assert '"add",\n    "plus"' in html
    assert "#f5c542" in html
    assert "#1fa65d" in html
    assert "streamlit:setComponentValue" in html
    assert "streamlit:componentReady" in html


def test_pipeline_editor_uses_component_module_rows_and_inset_palette():
    import inspect
    row = inspect.getsource(build_your_own._render_module_row)
    builder = inspect.getsource(build_your_own._render_builder)

    assert "_PIPELINE_MODULE_ROW(" in row
    assert 'action == "favorite"' in row
    assert 'action == "details"' in row
    assert 'action == "add"' in row
    assert "_render_module_details_dialog" in row
    assert 'key="byo-modules-inset"' in builder


def test_pipeline_editor_action_component_is_packaged_and_uses_fontawesome_icons():
    asset = build_your_own.PIPELINE_EDITOR_ACTIONS_COMPONENT_DIR / "index.html"
    assert asset.is_file()
    html = asset.read_text()

    assert '"copy":' in html
    assert '"file-circle-plus":' in html
    assert 'b.innerHTML = icon(iconName);' in html
    assert 'button("duplicate", "copy"' in html
    assert 'button("new", "file-circle-plus"' in html
    assert "streamlit:setComponentValue" in html
    assert "streamlit:componentReady" in html


def test_pipeline_draft_clear_paths_are_explicit():
    import inspect

    settings = inspect.getsource(build_your_own._render_settings)
    actions = inspect.getsource(build_your_own._render_pipeline_editor_actions)
    library = inspect.getsource(build_your_own._render_pipeline_library_card)
    saved = inspect.getsource(build_your_own._render_pipeline_library)

    assert "_persist_pipeline_draft(" in settings
    assert "_discard_pipeline_draft(db)" in settings
    assert '"byo_discard_pipeline_draft_pending"' in actions
    assert "_discard_pipeline_draft(db)" in library
    assert "_discard_pipeline_draft(db)" in saved


def test_pipeline_editor_actions_replace_duplicate_and_new_text_buttons():
    import inspect
    source = inspect.getsource(build_your_own._render_pipeline_column)
    actions = inspect.getsource(build_your_own._render_pipeline_editor_actions)

    assert "_render_pipeline_editor_actions(" in source
    assert 'duplicate_col.button(' not in source
    assert 'new_col.button(' not in source
    assert "_PIPELINE_EDITOR_ACTIONS(" in actions
    assert 'action == "duplicate"' in actions
    assert 'action == "new"' in actions


def _saved_pipeline_row(
    pipeline_id,
    name,
    *,
    target_mode="family",
    stages=None,
    target_rank=None,
    created_at="2026-01-01T00:00:00+00:00",
    updated_at="2026-01-01T00:00:00+00:00",
):
    import json

    return {
        "id": int(pipeline_id),
        "name": str(name),
        "target_mode": str(target_mode),
        "stages_json": json.dumps(list(stages or [])),
        "config_json": json.dumps(
            {
                "target_rank": target_rank,
                "feature_plugin_ids": [],
            }
        ),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def test_saved_pipeline_library_component_is_packaged_with_fontawesome_actions():
    asset = build_your_own.PIPELINE_LIBRARY_CARD_COMPONENT_DIR / "index.html"
    assert asset.is_file()
    html = asset.read_text()

    assert '"copy":' in html
    assert '"trash":' in html
    assert 'emit("open")' in html
    assert 'action("duplicate","copy"' in html
    assert 'action("confirm-delete","trash"' in html
    assert 'prompt.textContent="Delete pipeline?"' in html
    assert 'ok.textContent="OK"' in html
    assert 'cancel.textContent="Cancel"' in html
    assert 'badge.textContent="EDITING"' in html
    assert 'background:var(--rh-surface);' in html
    assert 'background:#fff;' not in html
    assert 'function applyTheme(theme,palette)' in html
    assert 'palette["rh-card"]' in html
    assert 'palette["rh-surface-hover"]' in html
    assert 'applyTheme(event.data.theme,args.palette||{})' in html
    assert "streamlit:setComponentValue" in html
    assert "streamlit:componentReady" in html


def test_saved_pipeline_library_panel_is_extracted_without_changing_contract():
    from rank42.ui_pages import pipeline_saved_library

    assert (
        build_your_own.PIPELINE_LIBRARY_CARD_COMPONENT_DIR
        == pipeline_saved_library.PIPELINE_LIBRARY_CARD_COMPONENT_DIR
    )
    assert (
        build_your_own._pipeline_library_item
        is pipeline_saved_library._pipeline_library_item
    )
    assert (
        build_your_own._filtered_pipeline_library
        is pipeline_saved_library._filtered_pipeline_library
    )
    assert (
        build_your_own._pipeline_library_page_slice
        is pipeline_saved_library._pipeline_library_page_slice
    )
    assert (
        build_your_own._render_pipeline_library_card
        is pipeline_saved_library._render_pipeline_library_card
    )
    assert (
        build_your_own._render_pipeline_library
        is pipeline_saved_library._render_pipeline_library
    )


def test_saved_pipeline_library_target_curve_filter_is_first_class():
    item = build_your_own._pipeline_library_item(
        _saved_pipeline_row(
            44,
            "Curve replay",
            target_mode="curve",
            target_rank=18,
            stages=[{"id": "integral_seed", "config": {}}],
        )
    )
    visible = build_your_own._filtered_pipeline_library(
        [item],
        target_type="Target Curve",
    )
    assert [row["payload"]["id"] for row in visible] == [44]


def test_saved_pipeline_library_filters_sorts_and_pins_loaded_pipeline():
    items = [
        build_your_own._pipeline_library_item(
            _saved_pipeline_row(
                1,
                "Beta Family",
                target_mode="family",
                target_rank=11,
                stages=[{"id": "nagao_screen", "config": {}}],
                updated_at="2026-09-20T10:00:00+00:00",
            )
        ),
        build_your_own._pipeline_library_item(
            _saved_pipeline_row(
                2,
                "Alpha Torsion",
                target_mode="torsion",
                target_rank=8,
                stages=[
                    {"id": "nagao_screen", "config": {}},
                    {"id": "integral_seed", "config": {}},
                ],
                updated_at="2026-09-21T10:00:00+00:00",
            )
        ),
        build_your_own._pipeline_library_item(
            _saved_pipeline_row(
                3,
                "Gamma General",
                target_mode="general",
                target_rank=20,
                stages=[{"id": "integral_seed", "config": {}}],
                updated_at="2026-09-19T10:00:00+00:00",
            )
        ),
    ]

    family = build_your_own._filtered_pipeline_library(
        items,
        target_type="Family",
        sort="Recently updated",
    )
    assert [item["payload"]["id"] for item in family] == [1]

    search = build_your_own._filtered_pipeline_library(
        items,
        query="integral seed",
        sort="Recently updated",
    )
    assert [item["payload"]["id"] for item in search] == [2, 3]

    alphabetical = build_your_own._filtered_pipeline_library(
        items,
        sort="Name A–Z",
        loaded_id=3,
    )
    assert [item["payload"]["id"] for item in alphabetical] == [3, 2, 1]

    target_desc = build_your_own._filtered_pipeline_library(
        items,
        sort="Target rank ↓",
    )
    assert [item["payload"]["id"] for item in target_desc] == [3, 1, 2]


def test_saved_pipeline_library_page_slice_clamps_and_limits():
    items = [
        {"payload": {"id": index}}
        for index in range(1, 46)
    ]
    page_items, page, total_pages, start, end = (
        build_your_own._pipeline_library_page_slice(
            items,
            page=2,
            page_size=20,
        )
    )
    assert [item["payload"]["id"] for item in page_items] == list(range(21, 41))
    assert (page, total_pages, start, end) == (2, 3, 20, 40)

    page_items, page, total_pages, start, end = (
        build_your_own._pipeline_library_page_slice(
            items,
            page=99,
            page_size=20,
        )
    )
    assert [item["payload"]["id"] for item in page_items] == list(range(41, 46))
    assert (page, total_pages, start, end) == (3, 3, 40, 45)


def test_saved_pipeline_library_has_manager_controls_and_card_actions():
    import inspect

    source = inspect.getsource(build_your_own._render_pipeline_library)
    card = inspect.getsource(build_your_own._render_pipeline_library_card)

    assert '"Saved pipelines"' in source
    assert '"New Pipeline"' in source
    assert '"Search pipelines"' in source
    assert '["All", "Family", "Torsion Group", "General Curves", "Target Curve"]' in source
    assert '"Recently updated"' in source
    assert '"Stage count ↓"' in source
    assert '"Target rank ↓"' in source
    assert "[10, 20, 50]" in source
    assert "_filtered_pipeline_library(" in source
    assert "_pipeline_library_page_slice(" in source
    assert '"Previous"' in source
    assert '"Next"' in source
    assert "list_pipelines(db, limit=5000)" in source

    assert "_PIPELINE_LIBRARY_CARD(" in card
    assert "palette=_component_theme_palette()" in card
    assert 'action == "open"' in card
    assert 'action == "duplicate"' in card
    assert "_duplicate_loaded_pipeline()" in card
    assert 'action == "delete"' in card
    assert "delete_pipeline(db, pipeline_id)" in card


def test_curve_mode_pipeline_loads_through_same_editor(monkeypatch):
    from rank42.pipeline_state import (
        ensure_pipeline_schema,
        get_pipeline,
        save_pipeline,
    )

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    ensure_pipeline_schema(db)

    pid = save_pipeline(
        db,
        name="Target curve recipe",
        target_mode="curve",
        stages=[{"id": "integral_seed", "config": {}}],
        config={
            "target_rank": 9,
            "launch_defaults": {
                "target": {"curve_id": 17},
            },
        },
    )
    state = {}
    _fake_streamlit(monkeypatch, state)

    build_your_own._load_pipeline_for_edit(get_pipeline(db, pid))

    assert state["byo-target-mode"] == "Target Curve"
    assert state["byo_stage_mode"] == "curve"
    assert state["byo-curve"] == 17
    assert state["byo-definition-config-shape"] == "launch_defaults"
    assert state["byo_stages"][0]["id"] == "integral_seed"
    assert state["byo_loaded_pipeline_id"] == pid


def test_pipeline_editor_separates_recipe_launch_defaults_and_queue_concurrency():
    page = inspect.getsource(build_your_own.page)
    settings = inspect.getsource(build_your_own._render_settings)

    assert '["Family", "Torsion Group", "General Curves", "Target Curve"]' in page
    assert '"Target Curve": "curve"' in page
    assert '"Default target curve"' in page
    assert '"None — choose a curve at launch"' in page
    assert '"launch_defaults"' in settings
    assert '"target": dict(target)' in settings

    # Active Pipeline jobs are displayed, but they no longer disable launch.
    assert "blocking = bool(_active_pipeline_jobs(db))" not in page
    assert "or blocking" not in settings
    assert "launch_blocked" in settings


def test_saved_pipeline_target_accepts_legacy_and_new_launch_default_shapes():
    assert build_your_own._saved_pipeline_target({
        "launch_defaults": {"target": {"curve_id": 9}},
        "config": {},
    }) == {"curve_id": 9}
    assert build_your_own._saved_pipeline_target({
        "config": {"target": {"pool_mode": "open"}},
    }) == {"pool_mode": "open"}


def test_saved_pipeline_edit_notice_is_compact():
    import inspect
    source = inspect.getsource(build_your_own._render_pipeline_column)

    assert 'Editing saved pipeline #' in source
    assert 'Save Pipeline updates this saved definition in place.' not in source


def test_pipeline_settings_are_full_width_below_builder_columns():
    import inspect
    source = inspect.getsource(build_your_own._render_builder)
    pipeline_pos = source.index("with pipeline_col:")
    modules_pos = source.index("with modules_col:")
    settings_pos = source.index('key="byo-pipeline-settings"')

    assert pipeline_pos < modules_pos < settings_pos
    assert "_render_settings(" in source


def test_pipeline_settings_use_compact_two_row_form():
    import inspect
    source = inspect.getsource(build_your_own._render_settings)

    assert "[3.4, 1.0, 1.0]" in source
    assert "[1.0, 1.0, 1.45, 2.95]" in source
    assert 'vertical_alignment="bottom"' in source


def test_pipeline_modules_use_semantic_card_surface():
    import inspect
    style = inspect.getsource(build_your_own._module_action_style)

    assert 'div[class*="st-key-byo-modules-inset"]' in style
    assert "background: var(--rh-card,#FFFFFF);" in style
    assert "background: var(--rh-card-2" not in style


def test_pipeline_module_rows_keep_compact_height_inside_expanders():
    import inspect

    style = inspect.getsource(build_your_own._module_action_style)

    assert 'div[class*="st-key-byo-module-scroll-"] details [data-testid="stCustomComponentV1"]' in style
    assert '[data-testid="stCustomComponentV1"] iframe' in style
    assert "height: 54px !important;" in style
    assert "min-height: 54px !important;" in style
    assert "max-height: 54px !important;" in style


def test_pipeline_module_copy_is_short_and_scroll_caption_is_removed():
    import inspect
    source = inspect.getsource(build_your_own._render_stage_palette)

    assert '"Search and add stages."' in source
    assert "without turning the whole page into the module list" not in source
    assert "Module results scroll independently from the pipeline editor." not in source


def test_pipeline_search_target_is_spaced_and_compact():
    import inspect
    page = inspect.getsource(build_your_own.page)
    style = inspect.getsource(build_your_own._pipeline_editor_style)

    assert "_pipeline_editor_style()" in page
    assert 'key="byo-search-space"' in page
    assert 'st.subheader("Search target")' in page
    assert "[1.15, 2.85]" in page
    assert "[2.45, 1.3, 2.65, 0.95]" in page
    assert "margin-top: 0.8rem" in style
    assert "margin-bottom: 1.15rem" in style
    assert "background: var(--rh-card,#FFFFFF);" in style


def test_pipeline_page_has_separate_runs_view_and_no_duplicate_campaign_banner():
    import inspect
    source = inspect.getsource(build_your_own.page)
    assert '["Editor", "Saved", "Runs"]' in source
    assert '_render_pipeline_runs_page(db, ctx, campaign)' in source
    assert 'key="byo-search-space"' in source
    assert '"Search target"' in source
    assert 'st.success(" · ".join(context))' not in source


def test_pipeline_runs_page_is_scientific_inspection_not_process_manager():
    import inspect

    source = inspect.getsource(build_your_own._render_pipeline_runs_page)
    running = inspect.getsource(build_your_own._render_running)

    assert "_render_running(db)" in source
    assert "list_pipeline_runs" in source
    assert "campaign_pipeline_runs" in source
    assert '"No active campaign · showing all Pipeline runs."' in source
    no_campaign = source.index('"No active campaign · showing all Pipeline runs."')
    global_runs = source.index("runs = list_pipeline_runs(db, limit=25)", no_campaign)
    assert no_campaign < global_runs
    assert "pipeline_candidates" in source
    assert "pipeline_derivations" in source
    assert "pipeline_run_runtime_drift(" in source
    assert '"Replay/runtime drift: "' in source
    assert '"Open in Jobs"' in source
    assert "_open_pipeline_run_in_jobs(" in source
    assert '"This Pipeline Run is resumable.' in source

    assert "resume_pipeline_run(" not in source
    assert "pause_pipeline_run(" not in source
    assert "stop_pipeline_run(" not in source
    assert "stop_job(" not in source
    assert "update_pipeline_run(" not in source

    assert '"Open process in Jobs"' in running
    assert "_open_pipeline_run_in_jobs(" in running
    assert "pause_pipeline_run(" not in running
    assert "stop_pipeline_run(" not in running
    assert "stop_job(" not in running


def test_module_palette_groups_preserve_category_order():
    specs = [
        build_your_own.stage_spec("nagao_screen"),
        build_your_own.stage_spec("integral_seed"),
        build_your_own.stage_spec("nagao_rescore"),
    ]
    groups = build_your_own._module_palette_groups(specs)
    assert [name for name, _items in groups] == ["Candidates", "Points"]
    assert [spec.id for spec in groups[0][1]] == ["nagao_screen", "nagao_rescore"]
    assert [spec.id for spec in groups[1][1]] == ["integral_seed"]


def test_module_palette_favorites_and_recent_views():
    favorites = build_your_own._module_palette_specs(
        "family",
        [],
        view="Favorites",
        favorites={"integral_seed", "nagao_screen"},
    )
    assert {spec.id for spec in favorites} == {"integral_seed", "nagao_screen"}

    recent = build_your_own._module_palette_specs(
        "family",
        [],
        view="Recent",
        recent=["integral_seed", "nagao_screen"],
    )
    assert [spec.id for spec in recent] == ["integral_seed", "nagao_screen"]



def test_deep_hunt_timeout_envelope_matches_no_growth_wave_policy():
    envelope = build_your_own._deep_hunt_timeout_envelope({
        "mw_growth_anchors": 24,
        "mw_growth_deep_keep": 8,
        "mw_growth_heights": [1000000, 10000000],
        "mw_growth_timeout": 8,
        "mw_growth_reduce_timeout": 12,
        "mw_growth_rounds": 2,
        "denominator_bands": [[10001, 100000], [100001, 1000000]],
        "denominator_charts": 6,
        "denominator_heights": [1000000, 10000000],
        "denominator_timeout": 8,
    })
    assert envelope["mw_searches_no_growth"] == 32
    assert envelope["mw_seconds_no_growth"] == 268
    assert envelope["mw_rounds_max"] == 2
    assert envelope["denominator_configured_slots"] == 24
    assert envelope["denominator_configured_seconds"] == 192
    assert envelope["denominator_searches"] == 24
    assert envelope["denominator_seconds"] == 192
    assert envelope["checkpoint_scope"] == "chart_height"


def test_format_budget_seconds_is_compact():
    assert build_your_own._format_budget_seconds(8) == "8s"
    assert build_your_own._format_budget_seconds(268) == "4m 28s"
    assert build_your_own._format_budget_seconds(3720) == "1h 02m"



def test_ui_requirements_include_streamlit_shutdown_race_fix():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text()
    assert "streamlit>=1.63,<2" in requirements



def test_pipeline_editor_draft_uses_application_state_not_settings():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "rank42" / "ui_pages" / "build_your_own.py"
    ).read_text(encoding="utf-8")

    assert "PIPELINE_DRAFT_SETTING_KEY = \"pipeline_editor_draft_v1\"" in source
    assert "get_application_state(db, PIPELINE_DRAFT_SETTING_KEY" in source
    assert "set_application_state(db, PIPELINE_DRAFT_SETTING_KEY" in source
    assert "get_setting(db, PIPELINE_DRAFT_SETTING_KEY" not in source
    assert "set_setting(db, PIPELINE_DRAFT_SETTING_KEY" not in source



def test_point_stage_editor_shows_worst_case_ratpoints_budget():
    import inspect

    helper = inspect.getsource(build_your_own._render_point_search_budget)
    editor = inspect.getsource(build_your_own._config_editor)

    assert "Configured ratpoints plan:" in helper
    assert "calls per curve" in helper or "call" in helper
    assert "ratpoints timeout budget" in helper
    assert "Excludes model preparation, certification" in helper
    assert "_render_point_search_budget(stage_id, cfg)" in editor
    for stage_id in (
        "small_point_density",
        "integral_seed",
        "denominator_band",
        "affine_search",
        "adaptive_ladder",
    ):
        assert f'"{stage_id}"' in editor



def test_point_editor_explains_experimental_yield_and_chart_denominator_semantics():
    import inspect

    editor = inspect.getsource(build_your_own._config_editor)
    assert "experimental search-yield heuristic, not a mathematical density statistic" in editor
    assert "chart-search regions, not invariant or disjoint native Mordell–Weil denominator shells" in editor
    assert "completed no-hit region says only that no finite point was found" in editor
    assert "experimental scheduling heuristic" in editor
    assert "progressively deeper chart-denominator searches" in editor
    assert "not invariant native Mordell–Weil shells or a theorem" in editor



def test_simon_editor_exposes_deterministic_limbigprime_boundary():
    import inspect

    editor = inspect.getsource(build_your_own._config_editor)
    assert '"LIMBIGPRIME"' in editor
    assert "0 requests deterministic local tests" in editor
    assert "Rank Hunter will " in editor
    assert "not promote that reported upper as rigorous evidence." in editor
    assert "Legacy/alternative Sage Simon path" in editor



def test_mwrank_covering_editor_exposes_explicit_retry_policy():
    import inspect

    editor = inspect.getsource(build_your_own._config_editor)
    assert 'stage_id == "mwrank_covering"' in editor
    assert '"Retry policy"' in editor
    assert '"Retry timeout"' in editor
    assert "automatic repeats the same budget" in editor
    assert "escalated retries once" in editor
    assert "will not repeat automatically under the default manual policy" in editor


def test_transform_editor_exposes_hard_timeout_controls():
    source = inspect.getsource(build_your_own._config_editor)
    assert "Plugin hard timeout (s)" in source
    assert "Per-degree hard timeout (s)" in source
    assert 'cfg["timeout"]' in source


def test_targeted_twist_editor_exposes_score_and_budget_controls():
    source = inspect.getsource(build_your_own._config_editor)
    assert "RH Nagao-style prime bound" in source
    assert "Stage hard timeout (s)" in source
    assert "Per-twist hard timeout (s)" in source
    assert "Allow partial-prime-coverage scores" in source
    assert "cached-nagao-log-cardinality-v1" in source


def test_section_search_shell_editor_exposes_height_semantics_and_exactness_boundary():
    source = inspect.getsource(build_your_own._config_editor)
    assert "Height / shell kind" in source
    assert "Height normalization" in source
    assert "family_enumeration_shell" in source
    assert "canonical_shioda_height" in source
    assert "not interchangeable" in source
    assert "unverified by default" in source
    assert "registered verifier" in source


def test_trace_division_editor_exposes_verifier_owned_exactness():
    source = inspect.getsource(build_your_own._config_editor)
    assert "exact source-section hash" in source
    assert "registered verifier can mark the trace construction exact" in source
    assert "Division n in nP = Q" in source
    assert "forced_tangent" in source
    assert "division_polynomial" in source
    assert "family_exact" in source
    assert "producer-declared exactness is ignored" in source
    assert "registered verifier must certify the nP = Q relation" in source


def test_constructive_editor_exposes_hard_hook_timeout_controls():
    source = inspect.getsource(build_your_own._config_editor)
    assert source.count("Hook hard timeout (s)") >= 3
    assert "core-owned" in source
    assert "process-isolated" in source
    assert "configured hard timeout" in source


def test_square_condition_editor_explains_condition_binding_boundary():
    source = inspect.getsource(build_your_own._config_editor)
    assert "exact parent-condition artifact" in source
    assert "condition(t)=square_value" in source
    assert "square_root²=square_value" in source
    assert "partial/timeout/inconclusive/error status is preserved" in source
    assert "requested/completed test and domain coverage" in source
    assert "missing coverage cannot be called completed" in source
    assert "new specializations start with no inherited heuristic score" in source



def test_upper_bound_rescue_editor_exposes_isogeny_discovery_budget():
    source = inspect.getsource(build_your_own._config_editor)
    assert "Seconds / isogeny degree" in source
    assert "Isogeny degrees must be prime" in source
    assert "core-owned subprocess" in source
    assert "separate per-degree hard timeout" in source



def test_deep_strategy_editor_exposes_wall_budget_and_resume():
    source = inspect.getsource(build_your_own._config_editor)
    assert "Strategy wall seconds" in source
    assert "Strategy retry policy" in source
    assert "first unfinished checkpoint" in source
    assert "total wall budget" in source



def test_record_breaker_default_budget_surfaces_multi_hour_portfolio():
    cfg = build_your_own.stage_spec("record_breaker_lane").defaults
    envelope = build_your_own._deep_hunt_timeout_envelope(cfg)
    assert envelope["denominator_configured_slots"] == 768
    assert envelope["denominator_configured_seconds"] == 23040
    assert envelope["denominator_searches"] == 384
    assert envelope["denominator_seconds"] == 11520
    assert envelope["wall_timeout_seconds"] == 7200
    assert envelope["checkpoint_scope"] == "chart_height"

    source = inspect.getsource(build_your_own._config_editor)
    assert "configured denominator portfolio" in source
    assert "runnable after denominator/height pruning" in source
    assert "Each denominator chart/height attempt is durably checkpointed" in source
    assert "Total strategy wall cap" in source
