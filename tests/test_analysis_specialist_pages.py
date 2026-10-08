from pathlib import Path

from rank42.analysis_planner import analysis_plan
from rank42.plugin_hooks import SUPPORTED_FEATURE_HOOKS
from rank42.ui_pages import saturation_page


ROOT = Path(__file__).resolve().parents[1]


def _source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def _snapshot():
    return {
        "curve": {"id": 7},
        "state": {
            "rigorous_lower": 4,
            "rigorous_upper": None,
            "exact_rank": None,
            "inconsistent": False,
        },
        "actionable_points": [],
        "numerical_novel_points": [],
        "witnesses": [object() for _ in range(4)],
        "witness_gap": 0,
        "lattices": [object()],
        "evidence": [],
        "evidence_timeouts": 0,
        "quartic_searches": [],
        "pipeline_candidates": [],
        "family_section_lower": 2,
        "extra_directions_lower": 2,
        "method_history": {"upper_bound": {}},
    }


def test_analysis_shell_uses_descent_canonical_route_with_rank_proof_alias():
    source = _source("rank42/ui.py")
    assert '("Descent", "Descent")' in source
    assert '"Descent": descent_page.page' in source
    assert '"Rank Proof": "Descent"' in source
    assert '("Rank Proof", "Rank Proof")' not in source


def test_descent_page_is_task_centric_and_consumes_shared_planner():
    source = _source("rank42/ui_pages/descent_page.py")
    assert "title('Descent'" in source
    assert "curve_analysis_snapshot" in source
    assert "analysis_plan(snapshot)" in source
    assert "**Recommended next step:**" in source
    assert "title('Rank Proof'" not in source


def test_planner_routes_curve_level_proof_work_to_descent():
    plan = analysis_plan(_snapshot())
    proof_actions = [action for action in plan["actions"] if action["kind"] == "descent"]
    assert proof_actions
    assert all(action["page"] == "Descent" for action in proof_actions)


def test_independence_keeps_point_resolution_and_hands_curve_proof_to_descent():
    source = _source("rank42/ui_pages/independence_page.py")
    assert "**Research question:** does a newly found exact point add a new Mordell–Weil direction?" in source
    assert "Open Descent" in source
    assert 'st.session_state["rh_page"] = "Descent"' in source
    assert "Alternative rigorous upper-bound attack" not in source
    assert "rank42.advanced_upper_bound" not in source
    assert "rank42.mw_relation_attack" in source
    assert "rank42.hard_case_escalator" in source



def test_saturation_is_first_class_analysis_route_and_classical_only():
    shell = _source("rank42/ui.py")
    page = _source("rank42/ui_pages/saturation_page.py")

    assert '("Saturation", "Saturation")' in shell
    assert '"Saturation": saturation_page.page' in shell
    assert '"Saturation": PAGE_ICONS["Saturation"]' in shell
    assert 'title(\n        "Saturation",' in page
    assert 'state_prefix="saturation"' in page
    assert 'analysis.saturation.after_header' in page
    assert "analysis.saturation.after_header" in SUPPORTED_FEATURE_HOOKS
    assert '"rank42.cli",' in page
    assert '"saturate",' in page
    assert '"--mode",' in page
    assert '"--min-prime",' in page
    assert '"--max-prime",' in page
    assert 'kind="saturation"' in page
    assert "eclib" not in page.lower()


def test_saturation_page_keeps_rank_and_basis_semantics_explicit():
    page = _source("rank42/ui_pages/saturation_page.py")

    assert "is the current rigorous witness subgroup primitive" in page
    assert "It does not look for a new independent Mordell–Weil direction." in page
    assert "never establishes exact rank by itself" in page
    assert "Exact rank still requires rigorous lower = rigorous upper." in page
    assert '"Exact saturated basis returned by Sage"' in page
    assert "does not automatically replace the active witness basis" in page
    assert '"Prime-by-prime ladder"' in page
    assert '"Open Job / raw log"' in page


def test_saturation_history_rows_keep_arrow_display_columns_text_only():
    complete = saturation_page._history_row(
        {
            "id": 1,
            "status": "done",
            "options_json": (
                '{"mode":"ladder","min_prime":2,"max_prime":13,'
                '"saturated_through_prime":13,"index":1,'
                '"witness_subgroup_regulator":2.5,"model_used":"minimal"}'
            ),
            "elapsed_seconds": 1.25,
            "created_at": "2026-10-01T07:00:00",
        }
    )
    incomplete = saturation_page._history_row(
        {
            "id": 2,
            "status": "timeout",
            "options_json": "{}",
            "elapsed_seconds": None,
            "created_at": "2026-10-01T07:01:00",
        }
    )

    for key in ("Saturated through", "Index", "Subgroup regulator", "Model"):
        assert isinstance(complete[key], str)
        assert isinstance(incomplete[key], str)

    assert complete["Saturated through"] == "13"
    assert complete["Index"] == "1"
    assert complete["Subgroup regulator"] == "2.5"
    assert incomplete["Saturated through"] == "—"


def test_descent_hands_saturation_to_dedicated_workbench():
    source = _source("rank42/ui_pages/descent_page.py")

    assert "'Open Saturation'" in source
    assert "st.session_state['rh_page']='Saturation'" in source
    assert "'Saturate rigorous basis'" not in source
    assert "'Saturate through prime'" not in source
