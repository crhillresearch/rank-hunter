import sqlite3

import pytest

from rank42.db import CURRENT_SCHEMA_VERSION, connect, stats, upsert_curve
from rank42.db_backup import (
    create_backup,
    list_backups,
    restore_backup,
    validate_backup,
)


def test_database_backup_and_restore_round_trip(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        upsert_curve(db, family="fixture", parameter="1", generic_lower=9)
        backup = create_backup(db, project_root=tmp_path, label="manual")
        assert backup.exists()
        assert backup in list_backups(tmp_path)

        upsert_curve(db, family="fixture", parameter="2", generic_lower=10)
        assert stats(db)["curves"] == 2

        pre_restore = restore_backup(db, backup, project_root=tmp_path)
        assert pre_restore.exists()
        assert "pre-restore" in pre_restore.name
        assert stats(db)["curves"] == 1
    finally:
        db.close()


def test_imported_backup_is_validated_and_staged(tmp_path):
    from rank42.db_backup import import_backup_bytes

    source_root = tmp_path / "source"
    source_root.mkdir()
    source_db = connect(source_root / "rank42.db")
    try:
        upsert_curve(source_db, family="fixture", parameter="1", generic_lower=9)
    finally:
        source_db.close()

    staged = import_backup_bytes(
        (source_root / "rank42.db").read_bytes(),
        "my-old-rank42.db",
        project_root=tmp_path / "target",
    )
    assert staged.exists()
    assert staged.parent.name == "backups"


def test_backup_validation_rejects_unrelated_sqlite_with_curves_table(tmp_path):
    path = tmp_path / "not-rank-hunter.db"
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
        db.commit()
    finally:
        db.close()

    with pytest.raises(ValueError, match="compatible Rank Hunter"):
        validate_backup(path)


def test_current_schema_backup_requires_full_core_manifest(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        backup = create_backup(db, project_root=tmp_path, label="damaged-current")
    finally:
        db.close()

    raw = sqlite3.connect(backup)
    try:
        raw.execute("DROP TABLE rank_evidence")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(
        ValueError,
        match="claims current Rank Hunter schema",
    ):
        validate_backup(backup)


def test_legacy_backup_signature_requires_events_columns(tmp_path):
    path = tmp_path / "legacy-shaped.db"
    db = sqlite3.connect(path)
    try:
        db.execute(
            """CREATE TABLE curves(
                   id INTEGER PRIMARY KEY,
                   family TEXT,
                   parameter TEXT,
                   created_at TEXT,
                   updated_at TEXT
               )"""
        )
        db.execute(
            """CREATE TABLE events(
                   id INTEGER PRIMARY KEY,
                   curve_id INTEGER,
                   level TEXT,
                   created_at TEXT
               )"""
        )
        db.execute(
            f"PRAGMA user_version={CURRENT_SCHEMA_VERSION - 1}"
        )
        db.commit()
    finally:
        db.close()

    with pytest.raises(
        ValueError,
        match=r"events missing column\(s\): message",
    ):
        validate_backup(path)


def test_backup_validation_rejects_future_schema(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        backup = create_backup(db, project_root=tmp_path, label="future")
    finally:
        db.close()

    raw = sqlite3.connect(backup)
    try:
        raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(ValueError, match="newer than this core supports"):
        validate_backup(backup)


def test_backup_validation_allows_older_schema_signature_for_migration(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        backup = create_backup(db, project_root=tmp_path, label="older")
    finally:
        db.close()

    raw = sqlite3.connect(backup)
    try:
        raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION - 1}")
        raw.commit()
    finally:
        raw.close()

    validate_backup(backup)
