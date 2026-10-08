"""Consistent SQLite backup/restore helpers for Rank Hunter.

The live database stays the scientific source of truth.  Backups use SQLite's
online backup API so WAL-mode state is copied as a consistent snapshot.
Restores validate the source, make an automatic pre-restore snapshot, then copy
the selected database into the already-open live connection.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rank42.db import CURRENT_SCHEMA_VERSION
from rank42.schema_manifest import (
    CORE_SCHEMA_MANIFEST,
    inspect_schema,
    schema_issue_summary,
)


BACKUP_DIRNAME = "backups"

# Keep this signature deliberately older than the current schema.  Backups from
# supported older Rank Hunter versions may be migrated after restore, while an
# unrelated SQLite file should not pass merely because it has a table named
# "curves".
_REQUIRED_CORE_TABLES = frozenset({"curves", "events"})
_REQUIRED_CURVE_COLUMNS = frozenset({
    "id",
    "family",
    "parameter",
    "created_at",
    "updated_at",
})
_REQUIRED_EVENT_COLUMNS = frozenset({
    "id",
    "curve_id",
    "level",
    "message",
    "created_at",
})


def backup_directory(project_root: str | Path) -> Path:
    path = Path(project_root).resolve() / BACKUP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_label(label: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(label).strip())
    return value.strip("-") or "backup"


def _backup_schema_info(src: sqlite3.Connection) -> dict[str, object]:
    version = int(src.execute("PRAGMA user_version").fetchone()[0] or 0)
    tables = {
        str(row[0])
        for row in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    curve_columns = set()
    if "curves" in tables:
        curve_columns = {
            str(row[1])
            for row in src.execute("PRAGMA table_info(curves)")
        }
    event_columns = set()
    if "events" in tables:
        event_columns = {
            str(row[1])
            for row in src.execute("PRAGMA table_info(events)")
        }
    return {
        "user_version": version,
        "tables": tables,
        "curve_columns": curve_columns,
        "event_columns": event_columns,
    }


def validate_backup(path: str | Path) -> None:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    src = sqlite3.connect(
        f"file:{p.resolve()}?mode=ro",
        uri=True,
        timeout=30,
    )
    try:
        check = src.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise ValueError(f"SQLite quick_check failed for {p.name}: {check}")

        info = _backup_schema_info(src)
        version = int(info["user_version"])
        if version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"{p.name} uses Rank Hunter schema {version}, newer than "
                f"this core supports ({CURRENT_SCHEMA_VERSION})"
            )

        tables = set(info["tables"])
        missing_tables = sorted(_REQUIRED_CORE_TABLES - tables)
        if missing_tables:
            raise ValueError(
                f"{p.name} is not a compatible Rank Hunter database backup "
                f"(missing core table(s): {', '.join(missing_tables)})"
            )

        curve_columns = set(info["curve_columns"])
        missing_columns = sorted(_REQUIRED_CURVE_COLUMNS - curve_columns)
        if missing_columns:
            raise ValueError(
                f"{p.name} is not a compatible Rank Hunter database backup "
                f"(curves missing column(s): {', '.join(missing_columns)})"
            )

        event_columns = set(info["event_columns"])
        missing_event_columns = sorted(
            _REQUIRED_EVENT_COLUMNS - event_columns
        )
        if missing_event_columns:
            raise ValueError(
                f"{p.name} is not a compatible Rank Hunter database backup "
                f"(events missing column(s): "
                f"{', '.join(missing_event_columns)})"
            )

        # A backup that claims the current schema must actually satisfy the
        # authoritative current core manifest. Older supported backups retain
        # the stable legacy signature above so the Migration Manager can
        # upgrade them after restore.
        if version == CURRENT_SCHEMA_VERSION:
            status = inspect_schema(src, CORE_SCHEMA_MANIFEST)
            if not status["ready"]:
                detail = schema_issue_summary(status)
                raise ValueError(
                    f"{p.name} claims current Rank Hunter schema {version} "
                    "but does not match the current core schema"
                    + (f": {detail}" if detail else "")
                )
    finally:
        src.close()


def create_backup(
    db: sqlite3.Connection,
    *,
    project_root: str | Path,
    label: str = "manual",
) -> Path:
    directory = backup_directory(project_root)
    target = directory / f"rank-hunter-{_safe_label(label)}-{_stamp()}.db"
    # Avoid a same-second collision without weakening deterministic naming.
    counter = 2
    while target.exists():
        target = directory / f"rank-hunter-{_safe_label(label)}-{_stamp()}-{counter}.db"
        counter += 1
    db.commit()
    out = sqlite3.connect(str(target), timeout=30)
    try:
        db.backup(out)
        out.commit()
    finally:
        out.close()
    validate_backup(target)
    return target


def list_backups(project_root: str | Path) -> list[Path]:
    directory = backup_directory(project_root)
    return sorted(
        (p for p in directory.glob("*.db") if p.is_file()),
        key=lambda p: (p.stat().st_mtime_ns, p.name.lower()),
        reverse=True,
    )


def backup_metadata(path: str | Path) -> dict[str, object]:
    """Return cheap read-only metadata for a managed backup display."""
    p = Path(path)
    stat = p.stat()
    src = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True, timeout=5)
    try:
        user_version = int(_backup_schema_info(src)["user_version"])
    finally:
        src.close()
    return {
        "name": p.name,
        "size_bytes": int(stat.st_size),
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "user_version": user_version,
    }



def import_backup_bytes(
    data: bytes,
    filename: str,
    *,
    project_root: str | Path,
) -> Path:
    """Stage an uploaded SQLite backup in the managed backup directory."""
    directory = backup_directory(project_root)
    original = Path(str(filename)).name or "uploaded.db"
    stem = Path(original).stem or "uploaded"
    target = directory / f"rank-hunter-import-{_safe_label(stem)}-{_stamp()}.db"
    counter = 2
    while target.exists():
        target = directory / f"rank-hunter-import-{_safe_label(stem)}-{_stamp()}-{counter}.db"
        counter += 1
    target.write_bytes(bytes(data))
    try:
        validate_backup(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target

def restore_backup(
    db: sqlite3.Connection,
    backup_path: str | Path,
    *,
    project_root: str | Path,
) -> Path:
    """Restore ``backup_path`` and return the automatic pre-restore snapshot."""
    source_path = Path(backup_path).resolve()
    directory = backup_directory(project_root).resolve()
    if source_path.parent != directory:
        raise ValueError("restore source must be a managed Rank Hunter backup")
    validate_backup(source_path)

    pre_restore = create_backup(db, project_root=project_root, label="pre-restore")
    db.commit()
    src = sqlite3.connect(str(source_path), timeout=30)
    try:
        src.backup(db)
        db.commit()
    finally:
        src.close()
    return pre_restore
