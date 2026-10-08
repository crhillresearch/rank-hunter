from pathlib import Path

from rank42.pipeline_catalog import stage_spec, validate_pipeline
from rank42.search_plugin_geometry import bounded_geometry_options
from rank42.ui_pages.target_curve import (
    _free_target_pipeline_choice,
    _plugin_target_pipeline_choice,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET_PAGE = ROOT / "rank42" / "ui_pages" / "target_curve.py"
PIPELINE_RUNNER = ROOT / "rank42" / "pipeline_runner.py"


class _Adapter:
    def search_options(self, *, context):
        assert context == "target"
        return [
            {"key": "charts", "default": 8},
            {"key": "timeout", "default": 10},
            {"key": "custom_mode", "default": "default"},
        ]


def test_plugin_geometry_is_available_for_exact_curve_targets():
    assert "curve" in stage_spec("plugin_geometry").targets

    choice = _plugin_target_pipeline_choice(
        "Demo Family",
        {
            "charts": 17,
            "timeout": 40,
            "custom_mode": "deep",
            "certificate_timeout": 180,
            "exact_candidates": 24,
            "ratpoints": "/tmp/ratpoints",
            "ratpoints_backend": "GPU",
            "plugin_variant": "v2",
            "family_spec": "demo:family",
            "chart_id": "chart-7",
        },
        12,
        resolved_config={
            "plugin_id": "demo",
            "plugin_version": "1",
            "variant_id": "v2",
            "family_spec": "demo:family",
            "context": "target",
            "preset_id": "deep",
            "config_hash": "def456",
        },
    )
    stages = choice["pipeline"]["stages"]
    assert choice["pipeline"]["target_mode"] == "curve"
    assert choice["target_rank"] == 12
    assert [rec["id"] for rec in stages] == [
        "plugin_geometry",
        "independence",
        "stop_goal",
    ]
    geometry_options = stages[0]["config"]["options"]
    assert geometry_options["charts"] == 17
    assert geometry_options["custom_mode"] == "deep"
    assert geometry_options["chart_id"] == "chart-7"
    assert geometry_options["target_lower"] == 12
    assert stages[0]["config"]["resolved_profile"] == {
        "plugin_id": "demo",
        "plugin_version": "1",
        "variant_id": "v2",
        "family_spec": "demo:family",
        "context": "target",
        "preset_id": "deep",
        "config_hash": "def456",
    }
    assert "ratpoints" not in geometry_options
    assert "ratpoints_backend" not in geometry_options
    assert "plugin_variant" not in geometry_options
    assert "family_spec" not in geometry_options
    assert stages[1]["config"]["certificate_timeout"] == 180
    assert stages[1]["config"]["max_candidates"] == 24
    assert validate_pipeline("curve", stages) == []


def test_plugin_geometry_runtime_merges_target_overrides_under_core_bounds():
    options = bounded_geometry_options(
        _Adapter(),
        target_rank=11,
        ratpoints="/opt/ratpoints-gpu",
        certificate_timeout=120,
        exact_candidates=32,
        overrides={
            "charts": 17,
            "timeout": 40,
            "custom_mode": "deep",
            "ratpoints": "ignored",
            "ratpoints_backend": "CPU",
        },
    )

    assert options["charts"] == 17
    assert options["timeout"] == 15
    assert options["custom_mode"] == "deep"
    assert options["ratpoints"] == "/opt/ratpoints-gpu"
    assert options["ratpoints_backend"] == "GPU"
    assert options["limit"] == 1


def test_free_target_controls_serialize_to_exact_curve_pipeline():
    choice = _free_target_pipeline_choice(
        stages="100000,1000,10000,1000",
        timeout=20,
        exact_candidates=8,
        certificate_timeout=120,
        model_prep_timeout=10,
        goal_rank=9,
    )
    stages = choice["pipeline"]["stages"]

    assert choice["pipeline"]["target_mode"] == "curve"
    assert choice["target_rank"] == 9
    assert [rec["id"] for rec in stages] == [
        "affine_search",
        "independence",
        "stop_goal",
    ]
    assert stages[0]["config"]["heights"] == [1000, 10000, 100000]
    assert stages[0]["config"]["charts"] == 1
    assert stages[0]["config"]["timeout"] == 20
    assert stages[0]["config"]["model_mode"] == "minimal"
    assert stages[0]["config"]["model_prep_timeout"] == 10
    assert stages[0]["config"]["certify_after_search"] is False
    assert stages[1]["config"]["certificate_timeout"] == 120
    assert stages[1]["config"]["max_candidates"] == 8
    assert validate_pipeline("curve", stages) == []




def test_free_target_zero_exact_candidates_keeps_search_but_skips_rank_certification():
    choice = _free_target_pipeline_choice(
        stages="1000,10000",
        timeout=20,
        exact_candidates=0,
        certificate_timeout=120,
        model_prep_timeout=10,
        goal_rank=9,
    )
    stages = choice["pipeline"]["stages"]

    assert [rec["id"] for rec in stages] == ["affine_search"]
    assert stages[0]["config"]["certify_after_search"] is False
    assert validate_pipeline("curve", stages) == []

def test_target_surface_has_no_native_plugin_or_free_execution_path():
    source = TARGET_PAGE.read_text(encoding="utf-8")

    assert "Built-in Pipeline · Plugin geometry" in source
    assert "Built-in Pipeline · Current FREE controls" in source
    assert "_plugin_target_pipeline_choice(" in source
    assert "_free_target_pipeline_choice(" in source
    assert "adapter.build_target_search_command(" not in source
    assert "rank42.fixed_curve_search" not in source
    assert source.count("require_pipeline=True") >= 3
    assert source.count("native_command=[]") >= 3


def test_curve_pipeline_runner_restores_plugin_context_for_target_geometry():
    source = PIPELINE_RUNNER.read_text(encoding="utf-8")

    assert 'plugin_id = str(target.get("plugin_id") or "").strip()' in source
    assert 'plugin = get_plugin(project_root, plugin_id)' in source
    assert 'variant = get_variant(plugin, target.get("variant_id"))' in source
    assert 'fingerprints = plugin_fingerprints(plugin, variant)' in source
    assert '"plugin": plugin' in source
    assert '"variant": variant' in source
