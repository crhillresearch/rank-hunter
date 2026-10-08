import json

from rank42.auto_search_state import create_campaign, ensure_auto_search_schema, upsert_trial
from rank42.db import connect, log_event, upsert_curve
from rank42.lattice_store import record_covering_search_attempt, store_covering, store_lattice
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    upsert_pipeline_candidate,
)
from rank42.points import record_point_discovery, set_point_hard_flag, upsert_point
from rank42.quartic_store import create_or_get_search, finish_search
from rank42.rank_evidence import record_rank_evidence
from rank42.research_timeline import research_timeline
from rank42.ui_store import create_job, ensure_ui_schema


def _curve(db, parameter="1"):
    return upsert_curve(
        db,
        family="timeline-family",
        parameter=str(parameter),
        a_invariants_json='["0","1","1","-2","0"]',
    )


def test_research_timeline_merges_durable_sources_without_writes(tmp_path):
    db = connect(tmp_path / "timeline.db")
    curve_id = _curve(db)

    point = upsert_point(
        db,
        curve_id=curve_id,
        x="0",
        y="0",
        source="fixture_search",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
        search_ref="fixture:point",
    )
    db.execute(
        "UPDATE points SET created_at=?,updated_at=? WHERE id=?",
        ("2026-09-27T10:00:00+00:00", "2026-09-27T10:05:00+00:00", int(point["id"])),
    )
    set_point_hard_flag(db, int(point["id"]), True, reason="fixture hard case")
    db.execute(
        "UPDATE points SET hard_flagged_at=?,updated_at=? WHERE id=?",
        ("2026-09-27T10:06:00+00:00", "2026-09-27T10:06:00+00:00", int(point["id"])),
    )
    discovery = record_point_discovery(
        db,
        curve_id=curve_id,
        point_id=int(point["id"]),
        source="fixture_search",
        outcome="mapped_exact",
        tier="target",
        search_ref="fixture:point",
        height=1000,
        exact_verified=True,
    )
    db.execute(
        "UPDATE point_discoveries SET created_at=? WHERE id=?",
        ("2026-09-27T10:04:00+00:00", int(discovery["id"])),
    )

    evidence_id = record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "1", "1", "-2", "0"],
        data={
            "engine": "fixture-proof",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": 1,
            "rigorous_upper": 3,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [],
            "options": {"fixture": True},
        },
    )
    db.execute(
        "UPDATE rank_evidence SET created_at=?,updated_at=?,finished_at=? WHERE id=?",
        (
            "2026-09-27T10:10:00+00:00",
            "2026-09-27T10:11:00+00:00",
            "2026-09-27T10:12:00+00:00",
            evidence_id,
        ),
    )

    hard_stage_id = record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "1", "1", "-2", "0"],
        data={
            "engine": "rank42.hard_case_escalator",
            "engine_version": "fixture",
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
                "method": "trial_saturation",
                "point_id": int(point["id"]),
                "budget": {"timeout": 30},
            },
        },
    )
    db.execute(
        "UPDATE rank_evidence SET created_at=?,updated_at=?,finished_at=? WHERE id=?",
        (
            "2026-09-27T10:13:00+00:00",
            "2026-09-27T10:14:00+00:00",
            "2026-09-27T10:15:00+00:00",
            hard_stage_id,
        ),
    )

    lattice = store_lattice(
        db,
        curve_id=curve_id,
        source="point-ledger",
        basis=[["0", "0"]],
        gram=[[1.25]],
        precision_bits=128,
        determinant="1.25",
        min_eigenvalue="1.25",
        positive_definite_screen=True,
        status="screened",
    )
    db.execute(
        "UPDATE mw_lattices SET created_at=?,updated_at=? WHERE id=?",
        ("2026-09-27T10:20:00+00:00", "2026-09-27T10:21:00+00:00", int(lattice["id"])),
    )

    quartic = create_or_get_search(
        db,
        curve_id=curve_id,
        family="timeline-family",
        parameter="1",
        hole_label="fixture",
        coefficients=["1", "0", "1"],
        integer_coefficients=[1, 0, 1],
        y_scale=1,
        degree=2,
        height_bound=1000,
        metadata={"schema": "fixture.quartic"},
    )
    finish_search(db, quartic["id"], status="timeout", runtime=2.5, error="fixture timeout")
    db.execute(
        "UPDATE quartic_searches SET created_at=?,updated_at=?,finished_at=? WHERE id=?",
        (
            "2026-09-27T10:30:00+00:00",
            "2026-09-27T10:31:00+00:00",
            "2026-09-27T10:32:00+00:00",
            int(quartic["id"]),
        ),
    )

    covering = store_covering(
        db,
        {
            "schema": "rank42.covering.v1",
            "curve_id": curve_id,
            "quartic": {
                "coefficients": ["1", "0", "0", "0", "1"],
                "height": 5000,
            },
            "map": {"x": "u", "y": "v"},
            "metadata": {"fixture": True},
        },
    )
    attempt_id = record_covering_search_attempt(
        db,
        covering_id=int(covering["id"]),
        curve_id=curve_id,
        pipeline_run_id=None,
        pipeline_stage_index=None,
        pipeline_stage_id="analysis_quartics",
        height=5000,
        timeout_seconds=60,
        backend="ratpoints",
        one_point=False,
        outcome="timeout",
        runtime_seconds=60.0,
        error="fixture covering timeout",
    )
    db.execute(
        "UPDATE coverings SET created_at=?,updated_at=? WHERE id=?",
        ("2026-09-27T10:33:00+00:00", "2026-09-27T10:34:00+00:00", int(covering["id"])),
    )
    db.execute(
        "UPDATE covering_search_attempts SET created_at=? WHERE id=?",
        ("2026-09-27T10:35:00+00:00", int(attempt_id)),
    )

    ensure_auto_search_schema(db)
    auto_campaign_id = create_campaign(
        db,
        plugin_id="fixture-plugin",
        plugin_version="1",
        variant_id="default",
        family_spec="fixture.family",
        family_name="Fixture Family",
        target_rank=5,
    )
    auto_trial = upsert_trial(
        db,
        campaign_id=auto_campaign_id,
        parameter="1",
        curve_id=curve_id,
        status="completed",
        tier="geometry",
        rigorous_lower=1,
        exact_points=1,
        rank_growth=1,
    )
    db.execute(
        "UPDATE auto_search_trials SET created_at=?,updated_at=? WHERE id=?",
        (
            "2026-09-27T10:36:00+00:00",
            "2026-09-27T10:37:00+00:00",
            int(auto_trial["id"]),
        ),
    )

    ensure_pipeline_schema(db)
    run_id = create_pipeline_run(
        db,
        pipeline_name="Timeline Pipeline",
        target_mode="curve",
        target={"curve_id": curve_id},
        stages=[],
        run_config={},
    )
    candidate = upsert_pipeline_candidate(
        db,
        run_id=run_id,
        parameter="1",
        curve_id=curve_id,
        status="completed",
        current_stage_index=0,
        current_stage_id="pointed_quartic",
        rigorous_lower=1,
    )
    db.execute(
        "UPDATE search_pipeline_candidates SET created_at=?,updated_at=? WHERE id=?",
        (
            "2026-09-27T10:40:00+00:00",
            "2026-09-27T10:41:00+00:00",
            int(candidate["id"]),
        ),
    )

    ensure_ui_schema(db)
    job_id = create_job(
        db,
        kind="quartic",
        label="Timeline fixture job",
        command=["sage", "-m", "rank42.quartic_workbench_runner", "--curve-id", str(curve_id)],
        cwd=tmp_path,
        log_path=tmp_path / "timeline.log",
        metadata={"curve_id": curve_id},
    )
    db.execute(
        "UPDATE ui_jobs SET created_at=?,updated_at=?,finished_at=?,status='succeeded' WHERE id=?",
        (
            "2026-09-27T10:50:00+00:00",
            "2026-09-27T10:51:00+00:00",
            "2026-09-27T10:52:00+00:00",
            job_id,
        ),
    )

    log_event(db, curve_id, "info", "timeline fixture event")
    event = db.execute("SELECT * FROM events WHERE curve_id=? ORDER BY id DESC LIMIT 1", (curve_id,)).fetchone()
    db.execute(
        "UPDATE events SET created_at=? WHERE id=?",
        ("2026-09-27T11:00:00+00:00", int(event["id"])),
    )
    db.commit()

    before = db.total_changes
    rows = research_timeline(db, curve_id)
    after = db.total_changes

    assert after == before
    assert rows
    timestamps = [row["timestamp"] for row in rows]
    assert timestamps == sorted(timestamps, reverse=True)
    assert rows[0]["source"] == "Event Log"
    sources = {row["source"] for row in rows}
    assert {
        "Point Ledger",
        "Point Search",
        "Independence",
        "Rank Evidence",
        "MW Geometry",
        "Quartics",
        "Auto Search",
        "Pipeline",
        "Jobs",
        "Event Log",
    }.issubset(sources)
    assert any(row["artifact_ref"] == f"rank_evidence:{evidence_id}" for row in rows)
    assert any(
        row["artifact_ref"] == f"rank_evidence:{hard_stage_id}"
        and row["kind"] == "hard_case_stage"
        and row["status"] == "timeout"
        for row in rows
    )
    assert any(row["artifact_ref"] == f"ui_job:{job_id}" for row in rows)
    assert any(
        row["artifact_ref"] == f"point_discovery:{int(discovery['id'])}"
        and row["status"] == "mapped_exact"
        for row in rows
    )
    assert any(
        row["kind"] == "hard_case_bookmark"
        and row["artifact_ref"] == f"point:{int(point['id'])}"
        for row in rows
    )
    assert any(
        row["artifact_ref"] == f"covering_attempt:{int(attempt_id)}"
        and row["status"] == "timeout"
        for row in rows
    )


def test_research_timeline_filters_unrelated_curve_jobs(tmp_path):
    db = connect(tmp_path / "timeline-jobs.db")
    curve_id = _curve(db, "1")
    other_id = _curve(db, "2")
    ensure_ui_schema(db)
    mine = create_job(
        db,
        kind="target",
        label="mine",
        command=["sage", "--curve-id", str(curve_id)],
        cwd=tmp_path,
        log_path=tmp_path / "mine.log",
        metadata={},
    )
    other = create_job(
        db,
        kind="target",
        label="other",
        command=["sage", "--curve-id", str(other_id)],
        cwd=tmp_path,
        log_path=tmp_path / "other.log",
        metadata={"curve_id": other_id},
    )

    rows = research_timeline(db, curve_id)
    job_ids = {
        int(row["artifact_id"])
        for row in rows
        if row["artifact_type"] == "ui_job"
    }

    assert mine in job_ids
    assert other not in job_ids


def test_analysis_work_center_exposes_timeline_without_eager_snapshot():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "ui_pages" / "analyze_page.py").read_text(encoding="utf-8")

    assert '"Timeline",' in source
    assert "research_timeline(db, int(curve_id), limit=500)" in source
    assert 'if active == "Timeline":' in source
    timeline_branch = source.split('if active == "Timeline":', 1)[1].split("else:", 1)[0]
    assert "curve_analysis_snapshot(" not in timeline_branch
    assert "Timeline rows reference their owning records" in source
