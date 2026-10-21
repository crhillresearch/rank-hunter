"""Shared read-only database inspection helpers.

This module owns two System safety contracts:

* expensive SQLite inspection PRAGMAs run with a hard wall-clock budget; and
* Advanced SQL treats only explicitly safe query PRAGMAs as read-only.

Unknown PRAGMAs and mutable scalar PRAGMAs with assignment-style arguments are
conservatively classified as writes.
"""
from __future__ import annotations

import re
import sqlite3
import time


_READ_ONLY_PRAGMAS = frozenset({
    "application_id",
    "compile_options",
    "database_list",
    "encoding",
    "foreign_key_check",
    "foreign_keys",
    "freelist_count",
    "index_info",
    "index_list",
    "index_xinfo",
    "integrity_check",
    "journal_mode",
    "page_count",
    "page_size",
    "quick_check",
    "schema_version",
    "table_info",
    "table_xinfo",
    "user_version",
})

# These PRAGMAs are inspections/checks whose parenthesized argument selects a
# table/index or bounds the amount of checking. Parenthesized arguments on
# mutable scalar PRAGMAs (for example user_version(17) or journal_mode(WAL))
# are deliberately rejected as write-capable.
_READ_ONLY_PRAGMAS_WITH_QUERY_ARGS = frozenset({
    "foreign_key_check",
    "index_info",
    "index_list",
    "index_xinfo",
    "integrity_check",
    "quick_check",
    "table_info",
    "table_xinfo",
})

_PRAGMA_RE = re.compile(
    r"(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\((?P<arg>.*)\))?",
    re.DOTALL,
)


def sql_is_readonly(sql: str) -> bool:
    """Conservatively classify one statement accepted by sqlite3.execute().

    SELECT and EXPLAIN are read-only. A PRAGMA is read-only only when its name
    is explicitly allowlisted and its syntax cannot be an assignment form.
    Unknown PRAGMAs, equals assignments, mutable scalar parenthesized arguments,
    malformed statements, and multi-statement input are treated as writes.
    """
    statement = str(sql or "").strip()
    if not statement:
        return True

    # sqlite3.execute() accepts at most one statement. Treat anything that
    # looks like multiple statements conservatively before it reaches SQLite.
    statement = statement.rstrip(";").strip()
    if ";" in statement:
        return False

    head = statement.split(None, 1)[0].upper()
    if head in {"SELECT", "EXPLAIN"}:
        return True
    if head != "PRAGMA":
        return False

    payload = statement[len("PRAGMA"):].strip()
    if not payload or "=" in payload:
        return False

    match = _PRAGMA_RE.fullmatch(payload)
    if match is None:
        return False

    name = match.group("name").lower()
    if name not in _READ_ONLY_PRAGMAS:
        return False

    argument = match.group("arg")
    if argument is None:
        return True
    return name in _READ_ONLY_PRAGMAS_WITH_QUERY_ARGS


def pragma_rows_with_budget(db, sql: str, *, timeout_seconds: float = 8):
    """Run an expensive read-only PRAGMA with a hard wall-clock budget."""
    if not sql_is_readonly(sql) or not str(sql or "").lstrip().upper().startswith(
        "PRAGMA"
    ):
        raise ValueError("bounded database checks require a read-only PRAGMA")

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))

    def _progress():
        return 1 if time.monotonic() >= deadline else 0

    db.set_progress_handler(_progress, 5000)
    try:
        rows = list(db.execute(sql).fetchall())
        return {
            "completed": True,
            "rows": rows,
            "error": "",
        }
    except sqlite3.DatabaseError as exc:
        text = str(exc)
        interrupted = "interrupted" in text.lower()
        return {
            "completed": False,
            "rows": [],
            "error": (
                f"timed out after {float(timeout_seconds):g}s"
                if interrupted
                else text
            ),
        }
    finally:
        db.set_progress_handler(None, 0)
