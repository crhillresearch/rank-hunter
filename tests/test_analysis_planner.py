from __future__ import annotations

import inspect

from rank42.analysis_planner import (
    PLANNER_VERSION,
    analysis_plan,
    planner_immediate_actions,
    planner_research_paths,
)
from rank42.analyze_workspace import next_useful_actions, researcher_paths
from rank42.ui_pages import analyze_page
from rank42 import manage_store


def _snapshot(
    *,
    lower=4,
    upper=None,
    exact=None,
    inconsistent=False,
    actionable=0,
    novel=0,
    witnesses=0,
    witness_gap=0,
    lattices=0,
    family_section_lower=2,
    extra_directions_lower=2,
):
    return {
        "curve": {"id": 7},
        "state": {
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": exact,
            "inconsistent": inconsistent,
        },
        "actionable_points": [object() for _ in range(actionable)],
        "numerical_novel_points": [object() for _ in range(novel)],
        "witnesses": [object() for _ in range(witnesses)],
        "witness_gap": witness_gap,
        "lattices": [object() for _ in range(lattices)],
        "evidence": [object(), object()],
        "evidence_timeouts": 1,
        "quartic_searches": [object()],
        "pipeline_candidates": [object(), object(), object()],
        "family_section_lower": family_section_lower,
        "extra_directions_lower": extra_directions_lower,
        "family_pool_id": 41,
    }


def test_analysis_planner_returns_rich_ordered_action_contract():
    snapshot = _snapshot(
        lower=4,
        actionable=1,
        novel=1,
        witnesses=2,
        witness_gap=2,
    )

    plan = analysis_plan(snapshot)
    assert plan["planner_version"] == PLANNER_VERSION
    assert plan["curve_id"] == 7
    assert plan["actions"]

    immediate = planner_immediate_actions(plan)
    assert immediate[0]["title"] == "Repair the replayable witness basis"
    assert immediate[0]["page"] == "Independence"

    required = {
        "kind",
        "title",
        "why",
        "reason",
        "priority",
        "research_goal",
        "prerequisites",
        "prior_attempt_state",
        "recommended_method",
        "exhausted",
        "role",
        "destination",
    }
    for action in plan["actions"]:
        assert required.issubset(action)
        assert action["reason"] == action["why"]
        assert isinstance(action["prerequisites"], list)
        assert isinstance(action["prior_attempt_state"], dict)
        assert isinstance(action["exhausted"], bool)


def test_analysis_planner_merges_legacy_research_paths_without_duplicate_authority():
    snapshot = _snapshot(lower=4, witnesses=4)

    plan = analysis_plan(snapshot)
    paths = planner_research_paths(plan)
    assert [path["kind"] for path in paths] == [
        "determine_rank",
        "hunt_generator",
        "study_jump",
        "search_related",
    ]
    assert paths[0]["title"] == "Determine the actual rank"
    assert paths[1]["title"] == "Hunt another independent point"

    # Legacy helpers are compatibility projections over the same planner.
    assert researcher_paths(snapshot) == paths
    assert next_useful_actions(snapshot) == planner_immediate_actions(plan)


def test_analysis_planner_preserves_existing_interval_as_strategic_not_new_immediate_action():
    snapshot = _snapshot(lower=4, upper=6)

    plan = analysis_plan(snapshot)
    immediate = planner_immediate_actions(plan)
    paths = planner_research_paths(plan)

    assert not any(
        action["title"] == "Close the rigorous rank interval"
        for action in immediate
    )
    assert paths[0]["kind"] == "determine_rank"
    assert "4–6" in paths[0]["why"]


def test_analysis_planner_handles_campaign_target_context():
    reached = analysis_plan(_snapshot(lower=8), target_rank=8)
    reached_primary = planner_immediate_actions(reached)[0]
    assert reached_primary["kind"] == "campaign_target_review"
    assert reached_primary["page"] == "Curves"
    assert "meeting the campaign target ≥8" in reached_primary["why"]

    below = analysis_plan(
        _snapshot(lower=4, upper=4, exact=4, witnesses=4, lattices=1),
        target_rank=6,
    )
    below_primary = planner_immediate_actions(below)[0]
    assert below_primary["kind"] == "campaign_search"
    assert below_primary["page"] == "Pipelines"
    assert "exact rank 4" in below_primary["why"]
    assert "target ≥6" in below_primary["why"]


def test_analyze_and_campaign_consume_shared_planner_directly():
    analyze_source = inspect.getsource(analyze_page._overview)
    action_source = inspect.getsource(analyze_page._render_next_actions)
    campaign_source = inspect.getsource(manage_store.campaign_research_brief)

    assert "analysis_plan(snapshot)" in analyze_source
    assert "planner_research_paths(plan)" in analyze_source
    assert "_render_next_actions(snapshot, plan=plan)" in analyze_source
    assert "planner_immediate_actions(plan)" in action_source
    assert "next_useful_actions(" not in analyze_source
    assert "researcher_paths(" not in analyze_source

    assert "analysis_plan(best_analysis, target_rank=target)" in campaign_source
    assert "planner_immediate_actions(plan)" in campaign_source
    assert "next_useful_actions(" not in campaign_source
