from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import inspect

import pytest

from rank42.analysis_planner import PLANNER_VERSION
from rank42.db import connect, update_curve, upsert_curve
from rank42.manage_store import (
    active_campaign,
    current_campaign,
    add_campaign_note,
    attach_job_to_campaign,
    attach_pipeline_run_to_campaign,
    campaign_artifacts,
    campaign_curve_ids,
    campaign_pinned_curve_ids,
    campaign_handoff_markdown,
    campaign_notes,
    campaign_research_brief,
    campaign_snapshot,
    create_campaign,
    create_schedule,
    delete_campaign,
    delete_schedule,
    delete_terminal_job,
    dispatch_due_schedules,
    ensure_manage_schema,
    get_campaign,
    job_campaign_id,
    list_campaigns,
    list_schedules,
    pin_campaign_curve,
    pipeline_run_campaign_id,
    list_schedule_occurrences,
    retry_job,
    set_active_campaign,
    set_current_campaign,
    unpin_campaign_curve,
    update_campaign,
    update_schedule,
)
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline,
    get_pipeline_run,
    pipeline_payload,
    pipeline_run_payload,
    save_pipeline,
    update_pipeline_run,
)
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages import common
from rank42.ui_pages.build_your_own import (
    _campaign_context_token,
    _campaign_family_lane_key,
    _saved_family_lane_key,
    _saved_pipeline_run_settings,
    _saved_pipeline_target,
)
from rank42.ui_pages.campaigns_page import (
    _campaign_action_button_state,
    _campaign_family_choices,
    _overview,
)
from rank42.ui_pages.common import UIContext
from rank42.ui_store import (
    claim_next_queue_item,
    create_job,
    ensure_ui_schema,
    finalize_queue_for_job,
    get_application_state,
    get_job,
    mark_queue_running,
    queue_item_for_job,
    update_job,
)


def _db(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    ensure_ui_schema(db)
    ensure_manage_schema(db)
    return db, path


def _fake_job(db, tmp_path, *, status="succeeded", metadata=None, kind="test"):
    log = tmp_path / "job.log"
    log.write_text("test\n")
    job_id = create_job(
        db,
        kind=kind,
        label="Test search",
        command=["python", "-c", "print('ok')"],
        cwd=tmp_path,
        log_path=log,
        metadata=metadata or {},
    )
    update_job(
        db,
        job_id,
        status=status,
        exit_code=0 if status == "succeeded" else 1,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    return job_id, log



def test_jobs_owns_results_and_reuses_scientific_result_renderer():
    import rank42.ui as ui
    from rank42.ui_pages import jobs_detail_panel, jobs_page, results_page

    jobs_source = inspect.getsource(jobs_page.page)
    detail_source = inspect.getsource(jobs_detail_panel.render_job_detail)
    shared_source = inspect.getsource(results_page.render_job_scientific_result)
    raw_log_source = inspect.getsource(results_page._render_job_raw_log)
    results_source = inspect.getsource(results_page.render_jobs_results)

    assert '["Active", "Queue", "History", "Results", "Scheduled"]' in jobs_source
    assert "results_page.render_jobs_results(db, ctx)" in jobs_source
    assert "results_page.render_job_scientific_result(" in detail_source

    assert "_hunt_recap(db, row, summary, science_python)" in shared_source
    assert "_render_job_raw_log(db, row)" in shared_source
    assert "'Parsed scientific result'" not in shared_source
    assert 'st.markdown("#### Raw log")' in raw_log_source
    assert 'render_raw_log(read_log(row["log_path"]))' in raw_log_source
    assert "_scoreboard(db,pairs)" in results_source
    assert "'Interesting only'" in results_source
    assert "_detail(db,row,s,ctx.detected_science_python())" in results_source

    state = {"rh_page": "Results"}
    assert ui._normalize_route("Results", state) == "Jobs"
    assert state["rh_page"] == "Jobs"
    assert state["jobs_section_pending"] == "Results"



def test_jobs_tabs_flow_directly_into_content_without_spacing_divider():
    from rank42.ui_pages import jobs_page

    source = inspect.getsource(jobs_page.page)
    tabs_pos = source.index("active = tabs(")
    content_pos = source.index('if active == "Active":', tabs_pos)
    between = source[tabs_pos:content_pos]

    assert 'st.session_state["jobs_section"] = active' in between
    assert 'st.html("<div style=\'height:.85rem\'></div>")' in between
    assert "st.divider()" not in between
    assert "margin-top:-" not in between
    assert "margin-bottom:-" not in between

def test_campaign_overview_receives_ui_context():
    params = list(inspect.signature(_overview).parameters)
    assert params == ["db", "ctx", "snapshot", "brief"]
    assert "ctx" not in inspect.getclosurevars(_overview).unbound





def test_saved_pipeline_run_settings_restore_global_controls():
    payload = {
        "config": {
            "target_rank": 32,
            "retention_floor": 18,
            "certificate_timeout": 420,
            "exact_candidates": 256,
            "ratpoints_backend": "GPU",
        }
    }
    assert _saved_pipeline_run_settings(payload) == {
        "target_rank": 32,
        "retention_floor": 18,
        "certificate_timeout": 420,
        "exact_candidates": 256,
        "ratpoints_backend": "GPU",
    }


def test_saved_pipeline_run_settings_have_safe_legacy_defaults():
    assert _saved_pipeline_run_settings({"config": {}}) == {
        "target_rank": None,
        "retention_floor": 0,
        "certificate_timeout": 120,
        "exact_candidates": 64,
        "ratpoints_backend": None,
    }

def test_saved_pipeline_family_target_restores_exact_lane():
    payload = {
        "target_mode": "family",
        "config": {
            "target": {
                "plugin_id": "icarm_rank31_302",
                "variant_id": "recovered_mw17_s",
            }
        },
    }
    assert _saved_pipeline_target(payload) == {
        "plugin_id": "icarm_rank31_302",
        "variant_id": "recovered_mw17_s",
    }
    assert _saved_family_lane_key(payload) == (
        "icarm_rank31_302:recovered_mw17_s"
    )


def test_saved_pipeline_without_target_remains_backward_compatible():
    payload = {"target_mode": "family", "config": {}}
    assert _saved_pipeline_target(payload) == {}
    assert _saved_family_lane_key(payload) is None

def test_pipeline_campaign_family_lane_matches_canonical_family():
    plugin = SimpleNamespace(id="icarm_rank31_302")
    recovered = SimpleNamespace(
        id="recovered_mw17_s",
        curve_family_name="ICARM #302 recovered X1092 MW17 parent",
    )
    native = SimpleNamespace(
        id="native_t",
        curve_family_name="ICARM #302 source family — native T",
    )
    lanes = [
        (0, "", "", "icarm_rank31_302:recovered_mw17_s", plugin, recovered, {}),
        (0, "", "", "icarm_rank31_302:native_t", plugin, native, {}),
    ]
    campaign = {
        "id": 1,
        "plugin_id": "icarm_rank31_302",
        "variant_id": "recovered_mw17_s",
        "family": "ICARM #302 recovered X1092 MW17 parent",
        "target_rank": 32,
        "updated_at": "2026-09-22T12:00:00+00:00",
    }
    assert _campaign_family_lane_key(campaign, lanes) == (
        "icarm_rank31_302:recovered_mw17_s"
    )
    assert _campaign_context_token(campaign) == (
        "1:icarm_rank31_302:recovered_mw17_s:"
        "ICARM #302 recovered X1092 MW17 parent:32"
    )


def test_pipeline_campaign_family_lane_uses_unique_plugin_fallback():
    plugin = SimpleNamespace(id="single")
    variant = SimpleNamespace(id="default", curve_family_name="Renamed family")
    lanes = [(0, "", "", "single:default", plugin, variant, {})]
    campaign = {"id": 2, "plugin_id": "single", "family": "Older family label"}
    assert _campaign_family_lane_key(campaign, lanes) == "single:default"


def test_pipeline_campaign_family_lane_avoids_ambiguous_plugin_fallback():
    plugin = SimpleNamespace(id="multi")
    lanes = [
        (0, "", "", "multi:a", plugin, SimpleNamespace(id="a", curve_family_name="A"), {}),
        (0, "", "", "multi:b", plugin, SimpleNamespace(id="b", curve_family_name="B"), {}),
    ]
    campaign = {"id": 3, "plugin_id": "multi", "family": "Old missing label"}
    assert _campaign_family_lane_key(campaign, lanes) is None

def test_campaign_family_choices_use_installed_plugin_variants(monkeypatch, tmp_path):
    plugin = SimpleNamespace(
        id="icarm_rank31",
        name="ICARM Rank-31 Families",
        plugin_type="family",
        variants=(
            SimpleNamespace(
                id="rank31_302",
                name="ICARM #302",
                curve_family_name="icarm_302",
            ),
            SimpleNamespace(
                id="rank31_724",
                name="ICARM #724",
                curve_family_name="icarm_724",
            ),
        ),
    )
    broken = {"path": "bad", "error": "invalid"}
    extension = SimpleNamespace(
        id="notes",
        name="Notes",
        plugin_type="extension",
        variants=(),
    )
    monkeypatch.setattr(
        "rank42.ui_pages.campaigns_page.discover_plugins",
        lambda root: [plugin, broken, extension],
    )

    choices = _campaign_family_choices(tmp_path)
    assert choices[0]["plugin_id"] is None
    assert [
        (rec["plugin_id"], rec["variant_id"], rec["family"])
        for rec in choices[1:]
    ] == [
        ("icarm_rank31", "rank31_302", "icarm_302"),
        ("icarm_rank31", "rank31_724", "icarm_724"),
    ]
    assert choices[1]["label"] == "ICARM Rank-31 Families · ICARM #302"


def test_campaign_family_choices_preserve_unavailable_existing_selection(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "rank42.ui_pages.campaigns_page.discover_plugins",
        lambda root: [],
    )
    choices = _campaign_family_choices(
        tmp_path,
        current_plugin_id="missing_plugin",
        current_family="historic_family",
    )
    assert choices[-1] == {
        "key": "__existing__",
        "plugin_id": "missing_plugin",
        "variant_id": None,
        "family": "historic_family",
        "label": "Current unavailable selection · missing_plugin / historic_family",
    }


def test_campaign_single_variant_family_falls_back_to_plugin_name(
    monkeypatch, tmp_path
):
    plugin = SimpleNamespace(
        id="single",
        name="Single Family",
        plugin_type="family",
        variants=(
            SimpleNamespace(
                id="default",
                name="Single Family",
                curve_family_name="",
            ),
        ),
    )
    monkeypatch.setattr(
        "rank42.ui_pages.campaigns_page.discover_plugins",
        lambda root: [plugin],
    )
    choices = _campaign_family_choices(tmp_path)
    assert choices[1]["plugin_id"] == "single"
    assert choices[1]["family"] == "Single Family"
    assert choices[1]["label"] == "Single Family"


def test_campaign_action_button_claims_ownership_for_computation():
    selected = {"id": 7, "status": "active"}
    other_active = {"id": 3, "status": "active"}

    label, claim = _campaign_action_button_state(
        selected,
        other_active,
        {"page": "Target"},
    )
    assert claim
    assert label == "Set current & open Target"

    label, claim = _campaign_action_button_state(
        {"id": 7, "status": "paused"},
        other_active,
        {"page": "Pipelines"},
    )
    assert claim
    assert label == "Resume, set current & open Pipelines"

    label, claim = _campaign_action_button_state(
        {"id": 7, "status": "archived"},
        other_active,
        {"page": "Descent"},
    )
    assert claim
    assert label == "Reopen, set current & open Descent"

    label, claim = _campaign_action_button_state(
        selected,
        other_active,
        {"page": "Analyze"},
    )
    assert not claim
    assert label == "Open Analyze"

    label, claim = _campaign_action_button_state(
        selected,
        selected,
        {"page": "Target"},
    )
    assert not claim
    assert label == "Open Target"

    label, claim = _campaign_action_button_state(
        {"id": 7, "status": "paused"},
        {"id": 7, "status": "paused"},
        {"page": "Pipelines"},
    )
    assert claim
    assert label == "Resume & open Pipelines"


def test_campaign_notes_are_append_only_and_newest_first(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(
        db,
        name="Notes journal",
        notes="Initial legacy note",
    )

    first = add_campaign_note(db, cid, "Try denominator band 10^6 next.")
    second = add_campaign_note(db, cid, "Nagao shortlist improved after rescore.")

    rows = campaign_notes(db, cid)
    assert [int(row["id"]) for row in rows] == [second, first]
    assert [str(row["body"]) for row in rows] == [
        "Nagao shortlist improved after rescore.",
        "Try denominator band 10^6 next.",
    ]
    assert all(str(row["created_at"]) for row in rows)

    with pytest.raises(ValueError, match="cannot be blank"):
        add_campaign_note(db, cid, "   ")


def test_campaign_note_schema_migrates_and_notes_follow_campaign_delete(tmp_path):
    db, _ = _db(tmp_path)
    ensure_manage_schema(db)
    tables = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    indexes = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "research_campaign_notes" in tables
    assert "research_campaign_note_links" in tables
    assert "idx_research_campaign_notes_campaign" in indexes
    assert "idx_research_campaign_note_links_note" in indexes

    cid = create_campaign(db, name="Disposable notes")
    add_campaign_note(db, cid, "This should be deleted with the campaign.")
    delete_campaign(db, cid)
    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM research_campaign_notes WHERE campaign_id=?",
        (cid,),
    ).fetchone()
    assert int(remaining["n"]) == 0


def test_campaign_schema_migrates_variant_id_on_existing_database(tmp_path):
    db = sqlite3.connect(tmp_path / "legacy-manage.db")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        """CREATE TABLE research_campaigns(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL,
               objective TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'active',
               target_rank INTEGER,
               plugin_id TEXT,
               family TEXT,
               notes TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    db.commit()

    ensure_manage_schema(db)

    cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(research_campaigns)").fetchall()
    }
    assert "variant_id" in cols


def test_campaign_ownership_columns_and_indexes_are_relational(tmp_path):
    db, _ = _db(tmp_path)
    ensure_manage_schema(db)
    ensure_pipeline_schema(db)

    job_cols = {
        row["name"] for row in db.execute("PRAGMA table_info(ui_jobs)").fetchall()
    }
    run_cols = {
        row["name"]
        for row in db.execute("PRAGMA table_info(search_pipeline_runs)").fetchall()
    }
    job_indexes = {
        row["name"] for row in db.execute("PRAGMA index_list(ui_jobs)").fetchall()
    }
    run_indexes = {
        row["name"]
        for row in db.execute("PRAGMA index_list(search_pipeline_runs)").fetchall()
    }
    job_fks = db.execute("PRAGMA foreign_key_list(ui_jobs)").fetchall()
    run_fks = db.execute("PRAGMA foreign_key_list(search_pipeline_runs)").fetchall()

    assert "campaign_id" in job_cols
    assert "campaign_id" in run_cols
    assert "idx_ui_jobs_campaign" in job_indexes
    assert "idx_pipeline_runs_campaign" in run_indexes
    assert any(
        row["table"] == "research_campaigns"
        and row["from"] == "campaign_id"
        and row["on_delete"].upper() == "SET NULL"
        for row in job_fks
    )
    assert any(
        row["table"] == "research_campaigns"
        and row["from"] == "campaign_id"
        and row["on_delete"].upper() == "SET NULL"
        for row in run_fks
    )


def test_campaign_ownership_backfills_valid_legacy_json_links(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Legacy ownership")
    campaign = get_campaign(db, cid)

    job_id, _ = _fake_job(
        db,
        tmp_path,
        metadata={
            "campaign_id": cid,
            "campaign_name": "Legacy ownership",
            "campaign_created_at": campaign["created_at"],
        },
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Legacy run",
        target_mode="general",
        target={},
        stages=[],
        run_config={
            "campaign_id": cid,
            "campaign_name": "Legacy ownership",
            "campaign_created_at": campaign["created_at"],
        },
    )
    db.execute("UPDATE ui_jobs SET campaign_id=NULL WHERE id=?", (job_id,))
    db.execute(
        "UPDATE search_pipeline_runs SET campaign_id=NULL WHERE id=?",
        (run_id,),
    )
    db.commit()

    assert ensure_manage_schema(db) is True
    assert get_job(db, job_id)["campaign_id"] == cid
    assert get_pipeline_run(db, run_id)["campaign_id"] == cid


def test_campaign_relational_owner_wins_over_stale_json_snapshot(tmp_path):
    db, _ = _db(tmp_path)
    first = create_campaign(db, name="First")
    second = create_campaign(db, name="Second")
    job_id, _ = _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": first, "campaign_name": "First"},
    )
    attach_job_to_campaign(db, job_id, second)
    row = get_job(db, job_id)

    # Frozen/compat metadata may exist, but current ownership is relational.
    meta = json.loads(row["metadata_json"])
    meta["campaign_id"] = first
    db.execute(
        "UPDATE ui_jobs SET metadata_json=? WHERE id=?",
        (json.dumps(meta, sort_keys=True), job_id),
    )
    db.commit()
    row = get_job(db, job_id)
    assert row["campaign_id"] == second
    assert job_campaign_id(row) == second


def test_campaign_artifact_lineage_records_owned_work_and_explicit_curve_pin(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Lineage")
    campaign = get_campaign(db, cid)

    job_id, _ = _fake_job(
        db,
        tmp_path,
        metadata={
            "campaign_id": cid,
            "campaign_name": "Lineage",
            "campaign_created_at": campaign["created_at"],
        },
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Lineage run",
        target_mode="general",
        target={},
        stages=[],
        run_config={
            "campaign_id": cid,
            "campaign_name": "Lineage",
            "campaign_created_at": campaign["created_at"],
        },
    )
    curve_id = upsert_curve(db, family="demo", parameter="manual-pin")

    owned = campaign_artifacts(db, cid, relation="owned")
    assert {
        (row["artifact_kind"], int(row["artifact_id"]))
        for row in owned
    } == {("job", job_id), ("pipeline_run", run_id)}

    # This curve has no Job/Pool/Pipeline provenance; the explicit pin is enough.
    assert curve_id not in campaign_curve_ids(db, cid)
    pin_campaign_curve(db, cid, curve_id)
    assert campaign_pinned_curve_ids(db, cid) == [curve_id]
    assert curve_id in campaign_curve_ids(db, cid)

    pins = campaign_artifacts(
        db,
        cid,
        artifact_kind="curve",
        relation="pinned",
    )
    assert [(row["artifact_kind"], int(row["artifact_id"]), row["relation"]) for row in pins] == [
        ("curve", curve_id, "pinned")
    ]

    unpin_campaign_curve(db, cid, curve_id)
    assert campaign_pinned_curve_ids(db, cid) == []
    assert curve_id not in campaign_curve_ids(db, cid)


def test_campaign_lineage_follows_relational_owner_moves(tmp_path):
    db, _ = _db(tmp_path)
    first = create_campaign(db, name="First lineage")
    second = create_campaign(db, name="Second lineage")
    job_id, _ = _fake_job(db, tmp_path, metadata={"campaign_id": first})

    assert [
        int(row["artifact_id"])
        for row in campaign_artifacts(db, first, artifact_kind="job")
    ] == [job_id]

    attach_job_to_campaign(db, job_id, second)
    assert campaign_artifacts(db, first, artifact_kind="job") == []
    assert [
        int(row["artifact_id"])
        for row in campaign_artifacts(db, second, artifact_kind="job")
    ] == [job_id]

    attach_job_to_campaign(db, job_id, None)
    assert campaign_artifacts(db, second, artifact_kind="job") == []

    # Reconciliation follows authoritative relational ownership even after direct SQL.
    db.execute("UPDATE ui_jobs SET campaign_id=? WHERE id=?", (first, job_id))
    db.commit()
    ensure_manage_schema(db)
    assert [
        int(row["artifact_id"])
        for row in campaign_artifacts(db, first, artifact_kind="job")
    ] == [job_id]


def test_campaign_delete_removes_lineage_not_scientific_artifacts(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Disposable lineage")
    campaign = get_campaign(db, cid)
    curve_id = upsert_curve(db, family="demo", parameter="survives")
    pin_campaign_curve(db, cid, curve_id)
    job_id, _ = _fake_job(
        db,
        tmp_path,
        metadata={
            "campaign_id": cid,
            "campaign_created_at": campaign["created_at"],
        },
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Survives",
        target_mode="general",
        target={},
        stages=[],
        run_config={
            "campaign_id": cid,
            "campaign_created_at": campaign["created_at"],
        },
    )

    assert len(campaign_artifacts(db, cid)) == 3
    delete_campaign(db, cid)

    assert get_job(db, job_id) is not None
    assert get_job(db, job_id)["campaign_id"] is None
    assert get_pipeline_run(db, run_id) is not None
    assert get_pipeline_run(db, run_id)["campaign_id"] is None
    assert db.execute("SELECT id FROM curves WHERE id=?", (curve_id,)).fetchone() is not None
    assert db.execute(
        "SELECT COUNT(*) AS n FROM research_campaign_artifacts WHERE campaign_id=?",
        (cid,),
    ).fetchone()["n"] == 0


def test_campaign_lineage_schema_is_generic_and_indexed(tmp_path):
    db, _ = _db(tmp_path)
    ensure_manage_schema(db)
    cols = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(research_campaign_artifacts)"
        ).fetchall()
    }
    indexes = {
        row["name"]
        for row in db.execute(
            "PRAGMA index_list(research_campaign_artifacts)"
        ).fetchall()
    }
    fks = db.execute(
        "PRAGMA foreign_key_list(research_campaign_artifacts)"
    ).fetchall()

    assert {"campaign_id", "artifact_kind", "artifact_id", "relation", "created_at"}.issubset(cols)
    assert {
        "idx_campaign_artifacts_campaign",
        "idx_campaign_artifacts_artifact",
    }.issubset(indexes)
    assert any(
        row["table"] == "research_campaigns"
        and row["from"] == "campaign_id"
        and row["on_delete"].upper() == "CASCADE"
        for row in fks
    )


def test_current_campaign_pointer_is_unique_while_multiple_campaigns_stay_open(tmp_path):
    db, _ = _db(tmp_path)
    first = create_campaign(db, name="First open hunt", target_rank=10)
    second = create_campaign(db, name="Second open hunt", target_rank=11)

    assert get_campaign(db, first)["status"] == "active"
    assert get_campaign(db, second)["status"] == "active"

    set_current_campaign(db, first)
    assert int(current_campaign(db)["id"]) == first
    assert int(active_campaign(db)["id"]) == first
    assert get_application_state(db, "current_campaign_id") == first
    assert get_application_state(db, "active_campaign_id", None) is None
    assert db.execute(
        "SELECT 1 FROM ui_settings WHERE key='active_campaign_id'"
    ).fetchone() is None

    set_current_campaign(db, second)
    assert int(current_campaign(db)["id"]) == second
    assert int(active_campaign(db)["id"]) == second

    # Moving the pointer does not close the previous Campaign.
    assert get_campaign(db, first)["status"] == "active"
    assert get_campaign(db, second)["status"] == "active"

    # Compatibility aliases address the same one-pointer state.
    set_active_campaign(db, first)
    assert int(current_campaign(db)["id"]) == first
    db.close()


def test_set_current_campaign_does_not_change_lifecycle_status(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Paused current")
    update_campaign(db, cid, status="paused")

    set_current_campaign(db, cid)

    assert get_campaign(db, cid)["status"] == "paused"
    assert int(current_campaign(db)["id"]) == cid
    assert active_campaign(db) is None
    assert get_application_state(db, "current_campaign_id") == cid
    db.close()


def test_campaign_crud_and_active_state(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(
        db,
        name="Rank 32 Hunt",
        objective="Find rank >= 32",
        target_rank=32,
        plugin_id="icarm_rank31_302",
        variant_id="recovered_mw17_s",
        family="ICARM #302 recovered X1092 MW17 parent",
    )
    assert get_campaign(db, cid)["name"] == "Rank 32 Hunt"
    assert get_campaign(db, cid)["variant_id"] == "recovered_mw17_s"

    set_active_campaign(db, cid)
    assert int(active_campaign(db)["id"]) == cid

    update_campaign(db, cid, status="paused", notes="parked")
    assert get_campaign(db, cid)["status"] == "paused"
    assert int(current_campaign(db)["id"]) == cid
    assert active_campaign(db) is None

    set_current_campaign(db, cid)
    assert get_campaign(db, cid)["status"] == "paused"
    assert int(current_campaign(db)["id"]) == cid
    assert active_campaign(db) is None

    set_active_campaign(db, cid)
    assert get_campaign(db, cid)["status"] == "active"
    assert int(active_campaign(db)["id"]) == cid
    delete_campaign(db, cid)
    assert get_campaign(db, cid) is None
    assert current_campaign(db) is None
    assert active_campaign(db) is None
    assert get_application_state(db, "current_campaign_id", None) is None


def test_campaign_terminal_status_requires_idle_work_and_disables_schedules(tmp_path):
    db, path = _db(tmp_path)
    cid = create_campaign(db, name="Lifecycle guard", target_rank=10)
    set_active_campaign(db, cid)
    schedule_id = create_schedule(
        db,
        label="Keep searching",
        kind="test",
        command=["python", "-c", "print('ok')"],
        cwd=tmp_path,
        next_run_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        recurrence="daily",
        campaign_id=cid,
        enabled=True,
    )
    job_id, _ = _fake_job(
        db,
        tmp_path,
        status="running",
        metadata={"campaign_id": cid},
    )

    try:
        update_campaign(db, cid, status="completed")
    except ValueError as exc:
        assert "active or queued work" in str(exc)
    else:
        raise AssertionError("completed campaign should reject active work")

    assert get_campaign(db, cid)["status"] == "active"
    assert int(next(row for row in list_schedules(db) if int(row["id"]) == schedule_id)["enabled"]) == 1

    update_job(
        db,
        job_id,
        status="succeeded",
        exit_code=0,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    update_campaign(db, cid, status="completed")

    assert get_campaign(db, cid)["status"] == "completed"
    assert int(current_campaign(db)["id"]) == cid
    assert active_campaign(db) is None
    assert int(next(row for row in list_schedules(db) if int(row["id"]) == schedule_id)["enabled"]) == 0


def test_campaign_handoff_markdown_summarizes_research_state(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(
        db,
        name="Rank 8 handoff",
        objective="Find a specialization with rigorous rank >= 8.",
        target_rank=8,
        plugin_id="demo_family",
        family="demo",
        notes="Try a fresh denominator band if the current leader stalls.",
    )
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="11/7",
        descent_lower=7,
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Prime Funnel",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"campaign_id": cid, "campaign_name": "Rank 8 handoff"},
    )
    update_pipeline_run(
        db,
        run_id,
        status="completed",
        candidates_total=120,
        candidates_done=120,
        best_lower=6,
        best_curve_id=curve_id,
        finished_at="2026-09-21T18:00:00+00:00",
    )

    text = campaign_handoff_markdown(db, cid)
    assert "# Campaign Handoff — #" in text
    assert "Rank 8 handoff" in text
    assert "Find a specialization with rigorous rank >= 8." in text
    assert "**Current rigorous rank:** ≥7" in text
    assert "**Target outcome:** Not met" in text
    assert "## Next useful action" in text
    assert "## Strategy history" in text
    assert "Prime Funnel" in text
    assert "Historical best ≥" in text
    assert "## Frontier milestones" in text
    assert "## Execution state" in text
    assert "## Research notebook" in text
    assert "Try a fresh denominator band" in text
    assert "## Evidence policy" in text
    assert "Heuristic scores are intentionally omitted" in text


def test_campaign_handoff_includes_timestamped_journal_notes(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(
        db,
        name="Notes handoff",
        notes="Legacy starting note.",
    )
    add_campaign_note(db, cid, "First journal note.")
    add_campaign_note(db, cid, "Second journal note.")

    text = campaign_handoff_markdown(db, cid)

    assert "## Research notebook" in text
    assert "### Note —" in text
    assert "Second journal note." in text
    assert "First journal note." in text
    assert "Legacy starting note." in text
    assert "### Initial campaign note —" in text
    assert text.index("Second journal note.") < text.index("First journal note.")
    assert text.index("First journal note.") < text.index("Legacy starting note.")


def test_campaign_handoff_marks_completion_drift_for_review(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Drift handoff", target_rank=6)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="drift",
        descent_lower=6,
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": curve_id},
    )
    update_campaign(db, cid, status="completed")

    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "campaign-handoff-conflict",
            "evidence_type": "descent",
            "rigorous": True,
            "rigorous_upper": 5,
            "status": "completed",
        },
    )

    text = campaign_handoff_markdown(db, cid)
    assert "## Completion baseline" in text
    assert "**Evidence drift:** REVIEW REQUIRED" in text
    assert "target reached" in text
    assert "inconsistent" in text
    assert "CONFLICT: ≥6 / ≤5" in text


def test_campaign_completion_snapshot_detects_evidence_drift(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Completion drift", target_rank=6)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="completion",
        descent_lower=6,
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": curve_id},
    )

    update_campaign(db, cid, status="completed")
    row = get_campaign(db, cid)
    completion = json.loads(row["completion_snapshot_json"])
    assert row["completed_at"]
    assert completion["target_rank"] == 6
    assert completion["target_reached"] is True
    assert completion["best_curve_id"] == curve_id
    assert completion["best_lower"] == 6

    brief = campaign_research_brief(db, cid)
    assert brief["completion_drift"]["changed"] is False

    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "campaign-conflict-test",
            "evidence_type": "descent",
            "rigorous": True,
            "rigorous_upper": 5,
            "status": "completed",
        },
    )

    brief = campaign_research_brief(db, cid)
    assert brief["best_state"]["inconsistent"]
    assert brief["completion_drift"]["changed"]
    assert brief["completion_drift"]["requires_review"]
    assert "target_reached" in brief["completion_drift"]["changes"]
    assert "inconsistent" in brief["completion_drift"]["changes"]


def test_campaign_completion_snapshot_is_historical_until_recompleted(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Completion baseline", target_rank=5)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="baseline",
        descent_lower=5,
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": curve_id},
    )

    update_campaign(db, cid, status="completed")
    original = json.loads(get_campaign(db, cid)["completion_snapshot_json"])
    assert original["target_rank"] == 5

    update_campaign(db, cid, status="completed", target_rank=7)
    preserved = json.loads(get_campaign(db, cid)["completion_snapshot_json"])
    assert preserved == original

    brief = campaign_research_brief(db, cid)
    assert brief["completion_drift"]["changed"]
    assert brief["completion_drift"]["requires_review"]
    assert brief["completion_drift"]["changes"]["target_rank"] == {
        "completed": 5,
        "current": 7,
    }

    set_active_campaign(db, cid)
    update_campaign(db, cid, status="completed")
    refreshed = json.loads(get_campaign(db, cid)["completion_snapshot_json"])
    assert refreshed["target_rank"] == 7
    assert refreshed != original


def test_campaign_pause_preserves_enabled_schedules(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Pause preserves schedule")
    schedule_id = create_schedule(
        db,
        label="Resume later",
        kind="test",
        command=["python", "-c", "print('ok')"],
        cwd=tmp_path,
        next_run_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        recurrence="daily",
        campaign_id=cid,
        enabled=True,
    )

    update_campaign(db, cid, status="paused")
    row = next(row for row in list_schedules(db) if int(row["id"]) == schedule_id)
    assert int(row["enabled"]) == 1


def test_campaign_snapshot_works_without_builder_schema(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Fresh campaign", target_rank=10)
    job_id, _ = _fake_job(db, tmp_path, metadata={"campaign_id": cid})
    snap = campaign_snapshot(db, cid)
    assert int(snap["campaign"]["id"]) == cid
    assert [int(row["id"]) for row in snap["jobs"]] == [job_id]
    assert snap["pipeline_runs"] == []


def test_common_launch_inherits_active_campaign(tmp_path, monkeypatch):
    db, path = _db(tmp_path)
    cid = create_campaign(db, name="Active hunt", target_rank=12)
    set_active_campaign(db, cid)

    captured = {}

    def fake_enqueue_job(db_path, *, kind, label, command, cwd, metadata=None):
        captured.update(
            {
                "db_path": str(db_path),
                "kind": kind,
                "label": label,
                "command": list(command),
                "cwd": str(cwd),
                "metadata": dict(metadata or {}),
            }
        )
        return 41

    monkeypatch.setattr(common, "enqueue_job", fake_enqueue_job)
    ctx = UIContext(tmp_path, path, None)
    job_id = common.launch(
        ctx,
        db,
        kind="target_free",
        label="Target curve",
        command=["python", "-c", "print(1)"],
        metadata={"curve_id": 7},
    )
    assert job_id == 41
    assert captured["metadata"]["campaign_id"] == cid
    assert captured["metadata"]["campaign_name"] == "Active hunt"
    assert captured["metadata"]["campaign_created_at"] == get_campaign(db, cid)["created_at"]
    assert captured["metadata"]["curve_id"] == 7


def test_campaign_ignores_stale_numeric_id_job_collision(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Fresh generation", target_rank=12)
    job_id, _ = _fake_job(
        db,
        tmp_path,
        status="killed",
        metadata={"campaign_id": cid, "campaign_name": "Old generation"},
    )
    db.execute(
        "UPDATE ui_jobs SET campaign_id=NULL,created_at=?,updated_at=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", job_id),
    )
    db.commit()

    snap = campaign_snapshot(db, cid)
    assert snap["jobs"] == []
    assert snap["failed"] == 0
    brief = campaign_research_brief(db, cid)
    assert brief["next_action"]["page"] == "Pipelines"


def test_campaign_ignores_stale_numeric_id_pipeline_collision(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Fresh pipeline generation")
    run_id = create_pipeline_run(
        db,
        pipeline_name="Old run",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"campaign_id": cid, "campaign_name": "Old generation"},
    )
    db.execute(
        "UPDATE search_pipeline_runs SET campaign_id=NULL,created_at=?,updated_at=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", run_id),
    )
    db.commit()

    snap = campaign_snapshot(db, cid)
    assert snap["pipeline_runs"] == []


def test_explicit_old_job_attachment_is_preserved(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Historical import")
    job_id, _ = _fake_job(db, tmp_path)
    db.execute(
        "UPDATE ui_jobs SET created_at=?,updated_at=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", job_id),
    )
    db.commit()

    attach_job_to_campaign(db, job_id, cid)
    meta = json.loads(get_job(db, job_id)["metadata_json"])
    assert meta["campaign_created_at"] == get_campaign(db, cid)["created_at"]
    assert [int(row["id"]) for row in campaign_snapshot(db, cid)["jobs"]] == [job_id]


def test_job_can_be_attached_and_detached_from_campaign(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Attach test")
    job_id, _ = _fake_job(db, tmp_path)

    attach_job_to_campaign(db, job_id, cid)
    meta = json.loads(get_job(db, job_id)["metadata_json"])
    assert meta["campaign_id"] == cid
    assert meta["campaign_created_at"] == get_campaign(db, cid)["created_at"]

    attach_job_to_campaign(db, job_id, None)
    row = get_job(db, job_id)
    meta = json.loads(row["metadata_json"])
    assert row["campaign_id"] is None
    assert job_campaign_id(row) is None
    assert "campaign_id" not in meta
    assert meta["campaign_name"] == "Attach test"
    assert meta["campaign_created_at"] == get_campaign(db, cid)["created_at"]


def test_schedule_crud_and_due_dispatch(tmp_path):
    db, path = _db(tmp_path)
    cid = create_campaign(db, name="Night hunt")
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Nightly search",
        kind="test",
        command=["python", "-c", "print('night')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="daily",
        campaign_id=cid,
    )
    assert [int(row["id"]) for row in list_schedules(db)] == [sid]

    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1
    job_id = int(jobs[0])
    job = get_job(db, job_id)
    queue = queue_item_for_job(db, job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert queue is not None
    assert queue["state"] == "queued"
    meta = json.loads(job["metadata_json"])
    assert meta["campaign_id"] == cid
    assert meta["schedule_id"] == sid
    assert meta["schedule_occurrence_id"] is not None

    occurrences = list_schedule_occurrences(db, sid)
    assert len(occurrences) == 1
    assert int(occurrences[0]["job_id"]) == job_id
    assert occurrences[0]["state"] == "queued"

    row = list_schedules(db)[0]
    assert int(row["last_job_id"]) == job_id
    assert row["last_error"] is None
    assert row["next_run_at"] > due

    update_schedule(db, sid, enabled=False, label="Paused schedule")
    row = list_schedules(db)[0]
    assert not bool(row["enabled"])
    assert row["label"] == "Paused schedule"

    delete_schedule(db, sid)
    assert list_schedules(db) == []
    assert list_schedule_occurrences(db, sid) == []


def test_pipeline_schedule_replays_frozen_run_after_saved_pipeline_edit(tmp_path):
    db, path = _db(tmp_path)
    ensure_pipeline_schema(db)

    original_stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 97}},
    ]
    edited_stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 997}},
        {"id": "integral_seed", "config": {}},
    ]
    pipeline_id = save_pipeline(
        db,
        name="Frozen schedule source",
        target_mode="family",
        stages=original_stages,
        config={"target_rank": 12},
    )
    source_definition = pipeline_payload(get_pipeline(db, pipeline_id))
    source_run_id = create_pipeline_run(
        db,
        pipeline_id=pipeline_id,
        pipeline_name="Frozen schedule source",
        target_mode="family",
        target={"family": "demo"},
        stages=original_stages,
        run_config={"target_rank": 12},
    )
    source_run = pipeline_run_payload(get_pipeline_run(db, source_run_id))

    log = tmp_path / "pipeline-source.log"
    log.write_text("done\n")
    source_job_id = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline · Frozen schedule source",
        command=[
            "python",
            "-m",
            "rank42.pipeline_runner",
            "--run-id",
            str(source_run_id),
        ],
        cwd=tmp_path,
        log_path=log,
        metadata={
            "pipeline_run_id": source_run_id,
            "pipeline_id": pipeline_id,
            "pipeline_name": "Frozen schedule source",
        },
    )
    update_job(
        db,
        source_job_id,
        status="succeeded",
        exit_code=0,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )

    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Frozen Pipeline schedule",
        kind="pipeline_search",
        command=json.loads(get_job(db, source_job_id)["command_json"]),
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
        metadata=json.loads(get_job(db, source_job_id)["metadata_json"]),
    )

    # Edit the Saved Pipeline after the schedule is created.
    save_pipeline(
        db,
        pipeline_id=pipeline_id,
        name="Frozen schedule source",
        target_mode="family",
        stages=edited_stages,
        config={"target_rank": 20},
    )
    edited_definition = pipeline_payload(get_pipeline(db, pipeline_id))
    assert edited_definition["revision"] == source_definition["revision"] + 1
    assert edited_definition["content_hash"] != source_definition["content_hash"]

    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1
    scheduled_job = get_job(db, int(jobs[0]))
    scheduled_meta = json.loads(scheduled_job["metadata_json"])
    cloned_run_id = int(scheduled_meta["pipeline_run_id"])
    assert cloned_run_id != source_run_id

    cloned_run = pipeline_run_payload(get_pipeline_run(db, cloned_run_id))
    assert cloned_run["stages"] == source_run["stages"] == original_stages
    assert cloned_run["run_config"]["source_pipeline_revision"] == source_run["run_config"]["source_pipeline_revision"]
    assert cloned_run["run_config"]["source_pipeline_hash"] == source_run["run_config"]["source_pipeline_hash"]
    assert cloned_run["run_config"]["source_pipeline_revision"] == source_definition["revision"]
    assert cloned_run["run_config"]["source_pipeline_hash"] == source_definition["content_hash"]
    assert cloned_run["run_config"]["source_pipeline_revision"] != edited_definition["revision"]
    assert cloned_run["run_config"]["source_pipeline_hash"] != edited_definition["content_hash"]

    occurrence = list_schedule_occurrences(db, sid)[0]
    occurrence_snapshot = json.loads(occurrence["snapshot_json"])
    assert occurrence_snapshot["metadata"]["pipeline_run_id"] == source_run_id


def test_schedule_occurrence_history_survives_generated_job_deletion(tmp_path):
    db, path = _db(tmp_path)
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Durable occurrence",
        kind="test",
        command=["python", "-c", "print('scheduled')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
        metadata={"snapshot_marker": "durable"},
    )

    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1
    job_id = int(jobs[0])

    occurrence = list_schedule_occurrences(db, sid)[0]
    snapshot_before = json.loads(occurrence["snapshot_json"])
    assert int(occurrence["job_id"]) == job_id
    assert snapshot_before["metadata"]["snapshot_marker"] == "durable"

    update_job(
        db,
        job_id,
        status="succeeded",
        exit_code=0,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    assert delete_terminal_job(db, job_id, delete_log=True)
    assert get_job(db, job_id) is None

    occurrence_after = list_schedule_occurrences(db, sid)[0]
    assert int(occurrence_after["job_id"]) == job_id
    assert json.loads(occurrence_after["snapshot_json"]) == snapshot_before


def test_schedule_occurrence_is_idempotent_for_same_due_time(tmp_path):
    db, path = _db(tmp_path)
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Exactly once",
        kind="test",
        command=["python", "-c", "print('once')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
    )

    first = dispatch_due_schedules(db, path, tmp_path)
    assert len(first) == 1
    first_job = int(first[0])
    assert len(list_schedule_occurrences(db, sid)) == 1

    update_schedule(
        db,
        sid,
        enabled=True,
        next_run_at=due,
    )
    second = dispatch_due_schedules(db, path, tmp_path)
    assert second == []

    occurrences = list_schedule_occurrences(db, sid)
    assert len(occurrences) == 1
    assert int(occurrences[0]["job_id"]) == first_job
    queued_jobs = db.execute(
        "SELECT COUNT(*) AS n FROM ui_jobs WHERE kind='test'"
    ).fetchone()["n"]
    assert int(queued_jobs) == 1


def test_pending_schedule_occurrence_retries_after_enqueue_failure(
    tmp_path,
    monkeypatch,
):
    import rank42.manage_store as manage_store

    db, path = _db(tmp_path)
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Retry occurrence",
        kind="test",
        command=["python", "-c", "print('retry')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
    )

    original = manage_store.enqueue_job_record

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("synthetic queue failure")

    monkeypatch.setattr(manage_store, "enqueue_job_record", fail_enqueue)
    assert dispatch_due_schedules(db, path, tmp_path) == []

    occurrence = list_schedule_occurrences(db, sid)[0]
    assert occurrence["state"] == "pending"
    assert occurrence["job_id"] is None
    assert "synthetic queue failure" in str(occurrence["last_error"])

    monkeypatch.setattr(manage_store, "enqueue_job_record", original)
    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1

    occurrence = list_schedule_occurrences(db, sid)[0]
    assert occurrence["state"] == "queued"
    assert int(occurrence["job_id"]) == int(jobs[0])


def test_schedule_occurrence_tracks_queue_lifecycle(tmp_path):
    db, path = _db(tmp_path)
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Lifecycle",
        kind="test",
        command=["python", "-c", "print('lifecycle')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
    )
    job_id = int(dispatch_due_schedules(db, path, tmp_path)[0])

    claimed = claim_next_queue_item(db, "worker-test")
    assert int(claimed["job_id"]) == job_id
    assert mark_queue_running(db, job_id, "worker-test")

    occurrence = list_schedule_occurrences(db, sid)[0]
    assert occurrence["state"] == "running"

    update_job(
        db,
        job_id,
        status="succeeded",
        exit_code=0,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    assert finalize_queue_for_job(db, job_id, "succeeded")

    occurrence = list_schedule_occurrences(db, sid)[0]
    assert occurrence["state"] == "succeeded"


def test_schedule_latest_catch_up_collapses_backlog(tmp_path):
    db, path = _db(tmp_path)
    due = (
        datetime.now(timezone.utc)
        - timedelta(days=3, minutes=1)
    ).isoformat()
    sid = create_schedule(
        db,
        label="Latest backlog",
        kind="test",
        command=["python", "-c", "print('latest')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="daily",
        catch_up_policy="latest",
    )

    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1

    occurrences = list_schedule_occurrences(db, sid)
    assert len(occurrences) == 1
    snapshot = json.loads(occurrences[0]["snapshot_json"])
    assert snapshot["catch_up_policy"] == "latest"
    assert int(snapshot["catch_up"]["suppressed_occurrences"]) >= 3

    row = list_schedules(db)[0]
    next_run = datetime.fromisoformat(
        str(row["next_run_at"]).replace("Z", "+00:00")
    )
    assert next_run > datetime.now(timezone.utc)


def test_schedule_run_all_catch_up_drains_in_bounded_batches(tmp_path):
    db, path = _db(tmp_path)
    due = (
        datetime.now(timezone.utc)
        - timedelta(hours=25, minutes=1)
    ).isoformat()
    sid = create_schedule(
        db,
        label="Drain backlog",
        kind="test",
        command=["python", "-c", "print('all')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="interval",
        interval_minutes=60,
        catch_up_policy="all",
    )

    first = dispatch_due_schedules(db, path, tmp_path)
    assert len(first) == 20
    assert len(list_schedule_occurrences(db, sid)) == 20

    second = dispatch_due_schedules(db, path, tmp_path)
    assert len(second) == 6
    occurrences = list_schedule_occurrences(db, sid)
    assert len(occurrences) == 26
    assert all(row["state"] == "queued" for row in occurrences)

    third = dispatch_due_schedules(db, path, tmp_path)
    assert third == []
    row = list_schedules(db)[0]
    next_run = datetime.fromisoformat(
        str(row["next_run_at"]).replace("Z", "+00:00")
    )
    assert next_run > datetime.now(timezone.utc)


def test_schedule_skip_catch_up_records_audit_without_job(tmp_path):
    db, path = _db(tmp_path)
    due = (
        datetime.now(timezone.utc)
        - timedelta(days=3, minutes=1)
    ).isoformat()
    sid = create_schedule(
        db,
        label="Skip backlog",
        kind="test",
        command=["python", "-c", "print('skip')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="daily",
        catch_up_policy="skip",
    )

    assert dispatch_due_schedules(db, path, tmp_path) == []

    occurrences = list_schedule_occurrences(db, sid)
    assert len(occurrences) == 1
    occurrence = occurrences[0]
    assert occurrence["state"] == "skipped"
    assert occurrence["job_id"] is None
    assert "catch-up policy skipped" in str(occurrence["last_error"])

    snapshot = json.loads(occurrence["snapshot_json"])
    assert snapshot["catch_up_policy"] == "skip"
    assert int(snapshot["catch_up"]["suppressed_occurrences"]) >= 4

    row = list_schedules(db)[0]
    next_run = datetime.fromisoformat(
        str(row["next_run_at"]).replace("Z", "+00:00")
    )
    assert next_run > datetime.now(timezone.utc)


def test_pending_occurrence_uses_immutable_schedule_snapshot(
    tmp_path,
    monkeypatch,
):
    import rank42.manage_store as manage_store

    db, path = _db(tmp_path)
    original_campaign = create_campaign(db, name="Original campaign")
    replacement_campaign = create_campaign(db, name="Replacement campaign")
    due = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    sid = create_schedule(
        db,
        label="Original label",
        kind="test",
        command=["python", "-c", "print('original')"],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
        catch_up_policy="latest",
        metadata={
            "ratpoints_backend": "GPU",
            "snapshot_marker": "original",
        },
        campaign_id=original_campaign,
    )

    original_enqueue = manage_store.enqueue_job_record

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("hold occurrence pending")

    monkeypatch.setattr(
        manage_store,
        "enqueue_job_record",
        fail_enqueue,
    )
    assert dispatch_due_schedules(db, path, tmp_path) == []

    occurrence = list_schedule_occurrences(db, sid)[0]
    assert occurrence["state"] == "pending"
    snapshot = json.loads(occurrence["snapshot_json"])
    assert snapshot["label"] == "Original label"
    assert snapshot["campaign_id"] == original_campaign
    assert snapshot["metadata"]["campaign_name"] == "Original campaign"
    assert snapshot["resource_class"] == "gpu_ratpoints"

    update_campaign(
        db,
        original_campaign,
        name="Renamed after occurrence",
    )
    update_schedule(
        db,
        sid,
        label="Edited label",
        command_json=json.dumps(
            ["python", "-c", "print('edited')"]
        ),
        metadata_json=json.dumps(
            {
                "ratpoints_backend": "CPU",
                "snapshot_marker": "edited",
            }
        ),
        campaign_id=replacement_campaign,
    )

    monkeypatch.setattr(
        manage_store,
        "enqueue_job_record",
        original_enqueue,
    )
    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1
    job_id = int(jobs[0])

    job = get_job(db, job_id)
    queue = queue_item_for_job(db, job_id)
    meta = json.loads(job["metadata_json"])
    command = json.loads(job["command_json"])

    assert job["label"] == "Original label"
    assert command == ["python", "-c", "print('original')"]
    assert meta["snapshot_marker"] == "original"
    assert meta["campaign_id"] == original_campaign
    assert meta["campaign_name"] == "Original campaign"
    assert queue["resource_class"] == "gpu_ratpoints"


def test_jobs_scheduled_panel_is_extracted_without_changing_compatibility_names():
    from rank42.ui_pages import jobs_page, jobs_schedule_panel

    page_source = inspect.getsource(jobs_page)

    assert "def _schedule_source_jobs(db):" not in page_source
    assert "def _schedule_time_inputs(" not in page_source
    assert "def _new_schedule(db, ctx):" not in page_source
    assert "def _schedule_editor(db, ctx, row):" not in page_source
    assert "def _scheduled(db, ctx):" not in page_source

    assert jobs_page._schedule_source_jobs is jobs_schedule_panel.schedule_source_jobs
    assert jobs_page._schedule_time_inputs is jobs_schedule_panel.schedule_time_inputs
    assert jobs_page._new_schedule is jobs_schedule_panel.render_new_schedule
    assert jobs_page._schedule_editor is jobs_schedule_panel.render_schedule_editor
    assert jobs_page._scheduled is jobs_schedule_panel.render_scheduled

    scheduled_source = inspect.getsource(jobs_schedule_panel.render_scheduled)
    assert '"jobs-schedule-select"' in scheduled_source
    assert '"manage_schedule_id"' in scheduled_source

    new_source = inspect.getsource(jobs_schedule_panel.render_new_schedule)
    assert '"schedule-new-source"' in new_source
    assert '"schedule-new-recurrence"' in new_source
    assert '"schedule-new-catch-up"' in new_source
    assert '"schedule-new-create"' in new_source

    edit_source = inspect.getsource(jobs_schedule_panel.render_schedule_editor)
    assert "schedule-run-" in edit_source
    assert "schedule-toggle-" in edit_source
    assert "schedule-delete-confirm-" in edit_source
    assert "schedule-delete-" in edit_source


def test_jobs_scheduler_ui_labels_frozen_snapshot_semantics():
    from rank42.ui_pages import jobs_page

    new_source = inspect.getsource(jobs_page._new_schedule)
    edit_source = inspect.getsource(jobs_page._schedule_editor)
    table_source = inspect.getsource(jobs_page._scheduled)

    assert "**Frozen snapshot schedule**" in new_source
    assert "Later edits to a Saved " in new_source
    assert "Pipeline do not change this schedule." in new_source
    assert "Captured Pipeline Run" in new_source
    assert "not the latest" in new_source

    assert "**Frozen snapshot schedule**" in edit_source
    assert "Editing the Saved Pipeline later does not update this schedule." in edit_source
    assert "Captured Pipeline Run" in edit_source

    assert '"mode": "Frozen snapshot"' in table_source


def test_jobs_scheduler_ui_exposes_catch_up_policies():
    from rank42.ui_pages import jobs_schedule_panel

    source = inspect.getsource(jobs_schedule_panel)
    assert '"latest": "Latest only"' in source
    assert '"all": "Run all missed"' in source
    assert '"skip": "Skip missed"' in source
    assert '"After downtime"' in source


def test_paused_campaign_blocks_scheduled_dispatch(tmp_path):
    db, path = _db(tmp_path)
    cid = create_campaign(db, name="Paused campaign")
    update_campaign(db, cid, status="paused")
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sid = create_schedule(
        db,
        label="Blocked",
        kind="test",
        command=["python", "-c", "print('blocked')"],
        cwd=tmp_path,
        next_run_at=due,
        campaign_id=cid,
    )

    assert dispatch_due_schedules(db, path, tmp_path) == []
    assert list_schedule_occurrences(db, sid) == []
    row = list_schedules(db)[0]
    assert int(row["id"]) == sid
    assert bool(row["enabled"])
    assert row["last_job_id"] is None


def test_retry_and_delete_terminal_job(tmp_path, monkeypatch):
    db, path = _db(tmp_path)
    cid = create_campaign(db, name="Retry campaign")
    job_id, log = _fake_job(db, tmp_path, metadata={"campaign_id": cid})

    captured = {}

    def fake_enqueue_job(db_path, *, kind, label, command, cwd, metadata=None):
        captured.update({"metadata": dict(metadata or {}), "kind": kind})
        return 77

    monkeypatch.setattr("rank42.manage_store.enqueue_job", fake_enqueue_job)
    assert retry_job(db, path, job_id) == 77
    assert captured["metadata"]["campaign_id"] == cid
    assert captured["metadata"]["retry_of_job_id"] == job_id

    assert delete_terminal_job(db, job_id, delete_log=True)
    assert get_job(db, job_id) is None
    assert not log.exists()


def test_scheduled_pipeline_clone_rolls_back_with_failed_enqueue(
    tmp_path,
    monkeypatch,
):
    import rank42.manage_store as manage_store

    db, path = _db(tmp_path)
    ensure_pipeline_schema(db)
    campaign_id = create_campaign(db, name="Frozen pipeline campaign")
    source_run_id = create_pipeline_run(
        db,
        pipeline_name="Scheduled source",
        target_mode="family",
        target={"plugin_id": "test"},
        stages=[{"id": "rank_screen", "config": {}}],
        run_config={"target_rank": 12},
    )
    due = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    sid = create_schedule(
        db,
        label="Scheduled pipeline",
        kind="pipeline_search",
        command=[
            "python",
            "-m",
            "rank42.pipeline_runner",
            "--db",
            str(path),
            "--run-id",
            str(source_run_id),
        ],
        cwd=tmp_path,
        next_run_at=due,
        recurrence="once",
        metadata={"pipeline_run_id": source_run_id},
        campaign_id=campaign_id,
    )

    before = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM search_pipeline_runs"
        ).fetchone()["n"]
    )
    original_enqueue = manage_store.enqueue_job_record

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("synthetic queue insert failure")

    monkeypatch.setattr(
        manage_store,
        "enqueue_job_record",
        fail_enqueue,
    )
    assert dispatch_due_schedules(db, path, tmp_path) == []

    after_failure = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM search_pipeline_runs"
        ).fetchone()["n"]
    )
    assert after_failure == before
    occurrence = list_schedule_occurrences(db, sid)[0]
    assert occurrence["state"] == "pending"
    assert "synthetic queue insert failure" in str(
        occurrence["last_error"]
    )

    update_campaign(
        db,
        campaign_id,
        name="Renamed after snapshot",
    )

    monkeypatch.setattr(
        manage_store,
        "enqueue_job_record",
        original_enqueue,
    )
    jobs = dispatch_due_schedules(db, path, tmp_path)
    assert len(jobs) == 1

    after_success = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM search_pipeline_runs"
        ).fetchone()["n"]
    )
    assert after_success == before + 1

    job = get_job(db, int(jobs[0]))
    meta = json.loads(job["metadata_json"])
    cloned_run_id = int(meta["pipeline_run_id"])
    assert cloned_run_id != source_run_id
    cloned = get_pipeline_run(db, cloned_run_id)
    run_config = json.loads(cloned["run_config_json"])
    assert run_config["campaign_id"] == campaign_id
    assert run_config["campaign_name"] == "Frozen pipeline campaign"


def test_retry_pipeline_job_clones_fresh_pipeline_run(tmp_path, monkeypatch):
    db, path = _db(tmp_path)
    ensure_pipeline_schema(db)
    source_run_id = create_pipeline_run(
        db,
        pipeline_name="Record hunt",
        target_mode="family",
        target={"plugin_id": "test"},
        stages=[{"id": "rank_screen", "config": {}}],
        run_config={"target_rank": 12},
    )
    job_id, _ = _fake_job(
        db,
        tmp_path,
        kind="pipeline_search",
        metadata={"pipeline_run_id": source_run_id},
    )
    row = get_job(db, job_id)
    command = [
        "python",
        "-m",
        "rank42.pipeline_runner",
        "--db",
        str(path),
        "--run-id",
        str(source_run_id),
    ]
    db.execute(
        "UPDATE ui_jobs SET command_json=? WHERE id=?",
        (json.dumps(command), job_id),
    )
    db.commit()

    captured = {}

    def fake_enqueue_job(db_path, *, kind, label, command, cwd, metadata=None):
        captured["command"] = list(command)
        captured["metadata"] = dict(metadata or {})
        return 101

    monkeypatch.setattr("rank42.manage_store.enqueue_job", fake_enqueue_job)
    assert retry_job(db, path, job_id) == 101

    new_run_id = int(captured["metadata"]["pipeline_run_id"])
    assert new_run_id != source_run_id
    assert get_pipeline_run(db, new_run_id) is not None
    run_arg = captured["command"].index("--run-id")
    assert int(captured["command"][run_arg + 1]) == new_run_id


def test_manage_schema_migrates_schedule_catch_up_and_snapshots(tmp_path):
    path = tmp_path / "legacy-manage.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE research_campaigns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            target_rank INTEGER,
            plugin_id TEXT,
            family TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_research_campaigns_status
            ON research_campaigns(status, updated_at DESC);

        CREATE TABLE research_campaign_notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_research_campaign_notes_campaign
            ON research_campaign_notes(campaign_id, created_at DESC, id DESC);

        CREATE TABLE ui_job_schedules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            kind TEXT NOT NULL,
            command_json TEXT NOT NULL,
            cwd TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            campaign_id INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1,
            recurrence TEXT NOT NULL DEFAULT 'once',
            interval_minutes INTEGER,
            next_run_at TEXT NOT NULL,
            last_run_at TEXT,
            last_job_id INTEGER,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_ui_job_schedules_due
            ON ui_job_schedules(enabled, next_run_at);

        CREATE TABLE ui_job_schedule_occurrences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            scheduled_for TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            job_id INTEGER,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(schedule_id, scheduled_for)
        );
        CREATE INDEX idx_ui_job_schedule_occurrences_pending
            ON ui_job_schedule_occurrences(state, scheduled_for, id);
        CREATE INDEX idx_ui_job_schedule_occurrences_schedule
            ON ui_job_schedule_occurrences(
                schedule_id, scheduled_for DESC, id DESC
            );
        """
    )
    stamp = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        """INSERT INTO ui_job_schedules(
               label,kind,command_json,cwd,metadata_json,enabled,
               recurrence,next_run_at,created_at,updated_at
           ) VALUES(?,?,?,?,?,1,'daily',?,?,?)""",
        (
            "Legacy",
            "family_search",
            json.dumps(["python", "-c", "pass"]),
            str(tmp_path),
            "{}",
            stamp,
            stamp,
            stamp,
        ),
    )
    sid = int(cur.lastrowid)
    db.execute(
        """INSERT INTO ui_job_schedule_occurrences(
               schedule_id,scheduled_for,state,created_at,updated_at
           ) VALUES(?,?,'pending',?,?)""",
        (sid, stamp, stamp, stamp),
    )
    db.commit()

    assert ensure_manage_schema(db) is True

    schedule = db.execute(
        "SELECT * FROM ui_job_schedules WHERE id=?",
        (sid,),
    ).fetchone()
    occurrence = db.execute(
        """SELECT * FROM ui_job_schedule_occurrences
           WHERE schedule_id=?""",
        (sid,),
    ).fetchone()
    note_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(research_campaign_notes)").fetchall()
    }
    note_links = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_campaign_note_links'"
    ).fetchone()
    assert schedule["catch_up_policy"] == "latest"
    assert occurrence["snapshot_json"] == "{}"
    assert {"entry_type", "handoff_state"}.issubset(note_cols)
    assert note_links is not None
    db.close()


def test_manage_schema_second_check_is_read_only(tmp_path):
    path = tmp_path / "manage-readonly.db"
    db = connect(path)
    ensure_ui_schema(db)
    assert ensure_manage_schema(db) is True
    campaign_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(research_campaigns)").fetchall()
    }
    note_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(research_campaign_notes)").fetchall()
    }
    schedule_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(ui_job_schedules)").fetchall()
    }
    occurrence_cols = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(ui_job_schedule_occurrences)"
        ).fetchall()
    }
    assert {"completed_at", "completion_snapshot_json"}.issubset(campaign_cols)
    assert {"entry_type", "handoff_state"}.issubset(note_cols)
    assert "catch_up_policy" in schedule_cols
    assert "snapshot_json" in occurrence_cols
    traced = []
    db.set_trace_callback(traced.append)
    assert ensure_manage_schema(db) is False
    assert db.in_transaction is False
    writes = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(
            ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")
        )
    ]
    assert writes == []


def test_campaign_rename_preserves_job_launch_snapshot(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Old name")
    created_at = get_campaign(db, cid)["created_at"]
    job_id, _ = _fake_job(
        db,
        tmp_path,
        metadata={
            "campaign_id": cid,
            "campaign_name": "Old name",
            "campaign_created_at": created_at,
        },
    )
    update_campaign(db, cid, name="New name")
    row = get_job(db, job_id)
    meta = json.loads(row["metadata_json"])
    assert row["campaign_id"] == cid
    assert job_campaign_id(row) == cid
    assert meta["campaign_id"] == cid
    assert meta["campaign_name"] == "Old name"
    assert meta["campaign_created_at"] == created_at


def test_jobs_dataframe_row_uses_arrow_safe_display_types(tmp_path):
    from rank42.ui_pages.jobs_page import _job_table_row

    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Arrow safe")
    job_id, _ = _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid},
    )
    row = get_job(db, job_id)
    display = _job_table_row(row)
    assert display["campaign"] == f"#{cid}"
    assert isinstance(display["campaign"], str)
    assert isinstance(display["pid"], str)
    assert isinstance(display["exit"], str)


def test_campaign_research_brief_uses_only_traceable_curves(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Traceable hunt", target_rank=6)
    linked = upsert_curve(
        db,
        family="demo",
        parameter="1",
        generic_lower=2,
        descent_lower=4,
    )
    unrelated = upsert_curve(
        db,
        family="demo",
        parameter="2",
        generic_lower=9,
        descent_lower=9,
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": linked},
    )

    assert campaign_curve_ids(db, cid) == [linked]
    brief = campaign_research_brief(db, cid)
    assert int(brief["best_curve"]["id"]) == linked
    assert int(brief["best_lower"]) == 4
    assert int(brief["target_gap"]) == 2
    assert not brief["target_reached"]
    assert unrelated not in [int(row["id"]) for row in brief["curves"]]
    assert int(brief["next_action"]["curve_id"]) == linked


def test_campaign_shortlist_sees_rank_evidence_beyond_legacy_top_twelve(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Evidence-aware shortlist", target_rank=15)

    curve_ids = []
    for i in range(12):
        curve_ids.append(
            upsert_curve(
                db,
                family="demo",
                parameter=f"legacy-{i}",
                descent_lower=i + 1,
            )
        )

    evidence_leader = upsert_curve(
        db,
        family="demo",
        parameter="ledger-leader",
        descent_lower=0,
    )
    curve_ids.append(evidence_leader)
    record_rank_evidence(
        db,
        curve_id=evidence_leader,
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "campaign-test",
            "evidence_type": "certified_subgroup",
            "rigorous": True,
            "rigorous_lower": 15,
            "status": "completed",
        },
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_ids": curve_ids},
    )

    brief = campaign_research_brief(db, cid)
    assert int(brief["best_curve"]["id"]) == evidence_leader
    assert brief["best_lower"] == 15
    assert brief["target_reached"]
    assert brief["target_gap"] == 0


def test_campaign_conflicting_rank_evidence_blocks_target_completion(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Conflict hunt", target_rank=8)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="conflict",
        generic_lower=2,
        descent_lower=8,
    )
    update_curve(db, curve_id, descent_upper=6)
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": curve_id},
    )

    brief = campaign_research_brief(db, cid)
    assert brief["best_lower"] == 8
    assert brief["best_state"]["inconsistent"]
    assert brief["target_blocked_by_conflict"]
    assert brief["target_gap"] is None
    assert not brief["target_reached"]
    assert brief["next_action"]["title"] == "Resolve conflicting rank evidence"
    assert brief["next_action"]["page"] == "Descent"
    assert brief["next_action"]["planner_kind"] == "evidence"
    assert brief["next_action"]["planner_version"] == PLANNER_VERSION


def test_campaign_consistent_target_curve_outranks_higher_conflicted_curve(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Quarantine conflict", target_rank=7)

    conflicted = upsert_curve(
        db,
        family="demo",
        parameter="conflicted",
        descent_lower=9,
    )
    update_curve(db, conflicted, descent_upper=6)
    consistent = upsert_curve(
        db,
        family="demo",
        parameter="consistent",
        descent_lower=7,
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_ids": [conflicted, consistent]},
    )

    brief = campaign_research_brief(db, cid)
    assert int(brief["best_curve"]["id"]) == consistent
    assert brief["best_lower"] == 7
    assert brief["target_reached"]
    assert not brief["target_blocked_by_conflict"]
    assert [int(rec["curve"]["id"]) for rec in brief["conflicted_curves"]] == [conflicted]
    assert brief["next_action"]["page"] == "Curves"
    assert brief["next_action"]["curve_id"] == consistent


def test_campaign_target_reached_points_to_curves(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Target reached", target_rank=6)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="3",
        descent_lower=6,
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": curve_id},
    )

    brief = campaign_research_brief(db, cid)
    assert brief["target_reached"]
    assert brief["target_gap"] == 0
    assert brief["next_action"]["page"] == "Curves"
    assert brief["next_action"]["curve_id"] == curve_id
    assert brief["next_action"]["planner_kind"] == "campaign_target_review"
    assert brief["next_action"]["planner_version"] == PLANNER_VERSION
    assert "meeting the campaign target" in brief["next_action"]["why"]


def test_empty_campaign_research_brief_points_to_pipelines(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Empty hunt", target_rank=12)
    brief = campaign_research_brief(db, cid)
    assert brief["best_curve"] is None
    assert brief["next_action"]["page"] == "Pipelines"


def test_campaign_exact_leader_below_target_returns_to_search(tmp_path):
    db, _ = _db(tmp_path)
    cid = create_campaign(db, name="Rank 6 hunt", target_rank=6)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="4",
        generic_lower=2,
        descent_lower=4,
    )
    update_curve(db, curve_id, exact_rank=4, descent_upper=4)
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "curve_id": curve_id},
    )

    brief = campaign_research_brief(db, cid)
    assert not brief["target_reached"]
    assert brief["target_gap"] == 2
    assert brief["next_action"]["page"] == "Pipelines"
    assert brief["next_action"]["title"] == "Continue the campaign search"
    assert brief["next_action"]["planner_kind"] == "campaign_search"
    assert brief["next_action"]["planner_version"] == PLANNER_VERSION
    assert "exact rank 4" in brief["next_action"]["why"]
    assert "target ≥6" in brief["next_action"]["why"]
    assert "curve_id" not in brief["next_action"]


def test_campaign_strategy_history_tracks_frontier_gains(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Strategy history", target_rank=8)

    r1 = create_pipeline_run(
        db,
        pipeline_name="Prime Funnel",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    update_pipeline_run(
        db,
        r1,
        status="completed",
        candidates_total=100,
        candidates_done=100,
        best_lower=3,
        finished_at="2026-09-21T10:00:00+00:00",
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "pipeline_run_id": r1},
    )

    r2 = create_pipeline_run(
        db,
        pipeline_name="Geometry Grinder",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "integral_seed", "config": {}}],
    )
    update_pipeline_run(
        db,
        r2,
        status="completed",
        candidates_total=12,
        candidates_done=12,
        best_lower=5,
        finished_at="2026-09-21T11:00:00+00:00",
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "pipeline_run_id": r2},
    )

    r3 = create_pipeline_run(
        db,
        pipeline_name="Prime Funnel",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
    )
    update_pipeline_run(
        db,
        r3,
        status="completed",
        candidates_total=200,
        candidates_done=200,
        best_lower=4,
        finished_at="2026-09-21T12:00:00+00:00",
    )
    _fake_job(
        db,
        tmp_path,
        metadata={"campaign_id": cid, "pipeline_run_id": r3},
    )

    brief = campaign_research_brief(db, cid)
    assert [rec["new_lower"] for rec in brief["frontier_milestones"]] == [3, 5]

    by_name = {rec["strategy"]: rec for rec in brief["strategy_performance"]}
    assert by_name["Prime Funnel"]["runs"] == 2
    assert by_name["Prime Funnel"]["candidates"] == 300
    assert by_name["Prime Funnel"]["best_lower"] == 4
    assert by_name["Prime Funnel"]["frontier_gains"] == 1

    assert by_name["Geometry Grinder"]["runs"] == 1
    assert by_name["Geometry Grinder"]["candidates"] == 12
    assert by_name["Geometry Grinder"]["best_lower"] == 5
    assert by_name["Geometry Grinder"]["frontier_gains"] == 1

    assert [rec["run_id"] for rec in brief["recent_activity"][:3]] == [r3, r2, r1]
    assert [rec["strategy"] for rec in brief["recent_activity"][:3]] == [
        "Prime Funnel",
        "Geometry Grinder",
        "Prime Funnel",
    ]


def test_campaign_current_rank_is_separate_from_historical_run_best(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Current versus historical", target_rank=10)
    curve_id = upsert_curve(
        db,
        family="demo",
        parameter="5",
        descent_lower=5,
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Historical high",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"campaign_id": cid, "campaign_name": "Current versus historical"},
    )
    update_pipeline_run(
        db,
        run_id,
        status="completed",
        candidates_total=10,
        candidates_done=10,
        best_lower=9,
        best_curve_id=curve_id,
        finished_at="2026-09-21T12:30:00+00:00",
    )

    snap = campaign_snapshot(db, cid)
    brief = campaign_research_brief(db, cid)
    assert snap["best_lower"] == 9
    assert brief["best_lower"] == 5
    assert brief["target_gap"] == 5
    assert not brief["target_reached"]


def test_campaign_orphan_running_pipeline_is_counted_and_opened_directly(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Orphan active", target_rank=9)
    run_id = create_pipeline_run(
        db,
        pipeline_name="Durable active",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={
            "campaign_id": cid,
            "campaign_name": "Orphan active",
            "target_rank": 9,
        },
    )
    update_pipeline_run(
        db,
        run_id,
        status="running",
        candidates_total=100,
        candidates_done=25,
    )

    snap = campaign_snapshot(db, cid)
    assert snap["jobs"] == []
    assert snap["running"] == 1
    assert snap["queued"] == 0
    assert [int(row["id"]) for row in snap["orphan_pipeline_runs"]] == [run_id]

    brief = campaign_research_brief(db, cid)
    assert brief["best_curve"] is None
    assert brief["next_action"]["page"] == "Pipelines"
    assert brief["next_action"]["pipeline_run_id"] == run_id
    assert brief["next_action"]["title"] == "Inspect active pipeline run"


def test_campaign_linked_pipeline_job_does_not_double_count_activity(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="No double count")
    run_id = create_pipeline_run(
        db,
        pipeline_name="Linked active",
        target_mode="general",
        target={"pool_mode": "open"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={"campaign_id": cid, "campaign_name": "No double count"},
    )
    update_pipeline_run(db, run_id, status="running")
    _fake_job(
        db,
        tmp_path,
        status="running",
        metadata={"campaign_id": cid, "pipeline_run_id": run_id},
        kind="pipeline_search",
    )

    snap = campaign_snapshot(db, cid)
    assert snap["running"] == 1
    assert snap["orphan_pipeline_runs"] == []


def test_campaign_recovers_durable_pipeline_run_without_job_row(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Durable run", target_rank=9)

    curve_id = upsert_curve(
        db,
        family="durable",
        parameter="7/11",
        descent_lower=7,
    )
    run_id = create_pipeline_run(
        db,
        pipeline_name="Deep geometry",
        target_mode="family",
        target={"plugin_id": "durable"},
        stages=[{"id": "integral_seed", "config": {}}],
        run_config={
            "campaign_id": cid,
            "campaign_name": "Durable run",
            "target_rank": 9,
        },
    )
    update_pipeline_run(
        db,
        run_id,
        status="completed",
        candidates_total=50,
        candidates_done=42,
        best_lower=7,
        best_curve_id=curve_id,
        finished_at="2026-09-21T13:00:00+00:00",
    )

    snap = campaign_snapshot(db, cid)
    assert snap["jobs"] == []
    assert [int(row["id"]) for row in snap["pipeline_runs"]] == [run_id]
    assert snap["best_lower"] == 7
    assert snap["candidates_done"] == 42
    assert campaign_curve_ids(db, cid) == [curve_id]

    brief = campaign_research_brief(db, cid)
    assert int(brief["best_curve"]["id"]) == curve_id
    assert brief["frontier_milestones"][0]["new_lower"] == 7
    assert brief["frontier_milestones"][0]["job_id"] is None
    assert brief["frontier_milestones"][0]["run_id"] == run_id


def test_saved_pipeline_launch_persists_active_campaign_in_run_config(tmp_path, monkeypatch):
    db, path = _db(tmp_path)
    cid = create_campaign(db, name="Pipeline ownership", target_rank=14)
    set_active_campaign(db, cid)

    captured = {}

    monkeypatch.setattr(
        "rank42.pipeline_catalog.normalize_pipeline",
        lambda stages: list(stages),
    )
    monkeypatch.setattr(
        "rank42.pipeline_catalog.validate_pipeline",
        lambda mode, stages: [],
    )

    def fake_create_pipeline_run(db_, **kwargs):
        captured["run_config"] = dict(kwargs["run_config"])
        captured["target"] = dict(kwargs["target"])
        return 314

    monkeypatch.setattr(
        "rank42.pipeline_state.create_pipeline_run",
        fake_create_pipeline_run,
    )

    def fake_launch_resolved(ctx, db_, **kwargs):
        captured["metadata"] = dict(kwargs["metadata"])
        captured["campaign_failure"] = kwargs.get("campaign_failure")
        return 2718

    monkeypatch.setattr(common, "launch_resolved", fake_launch_resolved)

    ctx = UIContext(tmp_path, path, None)
    job_id = common.launch_with_pipeline(
        ctx,
        db,
        pipeline_choice={
            "pipeline": {
                "id": 1,
                "name": "Saved strategy",
                "target_mode": "family",
                "stages": [],
                "config": {},
                "revision": 4,
                "content_hash": "abc123",
            },
            "target_rank": 14,
        },
        target_mode="family",
        target={"plugin_id": "demo", "variant_id": "v1"},
        run_config={},
        native_kind="test",
        native_label="Native family search",
        native_command=["python", "-c", "print(1)"],
        native_metadata={"plugin_id": "demo"},
    )

    assert job_id == 2718
    assert captured["run_config"]["campaign_id"] == cid
    assert captured["run_config"]["campaign_name"] == "Pipeline ownership"
    assert captured["run_config"]["target_rank"] == 14
    assert captured["run_config"]["source_pipeline_id"] == 1
    assert captured["run_config"]["source_pipeline_revision"] == 4
    assert captured["run_config"]["source_pipeline_hash"] == "abc123"
    assert captured["run_config"]["pipeline_source_relation"] == "exact"
    assert captured["metadata"]["campaign_id"] == cid
    assert captured["metadata"]["campaign_name"] == "Pipeline ownership"
    assert captured["metadata"]["pipeline_run_id"] == 314
    assert captured["campaign_failure"] is None


def test_campaign_rename_preserves_pipeline_run_launch_snapshot(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Old pipeline campaign")
    created_at = get_campaign(db, cid)["created_at"]
    run_id = create_pipeline_run(
        db,
        pipeline_name="Owned run",
        target_mode="general",
        target={},
        stages=[],
        run_config={
            "campaign_id": cid,
            "campaign_name": "Old pipeline campaign",
            "campaign_created_at": created_at,
        },
    )

    update_campaign(db, cid, name="New pipeline campaign")
    row = get_pipeline_run(db, run_id)
    config = json.loads(row["run_config_json"])
    assert row["campaign_id"] == cid
    assert pipeline_run_campaign_id(row) == cid
    assert config["campaign_id"] == cid
    assert config["campaign_name"] == "Old pipeline campaign"
    assert config["campaign_created_at"] == created_at


def test_campaign_delete_detaches_pipeline_run_but_preserves_snapshot(tmp_path):
    db, _ = _db(tmp_path)
    ensure_pipeline_schema(db)
    cid = create_campaign(db, name="Delete pipeline campaign")
    created_at = get_campaign(db, cid)["created_at"]
    run_id = create_pipeline_run(
        db,
        pipeline_name="Detached run",
        target_mode="general",
        target={},
        stages=[],
        run_config={
            "campaign_id": cid,
            "campaign_name": "Delete pipeline campaign",
            "campaign_created_at": created_at,
        },
    )

    delete_campaign(db, cid)
    row = get_pipeline_run(db, run_id)
    config = json.loads(row["run_config_json"])
    assert row["campaign_id"] is None
    assert pipeline_run_campaign_id(row) is None
    assert "campaign_id" not in config
    assert config["campaign_name"] == "Delete pipeline campaign"
    assert config["campaign_created_at"] == created_at
