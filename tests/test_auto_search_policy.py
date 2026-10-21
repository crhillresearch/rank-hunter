from types import SimpleNamespace

import pytest

from rank42.auto_search_policy import (
    auto_search_policy,
    baseline_certificate_timeout,
    classical_covering_policy,
    classical_covering_should_run,
    default_target_rank,
    discard_fresh_curve_for_retention,
    point_search_mode,
    pointed_quartic_policy,
    pointed_quartic_should_run,
    rigorous_goal_reached,
    tier_chart_budget,
    tier_should_run,
)


def _obj(manifest):
    return SimpleNamespace(manifest=manifest)


def test_policy_merges_plugin_and_variant():
    plugin = _obj({
        "auto_search": {
            "target_rank": 31,
            "integral_minimal_until_rank": 25,
            "pool_size": 50,
        }
    })
    variant = _obj({"auto_search": {"pool_size": 80}})
    policy = auto_search_policy(plugin, variant)
    assert policy["target_rank"] == 31
    assert policy["integral_minimal_until_rank"] == 25
    assert policy["pool_size"] == 80


def test_integral_then_rational_policy_boundary():
    policy = {"integral_minimal_until_rank": 25}
    low = point_search_mode(policy, 17)
    assert low["native_only"] is True
    assert low["minimal_model"] is True
    assert low["denominator_low"] == 1
    assert low["denominator_high"] == 1

    at_threshold = point_search_mode(policy, 25)
    assert at_threshold["native_only"] is False
    assert at_threshold["minimal_model"] is False
    assert at_threshold["denominator_low"] is None
    assert at_threshold["denominator_high"] is None


def test_policy_defaults_and_certificate_floor():
    assert default_target_rank({}, 20) == 20
    assert default_target_rank({"target_rank": 31}, 20) == 31
    assert baseline_certificate_timeout({}, 120) == 120
    assert baseline_certificate_timeout({"baseline_certificate_timeout": 240}, 120) == 240
    assert baseline_certificate_timeout({"baseline_certificate_timeout": 60}, 120) == 120


def test_plugin_auto_search_policy_validation_rejects_typos_and_bad_values():
    from rank42.plugins import _validate_auto_search_policy

    _validate_auto_search_policy(
        {
            "target_rank": 31,
            "integral_minimal_until_rank": 25,
            "deep_keep": 0,
            "strategy_note": "test",
        },
        where="test plugin",
    )
    with pytest.raises(ValueError, match="unknown keys"):
        _validate_auto_search_policy(
            {"integral_minmal_until_rank": 25},
            where="test plugin",
        )
    with pytest.raises(ValueError, match="must be >= 1"):
        _validate_auto_search_policy(
            {"target_rank": 0},
            where="test plugin",
        )


def test_pipeline_search_manifest_validation_is_narrow_and_pipeline_native():
    from rank42.plugins import _validate_pipeline_search_config

    _validate_pipeline_search_config(
        {
            "target_rank": 31,
            "recommended_preset": "high_baseline_rank_hunter",
            "certificate_timeout": 240,
            "exact_candidates": 64,
            "retention_floor": 17,
            "strategy_note": "record hunt",
        },
        where="record family",
    )
    with pytest.raises(ValueError, match="unknown keys"):
        _validate_pipeline_search_config(
            {"deep_keep": 12},
            where="record family",
        )
    with pytest.raises(ValueError, match="cannot exceed target_rank"):
        _validate_pipeline_search_config(
            {"target_rank": 10, "retention_floor": 11},
            where="record family",
        )


def test_no_growth_high_baseline_does_not_unlock_priority():
    common = {
        "rank_order": 1,
        "deep_keep": 12,
        "lower": 18,
        "baseline_lower": 18,
        "target_rank": 20,
        "growth": 0,
        "novel_points": 0,
        "previous_growth": 0,
    }
    assert tier_should_run("priority", **common) is False


def test_one_rank_growth_near_target_unlocks_small_priority_budget():
    common = {
        "rank_order": 1,
        "deep_keep": 12,
        "lower": 19,
        "baseline_lower": 18,
        "target_rank": 20,
        "growth": 1,
        "novel_points": 1,
        "previous_growth": 1,
    }
    assert tier_should_run("priority", **common) is True
    assert tier_chart_budget({"id": "priority", "charts": 128}, growth=1) == 32
    assert tier_chart_budget({"id": "priority", "charts": 128}, growth=2) == 64
    assert tier_chart_budget({"id": "priority", "charts": 128}, growth=3) == 128


def test_target_proximity_alone_does_not_force_deep():
    common = {
        "rank_order": 20,
        "deep_keep": 12,
        "lower": 18,
        "baseline_lower": 18,
        "target_rank": 20,
        "growth": 0,
        "novel_points": 0,
        "previous_growth": 0,
    }
    assert tier_should_run("deep", **common) is False



def test_classical_covering_policy_defaults_and_manifest_values():
    disabled = classical_covering_policy({})
    assert disabled["enabled"] is False
    assert disabled["engine"] == "simon_known"
    assert disabled["min_rank"] == 17

    configured = classical_covering_policy({
        "classical_covering_enabled": True,
        "classical_covering_engine": "simon_known",
        "classical_covering_min_rank": 18,
        "classical_covering_top_fresh": 2,
        "classical_covering_timeout": 900,
        "classical_covering_lim3": 200,
    })
    assert configured["enabled"] is True
    assert configured["min_rank"] == 18
    assert configured["top_fresh"] == 2
    assert configured["timeout"] == 900
    assert configured["lim3"] == 200


def test_classical_covering_can_be_scheduled_from_rank_zero_when_explicit():
    policy = {
        "classical_covering_enabled": True,
        "classical_covering_min_rank": 0,
        "classical_covering_top_fresh": 2,
    }
    cfg = classical_covering_policy(policy)
    assert cfg["min_rank"] == 0
    assert classical_covering_should_run(
        policy=policy,
        rank_order=1,
        lower=0,
        target_rank=4,
        growth=0,
        created_by_campaign=True,
    ) is True


def test_classical_covering_escalates_growth_preexisting_and_top_fresh_only():
    policy = {
        "classical_covering_enabled": True,
        "classical_covering_min_rank": 17,
        "classical_covering_top_fresh": 2,
    }
    base = {
        "policy": policy,
        "lower": 18,
        "target_rank": 31,
    }
    assert classical_covering_should_run(
        **base, rank_order=20, growth=1, created_by_campaign=True
    ) is True
    assert classical_covering_should_run(
        **base, rank_order=0, growth=0, created_by_campaign=False
    ) is True
    assert classical_covering_should_run(
        **base, rank_order=2, growth=0, created_by_campaign=True
    ) is True
    assert classical_covering_should_run(
        **base, rank_order=3, growth=0, created_by_campaign=True
    ) is False
    assert classical_covering_should_run(
        policy=policy, rank_order=1, lower=16, target_rank=31,
        growth=0, created_by_campaign=True,
    ) is False
    assert classical_covering_should_run(
        policy=policy, rank_order=1, lower=31, target_rank=31,
        growth=5, created_by_campaign=True,
    ) is False


def test_plugin_policy_accepts_classical_covering_fields():
    from rank42.plugins import _validate_auto_search_policy

    _validate_auto_search_policy(
        {
            "classical_covering_enabled": True,
            "classical_covering_engine": "simon_known",
            "classical_covering_min_rank": 17,
            "classical_covering_top_fresh": 2,
            "classical_covering_timeout": 900,
            "classical_covering_lim1": 5,
            "classical_covering_lim3": 200,
            "classical_covering_first_limit": 20,
            "classical_covering_second_limit": 10,
            "classical_covering_n_aux": 33,
        },
        where="record family",
    )
    with pytest.raises(ValueError, match="classical_covering_engine"):
        _validate_auto_search_policy(
            {"classical_covering_engine": "magic"},
            where="record family",
        )



def test_pointed_quartic_policy_and_scheduler_gate():
    disabled = pointed_quartic_policy({})
    assert disabled["enabled"] is False
    assert disabled["heights"] == (10000, 1000000)

    policy = {
        "pointed_quartic_enabled": True,
        "pointed_quartic_min_rank": 17,
        "pointed_quartic_top_fresh": 2,
        "pointed_quartic_anchors": 32,
        "pointed_quartic_pool_size": 384,
        "pointed_quartic_rounds": 2,
        "pointed_quartic_deep_keep": 9,
        "pointed_quartic_timeout": 7,
        "pointed_quartic_reduce_timeout": 19,
        "pointed_quartic_heights": [1000000, 10000, 10000],
    }
    cfg = pointed_quartic_policy(policy)
    assert cfg["enabled"] is True
    assert cfg["anchors"] == 32
    assert cfg["pool_size"] == 384
    assert cfg["deep_keep"] == 9
    assert cfg["heights"] == (10000, 1000000)
    assert cfg["reduce_timeout"] == 19

    base = dict(policy=policy, lower=18, target_rank=31)
    assert pointed_quartic_should_run(
        **base, rank_order=20, growth=1, created_by_campaign=True
    ) is True
    assert pointed_quartic_should_run(
        **base, rank_order=0, growth=0, created_by_campaign=False
    ) is True
    assert pointed_quartic_should_run(
        **base, rank_order=2, growth=0, created_by_campaign=True
    ) is True
    assert pointed_quartic_should_run(
        **base, rank_order=3, growth=0, created_by_campaign=True
    ) is False
    assert pointed_quartic_should_run(
        policy=policy, rank_order=1, lower=16, target_rank=31,
        growth=0, created_by_campaign=True,
    ) is False


def test_plugin_policy_accepts_pointed_quartic_fields():
    from rank42.plugins import _validate_auto_search_policy

    _validate_auto_search_policy(
        {
            "pointed_quartic_enabled": True,
            "pointed_quartic_min_rank": 17,
            "pointed_quartic_top_fresh": 2,
            "pointed_quartic_anchors": 32,
            "pointed_quartic_pool_size": 384,
            "pointed_quartic_rounds": 2,
            "pointed_quartic_deep_keep": 8,
            "pointed_quartic_timeout": 8,
            "pointed_quartic_reduce_timeout": 20,
            "pointed_quartic_heights": [10000, 1000000],
        },
        where="record family",
    )
    with pytest.raises(ValueError, match="pointed_quartic_heights"):
        _validate_auto_search_policy(
            {"pointed_quartic_heights": []},
            where="record family",
        )


def test_rigorous_goal_stop_uses_lower_bound_and_toggle():
    assert rigorous_goal_reached(16, 16, stop_on_target=True) is True
    assert rigorous_goal_reached(17, 16, stop_on_target=True) is True
    assert rigorous_goal_reached(15, 16, stop_on_target=True) is False
    assert rigorous_goal_reached(20, 16, stop_on_target=False) is False


def test_retention_floor_discards_only_fresh_subfloor_curves():
    common = {
        "status": "target_hit",
        "growth": 4,
        "exact_rank": None,
    }
    assert discard_fresh_curve_for_retention(
        created_by_campaign=True,
        retention_floor=12,
        rigorous_lower=11,
        **common,
    ) is True
    assert discard_fresh_curve_for_retention(
        created_by_campaign=True,
        retention_floor=12,
        rigorous_lower=12,
        **common,
    ) is False
    assert discard_fresh_curve_for_retention(
        created_by_campaign=False,
        retention_floor=12,
        rigorous_lower=1,
        **common,
    ) is False


def test_zero_retention_floor_preserves_legacy_promotion_rule():
    assert discard_fresh_curve_for_retention(
        created_by_campaign=True,
        retention_floor=0,
        rigorous_lower=8,
        status="exhausted",
        growth=0,
        exact_rank=None,
    ) is True
    assert discard_fresh_curve_for_retention(
        created_by_campaign=True,
        retention_floor=0,
        rigorous_lower=9,
        status="exhausted",
        growth=1,
        exact_rank=None,
    ) is False
    assert discard_fresh_curve_for_retention(
        created_by_campaign=True,
        retention_floor=0,
        rigorous_lower=8,
        status="exact",
        growth=0,
        exact_rank=8,
    ) is False


def test_auto_search_manifest_accepts_direct_affine_toggle(tmp_path):
    import json
    from rank42.plugins import read_plugin

    root = tmp_path / "family"
    root.mkdir()
    (root / "family.py").write_text(
        "def name(): return 'demo'\n"
        "def curve(t): return None\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(
        json.dumps({
            "schema_version": 1,
            "id": "demo_direct_affine_toggle",
            "name": "Demo",
            "version": "1.0.0",
            "family": {
                "kind": "module",
                "spec": "demo_direct_affine_toggle_family",
                "file": "family.py",
            },
            "capabilities": ["candidate_generation"],
            "auto_search": {
                "direct_affine_enabled": False,
            },
        }),
        encoding="utf-8",
    )

    plugin = read_plugin(root)
    assert plugin.manifest["auto_search"]["direct_affine_enabled"] is False
