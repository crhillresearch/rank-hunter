import inspect
import sqlite3
import subprocess

from rank42 import dispatcher_service, ui_store
from rank42.ui_pages import jobs_page, jobs_queue_panel


def _cp(args, rc=0, out="", err=""):
    return subprocess.CompletedProcess(
        ["systemctl", "--user", *args],
        returncode=rc,
        stdout=out,
        stderr=err,
    )


def test_service_name_is_deterministic_per_checkout_and_database(tmp_path):
    root = tmp_path / "rank-hunter"
    db1 = root / "rank42.db"
    db2 = root / "other.db"

    name1 = dispatcher_service.service_name(root, db1)
    name1_again = dispatcher_service.service_name(root, db1)
    name2 = dispatcher_service.service_name(root, db2)

    assert name1 == name1_again
    assert name1.startswith("rank-hunter-dispatcher-")
    assert name1.endswith(".service")
    assert name1 != name2


def test_service_unit_preserves_running_science_jobs(tmp_path):
    root = tmp_path / "rank-hunter"
    db = root / "rank42.db"
    python = tmp_path / "venv" / "bin" / "python"

    text = dispatcher_service.service_unit_text(
        root,
        db,
        python_executable=python,
    )

    assert "[Service]" in text
    assert "Restart=always" in text
    assert "RestartSec=3" in text
    assert "KillMode=process" in text
    assert "TimeoutStopSec=10" in text
    assert "-m rank42.dispatcher" in text
    assert f"WorkingDirectory={root.resolve()}" in text
    assert f'WorkingDirectory="{root.resolve()}"' not in text
    assert f'--db "{db.resolve()}"' in text
    assert f'--project-root "{root.resolve()}"' in text
    assert 'Environment="PYTHONUNBUFFERED=1"' in text
    assert "[Install]" in text
    assert "WantedBy=default.target" in text


def test_install_and_uninstall_user_service(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "repo"
    db = root / "rank42.db"
    calls = []

    monkeypatch.setattr(
        dispatcher_service,
        "_systemctl",
        lambda: "/usr/bin/systemctl",
    )

    def fake_run(*args, **kwargs):
        calls.append(tuple(args))
        if args[0] == "is-active":
            return _cp(args, out="active\n")
        if args[0] == "is-enabled":
            return _cp(args, out="enabled\n")
        if args[0] == "show":
            return _cp(args, out="4242\n")
        return _cp(args)

    monkeypatch.setattr(dispatcher_service, "_run_systemctl", fake_run)

    status = dispatcher_service.install_service(
        root,
        db,
        python_executable="/opt/rh/python",
        home=home,
        start=False,
    )

    unit = dispatcher_service.service_path(root, db, home=home)
    assert unit.is_file()
    assert status["installed"] is True
    assert status["enabled"] is True
    assert status["active"] is True
    assert status["main_pid"] == 4242
    assert ("daemon-reload",) in calls
    assert ("enable", unit.name) in calls

    calls.clear()
    status = dispatcher_service.uninstall_service(root, db, home=home)

    assert status["installed"] is False
    assert not unit.exists()
    assert ("disable", "--now", unit.name) in calls
    assert ("daemon-reload",) in calls


def test_ensure_dispatcher_prefers_installed_service(tmp_path, monkeypatch):
    db_path = tmp_path / "rank42.db"
    db = ui_store._connect(db_path)
    db.close()

    monkeypatch.setattr(
        dispatcher_service,
        "service_status",
        lambda project_root, database: {
            "installed": True,
            "systemctl_available": True,
            "active": True,
            "enabled": True,
            "main_pid": 515151,
        },
    )
    monkeypatch.setattr(
        dispatcher_service,
        "start_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("active service should not be started")
        ),
    )
    monkeypatch.setattr(
        dispatcher_service,
        "restart_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fresh service should not be restarted")
        ),
    )
    monkeypatch.setattr(
        dispatcher_service,
        "runtime_heartbeat_status",
        lambda project_root, database: {
            "protocol_ok": True,
            "fresh": True,
            "pid": 515151,
            "status": "running",
            "worker_id": "worker-515151",
            "age_seconds": 0.2,
        },
    )

    real_alive = ui_store.process_alive
    monkeypatch.setattr(
        ui_store,
        "process_alive",
        lambda pid: int(pid) == 515151,
    )
    try:
        pid = ui_store.ensure_dispatcher_running(db_path, tmp_path)
    finally:
        monkeypatch.setattr(ui_store, "process_alive", real_alive)

    assert pid == 515151


def test_ensure_dispatcher_starts_inactive_installed_service(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "rank42.db"
    db = ui_store._connect(db_path)
    db.close()

    monkeypatch.setattr(
        dispatcher_service,
        "service_status",
        lambda project_root, database: {
            "installed": True,
            "systemctl_available": True,
            "active": False,
            "enabled": True,
            "main_pid": None,
        },
    )
    calls = []

    def fake_start(project_root, database):
        calls.append((str(project_root), str(database)))
        return {
            "installed": True,
            "systemctl_available": True,
            "active": True,
            "enabled": True,
            "main_pid": 616161,
        }

    monkeypatch.setattr(dispatcher_service, "start_service", fake_start)
    monkeypatch.setattr(
        dispatcher_service,
        "runtime_heartbeat_status",
        lambda project_root, database: {
            "protocol_ok": True,
            "fresh": True,
            "pid": 616161,
            "status": "running",
            "worker_id": "worker-616161",
            "age_seconds": 0.1,
        },
    )
    monkeypatch.setattr(
        ui_store,
        "process_alive",
        lambda pid: int(pid) == 616161,
    )

    pid = ui_store.ensure_dispatcher_running(db_path, tmp_path)

    assert pid == 616161
    assert len(calls) == 1


def test_runtime_heartbeat_round_trip_is_independent_of_sqlite(tmp_path):
    root = tmp_path / "rank-hunter"
    root.mkdir()
    db_path = root / "rank42.db"

    written = dispatcher_service.write_runtime_heartbeat(
        root,
        db_path,
        pid=4242,
        worker_id="worker-runtime",
        status="running",
        max_workers=3,
        resource_limits={"sage_heavy": 2},
    )
    status = dispatcher_service.runtime_heartbeat_status(root, db_path)

    assert written["protocol"] == dispatcher_service.RUNTIME_HEARTBEAT_PROTOCOL
    assert status["exists"] is True
    assert status["valid"] is True
    assert status["protocol_ok"] is True
    assert status["fresh"] is True
    assert status["pid"] == 4242
    assert status["worker_id"] == "worker-runtime"
    assert status["resource_limits"] == {"sage_heavy": 2}
    assert status["age_seconds"] >= 0


def test_ensure_dispatcher_restarts_active_service_missing_current_runtime_protocol(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "rank42.db"
    db = ui_store._connect(db_path)
    db.close()
    restarted = {"value": False}

    monkeypatch.setattr(
        dispatcher_service,
        "service_status",
        lambda project_root, database: {
            "installed": True,
            "systemctl_available": True,
            "active": True,
            "enabled": True,
            "main_pid": 700001,
        },
    )

    def fake_restart(project_root, database):
        restarted["value"] = True
        return {
            "installed": True,
            "systemctl_available": True,
            "active": True,
            "enabled": True,
            "main_pid": 700002,
        }

    monkeypatch.setattr(dispatcher_service, "restart_service", fake_restart)
    monkeypatch.setattr(
        dispatcher_service,
        "start_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("active service should be restarted, not started")
        ),
    )

    def fake_runtime(project_root, database):
        if not restarted["value"]:
            return {
                "exists": False,
                "valid": False,
                "fresh": False,
                "protocol_ok": False,
            }
        return {
            "exists": True,
            "valid": True,
            "fresh": True,
            "protocol_ok": True,
            "pid": 700002,
            "status": "running",
            "worker_id": "worker-700002",
            "age_seconds": 0.1,
        }

    monkeypatch.setattr(
        dispatcher_service,
        "runtime_heartbeat_status",
        fake_runtime,
    )
    monkeypatch.setattr(
        ui_store,
        "process_alive",
        lambda pid: int(pid) in {700001, 700002},
    )

    pid = ui_store.ensure_dispatcher_running(db_path, tmp_path)

    assert restarted["value"] is True
    assert pid == 700002


def test_runtime_heartbeat_keeps_dispatcher_healthy_when_db_mirror_is_stale(
    monkeypatch,
):
    persistent = {
        "installed": True,
        "systemctl_available": True,
        "active": True,
        "main_pid": 8080,
    }
    runtime = {
        "protocol_ok": True,
        "fresh": True,
        "pid": 8080,
        "status": "running",
        "worker_id": "worker-8080",
        "age_seconds": 0.5,
    }
    monkeypatch.setattr(
        ui_store,
        "process_alive",
        lambda pid: int(pid) == 8080,
    )

    status = ui_store.resolve_dispatcher_health(
        {
            "healthy": False,
            "fresh": False,
            "age_seconds": 47.0,
            "status": "running",
            "pid": 8080,
        },
        persistent,
        runtime,
    )

    assert status["healthy"] is True
    assert status["state"] == "running"
    assert status["runtime_healthy"] is True
    assert status["heartbeat_healthy"] is False
    assert "database heartbeat mirror is delayed" in status["detail"].lower()


def test_dispatcher_health_projection_distinguishes_running_starting_degraded_and_offline():
    persistent = {
        "installed": True,
        "systemctl_available": True,
        "active": True,
        "main_pid": 4242,
    }

    running = ui_store.resolve_dispatcher_health(
        {
            "healthy": True,
            "status": "running",
            "pid": 4242,
            "worker_id": "worker-1",
            "max_workers": 2,
        },
        persistent,
    )
    assert running["healthy"] is True
    assert running["state"] == "running"
    assert running["label"] == "Running"
    assert running["tone"] == "ok"

    starting = ui_store.resolve_dispatcher_health(
        {"healthy": True, "status": "starting", "pid": 4242},
        persistent,
    )
    assert starting["healthy"] is False
    assert starting["state"] == "starting"
    assert starting["tone"] == "warning"

    degraded = ui_store.resolve_dispatcher_health(
        {"healthy": False, "status": "running", "pid": 4242},
        persistent,
    )
    assert degraded["healthy"] is False
    assert degraded["state"] == "degraded"
    assert "heartbeat" in degraded["detail"].lower()

    offline = ui_store.resolve_dispatcher_health(
        {"healthy": False, "status": "not started"},
        {
            "installed": False,
            "systemctl_available": False,
            "active": False,
            "main_pid": None,
        },
    )
    assert offline["healthy"] is False
    assert offline["state"] == "offline"
    assert offline["mode"] == "detached"
    assert "systemd" in offline["detail"].lower()


def test_dispatcher_health_projection_marks_healthy_detached_fallback_explicitly():
    status = ui_store.resolve_dispatcher_health(
        {
            "healthy": True,
            "status": "running",
            "pid": 5151,
            "worker_id": "detached-5151",
        },
        {
            "installed": False,
            "systemctl_available": False,
            "active": False,
            "main_pid": None,
        },
    )

    assert status["healthy"] is True
    assert status["state"] == "running"
    assert status["mode"] == "detached"
    assert "detached" in status["label"].lower()
    assert "systemd" in status["detail"].lower()


def test_dispatcher_runtime_heartbeat_survives_transient_sqlite_busy():
    from rank42 import dispatcher

    assert dispatcher._is_transient_sqlite_busy(
        sqlite3.OperationalError("database is locked")
    )
    assert dispatcher._is_transient_sqlite_busy(
        sqlite3.OperationalError("database table is busy")
    )
    assert not dispatcher._is_transient_sqlite_busy(
        sqlite3.OperationalError("malformed database schema")
    )

    source = inspect.getsource(dispatcher.run)
    worker = inspect.getsource(dispatcher._runtime_heartbeat_worker)
    assert "threading.Thread(" in source
    assert "except sqlite3.OperationalError as exc" in source
    assert "write_runtime_heartbeat(" in worker
    assert "set_dispatcher_heartbeat(" not in worker


def test_service_cli_status_includes_main_pid():
    source = inspect.getsource(dispatcher_service.main)
    assert '"main_pid"' in source


def test_jobs_page_caches_persistent_service_status():
    helper = inspect.getsource(jobs_page._dispatcher_service_status)
    owner_module = inspect.getsource(jobs_queue_panel)

    assert jobs_page._dispatcher_service_status is (
        jobs_queue_panel.dispatcher_service_status
    )
    assert "service_status(project_root, db_path)" in helper
    assert "@st.cache_data(ttl=5" in owner_module
    assert "runtime_heartbeat_status(project_root, db_path)" in owner_module
    assert "@st.cache_data(ttl=2" in owner_module
    assert '"Install persistent dispatcher"' in owner_module
    assert '"Restart dispatcher service"' in owner_module
    assert '"Remove persistent service"' in owner_module
