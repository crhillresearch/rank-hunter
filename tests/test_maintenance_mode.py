import json

import pytest

from rank42 import dispatcher, ui_store
from rank42.maintenance import (
    MaintenanceActiveError,
    MaintenanceBusyError,
    acquire_maintenance_lock,
    maintenance_state_path,
    maintenance_status,
    normal_operation_guard,
)


def _db(tmp_path):
    path = tmp_path / "rank42.db"
    db = ui_store._connect(path)
    return path, db


def test_maintenance_lease_blocks_normal_operations_and_releases(tmp_path):
    path, db = _db(tmp_path)
    try:
        lease = acquire_maintenance_lock(
            path,
            db=db,
            project_root=tmp_path,
            reason="test maintenance",
        )
        status = maintenance_status(path)
        assert status["active"] is True
        assert status["metadata"]["reason"] == "test maintenance"
        assert status["metadata"]["token"] == lease.token
        assert ".rank42-ui" in status["guard_path"]

        with pytest.raises(MaintenanceActiveError, match="test maintenance"):
            with normal_operation_guard(path):
                pass

        lease.release()
        status = maintenance_status(path)
        assert status["active"] is False
        assert status["metadata"] is None
    finally:
        db.close()


def test_maintenance_release_is_owner_token_protected(tmp_path):
    path, db = _db(tmp_path)
    try:
        lease = acquire_maintenance_lock(path, db=db, project_root=tmp_path)
        state_path = maintenance_state_path(path)
        metadata = json.loads(state_path.read_text(encoding="utf-8"))
        metadata["token"] = "different-owner"
        state_path.write_text(json.dumps(metadata), encoding="utf-8")

        lease.release()

        assert state_path.exists()
        status = maintenance_status(path)
        assert status["active"] is False
        assert status["stale_state"] is True
    finally:
        maintenance_state_path(path).unlink(missing_ok=True)
        db.close()


def test_maintenance_refuses_live_owned_job(tmp_path, monkeypatch):
    path, db = _db(tmp_path)
    try:
        job_id = ui_store.create_job(
            db,
            kind="test",
            label="running",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "running.log",
        )
        ui_store.update_job(
            db,
            job_id,
            status="running",
            pid=424242,
            started_at=ui_store.now(),
        )
        monkeypatch.setattr(
            ui_store,
            "process_alive",
            lambda pid: int(pid) == 424242,
        )

        with pytest.raises(MaintenanceBusyError, match="owned Rank Hunter work"):
            acquire_maintenance_lock(path, db=db, project_root=tmp_path)

        assert maintenance_status(path)["active"] is False
    finally:
        db.close()


def test_maintenance_refuses_claimed_queue_item(tmp_path):
    path, db = _db(tmp_path)
    try:
        job_id = ui_store.create_job(
            db,
            kind="test",
            label="claimed",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "claimed.log",
        )
        ui_store.create_queue_item(db, job_id=job_id)
        claimed = ui_store.claim_next_queue_item(db, "worker-test")
        assert int(claimed["job_id"]) == job_id

        with pytest.raises(MaintenanceBusyError, match="owned Rank Hunter work"):
            acquire_maintenance_lock(path, db=db, project_root=tmp_path)
    finally:
        db.close()


def test_maintenance_reports_external_rank_hunter_processes(tmp_path, monkeypatch):
    path, db = _db(tmp_path)
    external = [{
        "source": "terminal",
        "pid": 5151,
        "ppid": 1,
        "pgid": 5151,
        "elapsed_seconds": 12,
        "module": "rank42.search",
        "label": "Nagao candidate search",
        "command": "python -m rank42.search",
        "cwd": str(tmp_path),
        "status": "running",
    }]
    monkeypatch.setattr(
        ui_store,
        "discover_external_jobs",
        lambda project_root, ui_runner_pids=(): list(external),
    )

    try:
        with acquire_maintenance_lock(
            path,
            db=db,
            project_root=tmp_path,
            reason="restore",
        ) as lease:
            assert lease.external_processes == external
            stored = maintenance_status(path)["metadata"]["external_processes"]
            assert stored[0]["pid"] == 5151
            assert stored[0]["module"] == "rank42.search"
    finally:
        db.close()


def test_enqueue_job_fails_closed_during_maintenance(tmp_path):
    path, db = _db(tmp_path)
    try:
        with acquire_maintenance_lock(path, db=db, project_root=tmp_path):
            with pytest.raises(MaintenanceActiveError):
                ui_store.enqueue_job(
                    path,
                    kind="test",
                    label="blocked",
                    command=["python", "-c", "pass"],
                    cwd=tmp_path,
                    start_dispatcher=False,
                )
            count = int(
                db.execute("SELECT COUNT(*) FROM ui_jobs").fetchone()[0]
            )
            assert count == 0
    finally:
        db.close()


def test_dispatcher_claim_loop_fails_closed_during_maintenance(
    tmp_path,
    monkeypatch,
):
    path, db = _db(tmp_path)
    try:
        job_id = ui_store.create_job(
            db,
            kind="test",
            label="queued",
            command=["python", "-c", "pass"],
            cwd=tmp_path,
            log_path=tmp_path / "queued.log",
        )
        ui_store.create_queue_item(db, job_id=job_id)

        def unexpected_start(*args, **kwargs):
            raise AssertionError(
                "dispatcher process start must not run during maintenance"
            )

        monkeypatch.setattr(
            dispatcher,
            "_start_existing_job",
            unexpected_start,
        )

        with acquire_maintenance_lock(
            path,
            db=db,
            project_root=tmp_path,
            reason="restore",
        ):
            with pytest.raises(
                MaintenanceActiveError,
                match="restore",
            ):
                dispatcher._launch_available(
                    db,
                    path,
                    "worker-test",
                    2,
                    ui_store.DEFAULT_RESOURCE_LIMITS,
                )

            queue = ui_store.queue_item_for_job(db, job_id)
            job = ui_store.get_job(db, job_id)
            assert queue["state"] == "queued"
            assert queue["lease_owner"] is None
            assert job["status"] == "queued"
    finally:
        db.close()


def test_dispatcher_startup_is_suppressed_during_maintenance(
    tmp_path,
    monkeypatch,
):
    path, db = _db(tmp_path)

    def unexpected_popen(*args, **kwargs):
        raise AssertionError("dispatcher process must not start during maintenance")

    try:
        with acquire_maintenance_lock(path, db=db, project_root=tmp_path):
            # Acquire first: maintenance observes external Rank Hunter work via
            # subprocess.run(), which itself uses subprocess.Popen internally.
            # Patch only after the lease exists so this guard checks dispatcher
            # spawning rather than breaking the process observer.
            monkeypatch.setattr(ui_store.subprocess, "Popen", unexpected_popen)
            assert ui_store.ensure_dispatcher_running(path, tmp_path) is None
    finally:
        db.close()


def test_dispatcher_process_exits_before_opening_database_during_maintenance(
    tmp_path,
    monkeypatch,
):
    path, db = _db(tmp_path)
    opened = []

    monkeypatch.setattr(
        dispatcher,
        "open_database_with_migrations",
        lambda db_path: opened.append(str(db_path)),
    )
    try:
        with acquire_maintenance_lock(path, db=db, project_root=tmp_path):
            dispatcher.run(path, tmp_path, poll_seconds=0.01)
        assert opened == []
    finally:
        db.close()



def test_maintenance_context_releases_on_exception(tmp_path):
    path, db = _db(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with acquire_maintenance_lock(
                path,
                db=db,
                project_root=tmp_path,
                reason="failing maintenance",
            ):
                assert maintenance_status(path)["active"] is True
                raise RuntimeError("boom")

        status = maintenance_status(path)
        assert status["active"] is False
        assert status["metadata"] is None
    finally:
        db.close()



def test_maintenance_can_lock_core_database_before_ui_schema_exists(tmp_path):
    from rank42.db import connect

    path = tmp_path / "core-only.db"
    db = connect(path)
    try:
        tables = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "curves" in tables
        assert "ui_jobs" not in tables
        assert "ui_job_queue" not in tables

        with acquire_maintenance_lock(
            path,
            db=db,
            project_root=tmp_path,
            reason="pre-migration",
        ):
            assert maintenance_status(path)["active"] is True
    finally:
        db.close()
