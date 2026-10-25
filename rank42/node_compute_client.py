"""Controller-side bridge from existing search kernels to paired workers."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from fractions import Fraction
from pathlib import Path

from rank42.node_cluster import (
    cancel_task, connect_registry, enqueue_task, get_task, registry_path,
    list_nodes, full_jobs_enabled, remote_search_enabled,
)


def _project_root():
    return Path(os.environ.get("RANK_HUNTER_PROJECT_ROOT") or Path(__file__).resolve().parents[1]).resolve()


def enabled():
    root = _project_root()
    path = registry_path(root)
    if not path.exists():
        return False
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        return remote_search_enabled(db)
    except sqlite3.OperationalError:
        return False
    finally:
        db.close()


def full_job_routing_enabled():
    path = registry_path(_project_root())
    if not path.exists():
        return False
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        return full_jobs_enabled(db)
    except sqlite3.OperationalError:
        return False
    finally:
        db.close()


def run_remote_ui_job(row, db_path):
    """Keep the UI job on Node 1 while a paired workhorse executes its process."""
    return run_remote_pipeline_command(
        job_id=int(row["id"]), command=json.loads(row["command_json"]),
        project_root=_project_root(), db_path=db_path,
        log_path=row["log_path"], seconds=None,
    )[0]


def run_remote_pipeline_command(
    *, job_id, command, project_root, db_path, log_path, seconds,
):
    """Run a Pipeline command on a workhorse, respecting a caller's time limit."""
    root = Path(project_root).resolve()
    backend = "CPU"
    try:
        run_id = int(command[command.index("--run-id") + 1])
        main_db = sqlite3.connect(str(Path(db_path).resolve()), timeout=10)
        try:
            config_row = main_db.execute(
                "SELECT run_config_json,stages_json FROM search_pipeline_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        finally:
            main_db.close()
        if config_row:
            config = json.loads(config_row[0] or "{}")
            stages = json.loads(config_row[1] or "[]")
            requested = [config.get("ratpoints_backend")]
            requested.extend(
                (stage.get("config") or {}).get("ratpoints_backend")
                for stage in stages if isinstance(stage, dict)
            )
            if any(str(value or "").upper() == "GPU" for value in requested):
                backend = "GPU"
    except (ValueError, IndexError, sqlite3.Error, json.JSONDecodeError):
        pass
    db = connect_registry(root)
    payload = {
        "job_id": int(job_id), "db_path": str(Path(db_path).resolve()),
        "project_root": str(root), "log_path": str(log_path),
        "command": list(command),
        "backend": backend,
    }
    try:
        task_id = enqueue_task(db, "ui_job", payload)
        print(f"[nodes] full Pipeline job #{job_id} queued as workhorse task #{task_id}", flush=True)
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write(f"[nodes] Waiting for workhorse task #{task_id}\n")
        deadline = time.monotonic() + max(1, float(seconds)) if seconds is not None else None
        announced_worker = None
        while True:
            task = get_task(db, task_id)
            if task is None:
                raise RuntimeError("remote job task disappeared")
            if task["status"] == "running" and task.get("node_id") != announced_worker:
                announced_worker = task["node_id"]
                worker = next((node for node in list_nodes(db)
                               if node["id"] == announced_worker), None)
                name = worker["hostname"] if worker else announced_worker
                with Path(log_path).open("a", encoding="utf-8") as log:
                    log.write(f"[nodes] Running on {name} · task #{task_id}\n")
            if task["status"] == "succeeded":
                result = json.loads(task["result_json"])
                return int(result["exit_code"]), False
            if task["status"] in {"failed", "cancelled"}:
                with Path(log_path).open("a", encoding="utf-8") as log:
                    log.write(f"[nodes] Workhorse task failed: {task['error']}\n")
                return 1, False
            if deadline is not None and time.monotonic() >= deadline:
                cancel_task(db, task_id)
                return 124, True
            time.sleep(0.5)
    except BaseException:
        if "task_id" in locals():
            cancel_task(db, task_id)
        raise
    finally:
        db.close()


def run_remote_ratpoints(
    coefficients, height, *, executable, timeout, denominator_low,
    denominator_high, one_point, extra_args,
):
    """Wait for an exact worker result while keeping SQLite on the controller."""
    from rank42.ratpoints import (
        Ratpoint, RatpointsFailure, RatpointsTimeout, normalize_polynomial,
    )

    backend = "GPU" if "gpu" in Path(str(executable or "")).name.lower() else "CPU"
    payload = {
        "coefficients": [str(Fraction(str(value))) for value in coefficients],
        "height": int(height), "timeout": int(timeout),
        "denominator_low": denominator_low,
        "denominator_high": denominator_high,
        "one_point": bool(one_point), "extra_args": list(extra_args or []),
        "backend": backend,
    }
    root = _project_root()
    db = connect_registry(root)
    ready_deadline = time.monotonic() + 15.0
    while True:
        capable = any(
            node["online"] and (
                node["has_ratpoints_gpu"] if backend == "GPU" else node["has_ratpoints_cpu"]
            )
            for node in list_nodes(db)
        )
        if capable or time.monotonic() >= ready_deadline:
            break
        time.sleep(0.25)
    if not capable:
        db.close()
        raise RatpointsFailure(f"no paired {backend} ratpoints workhorse is available")
    try:
        task_id = enqueue_task(db, "ratpoints_scan", payload)
    except BaseException:
        db.close()
        raise
    print(f"[nodes] ratpoints task #{task_id} queued for {backend} workhorse", flush=True)
    # Queue wait is separate from the worker's per-invocation timeout.
    deadline = time.monotonic() + max(600.0, float(timeout) + 600.0)
    try:
        while True:
            row = get_task(db, task_id)
            status = str(row["status"] or "") if row else "missing"
            if status == "succeeded":
                result = json.loads(row["result_json"])
                normalized = normalize_polynomial(coefficients)
                points = []
                for item in result["points"]:
                    a, Y, b = [int(value) for value in item["projective"]]
                    points.append(Ratpoint(
                        Fraction(str(item["x"])), Fraction(str(item["y"])),
                        a, Y, b,
                    ))
                provenance = dict(result.get("engine_provenance") or {})
                provenance["node_id"] = row["node_id"]
                provenance["execution_location"] = "remote_worker"
                print(f"[nodes] ratpoints task #{task_id} completed on {row['node_id']}", flush=True)
                return {
                    "normalized": normalized, "points": points,
                    "runtime": float(result.get("runtime") or 0),
                    "executable": provenance.get("executable") or f"node:{row['node_id']}",
                    "command": list(provenance.get("argv") or []),
                    "engine_provenance": provenance,
                    "stdout": "", "stderr": "",
                }
            if status in {"failed", "cancelled", "missing"}:
                error = str((row or {}).get("error") or f"remote task {status}")
                exception = RatpointsTimeout if error.startswith("timeout:") else RatpointsFailure
                exc = exception(error)
                exc.engine_provenance = {
                    "backend": backend, "node_id": (row or {}).get("node_id"),
                    "execution_location": "remote_worker", "status": status,
                }
                raise exc
            if time.monotonic() >= deadline:
                cancel_task(db, task_id)
                raise RatpointsTimeout(
                    f"remote ratpoints task #{task_id} did not finish before the queue deadline"
                )
            time.sleep(0.5)
    except BaseException:
        row = get_task(db, task_id)
        if row and row["status"] in {"queued", "running"}:
            cancel_task(db, task_id)
        raise
    finally:
        db.close()
