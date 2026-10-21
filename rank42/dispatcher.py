"""Durable Rank Hunter queue dispatcher.

This process is intentionally independent of Streamlit. UI actions enqueue work;
the dispatcher claims queue rows and is the only migrated component that starts
scientific job runners.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from rank42.dispatcher_service import write_runtime_heartbeat
from rank42.maintenance import MaintenanceActiveError, normal_operation_guard
from rank42.migration_manager import open_database_with_migrations
from rank42.settings_registry import setting_value
from rank42.ui_store import (
    _start_existing_job,
    claim_next_queue_item,
    heartbeat_queue_job,
    list_queue,
    mark_queue_running,
    now,
    reconcile_jobs,
    reconcile_queue,
    resource_limits,
    set_dispatcher_heartbeat,
    update_job,
    finalize_queue_for_job,
)


_STOP = False


def _request_stop(_signum, _frame):
    global _STOP
    _STOP = True


def _runtime_heartbeat_worker(
    stop_event,
    state_lock,
    state,
    project_root,
    db_path,
    interval_seconds,
):
    """Publish process liveness even while SQLite work is blocked or busy."""
    interval = max(0.5, float(interval_seconds))
    while True:
        with state_lock:
            snapshot = dict(state)
        try:
            write_runtime_heartbeat(
                project_root,
                db_path,
                pid=os.getpid(),
                worker_id=snapshot["worker_id"],
                status=snapshot["status"],
                max_workers=snapshot.get("max_workers"),
                resource_limits=snapshot.get("resource_limits"),
            )
        except OSError:
            # The database heartbeat remains as a fallback. A temporary
            # filesystem problem must not kill the dispatcher process.
            pass
        if stop_event.wait(interval):
            return


def _is_transient_sqlite_busy(exc):
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--poll-seconds", type=float, default=2.0)
    return ap.parse_args()


def _active_queue_rows(db):
    return [
        row
        for row in list_queue(db, include_terminal=False, limit=10000)
        if str(row["state"]) in {"claimed", "running"}
    ]


def _heartbeat_running(db, worker_id):
    for row in _active_queue_rows(db):
        if (
            str(row["state"]) == "running"
            and str(row["job_status"]) in {"running", "stopping"}
        ):
            heartbeat_queue_job(
                db,
                int(row["job_id"]),
                worker_id,
                lease_seconds=120,
            )


def _launch_available(
    db,
    db_path,
    worker_id,
    max_workers,
    resource_limit_map,
):
    """Claim/start available work only while the normal-operation gate is held."""
    with normal_operation_guard(db_path):
        return _launch_available_unlocked(
            db,
            db_path,
            worker_id,
            max_workers,
            resource_limit_map,
        )


def _launch_available_unlocked(
    db,
    db_path,
    worker_id,
    max_workers,
    resource_limit_map,
):
    launched = []
    while True:
        active = _active_queue_rows(db)
        if len(active) >= int(max_workers):
            break

        active_by_resource = {}
        for item in active:
            resource = str(item["resource_class"] or "default")
            active_by_resource[resource] = (
                int(active_by_resource.get(resource, 0)) + 1
            )

        # Database-maintenance jobs are globally exclusive. If one is active,
        # launch nothing else. If any ordinary work is active, leave queued
        # maintenance work waiting until the system is quiescent.
        if int(active_by_resource.get("database_maintenance", 0)) > 0:
            break

        blocked = {
            str(resource)
            for resource, limit in dict(resource_limit_map or {}).items()
            if int(active_by_resource.get(str(resource), 0)) >= int(limit)
        }

        if active:
            blocked.add("database_maintenance")

        row = claim_next_queue_item(
            db,
            worker_id,
            lease_seconds=60,
            blocked_resource_classes=blocked,
        )
        if row is None:
            break
        job_id = int(row["job_id"])
        try:
            _start_existing_job(db_path, job_id)
            mark_queue_running(
                db,
                job_id,
                worker_id,
                lease_seconds=120,
            )
        except Exception as exc:
            update_job(
                db,
                job_id,
                status="failed",
                finished_at=now(),
            )
            finalize_queue_for_job(
                db,
                job_id,
                "failed",
                error=str(exc),
            )
        else:
            launched.append(job_id)
    return launched


def run(db_path, project_root, *, poll_seconds=2.0):
    db_path = str(Path(db_path).resolve())
    project_root = str(Path(project_root).resolve())
    worker_id = f"dispatcher-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    lock_dir = Path(project_root) / ".rank42-ui"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = (lock_dir / "dispatcher.lock").open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        return

    try:
        with normal_operation_guard(db_path):
            db = open_database_with_migrations(db_path)
    except MaintenanceActiveError:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()
        return

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    runtime_lock = threading.Lock()
    runtime_state = {
        "worker_id": worker_id,
        "status": "starting",
        "max_workers": None,
        "resource_limits": None,
    }
    runtime_stop = threading.Event()
    runtime_thread = threading.Thread(
        target=_runtime_heartbeat_worker,
        args=(
            runtime_stop,
            runtime_lock,
            runtime_state,
            project_root,
            db_path,
            min(2.0, max(0.5, float(poll_seconds))),
        ),
        name="rank-hunter-dispatcher-heartbeat",
        daemon=True,
    )
    runtime_thread.start()
    with runtime_lock:
        runtime_state["status"] = "running"

    try:
        while not _STOP:
            try:
                with normal_operation_guard(db_path):
                    max_workers = max(
                        1,
                        int(setting_value(db, "queue_max_workers", 2) or 2),
                    )
                    limits = resource_limits(db)
                    with runtime_lock:
                        runtime_state["max_workers"] = max_workers
                        runtime_state["resource_limits"] = dict(limits)
                    set_dispatcher_heartbeat(
                        db,
                        pid=os.getpid(),
                        worker_id=worker_id,
                        status="running",
                        max_workers=max_workers,
                        resource_limits=limits,
                    )

                    # Reconcile runner deaths before deciding that worker slots
                    # are full. Scheduling and queue claiming share the same
                    # maintenance gate so destructive work cannot race a claim.
                    reconcile_jobs(db)
                    reconcile_queue(db)
                    _heartbeat_running(db, worker_id)

                    from rank42.manage_store import dispatch_due_schedules
                    dispatch_due_schedules(db, db_path, project_root)

                    _launch_available(
                        db,
                        db_path,
                        worker_id,
                        max_workers,
                        limits,
                    )
            except MaintenanceActiveError:
                pass
            except sqlite3.OperationalError as exc:
                if not _is_transient_sqlite_busy(exc):
                    raise
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
            time.sleep(max(0.25, float(poll_seconds)))
    finally:
        with runtime_lock:
            runtime_state["status"] = "stopped"
            final_max_workers = runtime_state.get("max_workers")
            final_resource_limits = runtime_state.get("resource_limits")
        runtime_stop.set()
        runtime_thread.join(timeout=3.0)
        try:
            write_runtime_heartbeat(
                project_root,
                db_path,
                pid=os.getpid(),
                worker_id=worker_id,
                status="stopped",
                max_workers=final_max_workers,
                resource_limits=final_resource_limits,
            )
        except OSError:
            pass
        try:
            set_dispatcher_heartbeat(
                db,
                pid=os.getpid(),
                worker_id=worker_id,
                status="stopped",
                max_workers=int(setting_value(db, "queue_max_workers", 2) or 2),
                resource_limits=resource_limits(db),
            )
        except Exception:
            pass
        db.close()
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def main():
    args = parse_args()
    run(
        args.db,
        args.project_root,
        poll_seconds=args.poll_seconds,
    )


if __name__ == "__main__":
    main()
