"""Transaction-preserving execution for trusted static schema scripts.

Unlike sqlite3.Connection.executescript(), this helper does not implicitly
commit before running the script. It is intentionally for Rank Hunter-owned
static schema DDL only, never arbitrary user SQL.
"""
from __future__ import annotations

import sqlite3


_TRANSACTION_CONTROL = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}


def execute_static_schema_script(db, script: str) -> None:
    """Execute a trusted multi-statement schema script without escaping a transaction."""
    buffer = ""
    for char in str(script):
        buffer += char
        if char != ";" or not sqlite3.complete_statement(buffer):
            continue

        statement = buffer.strip()
        first = (
            statement.lstrip().split(None, 1)[0].rstrip(";").upper()
            if statement
            else ""
        )
        if first in _TRANSACTION_CONTROL:
            raise ValueError(
                f"transaction control is not allowed in static schema scripts: {first}"
            )
        if statement:
            db.execute(statement)
        buffer = ""

    trailing = buffer.strip()
    if trailing:
        raise ValueError("incomplete static schema statement")
