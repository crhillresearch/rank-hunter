"""Durable Campaign-to-artifact research associations.

This ledger records research association only. Job/Pipeline ownership stays
authoritative in their relational campaign_id columns, and Curve scientific
truth remains in the Curve/evidence tables.
"""
from __future__ import annotations

from rank42.db import now
from rank42.schema_sql import execute_static_schema_script


ARTIFACT_KINDS = ("job", "pipeline_run", "curve")
OWNED_RELATION = "owned"
PINNED_RELATION = "pinned"

CAMPAIGN_LINEAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_campaign_artifacts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL
        CHECK(artifact_kind IN ('job','pipeline_run','curve')),
    artifact_id INTEGER NOT NULL,
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id,artifact_kind,artifact_id,relation),
    FOREIGN KEY(campaign_id) REFERENCES research_campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_campaign_artifacts_campaign
    ON research_campaign_artifacts(campaign_id,artifact_kind,relation,artifact_id);
CREATE INDEX IF NOT EXISTS idx_campaign_artifacts_artifact
    ON research_campaign_artifacts(artifact_kind,artifact_id,relation,campaign_id);
"""


def ensure_campaign_lineage_schema(db, *, commit=True):
    table = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='research_campaign_artifacts'"""
    ).fetchone()
    indexes = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='index' AND name IN (
                 'idx_campaign_artifacts_campaign',
                 'idx_campaign_artifacts_artifact'
               )"""
        ).fetchall()
    }
    changed = table is None or not {
        "idx_campaign_artifacts_campaign",
        "idx_campaign_artifacts_artifact",
    }.issubset(indexes)
    if changed:
        execute_static_schema_script(db, CAMPAIGN_LINEAGE_SCHEMA)
        if commit:
            db.commit()
    return changed


def _normalize_kind(kind):
    value = str(kind or "").strip()
    if value not in ARTIFACT_KINDS:
        raise ValueError(f"unsupported campaign artifact kind {value!r}")
    return value


def replace_owned_campaign_link(
    db,
    *,
    artifact_kind,
    artifact_id,
    campaign_id=None,
    commit=True,
):
    """Mirror current Job/Run ownership into the association ledger."""
    ensure_campaign_lineage_schema(db)
    kind = _normalize_kind(artifact_kind)
    if kind == "curve":
        raise ValueError("curve ownership is not mirrored; use explicit pinning")
    artifact_id = int(artifact_id)
    db.execute(
        """DELETE FROM research_campaign_artifacts
           WHERE artifact_kind=? AND artifact_id=? AND relation=?""",
        (kind, artifact_id, OWNED_RELATION),
    )
    if campaign_id is not None:
        db.execute(
            """INSERT OR IGNORE INTO research_campaign_artifacts(
                   campaign_id,artifact_kind,artifact_id,relation,created_at
               ) VALUES(?,?,?,?,?)""",
            (
                int(campaign_id),
                kind,
                artifact_id,
                OWNED_RELATION,
                now(),
            ),
        )
    if commit:
        db.commit()


def remove_artifact_links(db, *, artifact_kind, artifact_id, commit=True):
    ensure_campaign_lineage_schema(db)
    kind = _normalize_kind(artifact_kind)
    db.execute(
        """DELETE FROM research_campaign_artifacts
           WHERE artifact_kind=? AND artifact_id=?""",
        (kind, int(artifact_id)),
    )
    if commit:
        db.commit()


def pin_curve(db, *, campaign_id, curve_id, commit=True):
    ensure_campaign_lineage_schema(db)
    db.execute(
        """INSERT OR IGNORE INTO research_campaign_artifacts(
               campaign_id,artifact_kind,artifact_id,relation,created_at
           ) VALUES(?,'curve',?,'pinned',?)""",
        (int(campaign_id), int(curve_id), now()),
    )
    if commit:
        db.commit()


def unpin_curve(db, *, campaign_id, curve_id, commit=True):
    ensure_campaign_lineage_schema(db)
    db.execute(
        """DELETE FROM research_campaign_artifacts
           WHERE campaign_id=? AND artifact_kind='curve'
             AND artifact_id=? AND relation='pinned'""",
        (int(campaign_id), int(curve_id)),
    )
    if commit:
        db.commit()


def list_campaign_artifacts(db, campaign_id, *, artifact_kind=None, relation=None):
    ensure_campaign_lineage_schema(db)
    clauses = ["campaign_id=?"]
    values = [int(campaign_id)]
    if artifact_kind is not None:
        clauses.append("artifact_kind=?")
        values.append(_normalize_kind(artifact_kind))
    if relation is not None:
        clauses.append("relation=?")
        values.append(str(relation))
    return db.execute(
        """SELECT * FROM research_campaign_artifacts WHERE """
        + " AND ".join(clauses)
        + " ORDER BY artifact_kind,relation,artifact_id",
        values,
    ).fetchall()


def sync_owned_campaign_lineage(db, *, commit=True):
    """Idempotently mirror relational Job/Run ownership into lineage.

    Steady-state reconciliation is read-only. DML is issued only when the
    mirrored association set differs from authoritative relational ownership.
    """
    ensure_campaign_lineage_schema(db, commit=False)
    changed = False
    tables = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN ('ui_jobs','search_pipeline_runs')"""
        ).fetchall()
    }
    for table, kind in (
        ("ui_jobs", "job"),
        ("search_pipeline_runs", "pipeline_run"),
    ):
        if table not in tables:
            continue
        columns = {
            str(row["name"])
            for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "campaign_id" not in columns:
            continue

        source_rows = db.execute(
            f"""SELECT id,campaign_id,created_at
                FROM {table}
                WHERE campaign_id IS NOT NULL"""
        ).fetchall()
        expected = {
            (int(row["campaign_id"]), int(row["id"])): str(row["created_at"])
            for row in source_rows
        }
        mirrored_rows = db.execute(
            """SELECT campaign_id,artifact_id
               FROM research_campaign_artifacts
               WHERE artifact_kind=? AND relation='owned'""",
            (kind,),
        ).fetchall()
        mirrored = {
            (int(row["campaign_id"]), int(row["artifact_id"]))
            for row in mirrored_rows
        }
        wanted = set(expected)
        stale = mirrored - wanted
        missing = wanted - mirrored
        if not stale and not missing:
            continue

        for campaign_id, artifact_id in stale:
            db.execute(
                """DELETE FROM research_campaign_artifacts
                   WHERE campaign_id=? AND artifact_kind=?
                     AND artifact_id=? AND relation='owned'""",
                (campaign_id, kind, artifact_id),
            )
        for campaign_id, artifact_id in missing:
            db.execute(
                """INSERT OR IGNORE INTO research_campaign_artifacts(
                       campaign_id,artifact_kind,artifact_id,relation,created_at
                   ) VALUES(?,?,?,'owned',?)""",
                (
                    campaign_id,
                    kind,
                    artifact_id,
                    expected[(campaign_id, artifact_id)],
                ),
            )
        changed = True
    if changed and commit:
        db.commit()
    return changed
