import sqlite3

import pytest

from rank42.schema_sql import execute_static_schema_script


def test_static_schema_script_preserves_outer_transaction():
    db = sqlite3.connect(":memory:")
    try:
        db.execute("BEGIN IMMEDIATE")
        execute_static_schema_script(
            db,
            """
            CREATE TABLE demo(id INTEGER PRIMARY KEY, value TEXT);
            INSERT INTO demo(id,value) VALUES(1,'ok');
            """,
        )
        assert db.in_transaction is True
        assert db.execute("SELECT value FROM demo WHERE id=1").fetchone()[0] == "ok"

        db.rollback()
        assert db.in_transaction is False
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo'"
        ).fetchone()
        assert table is None
    finally:
        db.close()


def test_static_schema_script_rejects_transaction_control():
    db = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="transaction control"):
            execute_static_schema_script(db, "BEGIN; CREATE TABLE demo(id INTEGER);")
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo'"
        ).fetchone()
        assert table is None
    finally:
        db.close()


def test_static_schema_script_rejects_incomplete_statement():
    db = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="incomplete static schema statement"):
            execute_static_schema_script(db, "CREATE TABLE demo(id INTEGER)")
    finally:
        db.close()
