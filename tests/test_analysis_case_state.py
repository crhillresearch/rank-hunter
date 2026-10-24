import inspect

import pytest

from rank42.analysis_case_store import (
    CASE_PRIORITY_PRESETS,
    CASE_RESEARCH_GOALS,
    CASE_WORKFLOW_STATUSES,
    add_case_entry,
    analysis_case_map_readonly,
    get_analysis_case,
    list_case_entries,
    save_analysis_case,
    set_case_entry_outcome,
)
from rank42.analyze_workspace import research_inbox
from rank42.db import CURRENT_SCHEMA_VERSION, connect, upsert_curve
from rank42.research_timeline import research_timeline
from rank42.schema_manifest import UI_SCHEMA_MANIFEST, inspect_schema
from rank42.ui_pages import analysis_workcenter, analyze_page
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


def test_casebook_journal_is_durable_and_never_promotes_rank(tmp_path):
    db = connect(tmp_path / "case-journal.db")
    try:
        ensure_ui_schema(db)
        curve_id = _curve(db, "journal")
        before_evidence = db.execute(
            "SELECT COUNT(*) FROM rank_evidence WHERE curve_id=?", (curve_id,)
        ).fetchone()[0]
        entry = add_case_entry(
            db, curve_id=curve_id, entry_type="Experiment", title="Try a covering",
            body="Search H=1000, then map any point back.", artifact_ref="job:94",
        )
        assert entry["outcome"] == "open"
        set_case_entry_outcome(db, entry["id"], curve_id=curve_id, outcome="inconclusive")
        rows = list_case_entries(db, curve_id)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "inconclusive"
        assert rows[0]["artifact_ref"] == "job:94"
        assert db.execute(
            "SELECT COUNT(*) FROM rank_evidence WHERE curve_id=?", (curve_id,)
        ).fetchone()[0] == before_evidence
        assert any(row["source"] == "Casebook" for row in research_timeline(db, curve_id))
    finally:
        db.close()


def test_casebook_journal_migrates_existing_ui_database(tmp_path):
    db = connect(tmp_path / "case-journal-migration.db")
    try:
        ensure_ui_schema(db)
        db.execute("DROP INDEX idx_analysis_case_entries_curve")
        db.execute("DROP TABLE analysis_case_entries")
        db.commit()
        assert not inspect_schema(db, UI_SCHEMA_MANIFEST)["ready"]
        ensure_ui_schema(db)
        assert inspect_schema(db, UI_SCHEMA_MANIFEST)["ready"]
    finally:
        db.close()


def test_casebook_export_keeps_research_notes_distinct_from_evidence(tmp_path):
    db = connect(tmp_path / "case-export.db")
    try:
        ensure_ui_schema(db)
        curve_id = _curve(db, "export")
        case = save_analysis_case(db, curve_id=curve_id, reason="Can this rank grow?")
        add_case_entry(db, curve_id=curve_id, entry_type="Hypothesis",
                       title="A second section may exist", body="Check low-degree ansatz.")
        from rank42.analyze_workspace import curve_analysis_snapshot
        output = analysis_workcenter._casebook_markdown(
            curve_analysis_snapshot(db, curve_id), case, list_case_entries(db, curve_id)
        )
        assert "Can this rank grow?" in output
        assert "A second section may exist" in output
        assert "only Rank Hunter's evidence ledger certifies" in output
    finally:
        db.close()


def test_casebook_history_renders_multiple_events_for_same_point(tmp_path):
    from streamlit.testing.v1 import AppTest
    from rank42.points import upsert_point

    path = tmp_path / "case-history.db"
    db = connect(path)
    try:
        ensure_ui_schema(db)
        curve_id = _curve(db, "history")
        point = upsert_point(db, curve_id=curve_id, x="0", y="0", source="test",
                             enrich_torsion=False)
        db.execute("UPDATE points SET updated_at='2099-01-01T00:00:00+00:00' WHERE id=?",
                   (point["id"],))
        db.commit()
        events = [row for row in research_timeline(db, curve_id)
                  if row["artifact_ref"] == f"point:{point['id']}"]
        assert len(events) == 2
    finally:
        db.close()

    app = AppTest.from_string(
        "from rank42.db import connect\n"
        "from rank42.ui_pages.analysis_workcenter import page\n"
        "class Context: pass\n"
        f"page(connect({str(path)!r}), Context())\n",
        default_timeout=10,
    )
    app.session_state["analysis_workcenter_curve_id"] = curve_id
    app.session_state["casebook-section"] = "History"
    app.run()
    assert not app.exception
