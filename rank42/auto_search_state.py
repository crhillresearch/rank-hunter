"""Persistent state for core Auto Search campaigns.

Auto Search is orchestration state, not mathematical evidence. Exact points and
rank bounds continue to live in the ordinary Rank Hunter curve/point/evidence
tables. These rows record what campaign was attempted and why a candidate
stopped.
"""
from __future__ import annotations

import json

from rank42.db import now
from rank42.schema_sql import execute_static_schema_script


AUTO_SEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_search_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    plugin_version TEXT,
    variant_id TEXT NOT NULL,
    family_spec TEXT NOT NULL,
    family_name TEXT,
    target_rank INTEGER NOT NULL,
    search_mode TEXT NOT NULL DEFAULT 'family',
    torsion_group TEXT,
    retention_floor INTEGER NOT NULL DEFAULT 0,
    stop_on_target INTEGER NOT NULL DEFAULT 1,
    parent_campaign_id INTEGER,
    provider_key TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    pool_id INTEGER,
    best_curve_id INTEGER,
    best_lower INTEGER NOT NULL DEFAULT 0,
    candidates_total INTEGER NOT NULL DEFAULT 0,
    candidates_done INTEGER NOT NULL DEFAULT 0,
    exact_count INTEGER NOT NULL DEFAULT 0,
    exhausted_count INTEGER NOT NULL DEFAULT 0,
    target_hit_count INTEGER NOT NULL DEFAULT 0,
    torsion_mismatch_count INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(pool_id) REFERENCES candidate_pools(id) ON DELETE SET NULL,
    FOREIGN KEY(best_curve_id) REFERENCES curves(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_search_campaign_status
    ON auto_search_campaigns(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS auto_search_trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    candidate_id INTEGER,
    curve_id INTEGER,
    parameter TEXT NOT NULL,
    score REAL,
    status TEXT NOT NULL DEFAULT 'queued',
    tier TEXT,
    rigorous_lower INTEGER NOT NULL DEFAULT 0,
    rigorous_upper INTEGER,
    exact_rank INTEGER,
    exact_points INTEGER NOT NULL DEFAULT 0,
    rank_growth INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(campaign_id, parameter),
    FOREIGN KEY(campaign_id) REFERENCES auto_search_campaigns(id) ON DELETE CASCADE,
    FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE SET NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_search_trials_campaign
    ON auto_search_trials(campaign_id, status, rigorous_lower DESC);

CREATE TABLE IF NOT EXISTS auto_search_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    parameter TEXT NOT NULL,
    tier TEXT NOT NULL,
    center TEXT NOT NULL,
    scale TEXT NOT NULL,
    height INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    exact_points INTEGER NOT NULL DEFAULT 0,
    runtime REAL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(campaign_id, parameter, tier, center, scale, height),
    FOREIGN KEY(campaign_id) REFERENCES auto_search_campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auto_search_steps_campaign
    ON auto_search_steps(campaign_id, parameter, tier, status);
"""


AUTO_SEARCH_CAMPAIGN_COLUMNS = {
    "search_mode": "TEXT NOT NULL DEFAULT 'family'",
    "torsion_group": "TEXT",
    "retention_floor": "INTEGER NOT NULL DEFAULT 0",
    "stop_on_target": "INTEGER NOT NULL DEFAULT 1",
    "parent_campaign_id": "INTEGER",
    "provider_key": "TEXT",
    "torsion_mismatch_count": "INTEGER NOT NULL DEFAULT 0",
}


def ensure_auto_search_schema(db, *, commit=True):
    """Initialize/migrate Auto Search state once; stay read-only thereafter."""
    tables = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                 'auto_search_campaigns','auto_search_trials','auto_search_steps'
               )"""
        ).fetchall()
    }
    ready = tables == {
        "auto_search_campaigns", "auto_search_trials", "auto_search_steps"
    }
    if ready:
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(auto_search_campaigns)").fetchall()
        }
        indexes = {
            str(row["name"])
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='index' AND name IN (
                     'idx_auto_search_campaign_status',
                     'idx_auto_search_campaign_parent',
                     'idx_auto_search_child_provider',
                     'idx_auto_search_trials_campaign',
                     'idx_auto_search_steps_campaign'
                   )"""
            ).fetchall()
        }
        if set(AUTO_SEARCH_CAMPAIGN_COLUMNS).issubset(columns) and {
            "idx_auto_search_campaign_status",
            "idx_auto_search_campaign_parent",
            "idx_auto_search_child_provider",
            "idx_auto_search_trials_campaign",
            "idx_auto_search_steps_campaign",
        }.issubset(indexes):
            return False

    execute_static_schema_script(db, AUTO_SEARCH_SCHEMA)
    columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(auto_search_campaigns)").fetchall()
    }
    for name, ddl in AUTO_SEARCH_CAMPAIGN_COLUMNS.items():
        if name not in columns:
            db.execute(f"ALTER TABLE auto_search_campaigns ADD COLUMN {name} {ddl}")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_auto_search_campaign_parent "
        "ON auto_search_campaigns(parent_campaign_id, id)"
    )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_search_child_provider
           ON auto_search_campaigns(parent_campaign_id, provider_key)
           WHERE parent_campaign_id IS NOT NULL AND provider_key IS NOT NULL"""
    )
    if commit:
        db.commit()
    return True


def create_campaign(
    db,
    *,
    plugin_id,
    plugin_version,
    variant_id,
    family_spec,
    family_name=None,
    target_rank=20,
    search_mode="family",
    torsion_group=None,
    retention_floor=0,
    stop_on_target=True,
    parent_campaign_id=None,
    provider_key=None,
    config=None,
):
    ensure_auto_search_schema(db)
    ts = now()
    cur = db.execute(
        """
        INSERT INTO auto_search_campaigns(
            plugin_id,plugin_version,variant_id,family_spec,family_name,target_rank,
            search_mode,torsion_group,retention_floor,stop_on_target,
            parent_campaign_id,provider_key,status,config_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?)
        """,
        (
            str(plugin_id),
            None if plugin_version is None else str(plugin_version),
            str(variant_id),
            str(family_spec),
            None if family_name is None else str(family_name),
            int(target_rank),
            str(search_mode or "family"),
            None if torsion_group is None else str(torsion_group),
            max(0, int(retention_floor or 0)),
            1 if stop_on_target else 0,
            None if parent_campaign_id is None else int(parent_campaign_id),
            None if provider_key is None else str(provider_key),
            json.dumps(config or {}, sort_keys=True),
            ts,
            ts,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def get_campaign(db, campaign_id):
    ensure_auto_search_schema(db)
    return db.execute(
        "SELECT * FROM auto_search_campaigns WHERE id=?",
        (int(campaign_id),),
    ).fetchone()


def update_campaign(db, campaign_id, **fields):
    ensure_auto_search_schema(db)
    allowed = {
        "status", "pool_id", "best_curve_id", "best_lower",
        "candidates_total", "candidates_done", "exact_count",
        "exhausted_count", "target_hit_count", "torsion_mismatch_count", "config_json",
        "error", "started_at", "finished_at",
    }
    values = {k: v for k, v in fields.items() if k in allowed}
    if not values:
        return
    values["updated_at"] = now()
    cols = ", ".join(f"{key}=?" for key in values)
    db.execute(
        f"UPDATE auto_search_campaigns SET {cols} WHERE id=?",
        [*values.values(), int(campaign_id)],
    )
    db.commit()


def upsert_trial(
    db,
    *,
    campaign_id,
    parameter,
    candidate_id=None,
    curve_id=None,
    score=None,
    status="queued",
    tier=None,
    rigorous_lower=0,
    rigorous_upper=None,
    exact_rank=None,
    exact_points=0,
    rank_growth=0,
    metadata=None,
    error=None,
):
    ensure_auto_search_schema(db)
    ts = now()
    db.execute(
        """
        INSERT INTO auto_search_trials(
            campaign_id,candidate_id,curve_id,parameter,score,status,tier,
            rigorous_lower,rigorous_upper,exact_rank,exact_points,rank_growth,
            metadata_json,error,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(campaign_id,parameter) DO UPDATE SET
            candidate_id=COALESCE(excluded.candidate_id,auto_search_trials.candidate_id),
            curve_id=COALESCE(excluded.curve_id,auto_search_trials.curve_id),
            score=COALESCE(excluded.score,auto_search_trials.score),
            status=excluded.status,
            tier=COALESCE(excluded.tier,auto_search_trials.tier),
            rigorous_lower=excluded.rigorous_lower,
            rigorous_upper=excluded.rigorous_upper,
            exact_rank=excluded.exact_rank,
            exact_points=excluded.exact_points,
            rank_growth=excluded.rank_growth,
            metadata_json=excluded.metadata_json,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        (
            int(campaign_id),
            None if candidate_id is None else int(candidate_id),
            None if curve_id is None else int(curve_id),
            str(parameter),
            None if score is None else float(score),
            str(status),
            None if tier is None else str(tier),
            int(rigorous_lower or 0),
            None if rigorous_upper is None else int(rigorous_upper),
            None if exact_rank is None else int(exact_rank),
            int(exact_points or 0),
            int(rank_growth or 0),
            json.dumps(metadata or {}, sort_keys=True),
            error,
            ts,
            ts,
        ),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM auto_search_trials WHERE campaign_id=? AND parameter=?",
        (int(campaign_id), str(parameter)),
    ).fetchone()


def campaign_trials(db, campaign_id, *, limit=1000):
    ensure_auto_search_schema(db)
    return db.execute(
        """
        SELECT t.*, c.family
        FROM auto_search_trials t
        LEFT JOIN curves c ON c.id=t.curve_id
        WHERE t.campaign_id=?
        ORDER BY t.rigorous_lower DESC, COALESCE(t.score,-1e99) DESC, t.id ASC
        LIMIT ?
        """,
        (int(campaign_id), int(limit)),
    ).fetchall()


def list_campaigns(db, *, limit=50, top_level_only=False, parent_campaign_id=None):
    ensure_auto_search_schema(db)
    where = []
    values = []
    if top_level_only:
        where.append("parent_campaign_id IS NULL")
    if parent_campaign_id is not None:
        where.append("parent_campaign_id=?")
        values.append(int(parent_campaign_id))
    sql = "SELECT * FROM auto_search_campaigns"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    values.append(int(limit))
    return db.execute(sql, values).fetchall()


def child_campaign(db, parent_campaign_id, provider_key):
    ensure_auto_search_schema(db)
    return db.execute(
        """SELECT * FROM auto_search_campaigns
           WHERE parent_campaign_id=? AND provider_key=? LIMIT 1""",
        (int(parent_campaign_id), str(provider_key)),
    ).fetchone()


def reconcile_child_campaign_lifecycle(db, parent_campaign_id, *, parent_status=None):
    """Keep provider-child lifecycle consistent with a stopped parent campaign.

    A UI stop kills the process tree. Without explicit state repair, a provider
    child can remain marked running even though its process died with the parent.
    Completed/target-hit providers are never downgraded.
    """
    ensure_auto_search_schema(db)
    parent = get_campaign(db, parent_campaign_id)
    if parent is None:
        return 0
    status = str(parent_status or parent["status"] or "")
    mapped = {
        "paused": "paused",
        "killed": "killed",
        "interrupted": "interrupted",
    }.get(status)
    if mapped is None:
        return 0

    rows = db.execute(
        """SELECT id FROM auto_search_campaigns
           WHERE parent_campaign_id=? AND status IN ('queued','running','stopping')""",
        (int(parent_campaign_id),),
    ).fetchall()
    if not rows:
        return 0

    for row in rows:
        fields = {"status": mapped, "error": None}
        if mapped == "killed":
            fields["finished_at"] = now()
        update_campaign(db, int(row["id"]), **fields)
    return len(rows)


def get_step(db, *, campaign_id, parameter, tier, center, scale, height):
    ensure_auto_search_schema(db)
    return db.execute(
        """
        SELECT * FROM auto_search_steps
        WHERE campaign_id=? AND parameter=? AND tier=? AND center=? AND scale=? AND height=?
        """,
        (
            int(campaign_id), str(parameter), str(tier),
            str(center), str(scale), int(height),
        ),
    ).fetchone()


def upsert_step(
    db,
    *,
    campaign_id,
    parameter,
    tier,
    center,
    scale,
    height,
    status,
    exact_points=0,
    runtime=None,
    error=None,
):
    ensure_auto_search_schema(db)
    ts = now()
    db.execute(
        """
        INSERT INTO auto_search_steps(
            campaign_id,parameter,tier,center,scale,height,status,
            exact_points,runtime,error,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(campaign_id,parameter,tier,center,scale,height) DO UPDATE SET
            status=excluded.status,
            exact_points=excluded.exact_points,
            runtime=excluded.runtime,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        (
            int(campaign_id), str(parameter), str(tier),
            str(center), str(scale), int(height), str(status),
            int(exact_points or 0),
            None if runtime is None else float(runtime),
            error,
            ts, ts,
        ),
    )
    db.commit()
    return get_step(
        db,
        campaign_id=campaign_id,
        parameter=parameter,
        tier=tier,
        center=center,
        scale=scale,
        height=height,
    )


def completed_step_count(db, campaign_id, parameter=None):
    ensure_auto_search_schema(db)
    if parameter is None:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM auto_search_steps WHERE campaign_id=? AND status='completed'",
            (int(campaign_id),),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT COUNT(*) AS n FROM auto_search_steps
            WHERE campaign_id=? AND parameter=? AND status='completed'
            """,
            (int(campaign_id), str(parameter)),
        ).fetchone()
    return int(row["n"] or 0)


def campaign_progress(db, campaign_id):
    """Derive resumable campaign counters from durable terminal trials.

    A torsion-mismatch trial is audit history only. Its curve may have a large
    rigorous rank in some unrelated family, but that evidence is ineligible for
    the selected torsion campaign's best rank and goal counters.
    """
    ensure_auto_search_schema(db)
    row = db.execute(
        """
        SELECT
          COUNT(*) AS trials,
          SUM(t.status IN ('exact','target_hit','exhausted','singular','torsion_mismatch','screened_out')) AS done,
          SUM(t.status='exact') AS exact_count,
          SUM(
            CASE
              WHEN t.status NOT IN ('torsion_mismatch','screened_out')
               AND COALESCE(t.rigorous_lower,0) >= c.target_rank
              THEN 1 ELSE 0
            END
          ) AS target_hit_count,
          SUM(t.status='exhausted') AS exhausted_count,
          SUM(t.status='torsion_mismatch') AS torsion_mismatch_count,
          MAX(
            CASE
              WHEN t.status NOT IN ('torsion_mismatch','screened_out') THEN t.rigorous_lower
              ELSE NULL
            END
          ) AS best_lower
        FROM auto_search_trials t
        JOIN auto_search_campaigns c ON c.id=t.campaign_id
        WHERE t.campaign_id=?
        """,
        (int(campaign_id),),
    ).fetchone()
    best = db.execute(
        """
        SELECT curve_id, rigorous_lower
        FROM auto_search_trials
        WHERE campaign_id=? AND status NOT IN ('torsion_mismatch','screened_out')
        ORDER BY rigorous_lower DESC, id DESC
        LIMIT 1
        """,
        (int(campaign_id),),
    ).fetchone()
    return {
        "trials": int(row["trials"] or 0),
        "done": int(row["done"] or 0),
        "exact_count": int(row["exact_count"] or 0),
        "target_hit_count": int(row["target_hit_count"] or 0),
        "exhausted_count": int(row["exhausted_count"] or 0),
        "torsion_mismatch_count": int(row["torsion_mismatch_count"] or 0),
        "best_lower": int(row["best_lower"] or 0),
        "best_curve_id": (
            int(best["curve_id"])
            if best is not None and best["curve_id"] is not None
            else None
        ),
    }


def terminal_trial_parameters(db, campaign_id):
    ensure_auto_search_schema(db)
    rows = db.execute(
        """
        SELECT parameter FROM auto_search_trials
        WHERE campaign_id=? AND status IN ('exact','target_hit','exhausted','singular','torsion_mismatch','screened_out')
        """,
        (int(campaign_id),),
    ).fetchall()
    return {str(row["parameter"]) for row in rows}


def get_trial(db, campaign_id, parameter):
    ensure_auto_search_schema(db)
    return db.execute(
        "SELECT * FROM auto_search_trials WHERE campaign_id=? AND parameter=?",
        (int(campaign_id), str(parameter)),
    ).fetchone()
