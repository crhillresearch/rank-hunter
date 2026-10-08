"""Sage-free schema owner for prime/local cache tables.

UI and migration startup may import this module. Exact local arithmetic remains
in rank42.prime_local and may depend on Sage.
"""
from __future__ import annotations

from rank42.schema_manifest import (
    PRIME_LOCAL_SCHEMA_MANIFEST,
    inspect_schema,
    schema_issue_summary,
)
from rank42.schema_sql import execute_static_schema_script


PRIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS curve_prime_cache_state(
    curve_id INTEGER PRIMARY KEY,
    model_key TEXT NOT NULL,
    good_prime_bound INTEGER NOT NULL DEFAULT 0,
    bad_primes_complete INTEGER NOT NULL DEFAULT 0,
    bad_primes_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS curve_prime_data(
    curve_id INTEGER NOT NULL,
    p TEXT NOT NULL,
    model_key TEXT NOT NULL,
    good_reduction INTEGER NOT NULL,
    ap INTEGER,
    cardinality INTEGER,
    normalized_ap REAL,
    discriminant_valuation INTEGER,
    conductor_valuation INTEGER,
    tamagawa_number INTEGER,
    kodaira_symbol TEXT,
    local_root_number INTEGER,
    computed_at TEXT NOT NULL,
    PRIMARY KEY(curve_id,p),
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_curve_prime_data_curve
    ON curve_prime_data(curve_id,p);

CREATE TABLE IF NOT EXISTS quartic_local_tests(
    search_id INTEGER NOT NULL,
    p INTEGER NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    computed_at TEXT NOT NULL,
    PRIMARY KEY(search_id,p),
    FOREIGN KEY(search_id) REFERENCES quartic_searches(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_quartic_local_tests_search
    ON quartic_local_tests(search_id,status,p);
"""


def _curve_prime_p_declared_type(db):
    rows = db.execute("PRAGMA table_info(curve_prime_data)").fetchall()
    for row in rows:
        if str(row["name"]) == "p":
            return str(row["type"] or "").strip().upper()
    return None


def _migrate_curve_prime_p_to_text(db):
    """Rebuild the prime cache so p can hold arbitrary-size prime integers."""
    execute_static_schema_script(
        db,
        """
        DROP INDEX IF EXISTS idx_curve_prime_data_curve;
        CREATE TABLE curve_prime_data_new(
            curve_id INTEGER NOT NULL,
            p TEXT NOT NULL,
            model_key TEXT NOT NULL,
            good_reduction INTEGER NOT NULL,
            ap INTEGER,
            cardinality INTEGER,
            normalized_ap REAL,
            discriminant_valuation INTEGER,
            conductor_valuation INTEGER,
            tamagawa_number INTEGER,
            kodaira_symbol TEXT,
            local_root_number INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY(curve_id,p),
            FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
        );
        INSERT INTO curve_prime_data_new(
            curve_id,p,model_key,good_reduction,ap,cardinality,
            normalized_ap,discriminant_valuation,conductor_valuation,
            tamagawa_number,kodaira_symbol,local_root_number,computed_at
        )
        SELECT
            curve_id,CAST(p AS TEXT),model_key,good_reduction,ap,cardinality,
            normalized_ap,discriminant_valuation,conductor_valuation,
            tamagawa_number,kodaira_symbol,local_root_number,computed_at
        FROM curve_prime_data;
        DROP TABLE curve_prime_data;
        ALTER TABLE curve_prime_data_new RENAME TO curve_prime_data;
        CREATE INDEX idx_curve_prime_data_curve
            ON curve_prime_data(curve_id,p);
        """
    )


class PrimeSchemaNotReady(RuntimeError):
    """Raised when scientific prime/local code sees an unmigrated schema."""


def prime_schema_status(db):
    """Inspect prime/local schema readiness without mutating the database."""
    return inspect_schema(db, PRIME_LOCAL_SCHEMA_MANIFEST)


def require_prime_schema(db):
    """Require the manager-owned prime/local schema without repairing it.

    Scientific runtime code uses this read-only guard. Schema creation and
    legacy table rebuilds belong to the Migration Manager startup boundary.
    """
    status = prime_schema_status(db)
    if not status["ready"]:
        reason = schema_issue_summary(status) or "schema is not ready"
        raise PrimeSchemaNotReady(
            "prime/local schema is not ready; run the Migration Manager via "
            "rank42.migration_manager.open_database_with_migrations() "
            f"before scientific execution ({reason})"
        )
    return status


def ensure_prime_schema(db, *, commit=True):
    """Initialize/migrate prime/local cache state without importing Sage."""
    wanted = {
        "curve_prime_cache_state",
        "curve_prime_data",
        "quartic_local_tests",
    }
    tables = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                 'curve_prime_cache_state',
                 'curve_prime_data',
                 'quartic_local_tests'
               )"""
        ).fetchall()
    }
    changed = False
    if tables != wanted:
        execute_static_schema_script(db, PRIME_SCHEMA)
        changed = True

    # Older Rank Hunter builds stored p as SQLite INTEGER. Bad primes of
    # high-coefficient curves can exceed 2^63-1, so retain the exact positive
    # decimal prime label as TEXT instead of truncating or dropping it.
    if _curve_prime_p_declared_type(db) not in {None, "TEXT"}:
        _migrate_curve_prime_p_to_text(db)
        changed = True

    indexes = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='index' AND name IN (
                 'idx_curve_prime_data_curve',
                 'idx_quartic_local_tests_search'
               )"""
        ).fetchall()
    }
    if not {
        "idx_curve_prime_data_curve",
        "idx_quartic_local_tests_search",
    }.issubset(indexes):
        execute_static_schema_script(db, PRIME_SCHEMA)
        changed = True

    if changed and commit:
        db.commit()
    return changed
