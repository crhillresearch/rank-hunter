from datetime import datetime, timedelta, timezone
import inspect
import json
import sqlite3

import pytest

from rank42 import dispatcher, ui_store
from rank42.ui_pages import common, jobs_page, jobs_queue_panel
import rank42.ui as ui


def _queued(
    tmp_path,
    label,
    *,
    priority=100,
    metadata=None,
    resource_class=None,
):
    db_path = tmp_path / "rank42.db"
    job_id = ui_store.enqueue_job(
        db_path,
        kind="test",
        label=label,
        command=["python", "-c", "print('ok')"],
        cwd=tmp_path,
        metadata={"label": label, **dict(metadata or {})},
        priority=priority,
        resource_class=resource_class,
        start_dispatcher=False,
    )
    return db_path, job_id


def test_enqueue_creates_durable_job_and_queue_row(tmp_path):
    db_path, job_id = _queued(tmp_path, "one")
    db = ui_store._connect(db_path)
    try:
        job = ui_store.get_job(db, job_id)
        queue = ui_store.queue_item_for_job(db, job_id)
        assert job is not None
        assert job["status"] == "queued"
        assert queue is not None
        assert queue["state"] == "queued"
        assert int(queue["priority"]) == 100
        assert str(job["log_path"]).endswith(f"job-{job_id:06d}.log")
    finally:
        db.close()


def test_queue_claims_highest_priority_first(tmp_path):
    db_path, low = _queued(tmp_path, "low", priority=10)
    _, high = _queued(tmp_path, "high", priority=250)
    db = ui_store._connect(db_path)
    try:
        row = ui_store.claim_next_queue_item(db, "worker-a")
        assert int(row["job_id"]) == high
        assert row["state"] == "claimed"
        assert int(row["attempts"]) == 1

        row = ui_store.claim_next_queue_item(db, "worker-a")
        assert int(row["job_id"]) == low
    finally:
        db.close()


def test_queue_priority_mutation_changes_dispatch_order(tmp_path):
    db_path, first = _queued(tmp_path, "first", priority=100)
    _, second = _queued(tmp_path, "second", priority=100)
    db = ui_store._connect(db_path)
    try:
        assert ui_store.set_queue_priority(db, second, 200) == 200
        assert int(ui_store.queue_item_for_job(db, second)["priority"]) == 200

        claimed = ui_store.claim_next_queue_item(db, "worker-priority")
        assert int(claimed["job_id"]) == second

        claimed = ui_store.claim_next_queue_item(db, "worker-priority")
        assert int(claimed["job_id"]) == first
    finally:
        db.close()


def test_queue_priority_can_change_while_held_but_not_after_claim(tmp_path):
    db_path, job_id = _queued(tmp_path, "priority-state")
    db = ui_store._connect(db_path)
    try:
        assert ui_store.hold_queue_job(db, job_id)
        assert ui_store.set_queue_priority(db, job_id, 50) == 50
        held = ui_store.queue_item_for_job(db, job_id)
        assert held["state"] == "held"
        assert int(held["priority"]) == 50

        assert ui_store.release_queue_job(db, job_id)
        claimed = ui_store.claim_next_queue_item(db, "worker-priority-state")
        assert int(claimed["job_id"]) == job_id

        with pytest.raises(
            ValueError,
            match="only queued or held jobs can change priority",
        ):
            ui_store.set_queue_priority(db, job_id, 200)
    finally:
        db.close()


def test_queue_priority_is_bounded_and_human_labeled(tmp_path):
    assert ui_store.queue_priority_label(50) == "Low"
    assert ui_store.queue_priority_label(100) == "Normal"
    assert ui_store.queue_priority_label(200) == "High"
    assert ui_store.queue_priority_label(10) == "Low"
    assert ui_store.queue_priority_label(250) == "High"

    db_path, job_id = _queued(tmp_path, "bounded")
    db = ui_store._connect(db_path)
    try:
        with pytest.raises(ValueError, match="between 0 and 300"):
            ui_store.set_queue_priority(db, job_id, -1)
        with pytest.raises(ValueError, match="between 0 and 300"):
            ui_store.set_queue_priority(db, job_id, 301)
        assert int(ui_store.queue_item_for_job(db, job_id)["priority"]) == 100
    finally:
        db.close()


def test_resource_classifier_prefers_gpu_backend():
    assert (
        ui_store.infer_resource_class(
            "pipeline_search",
            {"ratpoints_backend": "GPU"},
        )
        == "gpu_ratpoints"
    )
    assert (
        ui_store.infer_resource_class(
            "pipeline_search",
            {"ratpoints_backend": "CPU"},
        )
        == "sage_heavy"
    )
    assert (
        ui_store.infer_resource_class("database_backup", {})
        == "database_maintenance"
    )


def test_blocked_resource_class_does_not_block_other_work(tmp_path):
    db_path, gpu_one = _queued(
        tmp_path,
        "gpu-one",
        metadata={"ratpoints_backend": "GPU"},
    )
    _, gpu_two = _queued(
        tmp_path,
        "gpu-two",
        metadata={"ratpoints_backend": "GPU"},
    )
    _, ordinary = _queued(
        tmp_path,
        "ordinary",
        resource_class="default",
    )
    db = ui_store._connect(db_path)
    try:
        first = ui_store.claim_next_queue_item(db, "worker-a")
        assert int(first["job_id"]) == gpu_one
        assert first["resource_class"] == "gpu_ratpoints"

        second = ui_store.claim_next_queue_item(
            db,
            "worker-a",
            blocked_resource_classes={"gpu_ratpoints"},
        )
        assert int(second["job_id"]) == ordinary
        assert second["resource_class"] == "default"

        remaining = ui_store.queue_item_for_job(db, gpu_two)
        assert remaining["state"] == "queued"
    finally:
        db.close()


def test_hold_release_and_cancel_waiting_job(tmp_path):
    db_path, job_id = _queued(tmp_path, "held")
    db = ui_store._connect(db_path)
    try:
        assert ui_store.hold_queue_job(db, job_id)
        assert ui_store.queue_item_for_job(db, job_id)["state"] == "held"
        assert ui_store.claim_next_queue_item(db, "worker-a") is None

        assert ui_store.release_queue_job(db, job_id)
        assert ui_store.queue_item_for_job(db, job_id)["state"] == "queued"

        assert ui_store.cancel_queue_job(db, job_id)
        assert ui_store.queue_item_for_job(db, job_id)["state"] == "cancelled"
        assert ui_store.get_job(db, job_id)["status"] == "stopped"
    finally:
        db.close()


def test_expired_claim_returns_to_queue(tmp_path):
    db_path, job_id = _queued(tmp_path, "recover")
    db = ui_store._connect(db_path)
    try:
        ui_store.claim_next_queue_item(db, "dead-worker", lease_seconds=10)
        db.execute(
            "UPDATE ui_job_queue SET lease_expires_at=? WHERE job_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                job_id,
            ),
        )
        db.commit()

        assert ui_store.recover_expired_queue_claims(db) == 1
        row = ui_store.queue_item_for_job(db, job_id)
        assert row["state"] == "queued"
        assert row["lease_owner"] is None
        assert row["last_error"] == "expired claim recovered"
    finally:
        db.close()


def test_expired_claim_adopts_live_started_runner(tmp_path, monkeypatch):
    db_path, job_id = _queued(tmp_path, "adopt")
    db = ui_store._connect(db_path)
    try:
        ui_store.claim_next_queue_item(db, "old-worker", lease_seconds=10)
        ui_store.update_job(
            db,
            job_id,
            status="running",
            pid=424242,
            started_at=ui_store.now(),
        )
        db.execute(
            "UPDATE ui_job_queue SET lease_expires_at=? WHERE job_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                job_id,
            ),
        )
        db.commit()
        monkeypatch.setattr(ui_store, "process_alive", lambda pid: int(pid) == 424242)

        assert ui_store.recover_expired_queue_claims(db) == 0
        row = ui_store.queue_item_for_job(db, job_id)
        assert row["state"] == "running"
        assert row["lease_expires_at"] is not None
    finally:
        db.close()


def test_legacy_launch_job_is_queue_alias(monkeypatch):
    captured = {}

    def fake_enqueue(db_path, **kwargs):
        captured["db_path"] = str(db_path)
        captured.update(kwargs)
        return 73

    monkeypatch.setattr(ui_store, "enqueue_job", fake_enqueue)
    jid = ui_store.launch_job(
        "/tmp/rank42.db",
        kind="test",
        label="compat",
        command=["python", "-c", "pass"],
        cwd="/tmp",
        metadata={"x": 1},
    )
    assert jid == 73
    assert captured["kind"] == "test"
    assert captured["metadata"] == {"x": 1}


def test_ui_schema_migrates_legacy_queued_jobs(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE ui_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            command_json TEXT NOT NULL,
            cwd TEXT NOT NULL,
            log_path TEXT NOT NULL,
            pid INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            exit_code INTEGER,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_ui_jobs_status_created
            ON ui_jobs(status, created_at DESC);
        CREATE TABLE ui_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    stamp = ui_store.now()
    cur = db.execute(
        """INSERT INTO ui_jobs(
               kind,label,command_json,cwd,log_path,status,metadata_json,
               created_at,updated_at
           ) VALUES(?,?,?,?,?,'queued',?,?,?)""",
        (
            "legacy",
            "Old queued work",
            json.dumps(["python", "-c", "pass"]),
            str(tmp_path),
            str(tmp_path / "old.log"),
            "{}",
            stamp,
            stamp,
        ),
    )
    job_id = int(cur.lastrowid)
    db.commit()

    assert ui_store.ensure_ui_schema(db) is True
    row = db.execute(
        "SELECT * FROM ui_job_queue WHERE job_id=?",
        (job_id,),
    ).fetchone()
    assert row is not None
    assert row["state"] == "queued"
    assert int(row["priority"]) == 100
    db.close()


def test_queue_schema_is_part_of_ui_schema(tmp_path):
    db = ui_store._connect(tmp_path / "rank42.db")
    try:
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
        assert "ui_job_queue" in tables
        assert "idx_ui_job_queue_dispatch" in indexes
        assert "idx_ui_job_queue_job" in indexes
    finally:
        db.close()


def test_migrated_ui_launch_and_scheduler_paths_queue_work():
    common_source = inspect.getsource(common.launch)
    enqueue_source = inspect.getsource(common._enqueue_resolved_launch)
    store_source = inspect.getsource(ui_store.launch_job)
    dispatcher_source = inspect.getsource(dispatcher.run)
    ui_source = inspect.getsource(ui.main)
    jobs_source = inspect.getsource(jobs_page.page)

    assert "launch_resolved(" in common_source
    assert "enqueue_job(" in enqueue_source
    assert "launch_job(" not in common_source

    assert "return enqueue_job(" in store_source
    assert "_start_existing_job(" not in store_source

    assert "dispatch_due_schedules" in dispatcher_source
    assert "_launch_available(" in dispatcher_source

    assert "dispatch_due_schedules" not in ui_source
    assert "ensure_dispatcher_running(" in ui_source

    assert '["Active", "Queue", "History", "Results", "Scheduled"]' in jobs_source


def test_jobs_queue_exposes_bounded_priority_presets():
    queue_source = inspect.getsource(jobs_page._queue)
    row_source = inspect.getsource(jobs_page._queue_row)

    assert 'priority_options = ["High", "Normal", "Low"]' in queue_source
    assert '"Set priority"' in queue_source
    assert "set_queue_priority(" in queue_source
    assert 'disabled=state not in {"queued", "held"}' in queue_source
    assert '"High": 200' in queue_source
    assert '"Normal": 100' in queue_source
    assert '"Low": 50' in queue_source
    assert "queue_priority_label(row['priority'])" in row_source


def test_jobs_queue_panel_is_extracted_without_changing_compatibility_names():
    page_source = inspect.getsource(jobs_page)

    assert "def _queue(db, ctx):" not in page_source
    assert "def _queue_row(row):" not in page_source
    assert "def _advanced_queue_dispatcher(db, ctx):" not in page_source

    assert jobs_page._queue is jobs_queue_panel.render_queue
    assert jobs_page._queue_row is jobs_queue_panel.queue_row
    assert (
        jobs_page._advanced_queue_dispatcher
        is jobs_queue_panel.render_advanced_queue_dispatcher
    )

    queue_source = inspect.getsource(jobs_queue_panel.render_queue)
    assert '"jobs-queue-select"' in queue_source
    assert "jobs-queue-priority-" in queue_source
    assert "jobs-queue-hold-" in queue_source
    assert "jobs-queue-release-" in queue_source
    assert "jobs-queue-cancel-" in queue_source
    assert "jobs-queue-open-" in queue_source


def test_jobs_queue_hides_dispatcher_plumbing_in_advanced_panel():
    queue_source = inspect.getsource(jobs_page._queue)
    advanced_source = inspect.getsource(jobs_page._advanced_queue_dispatcher)

    assert '"Start dispatcher"' in queue_source
    assert '"Dispatcher"' in queue_source
    assert '"Queued"' in queue_source
    assert '"Held"' in queue_source
    assert '"Running"' in queue_source
    assert "dispatcher_service_status(" in queue_source
    assert "dispatcher_health_status(" in queue_source
    assert "runtime=runtime" in queue_source
    assert 'status.get("label")' in queue_source
    assert 'state == "starting"' in queue_source
    assert 'state == "degraded"' in queue_source
    assert "_advanced_queue_dispatcher(db, ctx)" in queue_source

    assert '"Install persistent dispatcher"' not in queue_source
    assert '"Restart dispatcher service"' not in queue_source
    assert '"Remove persistent service"' not in queue_source
    assert '"Maximum concurrent Rank Hunter jobs"' not in queue_source
    assert '"Save queue limits"' not in queue_source

    assert 'st.expander("Advanced Queue / Dispatcher", expanded=False)' in advanced_source
    assert '"#### Persistent dispatcher"' in advanced_source
    assert '"Install persistent dispatcher"' in advanced_source
    assert '"Restart dispatcher service"' in advanced_source
    assert '"Remove persistent service"' in advanced_source
    assert '"#### Concurrency & resource limits"' in advanced_source
    assert '"Maximum concurrent Rank Hunter jobs"' in advanced_source
    assert '"GPU ratpoints"' in advanced_source
    assert '"Sage-heavy"' in advanced_source
    assert '"DB maintenance"' in advanced_source
    assert '"Save queue limits"' in advanced_source


def test_dispatcher_launches_only_claimed_queue_rows():
    wrapper = inspect.getsource(dispatcher._launch_available)
    source = inspect.getsource(dispatcher._launch_available_unlocked)

    assert "normal_operation_guard(db_path)" in wrapper
    assert "_launch_available_unlocked(" in wrapper
    assert "resource_limit_map" in source
    assert "blocked_resource_classes=blocked" in source
    claim_pos = source.index("claim_next_queue_item")
    start_pos = source.index("_start_existing_job", claim_pos)
    running_pos = source.index("mark_queue_running", start_pos)
    assert claim_pos < start_pos < running_pos



def test_typed_queue_resource_defaults_preserve_merge_and_safe_fallback(tmp_path):
    db = ui_store._connect(tmp_path / "typed-queue-settings.db")
    try:
        ui_store.set_setting(
            db,
            "queue_resource_limits",
            {"sage_heavy": 4},
        )
        assert ui_store.resource_limits(db) == {
            "gpu_ratpoints": 1,
            "sage_heavy": 4,
            "database_maintenance": 1,
        }

        # Legacy/raw storage can still contain an invalid payload. The typed
        # reader rejects it and preserves the known safe defaults.
        ui_store.set_setting(
            db,
            "queue_resource_limits",
            {"gpu_ratpoints": 0, "sage_heavy": 9},
        )
        assert ui_store.resource_limits(db) == {
            "gpu_ratpoints": 1,
            "sage_heavy": 2,
            "database_maintenance": 1,
        }
    finally:
        db.close()



def test_saturation_jobs_use_sage_heavy_resource_class():
    from rank42.ui_store import infer_resource_class

    assert infer_resource_class("saturation") == "sage_heavy"
