from pathlib import Path

from rank42.ui_pages.target_curve import _target_preset_modified, _torsion_target_pipeline_choice
import rank42.pipeline_runner as pipeline_runner
from rank42.ui_pages.family_search import _group_option_defs


def _defs():
    return [
        {"key": "adapter", "type": "choice"},
        {"key": "seed_curve_id", "type": "int"},
        {"key": "mode", "type": "choice"},
        {"key": "charts", "type": "int"},
        {"key": "chart_strategy", "type": "choice"},
        {"key": "anchor_pool", "type": "int"},
        {"key": "discovered_anchor_pool", "type": "int"},
        {"key": "free_bound", "type": "int"},
        {"key": "stages", "type": "str"},
        {"key": "timeout", "type": "int"},
        {"key": "construction_timeout", "type": "int"},
        {"key": "subgroup_scan", "type": "int"},
        {"key": "exact_candidates", "type": "int"},
        {"key": "certificate_timeout", "type": "int"},
    ]


def _deep_target():
    return {
        "id": "deep",
        "target": {
            "adapter": "pgl2",
            "seed_curve_id": 0,
            "mode": "both",
            "charts": 64,
            "chart_strategy": "hybrid",
            "anchor_pool": 24,
            "discovered_anchor_pool": 32,
            "free_bound": 5,
            "stages": "10000,100000,1000000",
            "timeout": 60,
            "construction_timeout": 180,
            "subgroup_scan": 2048,
            "exact_candidates": 24,
            "certificate_timeout": 180,
            "ratpoints_backend": "GPU",
        },
    }


def test_target_preset_modified_tracks_visible_controls_and_point_engine():
    preset = _deep_target()
    opts = dict(preset["target"])
    opts.pop("ratpoints_backend")

    assert not _target_preset_modified(preset, opts, _defs(), "GPU")

    changed = dict(opts)
    changed["charts"] = 24
    assert _target_preset_modified(preset, changed, _defs(), "GPU")
    assert _target_preset_modified(preset, opts, _defs(), "CPU")


def test_target_page_renders_family_owned_presets_on_the_side():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "target_curve.py"
    ).read_text(encoding="utf-8")

    assert "search_presets_for_variant" in source
    assert "Family-owned target presets fill the visible controls" in source
    assert "target-preset-button-" in source
    assert "ratpoints_backend" in source



def test_ratpoints_selector_omits_default_when_preset_session_state_exists():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "common.py"
    ).read_text(encoding="utf-8")

    assert "widget_key = rh_key(key)" in source
    assert "if widget_key not in st.session_state:" in source
    assert 'selector_kwargs["index"]' in source



def test_target_only_presets_are_hidden_from_candidate_and_family_surfaces():
    root = Path(__file__).resolve().parents[1]
    candidate_source = (root / "rank42" / "ui_pages" / "candidate_generate_page.py").read_text(encoding="utf-8")
    family_source = (root / "rank42" / "ui_pages" / "family_search.py").read_text(encoding="utf-8")

    assert "if p.get('candidate')" in candidate_source
    assert 'if p.get("family")' in family_source



def test_plugin_option_groups_are_global_stable_and_never_empty():
    defs = [
        {
            "key": "charts",
            "group": "Search geometry",
            "group_help": "geometry help",
            "group_columns": 2,
            "group_expanded": True,
        },
        {
            "key": "timeout",
            "group": "Search depth",
            "group_help": "depth help",
            "group_columns": 2,
            "group_expanded": False,
        },
        {
            "key": "certificate_timeout",
            "group": "Exact verification",
            "group_help": "proof help",
            "group_columns": 2,
            "group_expanded": False,
        },
    ]

    groups = _group_option_defs(defs)

    assert [group["name"] for group in groups] == [
        "Search geometry",
        "Search depth",
        "Exact verification",
    ]
    assert all(group["records"] for group in groups)
    assert groups[0]["expanded"] is True
    assert groups[1]["expanded"] is False


def test_grouped_option_renderer_supports_per_field_help_icons():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "family_search.py"
    ).read_text(encoding="utf-8")

    assert 'help_text = str(rec.get("help") or "").strip() or None' in source
    assert "help=help_text" in source
    assert "def _render_option_groups" in source
    assert 'st.expander(group["name"], expanded=False)' in source


def test_torsion_target_pipeline_inserts_exact_gate_before_deep_search():
    recipes = {
        "generator_breaker": "large_height_generator_hunt",
        "geometry": "pointed_quartic",
        "geometry_grinder": "mw_growth_loop",
        "aggressive_rank_hunter": "large_height_generator_hunt",
        "selmer_gate": "selmer_headroom",
        "covering_forest": "selmer_element_fanout",
        "isogeny_hunt": "isogeny_walk",
        "arithmetic_siege": "isogeny_walk",
    }
    for preset_id, expected_stage in recipes.items():
        choice = _torsion_target_pipeline_choice(
            preset_id,
            4,
            label=f"C2xC8 {preset_id}",
        )
        stages = choice["pipeline"]["stages"]
        ids = [rec["id"] for rec in stages]

        assert choice["pipeline"]["target_mode"] == "curve"
        assert choice["target_rank"] == 4
        assert ids[0] == "exact_torsion"
        assert expected_stage in ids
        assert "nagao_screen" not in ids
        assert "nagao_rescore" not in ids


def test_curve_target_exact_torsion_is_a_hard_gate(monkeypatch):
    monkeypatch.setattr(
        pipeline_runner,
        "persist_curve_torsion",
        lambda db, curve_id, E, algorithm=None, timeout=None: {
            "torsion_label": "C2",
            "torsion_order": 2,
        },
    )

    result, terminal, stop = pipeline_runner._stage_result(
        "exact_torsion",
        db=object(),
        run={"id": 1, "target_mode": "curve"},
        run_config={},
        target={"curve_id": 7, "torsion_group": "C2 × C8"},
        context={"curve_id": 7, "E": object(), "parameter": "curve:7"},
        config={},
        stage_index=1,
    )

    assert terminal == "torsion_mismatch"
    assert stop is False
    assert result["status"] == "torsion_mismatch"
    assert result["wanted_torsion"] == "C2 × C8"


def test_target_page_exposes_torsion_backend_without_target_adapter():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "target_curve.py"
    ).read_text(encoding="utf-8")

    assert "variant_torsion_groups" in source
    assert "modes.insert(0,'Torsion')" in source
    assert "target-torsion-strategy-" in source
    assert "target-torsion-goal-" in source
    assert "'torsion_group':str(torsion_group)" in source
    assert "Torsion mode exact-checks the stored curve" in source


def test_torsion_target_preset_modified_tracks_backend_recipe_and_goal():
    preset = {
        "id": "fiber_expansion",
        "target": {
            "backend": "Torsion",
            "pipeline_preset": "geometry",
            "goal_rank": 4,
            "ratpoints_backend": "GPU",
        },
    }
    assert not _target_preset_modified(
        preset,
        {},
        [],
        "GPU",
        mode="Torsion",
        torsion_strategy="geometry",
        torsion_goal=4,
    )
    assert _target_preset_modified(
        preset,
        {},
        [],
        "GPU",
        mode="Torsion",
        torsion_strategy="generator_breaker",
        torsion_goal=4,
    )


def test_selmer_target_recipes_respect_headroom_prerequisite():
    for preset_id in ("selmer_gate", "covering_forest", "arithmetic_siege"):
        choice = _torsion_target_pipeline_choice(preset_id, 4)
        ids = [rec["id"] for rec in choice["pipeline"]["stages"]]
        assert ids.index("selmer_bound") < ids.index("selmer_headroom")


def test_isogeny_target_recipes_use_only_degree_two_neighbors():
    for preset_id in ("isogeny_hunt", "arithmetic_siege"):
        choice = _torsion_target_pipeline_choice(preset_id, 4)
        stage = next(
            rec for rec in choice["pipeline"]["stages"]
            if rec["id"] == "isogeny_walk"
        )
        assert stage["config"]["degrees"] == [2]
        assert stage["config"]["include_parent"] is True
        assert stage["config"]["transfer_basis"] is True



def test_target_preset_provenance_is_frozen_for_plugin_and_torsion_launches():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "target_curve.py"
    ).read_text(encoding="utf-8")

    assert source.count("search_preset_provenance(") >= 2
    assert source.count("metadata['search_preset']=preset_provenance") >= 2
    assert source.count("run_config['search_preset']=preset_provenance") >= 2
    assert "'pipeline_preset':str(torsion_strategy)" in source
    assert "'goal_rank':int(torsion_goal)" in source
