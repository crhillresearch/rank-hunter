"""Direct read-only access to the public LMFDB PostgreSQL mirror.

This module is intentionally transport-only: it returns catalog rows and does
not decide mathematical equality.  Rank Hunter still verifies every positive
candidate with Sage ``E.is_isomorphic(F)`` over Q in :mod:`rank42.novelty`.

The defaults below are the public SQL Mirror credentials published by LMFDB.
They can be overridden with ``RANK_HUNTER_LMFDB_SQL_*`` environment variables.
"""

from __future__ import annotations

import math
import os
from typing import Iterable

LMFDB_SQL_HOST = os.environ.get("RANK_HUNTER_LMFDB_SQL_HOST", "devmirror.lmfdb.xyz")
LMFDB_SQL_PORT = int(os.environ.get("RANK_HUNTER_LMFDB_SQL_PORT", "5432"))
LMFDB_SQL_DBNAME = os.environ.get("RANK_HUNTER_LMFDB_SQL_DBNAME", "lmfdb")
LMFDB_SQL_USER = os.environ.get("RANK_HUNTER_LMFDB_SQL_USER", "lmfdb")
LMFDB_SQL_PASSWORD = os.environ.get("RANK_HUNTER_LMFDB_SQL_PASSWORD", "lmfdb")
LMFDB_SQL_SSLMODE = os.environ.get("RANK_HUNTER_LMFDB_SQL_SSLMODE", "prefer")


class LMFDBSQLUnavailable(RuntimeError):
    """Raised when no supported PostgreSQL driver is installed."""


class LMFDBSQLFailure(RuntimeError):
    """Raised when the public SQL mirror cannot be queried."""


def _load_driver():
    """Return (driver_name, module), preferring psycopg 3 over psycopg2."""
    try:
        import psycopg  # type: ignore
        return "psycopg", psycopg
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore
        return "psycopg2", psycopg2
    except ImportError as exc:
        raise LMFDBSQLUnavailable(
            "LMFDB SQL driver not installed; run bash scripts/install-catalog-sql.sh"
        ) from exc


def _connect(driver_name, module, *, timeout: float):
    kwargs = {
        "host": LMFDB_SQL_HOST,
        "port": LMFDB_SQL_PORT,
        "dbname": LMFDB_SQL_DBNAME,
        "user": LMFDB_SQL_USER,
        "password": LMFDB_SQL_PASSWORD,
        "connect_timeout": max(1, int(math.ceil(float(timeout)))),
        "sslmode": LMFDB_SQL_SSLMODE,
    }
    try:
        if driver_name == "psycopg":
            return module.connect(**kwargs, autocommit=True)
        conn = module.connect(**kwargs)
        conn.autocommit = True
        return conn
    except Exception as exc:
        raise LMFDBSQLFailure(f"LMFDB SQL connection failed: {exc}") from exc


def _normalize_row(row) -> dict:
    label, clabel, ainvs, conductor, rank = row
    return {
        "lmfdb_label": str(label) if label is not None else None,
        "Clabel": str(clabel) if clabel is not None else None,
        "ainvs": [int(x) for x in (ainvs or [])],
        "conductor": int(conductor),
        "rank": int(rank) if rank is not None else None,
    }


def fetch_lmfdb_sql_many(conductors: Iterable[int], *, timeout: float = 8) -> tuple[dict[int, list[dict]], dict]:
    """Fetch all ``ec_curvedata`` rows for many conductors in one SQL query.

    The public mirror is used read-only.  Results are grouped by conductor;
    requested conductors with no rows are present with an empty list.
    """
    values = sorted({int(c) for c in conductors})
    if not values:
        return {}, {"transport": "sql", "requested_conductors": 0}

    driver_name, module = _load_driver()
    conn = _connect(driver_name, module, timeout=float(timeout))
    try:
        try:
            cur = conn.cursor()
            try:
                # Bound server execution as well as TCP connection time.  The
                # value is an integer literal computed locally, never user SQL.
                statement_ms = max(1000, int(float(timeout) * 1000))
                cur.execute(f"SET statement_timeout = {statement_ms}")
                cur.execute(
                    'SELECT lmfdb_label, "Clabel", ainvs, conductor, rank '
                    'FROM ec_curvedata WHERE conductor = ANY(%s) '
                    'ORDER BY conductor, lmfdb_label',
                    (values,),
                )
                raw_rows = cur.fetchall()
            finally:
                cur.close()
        except Exception as exc:
            raise LMFDBSQLFailure(f"LMFDB SQL query failed: {exc}") from exc
    finally:
        conn.close()

    grouped = {c: [] for c in values}
    for raw in raw_rows:
        row = _normalize_row(raw)
        grouped.setdefault(int(row["conductor"]), []).append(row)
    return grouped, {
        "transport": "sql",
        "driver": driver_name,
        "host": LMFDB_SQL_HOST,
        "port": LMFDB_SQL_PORT,
        "dbname": LMFDB_SQL_DBNAME,
        "requested_conductors": len(values),
        "returned_rows": len(raw_rows),
    }


def probe_lmfdb_sql(*, conductor: int = 88024, timeout: float = 8) -> dict:
    grouped, meta = fetch_lmfdb_sql_many([int(conductor)], timeout=timeout)
    rows = grouped.get(int(conductor), [])
    return {
        **meta,
        "conductor": int(conductor),
        "rows": len(rows),
        "labels": [r.get("lmfdb_label") for r in rows],
    }
