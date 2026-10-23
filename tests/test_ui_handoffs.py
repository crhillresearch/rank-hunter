from pathlib import Path

import pytest

from rank42.ui_active_curve import ACTIVE_CURVE_KEY
from rank42.ui import _normalize_route
from rank42.ui_handoffs import (
    RESEARCH_HANDOFF_KEY,
    clear_research_handoff,
    get_research_handoff,
    normalize_research_handoff,
    research_handoff_for_curve,
    route_curve_to_target,
    route_pool_to_search,
    route_selection_to_pipelines,
)


ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return {
        "source": "fiber_atlas",
        "kind": "fiber_selection",
        "family": "Demo family",
        "curve_ids": [7, "9", 7, 0, "nope"],
        "parameters": [
            {
                "curve_id": 7,
                "parameter": "3/5",
                "rigorous_lower": 6,
                "generic_lower": 6,
                "family_baseline": 4,
                "family_baseline_kind": "generic_lower_bound",
                "baseline_excess": 2,
                "rank_jump": None,
                "score": 12.5,
                "root_number": -1,
            },
            {"curve_id": 9, "parameter": "7/11"},
            {"curve_id": 0, "parameter": "ignored"},
        ],
        "selection": {
            "view": "Rank jumps",
            "filters": {"root_number": "-1"},
            "axis": {"x": "log_height", "y": "rank_jump"},
        },
    }


def test_research_handoff_is_normalized_and_hash_is_deterministic():
    first = normalize_research_handoff(_payload())
    second = normalize_research_handoff(_payload())

    assert first["version"] == 1
    assert first["source"] == "fiber_atlas"
    assert first["kind"] == "fiber_selection"
    assert first["family"] == "Demo family"
    assert first["curve_ids"] == [7, 9]
    assert [rec["curve_id"] for rec in first["parameters"]] == [7, 9]
    assert first["parameters"][0]["family_baseline"] == 4
    assert first["parameters"][0]["family_baseline_kind"] == "generic_lower_bound"
    assert first["parameters"][0]["baseline_excess"] == 2
    assert "rank_jump" not in first["parameters"][0]
    assert len(first["selection_hash"]) == 64
    assert first["selection_hash"] == second["selection_hash"]


@pytest.mark.parametrize("payload", [None, [], {}, {"source": "x"}])
def test_research_handoff_rejects_invalid_payloads(payload):
    with pytest.raises(ValueError):
        normalize_research_handoff(payload)


def test_route_curve_to_target_uses_semantic_active_curve_and_provenance():
    state = {}
    route_curve_to_target(state, 7, handoff=_payload())

    assert state["rh_page"] == "Search"
    assert state["new_search_view"] == "Target"
    assert state[ACTIVE_CURVE_KEY] == 7
    assert state["target_curve_id"] == 7
    assert state[RESEARCH_HANDOFF_KEY]["selection_hash"]
    assert research_handoff_for_curve(state, 7) is not None
    assert research_handoff_for_curve(state, 8) is None


def test_route_pool_to_new_search_preserves_pool_and_torsion_selection():
    state = {"rh-family-pool-select": 99}
    route_pool_to_search(state, 12, torsion_group="C2xC8")

    assert state["rh_page"] == "Search"
    assert state["new_search_view"] == "Candidates"
    assert state["family_search_pool_id"] == 12
    assert state["new-search-family-mode-12"] == "Torsion"
    assert state["family-torsion-group-12"] == "C2xC8"
    assert "rh-family-pool-select" not in state


@pytest.mark.parametrize(
    ("requested", "expected_view"),
    [
        ("New Search", "Family"),
        ("Target", "Target"),
        ("Target Curve", "Target"),
        ("Auto", "Family"),
        ("Family Search", "Family"),
        ("General Hunt", "General"),
    ],
)
def test_old_search_routes_open_the_promoted_search_workbench(requested, expected_view):
    state = {"rh_page": requested}
    assert _normalize_route(requested, state) == "Search"
    assert state["new_search_view"] == expected_view
    assert state["rh_page"] == "Search"


def test_auto_torsion_route_keeps_its_selected_group_mode():
    state = {"rh_page": "Auto", "auto-search-mode": "Torsion"}
    assert _normalize_route("Auto", state) == "Search"
    assert state["new_search_view"] == "Torsion"


def test_route_selection_to_pipelines_changes_only_navigation_context():
    state = {
        "byo-target-mode": "General Curves",
        "byo-curve": 3,
        "byo_stages": [{"id": "nagao_screen", "config": {}}],
        "builder_main_view": "Saved",
        "builder_view_revision": 4,
    }
    payload = route_selection_to_pipelines(state, _payload())

    assert state["rh_page"] == "Pipelines"
    assert state["builder_main_view"] == "Editor"
    assert state["builder_view_revision"] == 5
    assert state["byo-target-mode"] == "General Curves"
    assert state["byo-curve"] == 3
    assert state["byo_stages"] == [{"id": "nagao_screen", "config": {}}]
    assert payload["selection_hash"] == get_research_handoff(state)["selection_hash"]




def test_clear_research_handoff_removes_only_optional_context():
    state = {
        "byo-target-mode": "General Curves",
        "byo-curve": 3,
        "byo_stages": [{"id": "nagao_screen", "config": {}}],
    }
    route_selection_to_pipelines(state, _payload())

    removed = clear_research_handoff(state)

    assert removed is not None
    assert RESEARCH_HANDOFF_KEY not in state
    assert state["byo-target-mode"] == "General Curves"
    assert state["byo-curve"] == 3
    assert state["byo_stages"] == [{"id": "nagao_screen", "config": {}}]

def test_target_and_pipeline_pages_surface_handoff_without_execution():
    target = (ROOT / "rank42" / "ui_pages" / "target_curve.py").read_text(
        encoding="utf-8"
    )
    pipelines = (ROOT / "rank42" / "ui_pages" / "build_your_own.py").read_text(
        encoding="utf-8"
    )

    assert "research_handoff_for_curve" in target
    assert "Research selection handoff" in target
    assert "Target Search remains the execution owner" in target
    assert '"Clear research context"' in target
    assert "clear_research_handoff(st.session_state)" in target

    assert "get_research_handoff" in pipelines
    assert "Research selection context" in pipelines
    assert "Context only: the originating Workspace has not changed Builder modules" in pipelines
    assert "Rank Hunter does not yet have a generic " in pipelines
    assert "arbitrary-curve/parameter population contract" in pipelines
    assert '"Selection metadata"' in pipelines
    assert "selection items" in pipelines
    assert '"Clear research context"' in pipelines
    assert "clear_research_handoff(st.session_state)" in pipelines
    assert '"Retained curve parameters' in pipelines
    assert '"Selected research items' in pipelines
    assert "Unstored twists/isogeny nodes remain explicit here as research items." in pipelines



def test_research_handoff_preserves_exact_workspace_selection_metadata():
    payload = {
        "source": "twist_isogeny_lab",
        "kind": "quadratic_twist_selection",
        "family": "Demo family",
        "curve_ids": [7],
        "parameters": [{"curve_id": 7, "parameter": "3/5"}],
        "selection": {
            "workspace": "Twist & Isogeny Lab",
            "item_count": 2,
            "source_curve": {
                "curve_id": 7,
                "parameter": "3/5",
                "a_invariants": ["0", "0", "1", "-1", "0"],
            },
            "items": [
                {
                    "d": 5,
                    "minimal_a_invariants": ["0", "0", "1", "-25", "0"],
                    "root_number": -1,
                    "retained_curve_ids": [],
                },
                {
                    "d": 13,
                    "minimal_a_invariants": ["0", "0", "1", "-169", "0"],
                    "root_number": 1,
                    "retained_curve_ids": [],
                },
            ],
        },
    }

    first = normalize_research_handoff(payload)
    second = normalize_research_handoff(payload)

    assert first["source"] == "twist_isogeny_lab"
    assert first["kind"] == "quadratic_twist_selection"
    assert first["selection"]["item_count"] == 2
    assert first["selection"]["items"][0]["d"] == 5
    assert first["selection"]["source_curve"]["a_invariants"] == [
        "0", "0", "1", "-1", "0"
    ]
    assert first["selection_hash"] == second["selection_hash"]
