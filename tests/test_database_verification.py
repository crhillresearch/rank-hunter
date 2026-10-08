import json
from pathlib import Path

from rank42 import dispatcher, ui_store
from rank42.database_verification import (
    enqueue_full_database_verification,
    full_verification_result_path,
    load_full_database_verification,
    persist_full_database_verification,
    run_full_database_verification,
)
from rank42.db import connect
from rank42.maintenance import (
    MaintenanceBusyError,
    acquire_maintenance_lock,
    maintenance_status,
)


def _db(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    ui_store.ensure_ui_schema(db)
    return path, db


def test_full_database_verification_is_read_only_and_healthy(tmp_path):
    path, db = _db(tmp_path)
    db.close()
    before = path.stat()

    result = run_full_database_verification(path, job_id=17)

    after = path.stat()
    assert result["status"] == "completed"
    assert result["healthy"] is True
    assert result["job_id"] == 17
    assert result["integrity_ok"] is True
    assert result["integrity_messages"] == ["ok"]
    assert result["foreign_key_violations"] == 0
    assert result["read_only"] is True
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns


def test_full_database_verification_detects_foreign_key_violation(tmp_path):
    path, db = _db(tmp_path)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO events(curve_id,level,message,created_at) VALUES(?,?,?,?)",
        (999999, "info", "orphan fixture", "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    db.close()

    result = run_full_database_verification(path)

    assert result["status"] == "completed"
    assert result["integrity_ok"] is True
    assert result["healthy"] is False
    assert result["foreign_key_violations"] == 1
    assert result["foreign_key_breakdown"]["events -> curves"] == 1


def test_loading_missing_verification_result_is_side_effect_free(tmp_path):
    path = tmp_path / "rank42.db"
    assert load_full_database_verification(path) is None
    assert not (tmp_path / ".rank42-ui" / "diagnostics").exists()


def test_persisted_verification_result_round_trips_atomically(tmp_path):
    path, db = _db(tmp_path)
    db.close()
    payload = {
        "status": "completed",
        "healthy": True,
        "job_id": 9,
        "integrity_ok": True,
        "foreign_key_violations": 0,
        "finished_at": "2026-09-26T23:00:00+00:00",
    }

    result_path = persist_full_database_verification(path, payload)

    assert result_path == full_verification_result_path(path)
    assert load_full_database_verification(path) == payload
    assert not result_path.with_name(result_path.name + ".tmp").exists()


def test_enqueue_full_verification_uses_maintenance_resource(monkeypatch, tmp_path):
    path, db = _db(tmp_path)
    db.close()
    captured = {}

    def fake_enqueue(db_path, **kwargs):
        captured["db_path"] = str(db_path)
        captured.update(kwargs)
        return 81

    monkeypatch.setattr(
        "rank42.database_verification.enqueue_job",
        fake_enqueue,
    )

    job_id = enqueue_full_database_verification(path, tmp_path)

    assert job_id == 81
    assert captured["kind"] == "database_full_verification"
    assert captured["label"] == "Full Database Verification"
    assert captured["resource_class"] == "database_maintenance"
    assert captured["priority"] == 200
    assert captured["metadata"]["queue_resource_class"] == "database_maintenance"
    assert "rank42.database_verification_worker" in captured["command"]


def test_maintenance_job_may_ignore_only_its_own_owned_job(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "rank42.db"
    db = ui_store._connect(path)
    try:
        job_id = ui_store.create_job(
            db,
            kind="database_full_verification",
            label="Full Database Verification",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "verify.log",
        )
        ui_store.create_queue_item(
            db,
            job_id=job_id,
            resource_class="database_maintenance",
        )
        claimed = ui_store.claim_next_queue_item(db, "worker-test")
        assert int(claimed["job_id"]) == job_id
        ui_store.mark_queue_running(db, job_id, "worker-test")
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
        monkeypatch.setattr(
            ui_store,
            "discover_external_jobs",
            lambda project_root, ui_runner_pids=(): [],
        )

        with acquire_maintenance_lock(
            path,
            db=db,
            project_root=tmp_path,
            reason="full database verification",
            ignore_job_ids=(job_id,),
            ignore_external_pids=(424242,),
        ):
            assert maintenance_status(path)["active"] is True
    finally:
        db.close()


def test_dispatcher_waits_to_launch_database_maintenance_until_quiescent(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "rank42.db"
    db = ui_store._connect(path)
    try:
        ordinary = ui_store.create_job(
            db,
            kind="ordinary",
            label="ordinary",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "ordinary.log",
        )
        ui_store.create_queue_item(db, job_id=ordinary)
        claimed = ui_store.claim_next_queue_item(db, "worker-test")
        assert int(claimed["job_id"]) == ordinary
        ui_store.mark_queue_running(db, ordinary, "worker-test")

        maintenance = ui_store.create_job(
            db,
            kind="database_full_verification",
            label="verification",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "verification.log",
        )
        ui_store.create_queue_item(
            db,
            job_id=maintenance,
            priority=200,
            resource_class="database_maintenance",
        )
        monkeypatch.setattr(
            dispatcher,
            "_start_existing_job",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("maintenance must stay queued")
            ),
        )

        launched = dispatcher._launch_available(
            db,
            path,
            "worker-test",
            4,
            ui_store.DEFAULT_RESOURCE_LIMITS,
        )

        assert launched == []
        assert ui_store.queue_item_for_job(db, maintenance)["state"] == "queued"
    finally:
        db.close()


def test_dispatcher_launches_database_maintenance_alone(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "rank42.db"
    db = ui_store._connect(path)
    try:
        maintenance = ui_store.create_job(
            db,
            kind="database_full_verification",
            label="verification",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "verification.log",
        )
        ui_store.create_queue_item(
            db,
            job_id=maintenance,
            priority=200,
            resource_class="database_maintenance",
        )
        ordinary = ui_store.create_job(
            db,
            kind="ordinary",
            label="ordinary",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "ordinary.log",
        )
        ui_store.create_queue_item(
            db,
            job_id=ordinary,
            priority=100,
            resource_class="default",
        )
        started = []
        monkeypatch.setattr(
            dispatcher,
            "_start_existing_job",
            lambda db_path, job_id: started.append(int(job_id)),
        )

        launched = dispatcher._launch_available(
            db,
            path,
            "worker-test",
            4,
            ui_store.DEFAULT_RESOURCE_LIMITS,
        )

        assert launched == [maintenance]
        assert started == [maintenance]
        assert ui_store.queue_item_for_job(db, maintenance)["state"] == "running"
        assert ui_store.queue_item_for_job(db, ordinary)["state"] == "queued"
    finally:
        db.close()


def test_maintenance_job_ignore_does_not_hide_other_owned_work(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "rank42.db"
    db = ui_store._connect(path)
    try:
        own_job = ui_store.create_job(
            db,
            kind="database_full_verification",
            label="verification",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "verification.log",
        )
        other_job = ui_store.create_job(
            db,
            kind="ordinary",
            label="ordinary",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "ordinary.log",
        )
        ui_store.update_job(
            db,
            own_job,
            status="running",
            pid=111111,
            started_at=ui_store.now(),
        )
        ui_store.update_job(
            db,
            other_job,
            status="running",
            pid=222222,
            started_at=ui_store.now(),
        )
        monkeypatch.setattr(
            ui_store,
            "process_alive",
            lambda pid: int(pid) in {111111, 222222},
        )
        monkeypatch.setattr(
            ui_store,
            "discover_external_jobs",
            lambda project_root, ui_runner_pids=(): [],
        )

        try:
            acquire_maintenance_lock(
                path,
                db=db,
                project_root=tmp_path,
                reason="full database verification",
                ignore_job_ids=(own_job,),
                ignore_external_pids=(111111,),
            )
        except MaintenanceBusyError as exc:
            assert f"#{other_job} running" in str(exc)
        else:
            raise AssertionError("other owned work must still block maintenance")
    finally:
        db.close()


def test_ui_job_runner_exposes_durable_job_id_to_child():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_job_runner.py"
    ).read_text(encoding="utf-8")

    assert 'env["RANK_HUNTER_JOB_ID"] = str(args.job_id)' in source
    assert "env=env" in source


def test_verification_result_paths_are_distinct_per_database(tmp_path):
    one = full_verification_result_path(tmp_path / "one.db")
    two = full_verification_result_path(tmp_path / "two.db")

    assert one != two
    assert one.parent == two.parent
