"""Durable file-backed migration run journal.

The journal lives outside the SQLite database being migrated so a partially
upgraded database cannot erase its own recovery record. Writes use a temporary
file plus os.replace for atomic metadata replacement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid


JOURNAL_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def journal_directory(db_path) -> Path:
    directory = Path(db_path).resolve().parent / ".rank42-ui" / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def journal_path(db_path, run_id: str) -> Path:
    return journal_directory(db_path) / f"{run_id}.json"


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def read_migration_journal(path: str | Path) -> dict[str, object]:
    return dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class MigrationJournal:
    db_path: str
    run_id: str
    path: Path
    payload: dict[str, object]

    @classmethod
    def start(
        cls,
        db_path,
        *,
        pending_components,
        target_user_version,
    ):
        resolved = str(Path(db_path).resolve())
        run_id = uuid.uuid4().hex
        path = journal_path(resolved, run_id)
        payload = {
            "journal_version": JOURNAL_VERSION,
            "run_id": run_id,
            "status": "started",
            "db_path": resolved,
            "target_user_version": int(target_user_version),
            "pending_components": [str(x) for x in pending_components],
            "backup_path": None,
            "transaction_model": "manager_global_schema_transaction",
            "atomicity": (
                "baseline_schema_atomic: all baseline schema components execute "
                "inside one manager-owned SQLite transaction; journal-mode "
                "preparation happens before that transaction and the automatic "
                "pre-migration backup remains the recovery boundary"
            ),
            "started_at": _now(),
            "updated_at": _now(),
            "completed_at": None,
            "failed_at": None,
            "current_component": None,
            "failed_component": None,
            "error": None,
            "recovery_message": None,
            "components": [],
        }
        _write_atomic(path, payload)
        return cls(
            db_path=resolved,
            run_id=run_id,
            path=path,
            payload=payload,
        )

    def _save(self) -> None:
        self.payload["updated_at"] = _now()
        _write_atomic(self.path, self.payload)

    def set_backup(self, backup_path) -> None:
        resolved = str(Path(backup_path).resolve())
        self.payload["backup_path"] = resolved
        self.payload["recovery_message"] = (
            "If migration cannot be resumed safely, restore the automatic "
            f"pre-migration backup: {resolved}"
        )
        self._save()

    def component_started(
        self,
        component_id,
        *,
        migration_ids=(),
        transaction_boundary="legacy_internal_commits",
    ) -> None:
        component_id = str(component_id)
        self.payload["current_component"] = component_id
        self.payload["components"].append({
            "id": component_id,
            "status": "started",
            "migration_ids": [str(x) for x in migration_ids],
            "transaction_boundary": str(transaction_boundary),
            "started_at": _now(),
            "completed_at": None,
            "failed_at": None,
            "error": None,
        })
        self._save()

    def component_completed(self, component_id) -> None:
        component_id = str(component_id)
        for entry in reversed(self.payload["components"]):
            if entry["id"] == component_id and entry["status"] == "started":
                entry["status"] = "completed"
                entry["completed_at"] = _now()
                break
        self.payload["current_component"] = None
        self._save()

    def component_failed(self, component_id, exc: BaseException) -> None:
        component_id = str(component_id)
        message = f"{type(exc).__name__}: {exc}"
        for entry in reversed(self.payload["components"]):
            if entry["id"] == component_id and entry["status"] == "started":
                entry["status"] = "failed"
                entry["failed_at"] = _now()
                entry["error"] = message
                break
        self.payload["status"] = "failed"
        self.payload["failed_component"] = component_id
        self.payload["current_component"] = component_id
        self.payload["failed_at"] = _now()
        self.payload["error"] = message
        self._save()

    def failed(self, exc: BaseException, *, component_id=None) -> None:
        message = f"{type(exc).__name__}: {exc}"
        self.payload["status"] = "failed"
        self.payload["failed_component"] = (
            None if component_id is None else str(component_id)
        )
        self.payload["failed_at"] = _now()
        self.payload["error"] = message
        self._save()

    def completed(self) -> None:
        self.payload["status"] = "completed"
        self.payload["current_component"] = None
        self.payload["completed_at"] = _now()
        self.payload["error"] = None
        self._save()


def latest_migration_journal(db_path) -> dict[str, object] | None:
    directory = journal_directory(db_path)
    paths = sorted(
        directory.glob("*.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    if not paths:
        return None
    return read_migration_journal(paths[0])
