from types import SimpleNamespace

import pytest

from rank42.auto_search import (
    _bounded_geometry_options,
    _geometry_policy,
    _geometry_rank_selected,
    _is_torsion_filtered_campaign,
)
from rank42.geometry_search import _keeps


class _Adapter:
    @staticmethod
    def search_options(context="family"):
        assert context == "target"
        return [
            {"key": "timeout", "default": 30},
            {"key": "construction_timeout", "default": 300},
            {"key": "selmer_timeout", "default": 300},
            {"key": "covering_timeout", "default": 1800},
            {"key": "charts", "default": 64},
            {"key": "subgroup_scan", "default": 512},
            {"key": "exact_candidates", "default": 64},
            {"key": "certificate_timeout", "default": 600},
            {"key": "target_lower", "default": 31},
        ]


def test_geometry_default_keeps_are_two_stage_and_end_at_pool_size():
    args = SimpleNamespace(stage_keeps=None, stage_bounds=[523, 1979], pool_size=250)
    assert _keeps(args) == [5000, 250]


def test_geometry_keeps_reject_wrong_stage_count():
    args = SimpleNamespace(stage_keeps=[5000], stage_bounds=[523, 1979], pool_size=250)
    with pytest.raises(SystemExit, match="stage-keeps"):
        _keeps(args)


def test_geometry_target_options_keep_plugin_geometry_but_bound_expensive_steps():
    options = _bounded_geometry_options(
        _Adapter(),
        target_rank=32,
        ratpoints="/tmp/ratpoints_gpu",
        certificate_timeout=120,
        exact_candidates=48,
    )
    assert options["target_lower"] == 32
    assert options["timeout"] == 15
    assert options["construction_timeout"] == 90
    assert options["selmer_timeout"] == 30
    assert options["covering_timeout"] == 90
    assert options["charts"] == 32
    assert options["subgroup_scan"] == 256
    assert options["exact_candidates"] == 48
    assert options["certificate_timeout"] == 120
    assert options["ratpoints_backend"] == "GPU"


@pytest.mark.parametrize(
    "mode,torsion,expected",
    [
        ("torsion_provider", "C2 x C8", True),
        ("geometry_torsion", "C2 x C8", True),
        ("geometry_torsion_provider", "C2 x C8", True),
        ("geometry", None, False),
        ("family", "C2 x C8", False),
    ],
)
def test_torsion_filtered_campaign_modes(mode, torsion, expected):
    campaign = {"search_mode": mode, "torsion_group": torsion}
    assert _is_torsion_filtered_campaign(campaign) is expected



def test_geometry_rank_selection_includes_existing_seed_and_top_fresh():
    assert _geometry_rank_selected(0, 48) is True
    assert _geometry_rank_selected(1, 48) is True
    assert _geometry_rank_selected(48, 48) is True
    assert _geometry_rank_selected(49, 48) is False


def test_geometry_policy_enables_core_geometry_even_when_plugin_does_not():
    args = SimpleNamespace(geometry_keep=48, geometry_timeout=180)
    policy = _geometry_policy(
        {
            "pointed_quartic_enabled": False,
            "pointed_quartic_min_rank": 17,
            "classical_covering_enabled": False,
            "classical_covering_min_rank": 17,
        },
        args,
    )
    assert policy["pointed_quartic_enabled"] is True
    assert policy["pointed_quartic_min_rank"] == 1
    assert policy["pointed_quartic_top_fresh"] >= 48
    assert policy["classical_covering_enabled"] is True
    assert policy["classical_covering_min_rank"] == 0
    assert policy["classical_covering_top_fresh"] >= 8
