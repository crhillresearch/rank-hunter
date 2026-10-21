import sqlite3

from rank42.ui_store import (
    create_job,
    ensure_ui_schema,
    get_application_state,
    get_job,
    get_setting,
    set_application_state,
    set_setting,
    update_job,
)


def test_ui_schema_settings_and_jobs(tmp_path):
    db = sqlite3.connect(tmp_path / "ui-test.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    tables = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    job_fks = db.execute("PRAGMA foreign_key_list(ui_jobs)").fetchall()
    assert "research_campaigns" in tables
    assert "analysis_cases" in tables
    assert any(
        row["table"] == "research_campaigns"
        and row["from"] == "campaign_id"
        and row["on_delete"].upper() == "SET NULL"
        for row in job_fks
    )

    assert get_setting(db, "missing", 17) == 17
    set_setting(db, "charts", 8)
    assert get_setting(db, "charts") == 8

    job_id = create_job(
        db,
        kind="test",
        label="test job",
        command=["python", "-c", "print(1)"],
        cwd=tmp_path,
        log_path=tmp_path / "job.log",
        metadata={"curve_id": 42},
    )
    row = get_job(db, job_id)
    assert row["status"] == "queued"
    assert row["label"] == "test job"

    update_job(db, job_id, status="succeeded", exit_code=0)
    row = get_job(db, job_id)
    assert row["status"] == "succeeded"
    assert row["exit_code"] == 0
    db.close()


def test_process_alive_rejects_linux_zombie(monkeypatch):
    from rank42 import ui_store

    monkeypatch.setattr(ui_store, "_linux_process_state", lambda pid: "Z")

    def should_not_probe(_pid, _sig):
        raise AssertionError("zombie should be rejected before kill(0)")

    monkeypatch.setattr(ui_store.os, "kill", should_not_probe)
    assert ui_store.process_alive(424242) is False


def test_process_alive_accepts_non_zombie_existing_process(monkeypatch):
    from rank42 import ui_store

    monkeypatch.setattr(ui_store, "_linux_process_state", lambda pid: "S")
    monkeypatch.setattr(ui_store.os, "kill", lambda pid, sig: None)
    assert ui_store.process_alive(424242) is True


def test_ui_runner_forces_unbuffered_child_python():
    from rank42.ui_job_runner import child_environment

    env = child_environment()
    assert env["PYTHONUNBUFFERED"] == "1"


def test_failed_batch_cursor_resumes_at_first_unfinished_candidate(tmp_path):
    import json

    from rank42.ui_store import finalize_batch_cursor

    db = sqlite3.connect(tmp_path / "ui-test.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    set_application_state(db, "candidate_file", "candidates/scout.jsonl")
    set_application_state(db, "batch_offset", 0)
    log_path = tmp_path / "job.log"
    job_id = create_job(
        db,
        kind="kihara_chart_batch",
        label="batch",
        command=["python", "-m", "rank42.kihara_chart_search"],
        cwd=tmp_path,
        log_path=log_path,
        metadata={"source": "candidates/scout.jsonl", "offset": 0, "count": 20},
    )
    log_path.write_text(
        """candidates          = 20
charts/candidate    = 8
[1/20] curve #1 t=1 score=1.0
[candidate done] 1/20
[2/20] curve #2 t=2 score=0.9
    [chart 1/8]
      H=1000      4 point(s) in 0.1s
"""
    )
    update_job(db, job_id, status="failed", exit_code=1)
    next_offset = finalize_batch_cursor(db, job_id)
    assert next_offset == 1
    assert get_application_state(db, "batch_offset") == 1  # candidate 2 next.
    metadata = json.loads(get_job(db, job_id)["metadata_json"])
    assert metadata["cursor_completed"] == 1
    db.close()


def test_failed_mestre_batch_cursor_resumes_at_first_unfinished_candidate(tmp_path):
    import json

    from rank42.ui_store import finalize_batch_cursor

    db = sqlite3.connect(tmp_path / "ui-mestre-test.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    set_application_state(db, "candidate_file", "candidates/mestre.jsonl")
    set_application_state(db, "batch_offset", 10)
    log_path = tmp_path / "mestre.log"
    job_id = create_job(
        db,
        kind="mestre_quartic_batch",
        label="mestre batch",
        command=["python", "-m", "rank42.mestre_quartic_search"],
        cwd=tmp_path,
        log_path=log_path,
        metadata={"source": "candidates/mestre.jsonl", "offset": 10, "count": 20},
    )
    log_path.write_text(
        """candidates          = 20
[1/20] curve #10 t=1069/4 score=8.0
[candidate done] 1/20
[2/20] curve #11 t=842/9 score=7.9
    [mestre stage] mode=integer H=1000 points=12 runtime=0.1
"""
    )
    update_job(db, job_id, status="failed", exit_code=1)
    next_offset = finalize_batch_cursor(db, job_id)
    assert next_offset == 11
    assert get_application_state(db, "batch_offset") == 11
    metadata = json.loads(get_job(db, job_id)["metadata_json"])
    assert metadata["cursor_completed"] == 1
    db.close()


def test_external_job_module_parser_recognizes_rank42_cli():
    from rank42.ui_store import _module_from_command

    assert _module_from_command(
        "/opt/sage/bin/python -m rank42.mestre_chart_search --db rank42.db --charts 32"
    ) == "rank42.mestre_chart_search"
    assert _module_from_command("python script.py") is None


def test_pool_job_cancel_marks_only_completed_candidates(tmp_path):
    import json
    from rank42.db import connect
    from rank42.candidates import create_pool, replace_pool_rows, candidate_rows, pool_resume_offset
    from rank42.ui_store import finalize_pool_search

    db=connect(tmp_path/'rank42.db')
    ensure_ui_schema(db)
    pool=create_pool(db,name='pool-job',plugin_id='p',family_spec='f')
    replace_pool_rows(db,pool['id'],[{'t':str(i),'score':10-i} for i in range(1,5)])
    rows=candidate_rows(db,pool['id'])
    ids=[int(r['id']) for r in rows]
    log=tmp_path/'pool.log'
    jid=create_job(db,kind='family_search',label='pool',command=['python'],cwd=tmp_path,log_path=log,
                   metadata={'pool_id':int(pool['id']),'candidate_ids':ids,'offset':0,'count':4})
    log.write_text('''[1/4] curve #1 t=1 score=9\n[candidate done] 1/4\n[2/4] curve #2 t=2 score=8\n[candidate done] 2/4\n[3/4] curve #3 t=3 score=7\n    building exact native Mestre quartic + map...\n''')
    update_job(db,jid,status='stopped')
    assert finalize_pool_search(db,jid)==2
    states=[r['status'] for r in candidate_rows(db,pool['id'])]
    assert states==['searched','searched','unsearched','unsearched']
    assert pool_resume_offset(db,pool['id'])==2
    meta=json.loads(get_job(db,jid)['metadata_json'])
    assert meta['pool_candidates_completed']==2
    assert meta['pool_candidates_planned']==4
    db.close()

def test_force_kill_marks_job_killed_and_finalizes(tmp_path, monkeypatch):
    from rank42 import ui_store
    db = sqlite3.connect(tmp_path / 'kill.db')
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    log = tmp_path / 'kill.log'
    jid = create_job(db, kind='test', label='kill me', command=['python'], cwd=tmp_path, log_path=log)
    update_job(db, jid, pid=424242, status='running')
    monkeypatch.setattr(ui_store, 'process_alive', lambda pid: True)
    calls=[]
    monkeypatch.setattr(ui_store, '_descendant_process_groups', lambda pid: [515151])
    monkeypatch.setattr(ui_store.os, 'killpg', lambda pid, sig: calls.append((pid, sig)))
    assert ui_store.stop_job(db, jid, force=True) is True
    row = get_job(db, jid)
    assert row['status'] == 'killed'
    assert [pid for pid, _ in calls] == [515151, 424242]
    db.close()



def test_stop_job_signals_before_database_status_write(tmp_path, monkeypatch):
    from rank42 import ui_store

    db = sqlite3.connect(tmp_path / "signal-first.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    log = tmp_path / "signal-first.log"
    jid = create_job(
        db,
        kind="test",
        label="signal first",
        command=["python"],
        cwd=tmp_path,
        log_path=log,
    )
    update_job(db, jid, pid=424242, status="running")

    events = []
    monkeypatch.setattr(ui_store, "process_alive", lambda pid: True)
    monkeypatch.setattr(ui_store, "_descendant_process_groups", lambda pid: [515151])
    monkeypatch.setattr(
        ui_store.os,
        "killpg",
        lambda pid, sig: events.append(("signal", int(pid))),
    )
    real_update = ui_store.update_job

    def tracked_update(db_, job_id, **fields):
        events.append(("update", fields.get("status")))
        return real_update(db_, job_id, **fields)

    monkeypatch.setattr(ui_store, "update_job", tracked_update)

    assert ui_store.stop_job(db, jid, force=False) is True
    assert events[:2] == [("signal", 515151), ("signal", 424242)]
    assert events[2] == ("update", "stopping")
    db.close()


def test_best_effort_ui_write_defers_locked_shutdown_bookkeeping(tmp_path):
    from rank42 import ui_store

    path = tmp_path / "locked-stop.db"
    db1 = sqlite3.connect(path)
    db2 = sqlite3.connect(path)
    db1.execute("CREATE TABLE t(x INTEGER)")
    db1.commit()
    db1.execute("BEGIN IMMEDIATE")
    db1.execute("INSERT INTO t VALUES(1)")

    completed = []
    ok = ui_store._best_effort_ui_write(
        db2,
        lambda: db2.execute("INSERT INTO t VALUES(2)"),
        busy_timeout_ms=10,
    )
    assert ok is False
    assert completed == []

    db1.rollback()
    db1.close()
    db2.close()


def test_force_kill_persists_interrupted_hard_case_stage(tmp_path, monkeypatch):
    import json
    from rank42 import ui_store
    from rank42.db import connect, update_curve, upsert_curve

    db = connect(tmp_path / "hard-stop.db")
    ensure_ui_schema(db)
    cid = upsert_curve(db, family="test", parameter="hard-stop")
    update_curve(
        db,
        cid,
        a_invariants_json='["0","0","0","-1","1"]',
        descent_lower=1,
        generators_json='[["0","1"]]',
    )
    log = tmp_path / "hard-stop.log"
    jid = create_job(
        db,
        kind="independence",
        label="hard stop",
        command=[
            "python", "-m", "rank42.independence_check",
            "--timeout", "600",
            "--max-halvings", "250",
            "--max-prime", "2000000",
            "--max-columns", "1280",
        ],
        cwd=tmp_path,
        log_path=log,
        metadata={
            "curve_id": cid,
            "point_ids": [77],
            "mode": "hard_case_basis_first_saturation",
            "saturation_timeout": 1200,
        },
    )
    log.write_text(
        "[stage 1/3] rigorous-basis 2-saturation started · points=1\n"
        "[heartbeat] basis 2-saturation curve #1 still running · 780s elapsed\n"
    )
    update_job(db, jid, pid=424242, status="running")

    monkeypatch.setattr(ui_store, "process_alive", lambda pid: True)
    monkeypatch.setattr(ui_store, "_descendant_process_groups", lambda pid: [])
    monkeypatch.setattr(ui_store.os, "killpg", lambda pid, sig: None)

    assert ui_store.stop_job(db, jid, force=True) is True

    evidence = db.execute(
        """SELECT * FROM rank_evidence
           WHERE curve_id=? AND engine='rank42.hard_case_escalator'
             AND evidence_type='hard_case_stage'
           ORDER BY id DESC LIMIT 1""",
        (cid,),
    ).fetchone()
    assert evidence is not None
    assert evidence["status"] == "interrupted"
    options = json.loads(evidence["options_json"])
    assert options["method"] == "basis_2_saturation"
    assert options["budget"]["saturation_timeout"] == 780
    assert options["budget"]["certificate_timeout"] == 600
    assert options["budget"]["max_halvings"] == 250
    db.close()



def test_live_candidate_progress_reads_only_bounded_log_tail(tmp_path):
    from rank42.ui_store import count_candidate_done, latest_candidate_done

    log = tmp_path / "large-job.log"
    log.write_text(
        "[candidate done] 1/20\n"
        + ("x" * 200_000)
        + "\n",
        encoding="utf-8",
    )

    # The old marker is outside the bounded polling window, so live progress
    # reports unknown instead of replaying the complete historical log.
    assert latest_candidate_done(log, max_bytes=4096) is None

    with log.open("a", encoding="utf-8") as handle:
        handle.write("[candidate done] 7/20\n")

    assert latest_candidate_done(log, max_bytes=4096) == 7

    # Recovery/finalization still has the exact full-log primitive.
    assert count_candidate_done(log) == 2



def test_dispatcher_heartbeat_uses_service_state_table_not_settings(
    tmp_path,
    monkeypatch,
):
    from rank42 import ui_store

    db = sqlite3.connect(tmp_path / "heartbeat-service-state.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    monkeypatch.setattr(ui_store, "process_alive", lambda pid: int(pid) == 4242)

    payload = ui_store.set_dispatcher_heartbeat(
        db,
        pid=4242,
        worker_id="worker-1",
        status="running",
        max_workers=3,
        resource_limits={"sage_heavy": 2},
    )

    assert get_setting(db, "dispatcher_heartbeat", None) is None
    row = db.execute(
        """SELECT service_id,pid,worker_id,status,max_workers,
                  resource_limits_json,updated_at
           FROM ui_service_heartbeats
           WHERE service_id='dispatcher'"""
    ).fetchone()
    assert row is not None
    assert row["pid"] == 4242
    assert row["worker_id"] == "worker-1"
    assert row["status"] == "running"
    assert row["max_workers"] == 3
    assert '"sage_heavy": 2' in row["resource_limits_json"]
    assert row["updated_at"] == payload["updated_at"]

    status = ui_store.dispatcher_status(db)
    assert status["healthy"] is True
    assert status["pid"] == 4242
    assert status["resource_limits"] == {"sage_heavy": 2}
    db.close()


def test_legacy_dispatcher_heartbeat_moves_out_of_ui_settings(tmp_path):
    import json

    from rank42 import ui_store

    db = sqlite3.connect(tmp_path / "heartbeat-migration.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    # Recreate the pre-SYSTEM-12 shape: generic settings exists but the
    # dedicated service heartbeat table does not.
    db.execute("DROP TABLE ui_service_heartbeats")
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('dispatcher_heartbeat',?,?)""",
        (
            json.dumps({
                "pid": 111,
                "worker_id": "legacy-worker",
                "status": "running",
                "max_workers": 2,
                "resource_limits": {"gpu_ratpoints": 1},
                "updated_at": "2026-09-25T12:00:00+00:00",
            }),
            "2026-09-25T12:00:00+00:00",
        ),
    )
    db.commit()

    assert ensure_ui_schema(db) is True

    assert get_setting(db, "dispatcher_heartbeat", None) is None
    row = db.execute(
        """SELECT pid,worker_id,status,max_workers,resource_limits_json,
                  updated_at
           FROM ui_service_heartbeats
           WHERE service_id='dispatcher'"""
    ).fetchone()
    assert row is not None
    assert row["pid"] == 111
    assert row["worker_id"] == "legacy-worker"
    assert row["status"] == "running"
    assert row["max_workers"] == 2
    assert json.loads(row["resource_limits_json"]) == {"gpu_ratpoints": 1}
    assert row["updated_at"] == "2026-09-25T12:00:00+00:00"
    db.close()



def test_application_state_round_trips_outside_ui_settings(tmp_path):
    db = sqlite3.connect(tmp_path / "application-state.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    set_application_state(db, "current_campaign_id", 17)
    assert get_application_state(db, "current_campaign_id") == 17
    assert get_setting(db, "current_campaign_id", None) is None

    set_application_state(db, "current_campaign_id", None)
    assert get_application_state(db, "current_campaign_id", None) is None
    row = db.execute(
        """SELECT 1 FROM ui_application_state
           WHERE state_key='current_campaign_id'"""
    ).fetchone()
    assert row is None
    db.close()


def test_legacy_active_campaign_moves_to_current_application_state(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "current-campaign-migration.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    db.execute("DROP TABLE ui_application_state")
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('active_campaign_id',?,?)""",
        (json.dumps(29), "2026-09-25T12:00:00+00:00"),
    )
    db.commit()

    assert ensure_ui_schema(db) is True

    assert get_setting(db, "active_campaign_id", None) is None
    assert get_application_state(db, "current_campaign_id", None) == 29
    row = db.execute(
        """SELECT value_json,updated_at FROM ui_application_state
           WHERE state_key='current_campaign_id'"""
    ).fetchone()
    assert json.loads(row["value_json"]) == 29
    assert row["updated_at"] == "2026-09-25T12:00:00+00:00"
    db.close()


def test_legacy_active_application_state_moves_to_current_key(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "current-campaign-app-migration.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    set_application_state(db, "active_campaign_id", 23)
    assert ensure_ui_schema(db) is True

    assert get_application_state(db, "current_campaign_id") == 23
    assert get_application_state(db, "active_campaign_id", None) is None
    db.close()


def test_existing_current_application_state_wins_over_legacy_campaign_setting(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "current-campaign-precedence.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    set_application_state(db, "current_campaign_id", 31)
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('active_campaign_id',?,?)""",
        (json.dumps(12), "2026-09-25T12:00:00+00:00"),
    )
    db.commit()

    assert ensure_ui_schema(db) is True
    assert get_application_state(db, "current_campaign_id") == 31
    assert get_setting(db, "active_campaign_id", None) is None
    db.close()



def test_legacy_candidate_cursor_moves_out_of_ui_settings(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "candidate-cursor-migration.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('candidate_file',?,?)""",
        (json.dumps("candidates/legacy.jsonl"), "2026-09-25T12:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('batch_offset',?,?)""",
        (json.dumps(40), "2026-09-25T12:01:00+00:00"),
    )
    db.commit()

    assert ensure_ui_schema(db) is True

    assert get_setting(db, "candidate_file", None) is None
    assert get_setting(db, "batch_offset", None) is None
    assert get_application_state(db, "candidate_file") == (
        "candidates/legacy.jsonl"
    )
    assert get_application_state(db, "batch_offset") == 40
    db.close()


def test_existing_candidate_cursor_state_wins_over_legacy_settings(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "candidate-cursor-precedence.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    set_application_state(db, "candidate_file", "candidates/current.jsonl")
    set_application_state(db, "batch_offset", 80)
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('candidate_file',?,?)""",
        (json.dumps("candidates/stale.jsonl"), "2026-09-25T12:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('batch_offset',?,?)""",
        (json.dumps(20), "2026-09-25T12:01:00+00:00"),
    )
    db.commit()

    assert ensure_ui_schema(db) is True

    assert get_application_state(db, "candidate_file") == (
        "candidates/current.jsonl"
    )
    assert get_application_state(db, "batch_offset") == 80
    assert get_setting(db, "candidate_file", None) is None
    assert get_setting(db, "batch_offset", None) is None
    db.close()


def test_batch_cursor_does_not_advance_after_application_state_override(tmp_path):
    import json

    from rank42.ui_store import finalize_batch_cursor

    db = sqlite3.connect(tmp_path / "candidate-cursor-override.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    set_application_state(db, "candidate_file", "candidates/original.jsonl")
    set_application_state(db, "batch_offset", 10)

    log_path = tmp_path / "override.log"
    job_id = create_job(
        db,
        kind="kihara_chart_batch",
        label="cursor override",
        command=["python", "-m", "rank42.kihara_chart_search"],
        cwd=tmp_path,
        log_path=log_path,
        metadata={
            "source": "candidates/original.jsonl",
            "offset": 10,
            "count": 20,
        },
    )
    log_path.write_text(
        """candidates          = 20
charts/candidate    = 8
[1/20] curve #10 t=1 score=1.0
[candidate done] 1/20
[2/20] curve #11 t=2 score=0.9
[candidate done] 2/20
""",
        encoding="utf-8",
    )

    set_application_state(db, "batch_offset", 99)
    next_offset = finalize_batch_cursor(db, job_id)

    assert next_offset == 12
    assert get_application_state(db, "batch_offset") == 99
    metadata = json.loads(get_job(db, job_id)["metadata_json"])
    assert metadata["cursor_advanced"] is False
    db.close()



def test_candidate_cursor_migration_participates_in_outer_transaction(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "candidate-cursor-rollback.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('candidate_file',?,?)""",
        (json.dumps("candidates/rollback.jsonl"), "2026-09-25T12:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('batch_offset',?,?)""",
        (json.dumps(60), "2026-09-25T12:01:00+00:00"),
    )
    db.commit()

    db.execute("BEGIN IMMEDIATE")
    assert ensure_ui_schema(db, commit=False) is True
    assert get_setting(db, "candidate_file", None) is None
    assert get_application_state(db, "candidate_file") == (
        "candidates/rollback.jsonl"
    )
    assert get_application_state(db, "batch_offset") == 60

    db.rollback()

    assert get_setting(db, "candidate_file") == "candidates/rollback.jsonl"
    assert get_setting(db, "batch_offset") == 60
    assert get_application_state(db, "candidate_file", None) is None
    assert get_application_state(db, "batch_offset", None) is None
    db.close()



def test_legacy_pipeline_editor_draft_moves_out_of_ui_settings(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "pipeline-draft-migration.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    legacy = {
        "version": 1,
        "mode": "general",
        "session": {"byo_stages": [{"id": "nagao_screen", "config": {}}]},
    }
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('pipeline_editor_draft_v1',?,?)""",
        (json.dumps(legacy), "2026-09-25T13:00:00+00:00"),
    )
    db.commit()

    assert ensure_ui_schema(db) is True

    assert get_setting(db, "pipeline_editor_draft_v1", None) is None
    assert get_application_state(
        db, "pipeline_editor_draft_v1", None
    ) == legacy
    row = db.execute(
        """SELECT updated_at FROM ui_application_state
           WHERE state_key='pipeline_editor_draft_v1'"""
    ).fetchone()
    assert row["updated_at"] == "2026-09-25T13:00:00+00:00"
    db.close()


def test_existing_pipeline_editor_draft_state_wins_over_legacy_setting(tmp_path):
    import json

    db = sqlite3.connect(tmp_path / "pipeline-draft-precedence.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    current = {"version": 1, "mode": "family", "session": {"byo_stages": []}}
    stale = {"version": 1, "mode": "general", "session": {"byo_stages": []}}
    set_application_state(db, "pipeline_editor_draft_v1", current)
    db.execute(
        """INSERT INTO ui_settings(key,value_json,updated_at)
           VALUES('pipeline_editor_draft_v1',?,?)""",
        (json.dumps(stale), "2026-09-25T13:00:00+00:00"),
    )
    db.commit()

    assert ensure_ui_schema(db) is True
    assert get_application_state(db, "pipeline_editor_draft_v1") == current
    assert get_setting(db, "pipeline_editor_draft_v1", None) is None
    db.close()
