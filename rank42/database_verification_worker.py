"""Queued worker for Full Database Verification."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rank42.database_verification import (
    full_verification_result_path,
    persist_full_database_verification,
    run_full_database_verification,
)
from rank42.db import connect_existing
from rank42.maintenance import MaintenanceBusyError, acquire_maintenance_lock


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--result-path", required=True)
    return ap.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db).resolve()
    project_root = Path(args.project_root).resolve()
    configured_result_path = Path(args.result_path).resolve()
    expected_result_path = full_verification_result_path(db_path).resolve()
    if configured_result_path != expected_result_path:
        raise SystemExit("unexpected Full Database Verification result path")

    try:
        job_id = int(os.environ.get("RANK_HUNTER_JOB_ID") or 0) or None
    except (TypeError, ValueError):
        job_id = None

    control_db = connect_existing(db_path)
    try:
        with acquire_maintenance_lock(
            db_path,
            db=control_db,
            project_root=project_root,
            reason="full database verification",
            ignore_job_ids=(() if job_id is None else (job_id,)),
            ignore_external_pids=(os.getpid(), os.getppid()),
        ) as lease:
            if lease.external_processes:
                summary = {
                    "status": "blocked",
                    "reason": "terminal_rank_hunter_processes_active",
                    "job_id": job_id,
                    "external_processes": lease.external_processes,
                }
                print(json.dumps(summary, sort_keys=True), flush=True)
                raise SystemExit(3)

            result = run_full_database_verification(
                db_path,
                job_id=job_id,
            )
            persist_full_database_verification(db_path, result)
            print(json.dumps(result, sort_keys=True), flush=True)
    except MaintenanceBusyError as exc:
        print(
            json.dumps({
                "status": "blocked",
                "reason": "maintenance_not_quiescent",
                "job_id": job_id,
                "error": str(exc),
            }, sort_keys=True),
            flush=True,
        )
        raise SystemExit(2)
    finally:
        control_db.close()


if __name__ == "__main__":
    main()
