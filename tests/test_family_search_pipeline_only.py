from pathlib import Path

from rank42.pipeline_catalog import validate_pipeline
from rank42.ui_pages.family_search import (
    _free_family_pipeline_choice,
    _plugin_family_pipeline_choice,
)


ROOT = Path(__file__).resolve().parents[1]
FAMILY_PAGE = ROOT / "rank42" / "ui_pages" / "family_search.py"


def test_free_family_quick_only_controls_serialize_to_pipeline():
    choice = _free_family_pipeline_choice(
        quick_timeout=300,
        strong_timeout=900,
        depth="PARI screen only",
        goal_rank=8,
    )
    stages = choice["pipeline"]["stages"]

    assert choice["pipeline"]["target_mode"] == "family"
    assert choice["target_rank"] == 8
    assert [rec["id"] for rec in stages] == [
        "nagao_screen",
        "pari_upper_gate",
    ]
    assert stages[1]["config"]["timeout"] == 300
    assert stages[1]["config"]["eliminate_below_goal"] is True
    assert validate_pipeline("family", stages) == []


def test_free_family_strong_controls_serialize_to_pipeline():
    choice = _free_family_pipeline_choice(
        quick_timeout=45,
        strong_timeout=600,
        depth="PARI + strong mwrank descent",
        goal_rank=11,
    )
    stages = choice["pipeline"]["stages"]

    assert [rec["id"] for rec in stages] == [
        "nagao_screen",
        "pari_upper_gate",
        "mwrank_covering",
        "independence",
        "stop_goal",
    ]
    assert stages[1]["config"]["timeout"] == 45
    assert stages[2]["config"]["timeout"] == 600
    assert stages[2]["config"]["first_limit"] == 40
    assert stages[2]["config"]["second_limit"] == 20
    assert stages[3]["config"]["certificate_timeout"] == 120
    assert stages[3]["config"]["max_candidates"] == 64
    assert validate_pipeline("family", stages) == []


def test_free_family_surface_no_longer_launches_auto_analyze():
    source = FAMILY_PAGE.read_text(encoding="utf-8")

    assert "Built-in Pipeline · Current FREE controls" in source
    assert "_free_family_pipeline_choice(" in source
    assert '"rank42.auto_analyze"' not in source
    assert "require_pipeline=True" in source
    assert "pipeline_search_config(" in source


def test_plugin_family_controls_serialize_to_pipeline_owned_adapter_stage():
    choice = _plugin_family_pipeline_choice(
        "Demo Family",
        {
            "charts": 17,
            "timeout": 40,
            "certificate_timeout": 180,
            "exact_candidates": 24,
            "ratpoints": "/opt/ratpoints-gpu",
            "ratpoints_backend": "GPU",
            "plugin_variant": "v2",
            "family_spec": "demo:family",
            "limit": 20,
        },
        12,
        resolved_config={
            "plugin_id": "demo",
            "plugin_version": "1",
            "variant_id": "v2",
            "family_spec": "demo:family",
            "context": "family",
            "preset_id": "deep",
            "config_hash": "abc123",
        },
    )
    stages = choice["pipeline"]["stages"]

    assert choice["pipeline"]["target_mode"] == "family"
    assert choice["target_rank"] == 12
    assert [rec["id"] for rec in stages] == [
        "nagao_screen",
        "family_baseline",
        "plugin_family_search",
        "independence",
        "stop_goal",
    ]
    options = stages[2]["config"]["options"]
    assert options["charts"] == 17
    assert options["timeout"] == 40
    assert options["target_lower"] == 12
    assert "limit" not in options
    assert "ratpoints" not in options
    assert "ratpoints_backend" not in options
    assert "plugin_variant" not in options
    assert "family_spec" not in options
    assert stages[2]["config"]["resolved_profile"] == {
        "plugin_id": "demo",
        "plugin_version": "1",
        "variant_id": "v2",
        "family_spec": "demo:family",
        "context": "family",
        "preset_id": "deep",
        "config_hash": "abc123",
    }
    assert stages[2]["config"]["certificate_timeout"] == 180
    assert stages[2]["config"]["exact_candidates"] == 24
    assert validate_pipeline("family", stages) == []


def test_all_family_search_backends_are_pipeline_only():
    source = FAMILY_PAGE.read_text(encoding="utf-8")

    assert "Built-in Pipeline · Plugin family search" in source
    assert "_plugin_family_pipeline_choice(" in source
    assert "adapter.build_family_search_command(" not in source
    assert '"rank42.auto_analyze"' not in source
    assert "require_pipeline=True" in source



def test_plugin_family_preset_provenance_is_frozen_into_run_and_job():
    source = FAMILY_PAGE.read_text(encoding="utf-8")

    assert "search_preset_provenance(" in source
    assert 'run_config["search_preset"] = preset_provenance' in source
    assert 'launch_metadata["search_preset"] = preset_provenance' in source
    assert '"limit": int(count)' in source
    assert '"ratpoints_backend": str(rp_backend)' in source
