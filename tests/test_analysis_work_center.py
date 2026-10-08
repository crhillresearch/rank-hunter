from rank42.analysis_method_history import basis_fingerprint_from_curve_row
from rank42.analysis_case_store import save_analysis_case
import inspect
import json

from rank42.analyze_workspace import (
    case_board,
    curve_analysis_snapshot,
    research_inbox,
    research_inbox_default_curve_id,
)
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.manage_store import (
    create_campaign,
    ensure_manage_schema,
    get_campaign,
    pin_campaign_curve,
    set_current_campaign,
)
from rank42.points import set_point_hard_flag, upsert_point
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    upsert_pipeline_candidate,
)
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages import analyze_page, curves as curves_page
from rank42.ui_store import ensure_ui_schema


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter, *, lower=2, baseline=2):
    curve_id = upsert_curve(
        db,
        family="work-center",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )
    update_curve(
        db,
        curve_id,
        generic_lower=baseline,
        descent_lower=lower if lower > baseline else None,
    )
    return curve_id


def _rank_lower(db, curve_id, lower):
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "work-center-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": int(lower),
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )


def _candidate(db, curve_id, x, *, status="unknown", hard=False):
    rec = upsert_point(
        db,
        curve_id=curve_id,
        x=str(x),
        y="1",
        source="work-center-test",
        role="candidate_extra",
        exact_verified=True,
        independence_status=status,
        rigorous_independent=False,
    )
    if hard:
        set_point_hard_flag(db, int(rec["id"]), True)
    return rec


def test_research_inbox_prioritizes_active_campaign_unresolved_curve(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        ensure_manage_schema(db)
        ordinary = _curve(db, "ordinary", lower=6, baseline=2)
        campaign_curve = _curve(db, "campaign", lower=3, baseline=2)
        _candidate(db, ordinary, 10)
        _candidate(db, campaign_curve, 11)

        campaign_id = create_campaign(
            db,
            name="Rank 8 campaign",
            objective="Find rank >= 8",
            target_rank=8,
        )
        pin_campaign_curve(db, campaign_id, campaign_curve)
        set_current_campaign(db, campaign_id)

        inbox = research_inbox(db)

        assert inbox[0]["curve_id"] == campaign_curve
        assert inbox[0]["active_campaign"] is True
        assert inbox[0]["active_campaign_name"] == "Rank 8 campaign"
        assert inbox[0]["campaign_target_rank"] == 8
        assert inbox[0]["campaign_target_gap"] == 5
        assert inbox[0]["next_action"] is not None
        ordinary_row = next(row for row in inbox if row["curve_id"] == ordinary)
        assert ordinary_row["active_campaign"] is False
    finally:
        db.close()


def test_research_inbox_traces_active_campaign_pipeline_curve_without_pin(tmp_path):
    db = connect(tmp_path / "analysis-pipeline-campaign.db")
    try:
        ensure_manage_schema(db)
        ensure_pipeline_schema(db)
        campaign_curve = _curve(db, "pipeline-campaign", lower=5, baseline=2)
        ordinary = _curve(db, "pipeline-ordinary", lower=7, baseline=2)

        campaign_id = create_campaign(
            db,
            name="Pipeline Campaign",
            objective="Push retained candidates",
            target_rank=8,
        )
        campaign = get_campaign(db, campaign_id)
        set_current_campaign(db, campaign_id)

        run_id = create_pipeline_run(
            db,
            pipeline_name="Campaign Pipeline",
            target_mode="family",
            target={"family": "work-center"},
            stages=[],
            run_config={
                "campaign_id": campaign_id,
                "campaign_created_at": str(campaign["created_at"]),
            },
        )
        upsert_pipeline_candidate(
            db,
            run_id=run_id,
            parameter="pipeline-campaign",
            curve_id=campaign_curve,
            status="completed",
            current_stage_index=0,
            current_stage_id="rank_bounds",
            rigorous_lower=5,
        )

        changes_before = db.total_changes
        inbox = research_inbox(db)
        assert db.total_changes == changes_before
        campaign_row = next(row for row in inbox if row["curve_id"] == campaign_curve)
        ordinary_row = next(row for row in inbox if row["curve_id"] == ordinary)

        assert campaign_row["active_campaign"] is True
        assert campaign_row["queue_bucket"] == "active_campaign"
        assert campaign_row["campaign_target_gap"] == 3
        assert campaign_row["campaign_rank_growth_lower"] == 3
        assert campaign_row["campaign_followup_owner"]
        assert campaign_row["campaign_followup_exhausted"] is False
        assert ordinary_row["active_campaign"] is False
        assert inbox.index(campaign_row) < inbox.index(ordinary_row)
    finally:
        db.close()


def test_campaign_inbox_reports_rank_proof_exhaustion_separately(tmp_path):
    db = connect(tmp_path / "analysis-campaign-proof-history.db")
    try:
        ensure_manage_schema(db)
        curve_id = _curve(db, "campaign-proof", lower=1, baseline=1)
        update_curve(
            db,
            curve_id,
            descent_lower=1,
            generators_json='[["0","0"]]',
        )
        upsert_point(
            db,
            curve_id=curve_id,
            x="0",
            y="0",
            source="campaign-proof-test",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
        )

        campaign_id = create_campaign(
            db,
            name="Proof Exhaustion Campaign",
            objective="Classify retained curve",
            target_rank=4,
        )
        pin_campaign_curve(db, campaign_id, curve_id)
        set_current_campaign(db, campaign_id)

        fingerprint = basis_fingerprint_from_curve_row(get_curve(db, curve_id))
        for method, budget in (
            ("descent:mwrank_selmer", {"timeout": 300}),
            ("descent:simon_known", {"timeout": 300}),
            ("descent:mwrank_coverings", {"timeout": 300}),
            ("sage_proof_rank", {"timeout": 180}),
        ):
            record_rank_evidence(
                db,
                curve_id=curve_id,
                model=MODEL,
                data={
                    "engine": "rank42.hard_case_escalator",
                    "evidence_type": "hard_case_stage",
                    "status": "timeout",
                    "rigorous": False,
                    "rigorous_lower": None,
                    "rigorous_upper": None,
                    "exact_rank": None,
                    "assumptions": [],
                    "points_found": [],
                    "timed_out": True,
                    "options": {
                        "method": method,
                        "basis_fingerprint": fingerprint,
                        "budget": budget,
                    },
                },
            )

        row = next(rec for rec in research_inbox(db) if rec["curve_id"] == curve_id)

        assert row["active_campaign"] is True
        assert row["campaign_needs_rank_proof"] is True
        assert row["campaign_proof_methods_exhausted"] is True
        assert row["campaign_stronger_proof_budget_available"] is False
        assert row["next_action"] is not None
    finally:
        db.close()


def test_analysis_case_campaign_context_does_not_create_campaign_lineage(tmp_path):
    db = connect(tmp_path / "analysis-case-campaign-separation.db")
    try:
        ensure_ui_schema(db)
        ensure_manage_schema(db)
        curve_id = _curve(db, "case-only-campaign", lower=4, baseline=2)
        campaign_id = create_campaign(
            db,
            name="Separation Campaign",
            objective="Verify ownership boundaries",
            target_rank=8,
        )
        set_current_campaign(db, campaign_id)
        save_analysis_case(
            db,
            curve_id=curve_id,
            campaign_id=campaign_id,
            priority=200,
            workflow_status="open",
            research_goal="Find another independent point",
            reason="Human context only",
        )

        row = next(rec for rec in research_inbox(db) if rec["curve_id"] == curve_id)

        assert row["workflow_campaign_id"] == campaign_id
        assert row["active_campaign"] is False
        assert row["active_campaign_id"] is None
        assert row["queue_bucket"] == "recent_unresolved"
        assert row["campaign_target_gap"] is None
    finally:
        db.close()


def test_research_inbox_default_prefers_campaign_unresolved_on_fresh_visit():
    inbox = [
        {"curve_id": 11, "queue_bucket": "active_campaign"},
        {"curve_id": 22, "queue_bucket": "recent_unresolved"},
        {"curve_id": 33, "queue_bucket": "backlog"},
    ]

    assert research_inbox_default_curve_id(
        inbox,
        explicit_analyze_curve_id=None,
        semantic_curve_id=22,
    ) == 11

    # Once the researcher explicitly chooses an Analyze-local case, preserve it.
    assert research_inbox_default_curve_id(
        inbox,
        explicit_analyze_curve_id=22,
        semantic_curve_id=11,
    ) == 22


def test_research_inbox_default_falls_back_to_recent_then_backlog():
    unresolved = [
        {"curve_id": 22, "queue_bucket": "recent_unresolved"},
        {"curve_id": 33, "queue_bucket": "backlog"},
    ]
    backlog = [{"curve_id": 33, "queue_bucket": "backlog"}]

    assert research_inbox_default_curve_id(
        unresolved,
        explicit_analyze_curve_id=None,
        semantic_curve_id=None,
    ) == 22
    assert research_inbox_default_curve_id(
        backlog,
        explicit_analyze_curve_id=None,
        semantic_curve_id=None,
    ) == 33


def test_research_inbox_uses_authoritative_rank_state_and_attention_counts(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        curve_id = _curve(db, "authority", lower=2, baseline=1)
        _rank_lower(db, curve_id, 5)
        first = _candidate(db, curve_id, 20, status="numerical_novel", hard=True)
        _candidate(db, curve_id, 21, status="unknown")

        inbox = research_inbox(db)
        row = next(rec for rec in inbox if rec["curve_id"] == curve_id)

        assert row["rigorous_lower"] == 5
        assert row["family_section_lower"] == 1
        assert row["extra_directions_lower"] == 4
        assert row["unresolved_exact_points"] == 2
        assert row["numerical_novel_points"] == 1
        assert row["hard_cases"] == 1
        assert row["witness_gap"] == 5
        assert "2 unresolved point(s)" in row["attention_reasons"]
        assert "1 hard case(s)" in row["attention_reasons"]
        assert row["next_action"] == "Repair the replayable witness basis"

        stored = db.execute("SELECT hard_flag FROM points WHERE id=?", (int(first["id"]),)).fetchone()
        assert int(stored["hard_flag"]) == 1
    finally:
        db.close()


def test_research_inbox_ignores_resolved_hard_bookmarks(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        curve_id = _curve(db, "resolved-hard", lower=1, baseline=1)
        rec = _candidate(db, curve_id, 22, status="dependent", hard=True)

        row = next(
            item for item in research_inbox(db)
            if item["curve_id"] == curve_id
        )
        assert row["hard_cases"] == 0

        snapshot = curve_analysis_snapshot(db, curve_id)
        assert case_board(snapshot)["material"]["hard_cases"] == 0
        stored = db.execute(
            "SELECT hard_flag,independence_status FROM points WHERE id=?",
            (int(rec["id"]),),
        ).fetchone()
        assert int(stored["hard_flag"]) == 1
        assert stored["independence_status"] == "dependent"
    finally:
        db.close()


def test_research_inbox_is_read_only_and_does_not_launch_jobs(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        ensure_manage_schema(db)
        _curve(db, "readonly", lower=2)
        jobs_before = db.execute("SELECT COUNT(*) AS n FROM ui_jobs").fetchone()["n"]
        changes_before = db.total_changes

        inbox = research_inbox(db)

        jobs_after = db.execute("SELECT COUNT(*) AS n FROM ui_jobs").fetchone()["n"]
        assert inbox
        assert jobs_after == jobs_before
        assert db.total_changes == changes_before
    finally:
        db.close()


def test_case_board_is_actionable_projection_not_new_scientific_state(tmp_path):
    db = connect(tmp_path / "analysis.db")
    try:
        curve_id = _curve(db, "case", lower=3, baseline=2)
        _candidate(db, curve_id, 30, status="numerical_novel", hard=True)

        snapshot = curve_analysis_snapshot(db, curve_id)
        board = case_board(snapshot)

        assert board["curve_id"] == curve_id
        assert board["rank"]["rigorous_lower"] == snapshot["state"]["rigorous_lower"]
        assert board["subgroup"]["witness_gap"] == snapshot["witness_gap"]
        assert board["material"]["unresolved_exact_points"] == 1
        assert board["material"]["numerical_novel_points"] == 1
        assert board["material"]["hard_cases"] == 1
        assert board["primary_action"]["title"] == "Repair the replayable witness basis"
        assert board["planner_version"] >= 2
    finally:
        db.close()


def test_analyze_page_defaults_to_research_inbox_and_defers_full_snapshot():
    source = inspect.getsource(analyze_page.page)

    assert '"Research Inbox"' in source
    assert '"Case Board"' in source
    assert '"Timeline"' in source
    assert '"Rank Jump"' in source
    assert '"Rank Evidence"' not in source[source.index("sections = ["):source.index("pending =", source.index("sections = ["))]
    assert '"Points"' not in source[source.index("sections = ["):source.index("pending =", source.index("sections = ["))]
    assert '"Lattice"' not in source[source.index("sections = ["):source.index("pending =", source.index("sections = ["))]
    assert '"Search History"' not in source[source.index("sections = ["):source.index("pending =", source.index("sections = ["))]
    assert '"Raw"' not in source[source.index("sections = ["):source.index("pending =", source.index("sections = ["))]
    assert 'or "Research Inbox"' in source
    assert 'if active == "Research Inbox":' in source
    inbox_branch, remainder = source.split('if active == "Research Inbox":', 1)
    branch, after_return = remainder.split("return", 1)
    assert "research_inbox(db)" in branch
    assert "research_inbox_default_curve_id(" in branch
    assert '"analysis_inbox_campaign_context_id"' in branch
    assert "campaign_changed" in branch
    assert '"Active Campaign unresolved"' in inspect.getsource(analyze_page._research_inbox)
    assert '"Recent unresolved"' in inspect.getsource(analyze_page._research_inbox)
    assert '"Global backlog"' in inspect.getsource(analyze_page._research_inbox)
    assert "curve_analysis_snapshot(" not in branch
    assert "curve_analysis_snapshot(db, int(row[\"id\"]))" in after_return



def test_curve_detail_retires_case_workflow_but_keeps_computed_next_action():
    detail = inspect.getsource(curves_page._render_curve_detail)
    overview = inspect.getsource(curves_page._render_overview)
    next_action = inspect.getsource(curves_page._render_next_action)

    assert 'detail_view == "Research"' not in detail
    assert "render_curve_research_workflow" not in detail
    assert "_render_next_action(db, row)" in overview
    assert "curve_analysis_snapshot(db, int(row[\"id\"]))" in next_action
    assert "planner_immediate_actions(analysis_plan(snapshot))" in next_action
    assert "save_analysis_case" not in next_action
