import inspect
import json
import sqlite3
from pathlib import Path

from rank42 import manage_store
from rank42.ui_pages import build_your_own, common, jobs_detail_panel, jobs_page
from rank42.ui_store import create_job, ensure_ui_schema, get_job, update_job


ROOT = Path(__file__).resolve().parents[1]


def _job_db(tmp_path):
    db = sqlite3.connect(tmp_path / "jobs.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    return db


def test_pause_marker_is_durable_and_stop_clears_it(tmp_path):
    from rank42 import ui_store

    db = _job_db(tmp_path)
    log = tmp_path / "job.log"
    jid = create_job(
        db,
        kind="test",
        label="pause me",
        command=["python", "-c", "print(1)"],
        cwd=tmp_path,
        log_path=log,
        metadata={"curve_id": 7},
    )
    update_job(db, jid, status="running", pid=None)

    assert ui_store.stop_job(db, jid, force=False, pause=True) is False
    row = get_job(db, jid)
    meta = json.loads(row["metadata_json"])
    assert row["status"] == "stopped"
    assert meta["pause_requested"] is True
    assert meta["paused_at"]

    assert ui_store.stop_job(db, jid, force=False, pause=False) is False
    meta = json.loads(get_job(db, jid)["metadata_json"])
    assert "pause_requested" not in meta
    assert "paused_at" not in meta
    assert meta["stop_requested_at"]
    assert meta["stop_requested_force"] is False
    db.close()


def test_job_runner_preserves_operator_stop_as_stopped(tmp_path):
    from rank42.ui_job_runner import _terminal_status

    db = _job_db(tmp_path)
    log = tmp_path / "runner-stop.log"
    jid = create_job(
        db,
        kind="test",
        label="stop race",
        command=["python", "-c", "raise SystemExit(143)"],
        cwd=tmp_path,
        log_path=log,
    )

    update_job(db, jid, status="stopping")
    assert _terminal_status(db, jid, -15) == "stopped"

    update_job(db, jid, status="running")
    assert _terminal_status(db, jid, 1) == "failed"
    assert _terminal_status(db, jid, 0) == "succeeded"
    db.close()


def test_global_resume_requeues_same_durable_work(tmp_path, monkeypatch):
    db = _job_db(tmp_path)
    log = tmp_path / "resume.log"
    jid = create_job(
        db,
        kind="target_plugin",
        label="Target #42",
        command=["python", "-m", "plugins.target", "--curve-id", "42"],
        cwd=tmp_path,
        log_path=log,
        metadata={"curve_id": 42, "pause_requested": True, "paused_at": "now"},
    )
    update_job(db, jid, status="stopped")

    captured = {}

    def fake_enqueue(db_path, **kwargs):
        captured.update(kwargs)
        return 99

    monkeypatch.setattr(manage_store, "enqueue_job", fake_enqueue)
    new_id = manage_store.resume_job(db, tmp_path / "jobs.db", jid)

    assert new_id == 99
    assert captured["command"] == [
        "python", "-m", "plugins.target", "--curve-id", "42"
    ]
    assert captured["metadata"]["resume_of_job_id"] == jid
    assert captured["metadata"]["curve_id"] == 42
    assert "pause_requested" not in captured["metadata"]

    source_meta = json.loads(get_job(db, jid)["metadata_json"])
    assert source_meta["resumed_as_job_id"] == 99
    assert "pause_requested" not in source_meta
    db.close()


def test_pool_resume_rebuilds_only_unsearched_original_slice(tmp_path, monkeypatch):
    from rank42.candidates import (
        candidate_rows,
        create_pool,
        export_rows_jsonl,
        replace_pool_rows,
    )
    from rank42.db import connect

    db = connect(tmp_path / "rank42.db")
    ensure_ui_schema(db)
    pool = create_pool(
        db,
        name="resume-pool",
        plugin_id="plugin",
        family_spec="family.json",
    )
    replace_pool_rows(
        db,
        pool["id"],
        [
            {"t": "1", "score": 3},
            {"t": "2", "score": 2},
            {"t": "3", "score": 1},
        ],
    )
    rows = candidate_rows(db, pool["id"])
    ids = [int(row["id"]) for row in rows]
    original = tmp_path / ".rank42-ui" / "candidate-slices" / "pool-1-o0-n3.jsonl"
    export_rows_jsonl(db, pool["id"], rows, original)
    db.execute(
        "UPDATE candidates SET status='searched' WHERE id=?",
        (ids[0],),
    )
    db.commit()

    jid = create_job(
        db,
        kind="family_search",
        label="Family search",
        command=[
            "python", "-m", "plugins.family_search",
            "--input", str(original),
            "--limit", "3",
        ],
        cwd=tmp_path,
        log_path=tmp_path / "pool.log",
        metadata={
            "pool_id": int(pool["id"]),
            "candidate_ids": ids,
            "candidate_rank_orders": [1, 2, 3],
            "offset": 0,
            "count": 3,
            "pause_requested": True,
        },
    )
    update_job(db, jid, status="stopped")

    captured = {}

    def fake_enqueue(db_path, **kwargs):
        captured.update(kwargs)
        return 100

    monkeypatch.setattr(manage_store, "enqueue_job", fake_enqueue)
    manage_store.resume_job(db, tmp_path / "rank42.db", jid)

    assert captured["metadata"]["candidate_ids"] == ids[1:]
    assert captured["metadata"]["candidate_rank_orders"] == [2, 3]
    assert captured["metadata"]["count"] == 2
    assert captured["metadata"]["offset"] == 1
    limit_index = captured["command"].index("--limit")
    assert captured["command"][limit_index + 1] == "2"

    resumed_path = Path(captured["command"][captured["command"].index("--input") + 1])
    payloads = [
        json.loads(line)
        for line in resumed_path.read_text().splitlines()
        if line.strip()
    ]
    assert [row["_candidate_id"] for row in payloads] == ids[1:]
    db.close()


def test_pause_job_marks_pipeline_owner_paused_before_stopping(tmp_path, monkeypatch):
    from rank42 import pipeline_state

    db = _job_db(tmp_path)
    jid = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline",
        command=["python", "-m", "rank42.pipeline_runner", "--run-id", "21"],
        cwd=tmp_path,
        log_path=tmp_path / "pipeline-pause.log",
        metadata={"pipeline_run_id": 21},
    )
    update_job(db, jid, status="running", pid=424242)

    updates = []
    monkeypatch.setattr(
        pipeline_state,
        "get_pipeline_run",
        lambda db_, run_id: {"id": int(run_id)},
    )
    monkeypatch.setattr(
        pipeline_state,
        "update_pipeline_run",
        lambda db_, run_id, **fields: updates.append((int(run_id), fields)),
    )
    stopped = []
    monkeypatch.setattr(
        manage_store,
        "stop_job",
        lambda db_, job_id, **kwargs: stopped.append((int(job_id), kwargs)) or True,
    )

    assert manage_store.pause_job(db, jid) is True
    assert updates == [
        (21, {"status": "paused", "error": None, "finished_at": None})
    ]
    assert stopped == [(jid, {"force": False, "pause": True})]
    db.close()


def test_pipeline_resume_reuses_existing_run_id(tmp_path, monkeypatch):
    from rank42 import pipeline_state

    db = _job_db(tmp_path)
    jid = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline",
        command=[
            "python", "-m", "rank42.pipeline_runner",
            "--run-id", "17",
        ],
        cwd=tmp_path,
        log_path=tmp_path / "pipeline.log",
        metadata={"pipeline_run_id": 17, "pause_requested": True},
    )
    update_job(db, jid, status="stopped")

    updates = []
    monkeypatch.setattr(
        pipeline_state,
        "get_pipeline_run",
        lambda db_, run_id: {"id": int(run_id)},
    )
    monkeypatch.setattr(
        pipeline_state,
        "update_pipeline_run",
        lambda db_, run_id, **fields: updates.append((int(run_id), fields)),
    )
    captured = {}
    monkeypatch.setattr(
        manage_store,
        "enqueue_job",
        lambda db_path, **kwargs: captured.update(kwargs) or 101,
    )

    manage_store.resume_job(db, tmp_path / "jobs.db", jid)
    assert updates == [
        (17, {"status": "queued", "error": None, "finished_at": None})
    ]
    assert captured["metadata"]["pipeline_run_id"] == 17
    run_index = captured["command"].index("--run-id")
    assert captured["command"][run_index + 1] == "17"
    db.close()


def test_active_work_component_is_sticky_soft_blue_and_fontawesome():
    asset = common.ACTIVE_WORK_COMPONENT_DIR / "index.html"
    assert asset.is_file()
    html = asset.read_text()

    for icon in ("play", "pause", "stop", "xmark"):
        assert f'"{icon}":' in html
    assert 'destructive(actions,"stop","stop","Stop job","Stop job?","stop")' in html
    assert 'destructive(actions,"force_stop","xmark","Force stop job","Force stop?","force")' in html
    assert "confirmAction(actions,actionName,label)" in html
    assert "streamlit:setComponentValue" in html

    source = inspect.getsource(common._render_active_jobs)
    assert "position: sticky;" in source
    assert "top: 1.75rem;" in source
    assert "var(--rh-info, #2563eb)" in source
    assert "var(--rh-raised, transparent)" in source
    assert "var(--rh-border, transparent)" in source
    assert "rgba(37, 99, 235, .10)" not in source
    assert "_ACTIVE_WORK(" in source
    assert "pause_job(db, job_id)" in source
    assert "resume_job(db, db_path, job_id)" in source


def test_pipeline_resume_blocks_newer_runtime_schema(tmp_path, monkeypatch):
    from rank42 import pipeline_state
    from rank42.pipeline_state import create_pipeline_run, ensure_pipeline_schema, update_pipeline_run

    db = _job_db(tmp_path)
    ensure_pipeline_schema(db)
    run_id = create_pipeline_run(
        db,
        pipeline_name="Future schema",
        target_mode="general",
        target={},
        stages=[],
    )
    update_pipeline_run(db, run_id, status="failed")

    monkeypatch.setattr(
        pipeline_state,
        "pipeline_run_runtime_drift",
        lambda row: {
            "legacy": False,
            "blocking": ["run schema 99 is newer than current schema 3"],
            "warnings": [],
            "current": {},
            "stored": {},
        },
    )
    try:
        manage_store.resume_pipeline_run(db, tmp_path / "jobs.db", run_id)
    except ValueError as exc:
        assert "cannot be resumed with this runtime" in str(exc)
        assert "run schema 99" in str(exc)
    else:
        raise AssertionError("newer Pipeline schema should block resume")
    db.close()


def test_pipeline_run_handoff_focuses_existing_process_in_jobs(tmp_path, monkeypatch):
    import types
    from rank42.pipeline_state import create_pipeline_run, ensure_pipeline_schema

    db = _job_db(tmp_path)
    ensure_pipeline_schema(db)
    run_id = create_pipeline_run(
        db,
        pipeline_name="Handoff",
        target_mode="general",
        target={},
        stages=[],
    )
    jid = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline",
        command=["python", "-m", "rank42.pipeline_runner", "--run-id", str(run_id)],
        cwd=tmp_path,
        log_path=tmp_path / "handoff.log",
        metadata={"pipeline_run_id": run_id},
    )
    update_job(db, jid, status="failed")

    state = {
        "manage_pipeline_run_id": run_id,
        "jobs-history-select": 999,
    }
    monkeypatch.setattr(
        jobs_page,
        "st",
        types.SimpleNamespace(session_state=state),
    )

    jobs_page._pipeline_run_handoff(db, types.SimpleNamespace())
    assert state["manage_job_id"] == jid
    assert state["jobs_section_pending"] == "History"
    assert "manage_pipeline_run_id" not in state
    assert "jobs-history-select" not in state
    db.close()


def test_pipelines_run_inspector_hands_process_lifecycle_to_jobs():
    source = inspect.getsource(build_your_own._render_pipeline_runs_page)

    assert "Open in Jobs" in source
    assert "Continue it from Jobs" in source
    assert "execution controls are managed in Jobs" in source
    for lifecycle_call in (
        "pause_pipeline_run(",
        "stop_pipeline_run(",
        "resume_pipeline_run(",
        "retry_pipeline_run(",
        "stop_job(",
        "resume_job(",
        "retry_job(",
    ):
        assert lifecycle_call not in source


def test_jobs_owns_orphan_pipeline_resume_attempt_creation():
    handoff = inspect.getsource(jobs_page._pipeline_run_handoff)
    launcher = inspect.getsource(jobs_page._launch_pipeline_run_resume_attempt)

    assert '"Resume as new Job attempt"' in handoff
    assert "PIPELINE_RESUMABLE" in handoff
    assert "resume_pipeline_run(" in handoff
    assert "launch_fallback=" in handoff
    assert "_launch_pipeline_run_resume_attempt(" in handoff
    assert "launch_resolved(" in launcher
    assert '"pipeline_resume"' in launcher


def test_jobs_detail_active_history_are_extracted_without_changing_compatibility_names():
    page_source = inspect.getsource(jobs_page)

    for definition in (
        "def job_provenance(",
        "def _render_job_provenance(",
        "def _job_table_row(",
        "def _job_selector(",
        "def _campaign_assignment(",
        "def _job_detail(",
        "def _active(",
        "def _history(",
    ):
        assert definition not in page_source

    assert jobs_page.job_provenance is jobs_detail_panel.job_provenance
    assert jobs_page._render_job_provenance is jobs_detail_panel.render_job_provenance
    assert jobs_page._job_table_row is jobs_detail_panel.job_table_row
    assert jobs_page._job_selector is jobs_detail_panel.job_selector
    assert jobs_page._campaign_assignment is jobs_detail_panel.campaign_assignment
    assert jobs_page._job_detail is jobs_detail_panel.render_job_detail
    assert jobs_page._active is jobs_detail_panel.render_active
    assert jobs_page._history is jobs_detail_panel.render_history
    assert jobs_page._job_is_paused is jobs_detail_panel.job_is_paused
    assert jobs_page._job_display_status is jobs_detail_panel.job_display_status
    assert jobs_page._pipeline_run_id is jobs_detail_panel.pipeline_run_id

    detail_source = inspect.getsource(jobs_detail_panel.render_job_detail)
    controls_source = inspect.getsource(jobs_detail_panel._render_job_controls)
    campaign_source = inspect.getsource(jobs_detail_panel.campaign_assignment)
    active_source = inspect.getsource(jobs_detail_panel.render_active)
    history_source = inspect.getsource(jobs_detail_panel.render_history)

    for key in (
        "jobs-campaign-",
        "jobs-save-campaign-",
    ):
        assert key in campaign_source

    for key in (
        "jobs-pause-",
        "jobs-stop-",
        "jobs-kill-",
        "jobs-resume-",
        "jobs-retry-",
        "jobs-results-",
        "jobs-delete-confirm-",
        "jobs-delete-",
    ):
        assert key in controls_source

    assert '"jobs-active-select"' in active_source
    assert '"jobs-history-status"' in history_source
    assert '"jobs-history-kind"' in history_source
    assert '"jobs-history-select"' in history_source
    assert '"manage_job_id"' in inspect.getsource(jobs_detail_panel.job_selector)


def test_jobs_campaign_selector_reads_relational_owner():
    source = inspect.getsource(jobs_page._job_campaign)
    assert "job_campaign_id(row)" in source
    assert "metadata_json" not in source


def test_jobs_owns_results_tab_and_shared_scientific_renderer():
    page_source = inspect.getsource(jobs_page.page)
    detail_source = inspect.getsource(jobs_page._job_detail)
    controls_source = inspect.getsource(jobs_detail_panel._render_job_controls)

    assert 'sections = ["Active", "Queue", "History", "Results", "Scheduled"]' in page_source
    assert 'elif active == "Results":' in page_source
    assert "results_page.render_jobs_results(db, ctx)" in page_source

    assert "results_page.render_job_scientific_result(" in detail_source
    assert 'with st.expander("Parsed result"' not in detail_source
    assert 'with st.expander("Raw log"' not in detail_source
    assert 'st.session_state["jobs_section_pending"] = "Results"' in controls_source
    assert 'st.session_state["rh_page"] = "Jobs"' in controls_source


def test_job_provenance_projects_structured_owner_and_launch_context(tmp_path):
    from rank42.pipeline_state import (
        create_pipeline_run,
        ensure_pipeline_schema,
        save_pipeline,
    )

    db = _job_db(tmp_path)
    manage_store.ensure_manage_schema(db)
    ensure_pipeline_schema(db)

    campaign_id = manage_store.create_campaign(
        db,
        name="Provenance campaign",
    )
    campaign = manage_store.get_campaign(db, campaign_id)
    pipeline_id = save_pipeline(
        db,
        name="Provenance pipeline",
        target_mode="family",
        stages=[{"id": "nagao_screen", "config": {}}],
        config={"target_rank": 12},
    )
    pipeline = db.execute(
        "SELECT * FROM search_pipeline_definitions WHERE id=?",
        (pipeline_id,),
    ).fetchone()
    run_id = create_pipeline_run(
        db,
        pipeline_id=pipeline_id,
        pipeline_name="Provenance pipeline",
        target_mode="family",
        target={"plugin_id": "family.demo"},
        stages=[{"id": "nagao_screen", "config": {}}],
        run_config={
            "campaign_id": campaign_id,
            "campaign_name": "Provenance campaign",
            "campaign_created_at": campaign["created_at"],
            "launch_surface": "pipelines",
            "plugin_id": "family.demo",
            "plugin_variant": "v2",
            "feature_plugin_ids": ["feature.alpha"],
            "source_pool_id": 91,
            "ratpoints_backend": "GPU",
        },
    )
    schedule_id = manage_store.create_schedule(
        db,
        label="Nightly provenance",
        kind="pipeline_search",
        command=["python", "-m", "rank42.pipeline_runner", "--run-id", str(run_id)],
        cwd=tmp_path,
        next_run_at="2099-01-01T00:00:00+00:00",
        recurrence="once",
        metadata={"pipeline_run_id": run_id},
        campaign_id=campaign_id,
    )
    jid = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline",
        command=["python", "-m", "rank42.pipeline_runner", "--run-id", str(run_id)],
        cwd=tmp_path,
        log_path=tmp_path / "provenance.log",
        metadata={
            "campaign_id": campaign_id,
            "campaign_name": "Provenance campaign",
            "campaign_created_at": campaign["created_at"],
            "launch_surface": "pipelines",
            "pipeline_run_id": run_id,
            "plugin_id": "family.demo",
            "plugin_variant": "v2",
            "feature_plugin_ids": ["feature.alpha"],
            "source_pool_id": 91,
            "ratpoints_backend": "GPU",
            "schedule_id": schedule_id,
            "schedule_occurrence_id": 13,
            "scheduled_for": "2099-01-01T00:00:00+00:00",
            "resume_of_job_id": 7,
        },
    )
    row = get_job(db, jid)

    provenance = dict(jobs_page.job_provenance(db, row))
    assert provenance["Launched from"] == "Pipelines"
    assert provenance["Campaign"] == f"#{campaign_id} · Provenance campaign"
    assert provenance["Pipeline Run"] == f"#{run_id} · Provenance pipeline"
    assert provenance["Pipeline"].startswith(
        f"#{pipeline_id} · revision {int(pipeline['revision'])}"
    )
    assert "hash " + str(pipeline["content_hash"])[:12] in provenance["Pipeline"]
    assert provenance["Schedule"] == f"#{schedule_id} · Nightly provenance"
    assert provenance["Occurrence"] == "#13 · 2099-01-01T00:00:00+00:00"
    assert provenance["Plugin"] == "family.demo · v2"
    assert provenance["Features"] == "feature.alpha"
    assert provenance["Candidate Pool"] == "#91"
    assert provenance["Point engine"] == "GPU"
    assert provenance["Resume lineage"] == "resumed from Job #7"
    db.close()


def test_job_provenance_uses_relational_campaign_owner_over_stale_metadata(tmp_path):
    db = _job_db(tmp_path)
    manage_store.ensure_manage_schema(db)
    first = manage_store.create_campaign(db, name="First")
    second = manage_store.create_campaign(db, name="Second")

    jid = create_job(
        db,
        kind="test",
        label="Owned",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        log_path=tmp_path / "owned.log",
        metadata={"campaign_id": first, "campaign_name": "First"},
    )
    manage_store.attach_job_to_campaign(db, jid, second)
    row = get_job(db, jid)
    meta = json.loads(row["metadata_json"])
    meta["campaign_id"] = first
    update_job(db, jid, metadata_json=json.dumps(meta, sort_keys=True))
    row = get_job(db, jid)

    provenance = dict(jobs_page.job_provenance(db, row))
    assert provenance["Campaign"] == f"#{second} · Second"
    db.close()


def test_job_provenance_surfaces_retry_lineage_without_inventing_fields(tmp_path):
    db = _job_db(tmp_path)
    jid = create_job(
        db,
        kind="test",
        label="Retry",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        log_path=tmp_path / "retry.log",
        metadata={
            "launch_surface": "target",
            "retry_of_job_id": 42,
            "plugin_id": "plugin.demo",
        },
    )
    provenance = dict(jobs_page.job_provenance(db, get_job(db, jid)))

    assert provenance["Launched from"] == "Target"
    assert provenance["Retry lineage"] == "fresh retry of Job #42"
    assert provenance["Plugin"] == "plugin.demo"
    assert "Pipeline" not in provenance
    assert "Schedule" not in provenance
    db.close()


def test_job_detail_places_separate_campaign_controls_left_and_compact_provenance_right():
    source = inspect.getsource(jobs_page._job_detail)
    renderer = inspect.getsource(jobs_page._render_job_provenance)

    assert 'left, right = st.columns(2, gap="large")' in source
    assert 'with left:' in source
    assert '"#### Campaign"' in source
    assert '"#### Controls"' in source
    assert '"#### Campaign & controls"' not in source
    assert source.count("with st.container(border=True):") >= 2
    assert "_campaign_assignment(db, row)" in source
    assert "_render_job_controls(" in source
    assert source.index('"#### Campaign"') < source.index("_campaign_assignment(db, row)")
    assert source.index("_campaign_assignment(db, row)") < source.index('"#### Controls"')
    assert source.index('"#### Controls"') < source.index("_render_job_controls(")
    assert 'with right:' in source
    assert "_render_job_provenance(db, row)" in source
    assert source.index("with right:") < source.index("_render_job_provenance(db, row)")
    assert '"#### Owner & provenance"' in renderer
    assert 'st.markdown(f"**{label}**: {value}")' in renderer
    assert '\\n{value}' not in renderer
    for label in (
        "Launched from",
        "Campaign",
        "Pipeline Run",
        "Pipeline",
        "Schedule",
        "Plugin",
        "Candidate Pool",
        "Resume lineage",
        "Retry lineage",
    ):
        assert label in inspect.getsource(jobs_page.job_provenance)


def test_jobs_page_exposes_resume_separately_from_fresh_retry():
    source = inspect.getsource(jobs_detail_panel._render_job_controls)

    assert '"Resume"' in source
    assert "resume_pipeline_run(" in source
    assert "resume_job(db, ctx.db_path" in source
    assert '"Retry fresh"' in source
    assert "retry_pipeline_run(" in source
    assert "pause_pipeline_run(" in source
    assert "stop_pipeline_run(" in source


def test_pipeline_lifecycle_authority_pauses_and_stops_same_run(tmp_path, monkeypatch):
    from rank42.pipeline_state import create_pipeline_run, ensure_pipeline_schema, get_pipeline_run

    db = _job_db(tmp_path)
    ensure_pipeline_schema(db)
    run_id = create_pipeline_run(
        db,
        pipeline_name="Lifecycle",
        target_mode="general",
        target={},
        stages=[],
    )
    jid = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline",
        command=["python", "-m", "rank42.pipeline_runner", "--run-id", str(run_id)],
        cwd=tmp_path,
        log_path=tmp_path / "pipeline-lifecycle.log",
        metadata={"pipeline_run_id": run_id},
    )
    update_job(db, jid, status="running", pid=424242)

    stopped = []
    monkeypatch.setattr(
        manage_store,
        "stop_job",
        lambda db_, job_id, **kwargs: stopped.append((int(job_id), kwargs)) or True,
    )

    assert manage_store.pause_pipeline_run(db, run_id, job_id=jid) is True
    assert get_pipeline_run(db, run_id)["status"] == "paused"
    assert stopped[-1] == (jid, {"force": False, "pause": True})

    assert manage_store.stop_pipeline_run(db, run_id, force=True, job_id=jid) is True
    run = get_pipeline_run(db, run_id)
    assert run["status"] == "killed"
    assert run["finished_at"] is not None
    assert stopped[-1] == (jid, {"force": True, "pause": False})
    db.close()


def test_pipeline_lifecycle_resume_reuses_job_or_falls_back_for_orphan(tmp_path, monkeypatch):
    from rank42.pipeline_state import create_pipeline_run, ensure_pipeline_schema, get_pipeline_run, update_pipeline_run

    db = _job_db(tmp_path)
    ensure_pipeline_schema(db)
    run_id = create_pipeline_run(
        db,
        pipeline_name="Resume",
        target_mode="general",
        target={},
        stages=[],
    )
    update_pipeline_run(db, run_id, status="paused")
    jid = create_job(
        db,
        kind="pipeline_search",
        label="Pipeline",
        command=["python", "-m", "rank42.pipeline_runner", "--run-id", str(run_id)],
        cwd=tmp_path,
        log_path=tmp_path / "pipeline-resume.log",
        metadata={"pipeline_run_id": run_id},
    )
    update_job(db, jid, status="stopped")

    captured = []
    monkeypatch.setattr(
        manage_store,
        "resume_job",
        lambda db_, db_path, job_id: captured.append(int(job_id)) or 701,
    )
    assert manage_store.resume_pipeline_run(db, tmp_path / "jobs.db", run_id) == 701
    assert captured == [jid]

    orphan_id = create_pipeline_run(
        db,
        pipeline_name="Orphan",
        target_mode="general",
        target={},
        stages=[],
    )
    update_pipeline_run(db, orphan_id, status="failed", error="old")
    launched = []
    assert manage_store.resume_pipeline_run(
        db,
        tmp_path / "jobs.db",
        orphan_id,
        launch_fallback=lambda run: launched.append(int(run["id"])) or 702,
    ) == 702
    assert launched == [orphan_id]
    assert get_pipeline_run(db, orphan_id)["status"] == "queued"
    db.close()



def test_polling_job_strip_does_not_consume_completion_notifications(monkeypatch):
    calls = []
    monkeypatch.setattr(
        common,
        "render_job_notifications",
        lambda db: calls.append("notify"),
    )
    monkeypatch.setattr(
        common,
        "_render_active_jobs",
        lambda db, db_path: [],
    )

    assert common.job_strip(object(), "rank42.db", notifications=False) == []
    assert calls == []

    assert common.job_strip(object(), "rank42.db", notifications=True) == []
    assert calls == ["notify"]


def test_active_work_progress_uses_bounded_tail_helper():
    source = inspect.getsource(common._render_active_jobs)

    assert "latest_candidate_done" in source
    assert "count_candidate_done" not in source
