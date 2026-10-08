"""Durable Full Database Verification service for SYSTEM-19."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from rank42.ui_store import enqueue_job


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def full_verification_result_path(db_path) -> Path:
    db_path = Path(db_path).resolve()
    digest = hashlib.sha256(str(db_path).encode()).hexdigest()[:16]
    return (
        db_path.parent
        / ".rank42-ui"
        / "diagnostics"
        / f"full-database-verification-{digest}.json"
    )


def load_full_database_verification(db_path):
    path = full_verification_result_path(db_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(data) if isinstance(data, dict) else None


def _write_atomic(path, payload):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _readonly_connection(db_path):
    resolved = Path(db_path).resolve()
    uri = "file:" + quote(str(resolved)) + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def run_full_database_verification(db_path, *, job_id=None):
    """Run unbounded read-only SQLite verification on a quiescent database."""
    db_path = Path(db_path).resolve()
    started_at = _utc_now()
    started = time.monotonic()
    stat = db_path.stat()

    db = _readonly_connection(db_path)
    try:
        user_version = int(db.execute("PRAGMA user_version").fetchone()[0] or 0)
        integrity_rows = list(db.execute("PRAGMA integrity_check").fetchall())
        integrity_messages = [str(row[0]) for row in integrity_rows]
        integrity_ok = (
            len(integrity_messages) == 1
            and integrity_messages[0].lower() == "ok"
        )

        fk_rows = list(db.execute("PRAGMA foreign_key_check").fetchall())
        breakdown = {}
        samples = []
        for row in fk_rows:
            table = str(row[0])
            parent = str(row[2])
            key = f"{table} -> {parent}"
            breakdown[key] = int(breakdown.get(key, 0)) + 1
            if len(samples) < 100:
                samples.append({
                    "table": table,
                    "rowid": row[1],
                    "parent": parent,
                    "fkid": row[3],
                })
    finally:
        db.close()

    elapsed = time.monotonic() - started
    return {
        "status": "completed",
        "healthy": bool(integrity_ok and not fk_rows),
        "job_id": None if job_id is None else int(job_id),
        "database_path": str(db_path),
        "database_size_bytes": int(stat.st_size),
        "database_mtime_ns": int(stat.st_mtime_ns),
        "user_version": int(user_version),
        "integrity_ok": bool(integrity_ok),
        "integrity_messages": integrity_messages,
        "foreign_key_violations": len(fk_rows),
        "foreign_key_breakdown": dict(
            sorted(
                breakdown.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "foreign_key_samples": samples,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "elapsed_seconds": elapsed,
        "verification_kind": "full_database",
        "read_only": True,
    }


def persist_full_database_verification(db_path, payload):
    path = full_verification_result_path(db_path)
    _write_atomic(path, payload)
    return path


def enqueue_full_database_verification(db_path, project_root):
    db_path = Path(db_path).resolve()
    root = Path(project_root).resolve()
    result_path = full_verification_result_path(db_path)
    return enqueue_job(
        db_path,
        kind="database_full_verification",
        label="Full Database Verification",
        command=[
            sys.executable,
            "-m",
            "rank42.database_verification_worker",
            "--db",
            str(db_path),
            "--project-root",
            str(root),
            "--result-path",
            str(result_path),
        ],
        cwd=root,
        metadata={
            "launch_surface": "database",
            "verification_kind": "full_database",
            "result_path": str(result_path),
            "queue_resource_class": "database_maintenance",
        },
        priority=200,
        resource_class="database_maintenance",
    )
