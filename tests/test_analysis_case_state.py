import inspect

import pytest

from rank42.analysis_case_store import (
    CASE_PRIORITY_PRESETS,
    CASE_RESEARCH_GOALS,
    CASE_WORKFLOW_STATUSES,
    analysis_case_map_readonly,
    get_analysis_case,
    save_analysis_case,
)
from rank42.analyze_workspace import research_inbox
from rank42.db import CURRENT_SCHEMA_VERSION, connect, upsert_curve
from rank42.research_timeline import research_timeline
from rank42.schema_manifest import UI_SCHEMA_MANIFEST, inspect_schema
from rank42.ui_pages import analyze_page
from rank42.ui_store import ensure_ui_schema


def _curve(db, parameter):
    return upsert_curve(
        db,
        family="analysis-case",
        parameter=str(parameter),
        a_invariants_json='["0","0","0","-1","0"]',
    )


def test_analysis_case_schema_is_ui_workflow_state_only(tmp_path):
    db = connect(tmp_path / "case-state.db")
    try:
        core_version = db.execute("PRAGMA user_version").fetchone()[0]
        ensure_ui_schema(db)

        status = inspect_schema(db, UI_SCHEMA_MANIFEST)
        assert status["ready"] is True
        assert db.execute("PRAGMA user_version").fetchone()[0] == core_version
        assert core_version == CURRENT_SCHEMA_VERSION

        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(analysis_cases)").fetchall()
        }
        assert columns == {
            "id",
            "curve_id",
            "campaign_id",
            "priority",
            "workflow_status",
            "research_goal",
            "reason",
            "note",
            "created_at",
            "updated_at",
        }
        forbidden = {
            "rank",
            "rigorous_lower",
            "rigorous_upper",
            "exact_rank",
            "proof_status",
            "independence_status",
            "regulator",
            "generators_json",
            "points_json",
        }
        assert columns.isdisjoint(forbidden)
    finally:
        db.close()


def test_analysis_case_save_updates_only_human_workflow_state(tmp_path):
    db = connect(tmp_path / "case-save.db")
    try:
        ensure_ui_schema(db)
        curve_id = _curve(db, "save")
        rank_rows_before = db.execute(
            "SELECT COUNT(*) AS n FROM rank_evidence WHERE curve_id=?",
            (curve_id,),
        ).fetchone()["n"]
        point_rows_before = db.execute(
            "SELECT COUNT(*) AS n FROM points WHERE curve_id=?",
            (curve_id,),
        ).fetchone()["n"]

        saved = save_analysis_case(
            db,
            curve_id=curve_id,
            priority=CASE_PRIORITY_PRESETS["High"],
            workflow_status="open",
            research_goal="Resolve candidate point independence",
            reason="Candidate needs exact follow-up",
            note="Try the bounded exact relation attack next.",
        )

        assert int(saved["curve_id"]) == curve_id
        assert int(saved["priority"]) == CASE_PRIORITY_PRESETS["High"]
        assert saved["workflow_status"] == "open"
        assert saved["research_goal"] == "Resolve candidate point independence"
        assert get_analysis_case(db, curve_id)["note"] == (
            "Try the bounded exact relation attack next."
        )
        assert db.execute(
            "SELECT COUNT(*) AS n FROM rank_evidence WHERE curve_id=?",
            (curve_id,),
        ).fetchone()["n"] == rank_rows_before
        assert db.execute(
            "SELECT COUNT(*) AS n FROM points WHERE curve_id=?",
            (curve_id,),
        ).fetchone()["n"] == point_rows_before

        updated = save_analysis_case(
            db,
            curve_id=curve_id,
            priority=CASE_PRIORITY_PRESETS["Low"],
            workflow_status="deferred",
            research_goal="Compare related fibers",
            reason="Waiting on a broader family search",
            note="No mathematical conclusion implied by deferral.",
        )
        assert int(updated["id"]) == int(saved["id"])
        assert updated["workflow_status"] == "deferred"
        assert int(updated["priority"]) == CASE_PRIORITY_PRESETS["Low"]
        assert len(analysis_case_map_readonly(db, [curve_id])) == 1
    finally:
        db.close()


def test_analysis_case_validation_rejects_invalid_workflow_values(tmp_path):
    db = connect(tmp_path / "case-validation.db")
    try:
        ensure_ui_schema(db)
        curve_id = _curve(db, "validation")

        with pytest.raises(ValueError, match="workflow status"):
            save_analysis_case(
                db,
                curve_id=curve_id,
                workflow_status="proved",
            )
        with pytest.raises(ValueError, match="priority"):
            save_analysis_case(
                db,
                curve_id=curve_id,
                priority=999,
            )
        with pytest.raises(ValueError, match="campaign"):
            save_analysis_case(
                db,
                curve_id=curve_id,
                campaign_id=99999,
            )

        assert set(CASE_WORKFLOW_STATUSES) == {"open", "deferred", "resolved"}
        assert "Close the rigorous rank interval" in CASE_RESEARCH_GOALS
    finally:
        db.close()


def test_research_inbox_surfaces_case_state_without_overriding_scientific_attention(tmp_path):
    db = connect(tmp_path / "case-inbox.db")
    try:
        ensure_ui_schema(db)
        open_curve = _curve(db, "open")
        resolved_curve = _curve(db, "resolved")

        save_analysis_case(
            db,
            curve_id=open_curve,
            priority=CASE_PRIORITY_PRESETS["High"],
            workflow_status="open",
            research_goal="Find another independent point",
            reason="Actively investigating",
        )
        save_analysis_case(
            db,
            curve_id=resolved_curve,
            priority=CASE_PRIORITY_PRESETS["Critical"],
            workflow_status="resolved",
            research_goal="Other",
            reason="Researcher considers this case finished",
        )

        changes_before = db.total_changes
        inbox = research_inbox(db)
        assert db.total_changes == changes_before

        first = next(row for row in inbox if row["curve_id"] == open_curve)
        resolved = next(row for row in inbox if row["curve_id"] == resolved_curve)
        assert first["workflow_status"] == "open"
        assert first["workflow_priority_label"] == "High"
        assert first["workflow_goal"] == "Find another independent point"
        assert resolved["workflow_status"] == "resolved"
        assert resolved["workflow_priority_label"] == "Critical"

        # Human workflow status never rewrites the derived mathematical queue.
        assert first["needs_attention"] is True
        assert resolved["needs_attention"] is True
        assert inbox.index(first) < inbox.index(resolved)
    finally:
        db.close()


def test_research_timeline_omits_dormant_case_state_without_deleting_it(tmp_path):
    db = connect(tmp_path / "case-timeline.db")
    try:
        ensure_ui_schema(db)
        curve_id = _curve(db, "timeline")
        saved = save_analysis_case(
            db,
            curve_id=curve_id,
            priority=CASE_PRIORITY_PRESETS["Normal"],
            workflow_status="deferred",
            research_goal="Understand Mordell–Weil geometry",
            reason="Waiting for a larger lattice run",
            note="Human workflow note only.",
        )

        changes_before = db.total_changes
        timeline = research_timeline(db, curve_id)
        assert db.total_changes == changes_before
        assert all(row["source"] != "Research Case" for row in timeline)
        assert get_analysis_case(db, curve_id)["id"] == saved["id"]
    finally:
        db.close()


def test_case_board_editor_writes_only_after_explicit_form_submit():
    source = inspect.getsource(analyze_page._case_state_editor)

    before_submit, after_submit = source.split("if submitted:", 1)
    assert "with st.form(" in before_submit
    assert "save_analysis_case(" not in before_submit
    assert "save_analysis_case(" in after_submit
    assert "Human workflow only" in source
    assert "never changes rank bounds" in source
