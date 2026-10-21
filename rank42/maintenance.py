"""System-wide maintenance gate for Rank Hunter destructive operations.

The lock intentionally lives outside the SQLite database so it survives and
coordinates database restore/replacement. Normal launch/dispatcher operations
take a short shared gate; destructive maintenance holds the exclusive gate.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import socket
import uuid


class MaintenanceActiveError(RuntimeError):
    """A normal operation was blocked because maintenance owns the gate."""


class MaintenanceBusyError(RuntimeError):
    """Maintenance could not start because the system is not quiescent."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _maintenance_dir(db_path) -> Path:
    directory = Path(db_path).resolve().parent / ".rank42-ui"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def maintenance_guard_path(db_path) -> Path:
    return _maintenance_dir(db_path) / "maintenance.guard"


def maintenance_state_path(db_path) -> Path:
    return _maintenance_dir(db_path) / "maintenance.json"


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(raw) if isinstance(raw, dict) else None


def maintenance_status(db_path) -> dict[str, object]:
    """Return current maintenance state without mutating the lock."""
    guard_path = maintenance_guard_path(db_path)
    state_path = maintenance_state_path(db_path)
    guard = guard_path.open("a+")
    active = False
    try:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
        else:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
    finally:
        guard.close()

    metadata = _read_state(state_path)
    return {
        "active": bool(active),
        "metadata": metadata,
        "stale_state": bool(metadata is not None and not active),
        "guard_path": str(guard_path),
        "state_path": str(state_path),
    }


@contextmanager
def normal_operation_guard(db_path):
    """Hold the shared gate around launch/claim operations.

    A maintenance owner holds the exclusive gate, so this non-blocking shared
    acquisition fails closed rather than waiting behind destructive work.
    """
    guard_path = maintenance_guard_path(db_path)
    guard = guard_path.open("a+")
    try:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            status = maintenance_status(db_path)
            metadata = status.get("metadata") or {}
            reason = str(metadata.get("reason") or "database maintenance")
            raise MaintenanceActiveError(
                f"Rank Hunter maintenance mode is active: {reason}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        guard.close()


def _table_exists(db, table_name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name),),
    ).fetchone() is not None


def _owned_active_work(
    db,
    *,
    ignore_job_ids=(),
) -> list[dict[str, object]]:
    from rank42.ui_store import process_alive

    ignored = {int(x) for x in ignore_job_ids if x is not None}

    rows = []
    if _table_exists(db, "ui_jobs"):
        rows = db.execute(
            """SELECT id,kind,label,pid,status
               FROM ui_jobs
               WHERE status IN ('running','stopping')
               ORDER BY id"""
        ).fetchall()
    out = []
    for row in rows:
        if int(row["id"]) in ignored:
            continue
        pid = row["pid"]
        if pid and process_alive(pid):
            out.append({
                "id": int(row["id"]),
                "kind": str(row["kind"]),
                "label": str(row["label"]),
                "pid": int(pid),
                "status": str(row["status"]),
            })

    claimed = []
    if _table_exists(db, "ui_jobs") and _table_exists(db, "ui_job_queue"):
        claimed = db.execute(
            """SELECT q.job_id,q.state,q.lease_owner,j.kind,j.label,j.pid,j.status
               FROM ui_job_queue q
               JOIN ui_jobs j ON j.id=q.job_id
               WHERE q.state IN ('claimed','running')
               ORDER BY q.job_id"""
        ).fetchall()
    known = {int(item["id"]) for item in out}
    for row in claimed:
        job_id = int(row["job_id"])
        if job_id in ignored or job_id in known:
            continue
        out.append({
            "id": job_id,
            "kind": str(row["kind"]),
            "label": str(row["label"]),
            "pid": None if row["pid"] is None else int(row["pid"]),
            "status": str(row["status"]),
            "queue_state": str(row["state"]),
            "lease_owner": row["lease_owner"],
        })
    return out


@dataclass
class MaintenanceLease:
    db_path: str
    token: str
    reason: str
    metadata: dict[str, object]
    external_processes: list[dict[str, object]]
    _guard: object
    _state_path: Path
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        try:
            current = _read_state(self._state_path)
            if current and str(current.get("token") or "") == self.token:
                self._state_path.unlink(missing_ok=True)
        finally:
            try:
                fcntl.flock(self._guard.fileno(), fcntl.LOCK_UN)
            finally:
                self._guard.close()
                self._released = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def acquire_maintenance_lock(
    db_path,
    *,
    db,
    project_root=None,
    reason="database maintenance",
    ignore_job_ids=(),
    ignore_external_pids=(),
) -> MaintenanceLease:
    """Acquire exclusive maintenance ownership after verifying quiescence.

    Owned running/stopping/claimed work blocks acquisition. Terminal-launched
    Rank Hunter processes are observed and returned to the caller so the UI can
    warn explicitly; they are never killed automatically.
    """
    resolved_db = str(Path(db_path).resolve())
    root = Path(project_root or Path(resolved_db).parent).resolve()
    guard_path = maintenance_guard_path(resolved_db)
    state_path = maintenance_state_path(resolved_db)
    guard = guard_path.open("a+")
    try:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            status = maintenance_status(resolved_db)
            metadata = status.get("metadata") or {}
            owner_reason = str(metadata.get("reason") or "database maintenance")
            raise MaintenanceBusyError(
                f"maintenance mode is already active: {owner_reason}"
            ) from exc

        owned = _owned_active_work(
            db,
            ignore_job_ids=ignore_job_ids,
        )
        if owned:
            summary = ", ".join(
                f"#{item['id']} {item['status']}"
                for item in owned[:8]
            )
            raise MaintenanceBusyError(
                "cannot enter maintenance mode while owned Rank Hunter work "
                f"is active: {summary}"
            )

        from rank42.ui_store import discover_external_jobs

        external = discover_external_jobs(
            root,
            ui_runner_pids=tuple(
                int(x) for x in ignore_external_pids if x is not None
            ),
        )
        token = uuid.uuid4().hex
        metadata = {
            "token": token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "reason": str(reason or "database maintenance"),
            "created_at": _utc_now(),
            "db_path": resolved_db,
            "project_root": str(root),
            "external_processes": [
                {
                    "pid": int(item["pid"]),
                    "pgid": int(item["pgid"]),
                    "module": str(item["module"]),
                    "label": str(item["label"]),
                    "command": str(item["command"]),
                }
                for item in external
            ],
        }
        tmp = state_path.with_name(f"{state_path.name}.{token}.tmp")
        tmp.write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, state_path)
        return MaintenanceLease(
            db_path=resolved_db,
            token=token,
            reason=str(metadata["reason"]),
            metadata=metadata,
            external_processes=list(external),
            _guard=guard,
            _state_path=state_path,
        )
    except BaseException:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        guard.close()
        raise
