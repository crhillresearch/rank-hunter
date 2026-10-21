"""Internal subprocess runner used by Rank Hunter Control Center."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from rank42.db import connect_existing
from rank42.ui_store import (
    finalize_batch_cursor,
    finalize_pool_search,
    finalize_queue_for_job,
    get_job,
    now,
    update_job,
)


def child_environment():
    """Environment for scientific children with live, line-oriented Python logs.

    UI jobs redirect stdout to a regular file.  CPython otherwise block-buffers
    stdout in that situation, which can hide candidate progress for minutes even
    while the search is healthy.  Force unbuffered Python I/O without changing
    the scientific command itself.
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return env


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--job-id", type=int, required=True)
    return ap.parse_args()


def _terminal_status(db, job_id, returncode):
    """Preserve an operator stop instead of relabeling SIGTERM as failure."""
    fresh = get_job(db, int(job_id))
    current = str(fresh["status"] or "") if fresh is not None else ""
    if current in {"stopping", "stopped"}:
        return "stopped"
    if current == "killed":
        return "killed"
    return "succeeded" if int(returncode) == 0 else "failed"


def main():
    args = parse_args()
    db_path = str(Path(args.db).resolve())
    db = connect_existing(db_path)
    row = get_job(db, args.job_id)
    if row is None:
        raise SystemExit(f"UI job #{args.job_id} not found")

    command = json.loads(row["command_json"])
    cwd = row["cwd"]
    log_path = Path(row["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    update_job(db, args.job_id, pid=os.getpid(), status="running", started_at=row["started_at"] or now())

    rc = None
    try:
        with log_path.open("a", buffering=1) as log:
            log.write("RANK HUNTER UI JOB\n")
            log.write("=" * 72 + "\n")
            log.write("cwd: " + cwd + "\n")
            log.write("command: " + " ".join(command) + "\n")
            log.write("=" * 72 + "\n\n")
            log.flush()
            env = child_environment()
            env["RANK_HUNTER_JOB_ID"] = str(args.job_id)
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
            rc = proc.wait()
        final_status = _terminal_status(db, args.job_id, rc)
        update_job(
            db,
            args.job_id,
            status=final_status,
            exit_code=int(rc),
            finished_at=now(),
        )
        finalize_batch_cursor(db, args.job_id)
        finalize_pool_search(db, args.job_id)
        finalize_queue_for_job(db, args.job_id, final_status)
    except BaseException:
        # If the UI intentionally stopped the process group it already changed
        # status to 'stopping'.  Avoid overwriting that with an invented failure.
        try:
            fresh = get_job(db, args.job_id)
            if fresh is not None and fresh["status"] == "stopping":
                final_status = "stopped"
            else:
                final_status = "interrupted"
            update_job(db, args.job_id, status=final_status, finished_at=now())
            finalize_batch_cursor(db, args.job_id)
            finalize_pool_search(db, args.job_id)
            finalize_queue_for_job(db, args.job_id, final_status)
        finally:
            raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
