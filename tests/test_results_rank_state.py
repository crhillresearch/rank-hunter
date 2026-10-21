from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence
import inspect

from rank42.ui_pages import jobs_detail_panel, jobs_page, results_page
from rank42.ui_pages.results_page import _best_rigorous_display


MODEL = ["0", "0", "0", "-1", "0"]


def _evidence(db, curve_id, *, lower=None, upper=None, engine="results-test"):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": engine,
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )


def test_results_best_rigorous_rank_includes_evidence_only_lower(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        legacy = upsert_curve(db, family="results-test", parameter="legacy")
        update_curve(db, legacy, descent_lower=4)
        evidence_only = upsert_curve(db, family="results-test", parameter="evidence")
        _evidence(db, evidence_only, lower=7)

        assert _best_rigorous_display(db) == "≥ 7"
    finally:
        db.close()



def test_results_best_rigorous_rank_excludes_conflicting_evidence(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        conflict = upsert_curve(db, family="results-test", parameter="conflict")
        valid = upsert_curve(db, family="results-test", parameter="valid")
        _evidence(db, conflict, lower=10, engine="results-test-lower")
        _evidence(db, conflict, upper=9, engine="results-test-upper")
        _evidence(db, valid, lower=7)

        state = get_curve_research_state(db, conflict)
        assert state["rigorous_lower"] == 10
        assert state["rigorous_upper"] == 9
        assert state["rank_inconsistent"] is True
        assert _best_rigorous_display(db) == "≥ 7"
    finally:
        db.close()


def test_results_best_rigorous_rank_ignores_numerical_signal(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="results-test", parameter="numerical")
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "results-test",
                "evidence_type": "numerical_rank_signal",
                "status": "completed",
                "rigorous": False,
                "rigorous_lower": None,
                "rigorous_upper": None,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "numerical_rank_signal": 12,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )

        assert _best_rigorous_display(db) == "—"
    finally:
        db.close()


def test_results_best_rigorous_rank_empty_database_is_dash(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        assert _best_rigorous_display(db) == "—"
    finally:
        db.close()


def test_results_module_exposes_shared_workspace_and_scientific_detail():
    workspace = inspect.getsource(results_page.render_jobs_results)
    result_detail = inspect.getsource(results_page._detail)
    scientific_detail = inspect.getsource(results_page.render_job_scientific_result)
    raw_log_detail = inspect.getsource(results_page._render_job_raw_log)
    jobs = inspect.getsource(jobs_page.page)
    job_detail = inspect.getsource(jobs_detail_panel.render_job_detail)
    job_controls = inspect.getsource(jobs_detail_panel._render_job_controls)
    legacy_page = inspect.getsource(results_page.page)

    assert "_scoreboard(db,pairs)" in workspace
    assert "_detail(db,row,s,ctx.detected_science_python())" in workspace
    assert "results_page.render_jobs_results(db, ctx)" in jobs

    assert "_hunt_recap(db, row, summary, science_python)" in scientific_detail
    assert "'Parsed scientific result'" in scientific_detail
    assert "'Load raw log'" not in scientific_detail
    assert "_render_job_raw_log(db, row)" in scientific_detail
    assert scientific_detail.index("_render_job_raw_log(db, row)") < scientific_detail.index(
        "_hunt_recap(db, row, summary, science_python)"
    )
    assert scientific_detail.index("_render_job_raw_log(db, row)") < scientific_detail.index(
        "'Parsed scientific result'"
    )
    assert "with expander('Command'" not in scientific_detail
    assert "command_json" not in scientific_detail
    assert 'st.markdown("#### Raw log")' in raw_log_detail
    assert 'render_raw_log(read_log(row["log_path"]))' in raw_log_detail
    assert "_render_live_raw_log_fragment" in raw_log_detail
    assert "results_page.render_job_scientific_result(" in job_detail

    assert "stop_job(" not in result_detail
    assert "Force Kill" not in result_detail
    assert 'st.session_state["jobs_section_pending"] = "Results"' in job_controls
    assert 'st.session_state["rh_page"] = "Jobs"' in job_controls
    assert 'st.session_state["rh_page"] = "Results"' not in job_detail

    assert "render_jobs_results(db, ctx)" in legacy_page
    assert "Canonical home: Manage → Jobs → Results." in legacy_page


def test_legacy_results_route_normalizes_to_jobs_results():
    from rank42.ui import _normalize_route

    state = {}
    assert _normalize_route("Results", state) == "Jobs"
    assert state["rh_page"] == "Jobs"
    assert state["jobs_section_pending"] == "Results"
