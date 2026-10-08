"""Persistent process/job state for the local Rank Hunter Streamlit control center.

The scientific state remains in the existing Rank Hunter tables.  These tables only
track UI-launched subprocesses and small operator preferences.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rank42.campaign_lineage import (
    ensure_campaign_lineage_schema,
    replace_owned_campaign_link,
)
from rank42.campaign_schema import RESEARCH_CAMPAIGN_SCHEMA
from rank42.maintenance import maintenance_status, normal_operation_guard
from rank42.schema_sql import execute_static_schema_script
from rank42.settings_registry import setting_value


UI_SCHEMA = RESEARCH_CAMPAIGN_SCHEMA + """
CREATE TABLE IF NOT EXISTS ui_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    command_json TEXT NOT NULL,
    cwd TEXT NOT NULL,
    log_path TEXT NOT NULL,
    pid INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    exit_code INTEGER,
    metadata_json TEXT,
    campaign_id INTEGER REFERENCES research_campaigns(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ui_jobs_status_created
    ON ui_jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ui_jobs_campaign
    ON ui_jobs(campaign_id, id DESC);

CREATE TABLE IF NOT EXISTS ui_job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 100,
    resource_class TEXT NOT NULL DEFAULT 'default',
    available_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT,
    claimed_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES ui_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ui_job_queue_dispatch
    ON ui_job_queue(state, available_at, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_ui_job_queue_job
    ON ui_job_queue(job_id);

CREATE TABLE IF NOT EXISTS ui_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_service_heartbeats (
    service_id TEXT PRIMARY KEY,
    pid INTEGER,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    max_workers INTEGER,
    resource_limits_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_application_state (
    state_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS analysis_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curve_id INTEGER NOT NULL UNIQUE,
    campaign_id INTEGER,
    priority INTEGER NOT NULL DEFAULT 100,
    workflow_status TEXT NOT NULL DEFAULT 'open',
    research_goal TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES research_campaigns(id) ON DELETE SET NULL,
    CHECK(workflow_status IN ('open','deferred','resolved'))
);
CREATE INDEX IF NOT EXISTS idx_analysis_cases_status_priority
    ON analysis_cases(workflow_status, priority DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_cases_campaign
    ON analysis_cases(campaign_id, updated_at DESC);
"""


QUEUE_PRIORITY_PRESETS = {
    "Low": 50,
    "Normal": 100,
    "High": 200,
}
QUEUE_PRIORITY_MIN = 0
QUEUE_PRIORITY_MAX = 300


def queue_priority_label(priority):
    value = int(priority)
    if value >= 150:
        return "High"
    if value <= 75:
        return "Low"
    return "Normal"


def now():
    return datetime.now(timezone.utc).isoformat()


def ensure_ui_schema(db, *, commit=True):
    """Ensure/migrate durable UI tables."""
    objects = {
        (str(row["type"]), str(row["name"]))
        for row in db.execute(
            """SELECT type,name FROM sqlite_master
               WHERE (type='table' AND name IN (
                      'research_campaigns','ui_jobs','ui_job_queue','ui_settings',
                      'ui_service_heartbeats','ui_application_state',
                      'analysis_cases'
                    ))
                  OR (
                      type='index'
                      AND name IN (
                          'idx_ui_jobs_status_created',
                          'idx_ui_jobs_campaign',
                          'idx_ui_job_queue_dispatch',
                          'idx_ui_job_queue_job',
                          'idx_analysis_cases_status_priority',
                          'idx_analysis_cases_campaign'
                      )
                  )"""
        ).fetchall()
    }
    required_tables = {
        ("table", "research_campaigns"),
        ("table", "ui_jobs"),
        ("table", "ui_job_queue"),
        ("table", "ui_settings"),
        ("table", "ui_service_heartbeats"),
        ("table", "ui_application_state"),
        ("table", "analysis_cases"),
    }
    required_indexes = {
        ("index", "idx_ui_jobs_status_created"),
        ("index", "idx_ui_jobs_campaign"),
        ("index", "idx_ui_job_queue_dispatch"),
        ("index", "idx_ui_job_queue_job"),
        ("index", "idx_analysis_cases_status_priority"),
        ("index", "idx_analysis_cases_campaign"),
    }
    changed = False

    if ("table", "research_campaigns") not in objects:
        execute_static_schema_script(db, RESEARCH_CAMPAIGN_SCHEMA)
        changed = True

    # Existing pre-MANAGE-12 ui_jobs must gain the column before UI_SCHEMA
    # attempts to create the campaign index.
    if ("table", "ui_jobs") in objects:
        job_columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(ui_jobs)").fetchall()
        }
        if "campaign_id" not in job_columns:
            db.execute(
                "ALTER TABLE ui_jobs ADD COLUMN campaign_id INTEGER "
                "REFERENCES research_campaigns(id) ON DELETE SET NULL"
            )
            changed = True

    if not required_tables.issubset(objects) or not required_indexes.issubset(objects):
        execute_static_schema_script(db, UI_SCHEMA)
        changed = True

    lineage_change = ensure_campaign_lineage_schema(db, commit=False)
    changed = bool(changed or lineage_change)

    heartbeat_change = _migrate_dispatcher_heartbeat_state(db)
    changed = bool(changed or heartbeat_change)

    campaign_state_change = _migrate_active_campaign_state(db)
    changed = bool(changed or campaign_state_change)

    cursor_state_change = _migrate_candidate_cursor_state(db)
    changed = bool(changed or cursor_state_change)

    pipeline_draft_change = _migrate_pipeline_editor_draft_state(db)
    changed = bool(changed or pipeline_draft_change)

    if changed:
        db.execute(
            """INSERT OR IGNORE INTO ui_job_queue(
                   job_id,state,priority,resource_class,available_at,
                   attempts,created_at,updated_at
               )
               SELECT id,'queued',100,'default',created_at,0,created_at,updated_at
               FROM ui_jobs
               WHERE status='queued'"""
        )
        if commit:
            db.commit()
    return changed


def _connect(db_path):
    db = sqlite3.connect(str(Path(db_path)), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    ensure_ui_schema(db)
    return db


def _ui_table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _sync_schedule_occurrence_state(db, job_id, state):
    if not _ui_table_exists(db, "ui_job_schedule_occurrences"):
        return
    db.execute(
        """UPDATE ui_job_schedule_occurrences
           SET state=?,updated_at=?
           WHERE job_id=?""",
        (str(state), now(), int(job_id)),
    )


def _migrate_dispatcher_heartbeat_state(db):
    """Move legacy dispatcher service state out of generic ui_settings."""
    if not _ui_table_exists(db, "ui_settings"):
        return False
    if not _ui_table_exists(db, "ui_service_heartbeats"):
        return False

    row = db.execute(
        "SELECT value_json FROM ui_settings WHERE key='dispatcher_heartbeat'"
    ).fetchone()
    if row is None:
        return False

    try:
        payload = json.loads(row["value_json"])
    except Exception:
        payload = None

    if isinstance(payload, dict):
        resources = payload.get("resource_limits")
        if not isinstance(resources, dict):
            resources = None
        db.execute(
            """INSERT OR IGNORE INTO ui_service_heartbeats(
                   service_id,pid,worker_id,status,max_workers,
                   resource_limits_json,updated_at
               ) VALUES('dispatcher',?,?,?,?,?,?)""",
            (
                payload.get("pid"),
                str(payload.get("worker_id") or ""),
                str(payload.get("status") or "unknown"),
                payload.get("max_workers"),
                None if resources is None else json.dumps(resources),
                str(payload.get("updated_at") or now()),
            ),
        )

    # The heartbeat is ephemeral service state. Even malformed legacy payloads
    # must stop masquerading as user/application configuration.
    db.execute(
        "DELETE FROM ui_settings WHERE key='dispatcher_heartbeat'"
    )
    return True


def _migrate_active_campaign_state(db):
    """Normalize the selected Campaign pointer to current_campaign_id."""
    if not _ui_table_exists(db, "ui_application_state"):
        return False

    current = db.execute(
        """SELECT value_json,updated_at FROM ui_application_state
           WHERE state_key='current_campaign_id'"""
    ).fetchone()
    legacy_app = db.execute(
        """SELECT value_json,updated_at FROM ui_application_state
           WHERE state_key='active_campaign_id'"""
    ).fetchone()
    legacy_setting = (
        db.execute(
            """SELECT value_json,updated_at FROM ui_settings
               WHERE key='active_campaign_id'"""
        ).fetchone()
        if _ui_table_exists(db, "ui_settings")
        else None
    )

    changed = False
    if current is None:
        source = legacy_app or legacy_setting
        if source is not None:
            try:
                value = json.loads(source["value_json"])
            except Exception:
                value = None
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                db.execute(
                    """INSERT OR IGNORE INTO ui_application_state(
                           state_key,value_json,updated_at
                       ) VALUES('current_campaign_id',?,?)""",
                    (json.dumps(value), str(source["updated_at"] or now())),
                )
                changed = True

    if legacy_app is not None:
        db.execute(
            "DELETE FROM ui_application_state WHERE state_key='active_campaign_id'"
        )
        changed = True
    if legacy_setting is not None:
        db.execute(
            "DELETE FROM ui_settings WHERE key='active_campaign_id'"
        )
        changed = True
    return changed


def _migrate_candidate_cursor_state(db):
    """Move the legacy candidate source/offset cursor out of ui_settings."""
    if not _ui_table_exists(db, "ui_settings"):
        return False
    if not _ui_table_exists(db, "ui_application_state"):
        return False

    rows = db.execute(
        """SELECT key,value_json,updated_at
           FROM ui_settings
           WHERE key IN ('candidate_file','batch_offset')"""
    ).fetchall()
    if not rows:
        return False

    for row in rows:
        key = str(row["key"])
        try:
            value = json.loads(row["value_json"])
        except Exception:
            value = None

        valid = (
            key == "candidate_file"
            and isinstance(value, str)
            and bool(value.strip())
        ) or (
            key == "batch_offset"
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        )
        if valid:
            db.execute(
                """INSERT OR IGNORE INTO ui_application_state(
                       state_key,value_json,updated_at
                   ) VALUES(?,?,?)""",
                (
                    key,
                    json.dumps(value),
                    str(row["updated_at"] or now()),
                ),
            )

    db.execute(
        """DELETE FROM ui_settings
           WHERE key IN ('candidate_file','batch_offset')"""
    )
    return True


def _migrate_pipeline_editor_draft_state(db):
    """Move durable Pipeline editor recovery state out of generic settings."""
    if not _ui_table_exists(db, "ui_settings"):
        return False
    if not _ui_table_exists(db, "ui_application_state"):
        return False

    row = db.execute(
        """SELECT value_json,updated_at FROM ui_settings
           WHERE key='pipeline_editor_draft_v1'"""
    ).fetchone()
    if row is None:
        return False

    try:
        value = json.loads(row["value_json"])
    except Exception:
        value = None

    if isinstance(value, dict):
        db.execute(
            """INSERT OR IGNORE INTO ui_application_state(
                   state_key,value_json,updated_at
               ) VALUES('pipeline_editor_draft_v1',?,?)""",
            (json.dumps(value), str(row["updated_at"] or now())),
        )

    db.execute(
        "DELETE FROM ui_settings WHERE key='pipeline_editor_draft_v1'"
    )
    return True


def get_application_state(db, key, default=None):
    row = db.execute(
        "SELECT value_json FROM ui_application_state WHERE state_key=?",
        (str(key),),
    ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default


def set_application_state(db, key, value, *, commit=True):
    key = str(key)
    if value is None:
        db.execute(
            "DELETE FROM ui_application_state WHERE state_key=?",
            (key,),
        )
    else:
        db.execute(
            """INSERT INTO ui_application_state(state_key,value_json,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(state_key) DO UPDATE SET
                   value_json=excluded.value_json,
                   updated_at=excluded.updated_at""",
            (key, json.dumps(value), now()),
        )
    if commit:
        db.commit()


def get_setting(db, key, default=None):
    row = db.execute("SELECT value_json FROM ui_settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default


def set_setting(db, key, value):
    ts = now()
    db.execute(
        """
        INSERT INTO ui_settings(key, value_json, updated_at) VALUES(?,?,?)
        ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        """,
        (key, json.dumps(value), ts),
    )
    db.commit()


def _metadata_campaign_id(db, metadata):
    meta = dict(metadata or {})
    value = meta.get("campaign_id")
    try:
        campaign_id = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if campaign_id is None or not _ui_table_exists(db, "research_campaigns"):
        return None
    row = db.execute(
        "SELECT created_at FROM research_campaigns WHERE id=?",
        (campaign_id,),
    ).fetchone()
    if row is None:
        return None
    marker = meta.get("campaign_created_at")
    if marker and str(marker) != str(row["created_at"]):
        return None
    return campaign_id


def create_job(db, *, kind, label, command, cwd, log_path, metadata=None, commit=True):
    ts = now()
    campaign_id = _metadata_campaign_id(db, metadata)
    cur = db.execute(
        """
        INSERT INTO ui_jobs(
            kind,label,command_json,cwd,log_path,status,metadata_json,campaign_id,
            created_at,updated_at
        )
        VALUES(?,?,?,?,?,'queued',?,?,?,?)
        """,
        (
            kind,
            label,
            json.dumps([str(x) for x in command]),
            str(cwd),
            str(log_path),
            json.dumps(metadata or {}, sort_keys=True),
            campaign_id,
            ts,
            ts,
        ),
    )
    job_id = int(cur.lastrowid)
    replace_owned_campaign_link(
        db,
        artifact_kind="job",
        artifact_id=job_id,
        campaign_id=campaign_id,
        commit=False,
    )
    if commit:
        db.commit()
    return job_id


def update_job(db, job_id, **fields):
    allowed = {
        "pid", "status", "exit_code", "started_at", "finished_at",
        "metadata_json", "campaign_id",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [int(job_id)]
    db.execute(f"UPDATE ui_jobs SET {cols} WHERE id=?", vals)
    if "campaign_id" in fields:
        replace_owned_campaign_link(
            db,
            artifact_kind="job",
            artifact_id=int(job_id),
            campaign_id=fields.get("campaign_id"),
            commit=False,
        )
    db.commit()


def get_job(db, job_id):
    return db.execute("SELECT * FROM ui_jobs WHERE id=?", (int(job_id),)).fetchone()


def list_jobs(db, *, limit=50):
    return db.execute(
        "SELECT * FROM ui_jobs ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()


def job_status_counts(db):
    """Return exact Job counts by persisted status without loading Job rows."""
    rows = db.execute(
        "SELECT status, COUNT(*) AS n FROM ui_jobs GROUP BY status"
    ).fetchall()
    return {str(row["status"]): int(row["n"] or 0) for row in rows}


QUEUE_WAITING = {"queued", "held", "claimed"}
QUEUE_ACTIVE = {"claimed", "running"}
QUEUE_TERMINAL = {
    "succeeded", "failed", "interrupted", "stopped", "killed", "cancelled"
}

DEFAULT_RESOURCE_LIMITS = {
    "gpu_ratpoints": 1,
    "sage_heavy": 2,
    "database_maintenance": 1,
}


def infer_resource_class(kind, metadata=None):
    meta = dict(metadata or {})
    explicit = str(
        meta.get("queue_resource_class")
        or meta.get("resource_class")
        or ""
    ).strip()
    if explicit:
        return explicit

    backend = str(meta.get("ratpoints_backend") or "").upper()
    if backend == "GPU":
        return "gpu_ratpoints"

    name = str(kind or "").lower()
    if any(
        token in name
        for token in (
            "pipeline",
            "target",
            "family_search",
            "auto",
            "geometry",
            "descent",
            "lattice",
            "quartic",
            "saturation",
        )
    ):
        return "sage_heavy"
    if any(
        token in name
        for token in ("backup", "database", "import", "maintenance")
    ):
        return "database_maintenance"
    return "default"


def resource_limits(db):
    raw = setting_value(db, "queue_resource_limits", {})
    configured = dict(raw) if isinstance(raw, dict) else {}
    out = dict(DEFAULT_RESOURCE_LIMITS)
    for key, value in configured.items():
        try:
            out[str(key)] = max(1, int(value))
        except (TypeError, ValueError):
            continue
    return out


def _queue_time(value=None):
    if value is None:
        return now()
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(value)


def create_queue_item(
    db,
    *,
    job_id,
    priority=100,
    resource_class="default",
    available_at=None,
    commit=True,
):
    ts = now()
    cur = db.execute(
        """INSERT INTO ui_job_queue(
               job_id,state,priority,resource_class,available_at,
               attempts,created_at,updated_at
           ) VALUES(?,'queued',?,?,?,?,?,?)""",
        (
            int(job_id),
            int(priority),
            str(resource_class or "default"),
            _queue_time(available_at),
            0,
            ts,
            ts,
        ),
    )
    if commit:
        db.commit()
    return int(cur.lastrowid)


def get_queue_item(db, queue_id):
    return db.execute(
        """SELECT q.*,j.kind,j.label,j.status AS job_status,j.pid,j.metadata_json
           FROM ui_job_queue q JOIN ui_jobs j ON j.id=q.job_id
           WHERE q.id=?""",
        (int(queue_id),),
    ).fetchone()


def queue_item_for_job(db, job_id):
    return db.execute(
        """SELECT q.*,j.kind,j.label,j.status AS job_status,j.pid,j.metadata_json
           FROM ui_job_queue q JOIN ui_jobs j ON j.id=q.job_id
           WHERE q.job_id=?""",
        (int(job_id),),
    ).fetchone()


def list_queue(db, *, include_terminal=False, limit=500):
    sql = """
        SELECT q.*,j.kind,j.label,j.status AS job_status,j.pid,j.metadata_json,
               j.created_at AS job_created_at,j.started_at,j.finished_at
        FROM ui_job_queue q JOIN ui_jobs j ON j.id=q.job_id
    """
    values = []
    if not include_terminal:
        sql += " WHERE q.state NOT IN ('succeeded','failed','interrupted','stopped','killed','cancelled')"
    sql += " ORDER BY CASE q.state WHEN 'running' THEN 0 WHEN 'claimed' THEN 1 WHEN 'queued' THEN 2 WHEN 'held' THEN 3 ELSE 4 END, q.priority DESC, q.id"
    sql += " LIMIT ?"
    values.append(int(limit))
    return db.execute(sql, values).fetchall()


def queue_counts(db):
    rows = db.execute(
        "SELECT state,COUNT(*) AS n FROM ui_job_queue GROUP BY state"
    ).fetchall()
    return {str(row["state"]): int(row["n"] or 0) for row in rows}


def set_queue_priority(db, job_id, priority):
    """Change dispatch priority for one waiting Job.

    Running/claimed Jobs are intentionally immutable because dispatcher
    ownership has already been assigned.
    """
    value = int(priority)
    if value < QUEUE_PRIORITY_MIN or value > QUEUE_PRIORITY_MAX:
        raise ValueError(
            f"queue priority must be between {QUEUE_PRIORITY_MIN} and "
            f"{QUEUE_PRIORITY_MAX}"
        )
    cur = db.execute(
        """UPDATE ui_job_queue
           SET priority=?,updated_at=?
           WHERE job_id=? AND state IN ('queued','held')""",
        (value, now(), int(job_id)),
    )
    if int(cur.rowcount or 0) != 1:
        db.rollback()
        raise ValueError("only queued or held jobs can change priority")
    db.commit()
    return value


def hold_queue_job(db, job_id):
    cur = db.execute(
        """UPDATE ui_job_queue SET state='held',lease_owner=NULL,
                  lease_expires_at=NULL,claimed_at=NULL,updated_at=?
           WHERE job_id=? AND state='queued'""",
        (now(), int(job_id)),
    )
    db.commit()
    return int(cur.rowcount or 0) == 1


def release_queue_job(db, job_id):
    cur = db.execute(
        """UPDATE ui_job_queue SET state='queued',available_at=?,
                  lease_owner=NULL,lease_expires_at=NULL,claimed_at=NULL,updated_at=?
           WHERE job_id=? AND state='held'""",
        (now(), now(), int(job_id)),
    )
    db.commit()
    return int(cur.rowcount or 0) == 1


def cancel_queue_job(db, job_id):
    ts = now()
    cur = db.execute(
        """UPDATE ui_job_queue SET state='cancelled',lease_owner=NULL,
                  lease_expires_at=NULL,last_error=NULL,updated_at=?
           WHERE job_id=? AND state IN ('queued','held','claimed')""",
        (ts, int(job_id)),
    )
    if int(cur.rowcount or 0) == 1:
        db.execute(
            """UPDATE ui_jobs SET status='stopped',finished_at=?,updated_at=?
               WHERE id=? AND status='queued'""",
            (ts, ts, int(job_id)),
        )
        _sync_schedule_occurrence_state(db, int(job_id), "cancelled")
        db.commit()
        return True
    db.commit()
    return False


def claim_next_queue_item(
    db,
    worker_id,
    *,
    lease_seconds=60,
    blocked_resource_classes=(),
):
    current = now()
    lease_until = (
        datetime.now(timezone.utc) + timedelta(seconds=max(10, int(lease_seconds)))
    ).isoformat()
    db.execute("BEGIN IMMEDIATE")
    try:
        blocked = [
            str(value)
            for value in (blocked_resource_classes or ())
            if str(value).strip()
        ]
        sql = """SELECT id FROM ui_job_queue
                 WHERE state='queued' AND available_at<=?"""
        values = [current]
        if blocked:
            marks = ",".join("?" for _ in blocked)
            sql += f" AND resource_class NOT IN ({marks})"
            values.extend(blocked)
        sql += " ORDER BY priority DESC,id LIMIT 1"
        row = db.execute(sql, values).fetchone()
        if row is None:
            db.commit()
            return None
        queue_id = int(row["id"])
        cur = db.execute(
            """UPDATE ui_job_queue
               SET state='claimed',lease_owner=?,lease_expires_at=?,
                   claimed_at=?,attempts=attempts+1,updated_at=?
               WHERE id=? AND state='queued'""",
            (
                str(worker_id),
                lease_until,
                current,
                current,
                queue_id,
            ),
        )
        if int(cur.rowcount or 0) != 1:
            db.rollback()
            return None
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_queue_item(db, queue_id)


def mark_queue_running(db, job_id, worker_id, *, lease_seconds=120):
    lease_until = (
        datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds)))
    ).isoformat()
    cur = db.execute(
        """UPDATE ui_job_queue
           SET state='running',lease_owner=?,lease_expires_at=?,updated_at=?
           WHERE job_id=? AND state IN ('claimed','running')""",
        (str(worker_id), lease_until, now(), int(job_id)),
    )
    if int(cur.rowcount or 0) == 1:
        _sync_schedule_occurrence_state(db, int(job_id), "running")
    db.commit()
    return int(cur.rowcount or 0) == 1


def heartbeat_queue_job(db, job_id, worker_id, *, lease_seconds=120):
    lease_until = (
        datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds)))
    ).isoformat()
    db.execute(
        """UPDATE ui_job_queue
           SET lease_owner=?,lease_expires_at=?,updated_at=?
           WHERE job_id=? AND state='running'""",
        (str(worker_id), lease_until, now(), int(job_id)),
    )
    db.commit()


def finalize_queue_for_job(db, job_id, status=None, *, error=None):
    job = get_job(db, int(job_id))
    final = str(status or (job["status"] if job is not None else "interrupted"))
    if final not in QUEUE_TERMINAL:
        if final == "stopping":
            return False
        final = "interrupted"
    cur = db.execute(
        """UPDATE ui_job_queue
           SET state=?,lease_owner=NULL,lease_expires_at=NULL,
               last_error=?,updated_at=?
           WHERE job_id=? AND state NOT IN (
               'succeeded','failed','interrupted','stopped','killed','cancelled'
           )""",
        (final, None if error is None else str(error)[:1000], now(), int(job_id)),
    )
    if int(cur.rowcount or 0) == 1:
        _sync_schedule_occurrence_state(db, int(job_id), final)
    db.commit()
    return int(cur.rowcount or 0) == 1


def recover_expired_queue_claims(db):
    current = now()
    rows = db.execute(
        """SELECT q.id,q.job_id,q.state,j.status AS job_status,j.pid
           FROM ui_job_queue q JOIN ui_jobs j ON j.id=q.job_id
           WHERE q.state IN ('claimed','running')
             AND q.lease_expires_at IS NOT NULL
             AND q.lease_expires_at<?""",
        (current,),
    ).fetchall()
    recovered = 0
    for row in rows:
        job_status = str(row["job_status"] or "")
        if job_status in {"running", "stopping"} and process_alive(row["pid"]):
            db.execute(
                """UPDATE ui_job_queue
                   SET state='running',lease_expires_at=?,updated_at=?
                   WHERE id=?""",
                (
                    (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
                    current,
                    int(row["id"]),
                ),
            )
            continue
        if job_status == "queued":
            db.execute(
                """UPDATE ui_job_queue
                   SET state='queued',lease_owner=NULL,lease_expires_at=NULL,
                       claimed_at=NULL,available_at=?,last_error='expired claim recovered',
                       updated_at=?
                   WHERE id=?""",
                (current, current, int(row["id"])),
            )
            recovered += 1
        elif job_status in QUEUE_TERMINAL:
            finalize_queue_for_job(db, int(row["job_id"]), job_status)
    db.commit()
    return recovered


def reconcile_queue(db):
    recover_expired_queue_claims(db)
    rows = db.execute(
        """SELECT q.job_id,j.status
           FROM ui_job_queue q JOIN ui_jobs j ON j.id=q.job_id
           WHERE q.state IN ('claimed','running')"""
    ).fetchall()
    for row in rows:
        status = str(row["status"] or "")
        if status in QUEUE_TERMINAL:
            finalize_queue_for_job(db, int(row["job_id"]), status)


def _dispatcher_heartbeat_payload(db):
    if not _ui_table_exists(db, "ui_service_heartbeats"):
        return None
    row = db.execute(
        """SELECT pid,worker_id,status,max_workers,resource_limits_json,
                  updated_at
           FROM ui_service_heartbeats
           WHERE service_id='dispatcher'"""
    ).fetchone()
    if row is None:
        return None

    payload = {
        "pid": row["pid"],
        "worker_id": str(row["worker_id"] or ""),
        "status": str(row["status"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }
    if row["max_workers"] is not None:
        payload["max_workers"] = int(row["max_workers"])
    if row["resource_limits_json"]:
        try:
            resources = json.loads(row["resource_limits_json"])
        except Exception:
            resources = None
        if isinstance(resources, dict):
            payload["resource_limits"] = resources
    return payload


def dispatcher_status(db):
    info = _dispatcher_heartbeat_payload(db)
    if not isinstance(info, dict):
        return {
            "healthy": False,
            "fresh": False,
            "age_seconds": None,
            "status": "not started",
        }
    pid = info.get("pid")
    updated = info.get("updated_at")
    age_seconds = None
    fresh = False
    try:
        dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        age_seconds = max(
            0.0,
            (
                datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
            ).total_seconds(),
        )
        fresh = age_seconds <= 20
    except Exception:
        age_seconds = None
        fresh = False
    alive = process_alive(pid)
    out = dict(info)
    out["fresh"] = fresh
    out["age_seconds"] = age_seconds
    out["alive"] = bool(alive)
    out["healthy"] = bool(
        alive
        and fresh
        and str(info.get("status") or "") in {"starting", "running"}
    )
    return out


def _heartbeat_age_text(age_seconds):
    if age_seconds is None:
        return "unknown age"
    return f"{float(age_seconds):.1f}s old"


def resolve_dispatcher_health(heartbeat, service=None, runtime=None):
    """Project DB mirror + independent runtime liveness into one health state.

    The atomic runtime heartbeat is the liveness authority because scientific
    SQLite writers can legitimately delay the database mirror. The DB heartbeat
    remains useful corroborating state and preserves existing queue metadata.
    """
    heartbeat = dict(heartbeat or {})
    service = dict(service or {})
    runtime = dict(runtime or {})

    raw_status = str(heartbeat.get("status") or "not started").strip().lower()
    heartbeat_healthy = bool(heartbeat.get("healthy"))
    heartbeat_age = heartbeat.get("age_seconds")

    service_installed = bool(service.get("installed"))
    service_active = bool(service.get("active"))
    systemctl_available = service.get("systemctl_available")
    service_pid = service.get("main_pid")
    persistent = bool(service_installed and systemctl_available)
    mode = "persistent" if persistent else "detached"

    runtime_status = str(runtime.get("status") or "").strip().lower()
    runtime_pid = runtime.get("pid")
    runtime_age = runtime.get("age_seconds")
    runtime_fresh = bool(runtime.get("fresh"))
    runtime_alive = process_alive(runtime_pid) if runtime_pid else False
    runtime_matches_service = True
    if persistent and service_pid and runtime_pid:
        try:
            runtime_matches_service = int(service_pid) == int(runtime_pid)
        except (TypeError, ValueError):
            runtime_matches_service = False
    runtime_healthy = bool(
        runtime_fresh
        and runtime_alive
        and runtime_matches_service
        and runtime_status in {"starting", "running"}
    )

    pid = runtime_pid or heartbeat.get("pid") or service_pid
    worker_id = runtime.get("worker_id") or heartbeat.get("worker_id")
    max_workers = runtime.get("max_workers")
    if max_workers is None:
        max_workers = heartbeat.get("max_workers")

    if runtime_healthy and runtime_status == "starting":
        state = "starting"
        label = "Starting"
        tone = "warning"
        healthy = False
        detail = (
            f"Runtime heartbeat is current ({_heartbeat_age_text(runtime_age)}); "
            "dispatcher startup is still in progress."
        )
    elif runtime_healthy:
        state = "running"
        healthy = True
        tone = "ok"
        label = "Running" if persistent else "Running · detached"
        if heartbeat_healthy:
            detail = (
                f"Runtime heartbeat current ({_heartbeat_age_text(runtime_age)}); "
                f"database mirror current ({_heartbeat_age_text(heartbeat_age)})."
            )
        else:
            detail = (
                f"Runtime heartbeat current ({_heartbeat_age_text(runtime_age)}). "
                "Database heartbeat mirror is delayed "
                f"({_heartbeat_age_text(heartbeat_age)}), usually because SQLite "
                "is busy; dispatcher liveness is unaffected."
            )
    elif heartbeat_healthy and raw_status == "starting":
        state = "starting"
        label = "Starting"
        tone = "warning"
        healthy = False
        detail = (
            "Database heartbeat is current and reports dispatcher startup; "
            "runtime liveness sidecar has not confirmed running yet."
        )
    elif heartbeat_healthy:
        state = "running"
        healthy = True
        tone = "ok"
        label = "Running" if persistent else "Running · detached"
        if systemctl_available is False:
            detail = (
                f"Database heartbeat is current ({_heartbeat_age_text(heartbeat_age)}). "
                "Runtime liveness sidecar is not yet available and systemd user "
                "services are unavailable; detached compatibility fallback is active."
            )
        else:
            detail = (
                f"Database heartbeat is current ({_heartbeat_age_text(heartbeat_age)}). "
                "Runtime liveness sidecar is not yet available; compatibility "
                "fallback is active."
            )
    elif raw_status == "starting" and (pid or service_active):
        state = "starting"
        label = "Starting"
        tone = "warning"
        healthy = False
        detail = (
            "Dispatcher startup is in progress, but neither heartbeat channel "
            "has confirmed a healthy running state yet."
        )
    elif service_active:
        state = "degraded"
        label = "Degraded"
        tone = "warning"
        healthy = False
        detail = (
            "Persistent dispatcher service is active, but both heartbeat "
            f"channels are stale or missing (runtime {_heartbeat_age_text(runtime_age)}; "
            f"database {_heartbeat_age_text(heartbeat_age)})."
        )
    elif pid and raw_status in {"running", "starting"}:
        state = "degraded"
        label = "Degraded"
        tone = "warning"
        healthy = False
        detail = (
            "Stored dispatcher state reports a process, but neither heartbeat "
            "channel currently proves liveness."
        )
    else:
        state = "offline"
        label = "Offline"
        tone = "warning"
        healthy = False
        if systemctl_available is False:
            detail = (
                "systemd user services are unavailable and no healthy detached "
                "dispatcher heartbeat is present."
            )
        elif service_installed:
            detail = (
                "Persistent dispatcher service is installed but inactive, and no "
                "healthy dispatcher heartbeat is present."
            )
        else:
            detail = "No healthy dispatcher heartbeat is present."

    return {
        **heartbeat,
        "healthy": healthy,
        "state": state,
        "label": label,
        "tone": tone,
        "detail": detail,
        "mode": mode,
        "service_installed": service_installed,
        "service_active": service_active,
        "systemctl_available": systemctl_available,
        "pid": pid,
        "worker_id": worker_id,
        "max_workers": max_workers,
        "heartbeat_status": raw_status,
        "heartbeat_healthy": heartbeat_healthy,
        "heartbeat_age_seconds": heartbeat_age,
        "runtime_status": runtime_status,
        "runtime_healthy": runtime_healthy,
        "runtime_fresh": runtime_fresh,
        "runtime_age_seconds": runtime_age,
        "runtime_pid": runtime_pid,
        "runtime_protocol_ok": runtime.get("protocol_ok"),
    }


def dispatcher_health_status(db, *, service=None, runtime=None):
    """Return the shared Jobs-owned dispatcher health projection."""
    return resolve_dispatcher_health(
        dispatcher_status(db),
        service,
        runtime,
    )


def set_dispatcher_heartbeat(
    db,
    *,
    pid,
    worker_id,
    status,
    max_workers=None,
    resource_limits=None,
):
    payload = {
        "pid": int(pid),
        "worker_id": str(worker_id),
        "status": str(status),
        "updated_at": now(),
    }
    if max_workers is not None:
        payload["max_workers"] = int(max_workers)
    if resource_limits is not None:
        payload["resource_limits"] = {
            str(key): int(value)
            for key, value in dict(resource_limits).items()
        }

    db.execute(
        """INSERT INTO ui_service_heartbeats(
               service_id,pid,worker_id,status,max_workers,
               resource_limits_json,updated_at
           ) VALUES('dispatcher',?,?,?,?,?,?)
           ON CONFLICT(service_id) DO UPDATE SET
               pid=excluded.pid,
               worker_id=excluded.worker_id,
               status=excluded.status,
               max_workers=excluded.max_workers,
               resource_limits_json=excluded.resource_limits_json,
               updated_at=excluded.updated_at""",
        (
            payload["pid"],
            payload["worker_id"],
            payload["status"],
            payload.get("max_workers"),
            (
                None
                if "resource_limits" not in payload
                else json.dumps(payload["resource_limits"])
            ),
            payload["updated_at"],
        ),
    )
    db.commit()
    return payload


def _linux_process_state(pid):
    """Return the Linux /proc process state letter when available."""
    try:
        status = Path(f"/proc/{int(pid)}/status").read_text(
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, TypeError, ValueError):
        return None
    for line in status.splitlines():
        if line.startswith("State:"):
            value = line.partition(":")[2].strip()
            return value.split(None, 1)[0] if value else None
    return None


def process_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False

    # kill(pid, 0) succeeds for zombies because their PID still exists until
    # the parent reaps them. A zombie cannot heartbeat, hold a dispatcher lock,
    # or do useful work, so treat it (and Linux's rare dead state) as gone.
    state = _linux_process_state(pid)
    if state in {"Z", "X"}:
        return False

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _reap_child_process(pid):
    """Best-effort reap when a dead process happens to be our child."""
    if not pid:
        return False
    try:
        waited, _status = os.waitpid(int(pid), os.WNOHANG)
    except (ChildProcessError, OSError, TypeError, ValueError):
        return False
    return int(waited or 0) == int(pid)


def reconcile_jobs(db):
    """Mark stale 'running' jobs whose runner PID no longer exists."""
    rows = db.execute(
        "SELECT id,pid,status FROM ui_jobs WHERE status IN ('queued','running','stopping')"
    ).fetchall()
    for row in rows:
        if row["pid"] and not process_alive(row["pid"]):
            # A clean runner normally writes succeeded/failed itself.  If it died
            # before doing so, preserve that ambiguity rather than inventing success.
            update_job(
                db,
                row["id"],
                status="stopped" if row["status"] == "stopping" else "interrupted",
                finished_at=now(),
            )
            finalize_batch_cursor(db, row["id"])
            finalize_pool_search(db, row["id"])
            fresh = get_job(db, row["id"])
            if fresh is not None:
                finalize_queue_for_job(db, row["id"], fresh["status"])



def finalize_batch_cursor(db, job_id):
    """Advance a batch cursor by completed candidates, never by the planned batch.

    v0.6.x advanced the cursor at launch time.  A killed/failed 20-candidate
    batch could therefore jump all the way to the next 20 even when only a few
    candidates had actually finished.  v0.7.0 derives the next row from durable
    per-candidate completion markers in the job log.
    """
    row = get_job(db, job_id)
    if row is None:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except Exception:
        metadata = {}
    # Batch cursor semantics are declared by metadata, not a family/plugin job name.
    # This keeps core restart/resume logic generic for every family adapter.
    if "offset" not in metadata or not str(metadata.get("source") or "").strip():
        return None
    if metadata.get("cursor_finalized"):
        return metadata.get("cursor_next_offset")

    start = int(metadata.get("offset", 0))
    source = str(metadata.get("source") or "")
    from rank42.ui_results import summarize_job

    summary = summarize_job(row, read_log(row["log_path"]))
    completed = int(summary.get("candidates_completed") or 0)
    next_offset = start + completed

    # Respect an operator override or a parallel job.  Only advance the saved
    # cursor if it still points at the batch we are finalizing.
    current_source = get_application_state(db, "candidate_file", source)
    current_offset = int(
        get_application_state(db, "batch_offset", start) or 0
    )
    advanced = False
    if current_source == source and current_offset == start:
        set_application_state(db, "batch_offset", next_offset)
        advanced = True

    metadata.update(
        {
            "cursor_finalized": True,
            "cursor_completed": completed,
            "cursor_next_offset": next_offset,
            "cursor_advanced": advanced,
        }
    )
    update_job(db, job_id, metadata_json=json.dumps(metadata, sort_keys=True))
    return next_offset

def finalize_pool_search(db, job_id):
    """Persist true per-pool completion from durable candidate-done markers.

    A cancelled run only advances over candidates that emitted [candidate done].
    The candidate that was in-flight at cancellation stays unsearched and is the
    default resume point next time.
    """
    row = get_job(db, job_id)
    if row is None:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except Exception:
        metadata = {}
    pool_id = metadata.get("pool_id")
    candidate_ids = [int(x) for x in (metadata.get("candidate_ids") or [])]
    if not pool_id or not candidate_ids:
        return None
    if metadata.get("pool_cursor_finalized"):
        return metadata.get("pool_next_offset")

    completed = count_candidate_done(row["log_path"])
    completed = min(completed, len(candidate_ids))
    ts = now()

    # Link newly materialized curve rows without inferring completion from their
    # transient status. Durable [candidate done] markers remain authoritative.
    from rank42.candidates import pool_resume_offset, reconcile_candidates
    reconcile_candidates(
        db,
        int(pool_id),
        candidate_ids,
        infer_searched=False,
    )

    if completed:
        marks = candidate_ids[:completed]
        q = ",".join("?" for _ in marks)
        db.execute(f"UPDATE candidates SET status='searched',updated_at=? WHERE id IN ({q})", [ts, *marks])
        db.commit()

        # v0.8.4: candidate/job state records misses; the Curves inventory should
        # not be polluted by search-created rows whose rigorous rank evidence is
        # still zero. Snapshot useful screen metadata on the candidate, then
        # conservatively discard only empty transient curve rows created by this
        # job. Rows with points/lattices/coverings/quartic hits are preserved.
        from rank42.db import discard_transient_zero_evidence_curve
        pool_row=db.execute("SELECT generation_json FROM candidate_pools WHERE id=?",(int(pool_id),)).fetchone()
        try: pool_generation=json.loads(pool_row["generation_json"] or "{}") if pool_row else {}
        except Exception: pool_generation={}
        for candidate_id in marks:
            # A well-behaved runner is reconciled above. For older/custom adapters
            # that materialize a curve but never link the candidate explicitly,
            # recover only an exact family+parameter match created by this job.
            link=db.execute("SELECT parameter,metadata_json,curve_id FROM candidates WHERE id=?",(int(candidate_id),)).fetchone()
            if link is not None and link["curve_id"] is None:
                try: lmeta=json.loads(link["metadata_json"] or "{}")
                except Exception: lmeta={}
                family_name=lmeta.get("family_name") or lmeta.get("family") or pool_generation.get("family_name")
                if family_name:
                    matches=db.execute(
                        "SELECT id FROM curves WHERE family=? AND parameter=? AND created_at>=? ORDER BY id DESC LIMIT 2",
                        (str(family_name),str(link["parameter"]),str(row["started_at"] or row["created_at"])),
                    ).fetchall()
                    if len(matches)==1:
                        db.execute("UPDATE candidates SET curve_id=?,updated_at=? WHERE id=?",(int(matches[0]["id"]),ts,int(candidate_id))); db.commit()
            rec = db.execute(
                """SELECT c.id,c.metadata_json,c.curve_id,cv.status AS curve_status,
                          cv.quick_upper,cv.descent_upper,cv.generic_lower,
                          cv.descent_lower,cv.exact_rank,cv.created_at AS curve_created_at
                   FROM candidates c LEFT JOIN curves cv ON cv.id=c.curve_id
                   WHERE c.id=?""",
                (int(candidate_id),),
            ).fetchone()
            if rec is None:
                continue
            try:
                cmeta = json.loads(rec["metadata_json"] or "{}")
            except Exception:
                cmeta = {}
            lower = max(
                int(rec["generic_lower"] or 0),
                int(rec["descent_lower"] or 0),
                int(rec["exact_rank"] or 0),
            )
            snapshot = {
                "job_id": int(job_id),
                "curve_id": (int(rec["curve_id"]) if rec["curve_id"] is not None else None),
                "curve_status": rec["curve_status"],
                "quick_upper": rec["quick_upper"],
                "descent_upper": rec["descent_upper"],
                "rigorous_lower": int(lower),
                "exact_rank": rec["exact_rank"],
                "no_retained_curve": rec["curve_id"] is None,
            }
            cmeta["last_search"] = snapshot
            db.execute(
                "UPDATE candidates SET metadata_json=?,updated_at=? WHERE id=?",
                (json.dumps(cmeta, sort_keys=True), ts, int(candidate_id)),
            )
            db.commit()
            discarded = False
            if rec["curve_id"] is not None:
                discarded = discard_transient_zero_evidence_curve(
                    db, int(rec["curve_id"]), created_not_before=row["started_at"] or row["created_at"]
                )
            if discarded:
                cmeta["last_search"]["zero_evidence_curve_discarded"] = True
                db.execute(
                    "UPDATE candidates SET metadata_json=?,curve_id=NULL,updated_at=? WHERE id=?",
                    (json.dumps(cmeta, sort_keys=True), ts, int(candidate_id)),
                )
                db.commit()

    # Never mark the in-flight candidate searched on stop/interruption.
    next_offset = pool_resume_offset(db, int(pool_id))
    db.execute("UPDATE candidate_pools SET status='ready',updated_at=? WHERE id=?", (ts, int(pool_id)))
    db.commit()
    metadata.update({
        "pool_cursor_finalized": True,
        "pool_candidates_completed": completed,
        "pool_candidates_planned": len(candidate_ids),
        "pool_next_offset": int(next_offset),
    })
    update_job(db, job_id, metadata_json=json.dumps(metadata, sort_keys=True))
    return int(next_offset)


def _start_existing_job(db_path, job_id):
    """Start one already-created queued job. Dispatcher-only primitive."""
    db_path = str(Path(db_path).resolve())
    db = _connect(db_path)
    row = get_job(db, int(job_id))
    if row is None:
        db.close()
        raise ValueError(f"UI job #{int(job_id)} not found")
    if str(row["status"]) != "queued":
        db.close()
        raise ValueError(
            f"UI job #{int(job_id)} is {row['status']}; only queued jobs can start"
        )
    cwd = str(Path(row["cwd"]).resolve())
    runner = [
        sys.executable,
        "-m",
        "rank42.ui_job_runner",
        "--db",
        db_path,
        "--job-id",
        str(int(job_id)),
    ]
    proc = subprocess.Popen(
        runner,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    update_job(
        db,
        int(job_id),
        pid=proc.pid,
        status="running",
        started_at=now(),
    )
    db.close()
    return int(proc.pid)


def stop_dispatcher_process(db, *, timeout=5.0):
    """Stop only dispatcher infrastructure; scientific runners keep running."""
    current = dispatcher_status(db)
    pid = current.get("pid")
    if not pid:
        return False
    if not process_alive(pid):
        _reap_child_process(pid)
        return False
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        _reap_child_process(pid)
        return False

    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        if not process_alive(pid):
            _reap_child_process(pid)
            return True
        time.sleep(0.05)
    return not process_alive(pid)


def _wait_for_runtime_heartbeat(
    project_root,
    db_path,
    *,
    expected_pid=None,
    timeout=6.0,
):
    from rank42.dispatcher_service import runtime_heartbeat_status

    deadline = time.monotonic() + max(0.1, float(timeout))
    latest = runtime_heartbeat_status(project_root, db_path)
    while time.monotonic() < deadline:
        latest = runtime_heartbeat_status(project_root, db_path)
        pid = latest.get("pid")
        pid_matches = expected_pid is None
        if expected_pid is not None and pid is not None:
            try:
                pid_matches = int(pid) == int(expected_pid)
            except (TypeError, ValueError):
                pid_matches = False
        if (
            latest.get("fresh")
            and latest.get("protocol_ok")
            and pid_matches
            and str(latest.get("status") or "") in {"starting", "running"}
        ):
            return latest
        time.sleep(0.1)
    return latest


def ensure_dispatcher_running(db_path, project_root):
    db_path = str(Path(db_path).resolve())
    project_root = str(Path(project_root).resolve())
    if maintenance_status(db_path)["active"]:
        return None
    db = _connect(db_path)
    current = dispatcher_status(db)

    # Prefer an installed persistent user service. An active systemd process is
    # not enough: it must publish the current runtime-heartbeat protocol. This
    # self-heals long-lived services that survived a Rank Hunter code update.
    service = None
    try:
        from rank42.dispatcher_service import (
            restart_service,
            runtime_heartbeat_status,
            service_status,
            start_service,
        )

        service = service_status(project_root, db_path)
        if service.get("installed") and service.get("systemctl_available"):
            if not service.get("active"):
                service = start_service(project_root, db_path)

            runtime = runtime_heartbeat_status(project_root, db_path)
            needs_protocol_restart = bool(
                service.get("active")
                and runtime.get("protocol_ok") is not True
            )
            if needs_protocol_restart:
                service = restart_service(project_root, db_path)
                runtime = _wait_for_runtime_heartbeat(
                    project_root,
                    db_path,
                    expected_pid=service.get("main_pid"),
                )

            current = dispatcher_status(db)
            resolved = resolve_dispatcher_health(current, service, runtime)
            if resolved.get("healthy") or resolved.get("state") == "starting":
                pid = resolved.get("pid") or service.get("main_pid")
                db.close()
                return None if pid is None else int(pid)

            # A persistent service that is active but has lost both heartbeat
            # channels is wedged or running stale code. Restart it once here
            # instead of treating an alive MainPID as proof of dispatcher health.
            if service.get("active"):
                service = restart_service(project_root, db_path)
                runtime = _wait_for_runtime_heartbeat(
                    project_root,
                    db_path,
                    expected_pid=service.get("main_pid"),
                )
                current = dispatcher_status(db)
                resolved = resolve_dispatcher_health(current, service, runtime)
                if resolved.get("healthy") or resolved.get("state") == "starting":
                    pid = resolved.get("pid") or service.get("main_pid")
                    db.close()
                    return None if pid is None else int(pid)

                service_pid = service.get("main_pid")
                if service_pid and process_alive(service_pid):
                    db.close()
                    return int(service_pid)
    except Exception:
        # Queue execution must remain available even when user-service control
        # is missing or temporarily broken.
        service = None

    if current.get("healthy"):
        pid = int(current["pid"])
        db.close()
        return pid

    pid = current.get("pid")
    if (
        pid
        and process_alive(pid)
        and str(current.get("status") or "") != "stopped"
    ):
        db.close()
        return int(pid)
    if pid and not process_alive(pid):
        _reap_child_process(pid)

    command = [
        sys.executable,
        "-m",
        "rank42.dispatcher",
        "--db",
        db_path,
        "--project-root",
        project_root,
    ]
    proc = subprocess.Popen(
        command,
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    set_dispatcher_heartbeat(
        db,
        pid=proc.pid,
        worker_id=f"starting-{proc.pid}",
        status="starting",
        max_workers=int(setting_value(db, "queue_max_workers", 2) or 2),
    )
    db.close()
    return int(proc.pid)


def enqueue_job_record(
    db,
    *,
    kind,
    label,
    command,
    cwd,
    metadata=None,
    priority=100,
    resource_class=None,
    available_at=None,
):
    """Create one durable job + queue row on an existing connection.

    The caller owns the transaction. This is used by scheduled occurrences so
    occurrence registration and queue creation can commit atomically.
    """
    cwd = str(Path(cwd).resolve())
    logs_dir = Path(cwd) / ".rank42-ui" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    placeholder = logs_dir / "pending.log"
    job_id = create_job(
        db,
        kind=kind,
        label=label,
        command=command,
        cwd=cwd,
        log_path=placeholder,
        metadata=metadata,
        commit=False,
    )
    log_path = logs_dir / f"job-{job_id:06d}.log"
    db.execute(
        "UPDATE ui_jobs SET log_path=?,updated_at=? WHERE id=?",
        (str(log_path), now(), int(job_id)),
    )
    resolved_class = (
        str(resource_class)
        if resource_class is not None
        else infer_resource_class(kind, metadata)
    )
    create_queue_item(
        db,
        job_id=int(job_id),
        priority=int(priority),
        resource_class=resolved_class,
        available_at=available_at,
        commit=False,
    )
    return int(job_id)


def enqueue_job(
    db_path,
    *,
    kind,
    label,
    command,
    cwd,
    metadata=None,
    priority=100,
    resource_class=None,
    available_at=None,
    start_dispatcher=True,
):
    """Persist a job + queue item; the dispatcher owns process creation."""
    db_path = str(Path(db_path).resolve())
    cwd = str(Path(cwd).resolve())
    with normal_operation_guard(db_path):
        db = _connect(db_path)
        try:
            job_id = enqueue_job_record(
                db,
                kind=kind,
                label=label,
                command=command,
                cwd=cwd,
                metadata=metadata,
                priority=int(priority),
                resource_class=resource_class,
                available_at=available_at,
            )
            db.commit()
        finally:
            db.close()
    if start_dispatcher:
        ensure_dispatcher_running(db_path, cwd)
    return int(job_id)


def launch_job(db_path, *, kind, label, command, cwd, metadata=None):
    """Backward-compatible alias for durable queued execution."""
    return enqueue_job(
        db_path,
        kind=kind,
        label=label,
        command=command,
        cwd=cwd,
        metadata=metadata,
    )

def _descendant_process_groups(root_pid):
    """Return descendant process groups before a UI job is terminated.

    ``run_ratpoints`` isolates each CPU/GPU invocation in its own process
    group so it can clean up helper workers.  Force-killing the outer UI job
    would bypass Python ``finally`` blocks, therefore the UI also retires any
    descendant groups it can see before killing the outer group.  Linux/WSL
    exposes the child tree through /proc; failure to inspect it is harmless.
    """
    root_pid = int(root_pid)
    pending = [root_pid]
    seen = {root_pid}
    descendants = []
    while pending:
        parent = pending.pop()
        children_file = Path(f"/proc/{parent}/task/{parent}/children")
        try:
            children = [int(x) for x in children_file.read_text().split()]
        except (OSError, ValueError):
            children = []
        for child in children:
            if child in seen:
                continue
            seen.add(child)
            descendants.append(child)
            pending.append(child)
    groups = set()
    for child in descendants:
        try:
            groups.add(int(os.getpgid(child)))
        except (ProcessLookupError, PermissionError):
            pass
    groups.discard(root_pid)
    return sorted(groups, reverse=True)


def _best_effort_ui_write(db, callback, *, busy_timeout_ms=250):
    """Run shutdown bookkeeping without letting SQLite delay process signals."""
    try:
        old_timeout = int(db.execute("PRAGMA busy_timeout").fetchone()[0] or 30000)
    except Exception:
        old_timeout = 30000
    try:
        db.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))}")
        callback()
        return True
    except sqlite3.OperationalError as exc:
        try:
            db.rollback()
        except Exception:
            pass
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            return False
        raise
    finally:
        try:
            db.execute(f"PRAGMA busy_timeout={max(0, old_timeout)}")
        except Exception:
            pass


def _record_interrupted_scientific_stage(db, row):
    """Best-effort durable record of expensive scientific work stopped by the UI."""
    try:
        from rank42.hard_case_escalator import record_interrupted_ui_job
        return record_interrupted_ui_job(db, row)
    except Exception:
        # Stopping the process is primary. Scientific interruption bookkeeping
        # must never prevent SIGTERM/SIGKILL or leave the UI job stuck.
        return None


def _pause_metadata_json(row, paused, *, force=False):
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}
    if paused:
        meta["pause_requested"] = True
        meta["paused_at"] = now()
        meta.pop("resumed_as_job_id", None)
        meta.pop("stop_requested_at", None)
        meta.pop("stop_requested_force", None)
    else:
        meta.pop("pause_requested", None)
        meta.pop("paused_at", None)
        meta["stop_requested_at"] = now()
        meta["stop_requested_force"] = bool(force)
    return json.dumps(meta, sort_keys=True)


def stop_job(db, job_id, *, force=False, pause=False):
    row = get_job(db, job_id)
    if row is None:
        raise ValueError(f"UI job #{job_id} not found")
    pid = row["pid"]
    metadata_json = _pause_metadata_json(
        row,
        bool(pause) and not bool(force),
        force=bool(force),
    )

    def finalize_dead():
        update_job(
            db,
            job_id,
            status="killed" if force else "stopped",
            finished_at=now(),
            metadata_json=metadata_json,
        )
        finalize_batch_cursor(db, job_id)
        finalize_pool_search(db, job_id)
        finalize_queue_for_job(
            db,
            job_id,
            "killed" if force else "stopped",
        )

    if not pid or not process_alive(pid):
        _best_effort_ui_write(
            db,
            lambda: _record_interrupted_scientific_stage(db, row),
        )
        _best_effort_ui_write(db, finalize_dead)
        return False

    sig = signal.SIGKILL if force else signal.SIGTERM
    # Signal first. Shutdown must never wait behind a scientific SQLite writer.
    # Ratpoints/GPU wrappers may own nested groups, so retire descendants before
    # the outer UI job group.
    for child_pgid in _descendant_process_groups(int(pid)):
        try:
            os.killpg(int(child_pgid), sig)
        except ProcessLookupError:
            pass
    try:
        os.killpg(int(pid), sig)
    except ProcessLookupError:
        pass

    # Signal first, then persist the expensive stage that was interrupted.
    # The actual heartbeat time is recorded, not the configured maximum budget.
    _best_effort_ui_write(
        db,
        lambda: _record_interrupted_scientific_stage(db, row),
    )

    if force:
        _best_effort_ui_write(db, finalize_dead)
    else:
        _best_effort_ui_write(
            db,
            lambda: update_job(
                db,
                job_id,
                status="stopping",
                metadata_json=metadata_json,
            ),
        )
    return True



_EXTERNAL_MODULE_LABELS = {
    "rank42.search": "Nagao candidate search",
    "rank42.playground_focus": "Legacy focused Workbench search",
    "rank42.general_hunt": "General Hunt",
    "rank42.geometry_search": "Geometry",
    "rank42.pipeline_runner": "Build Your Own",
    "rank42.record_search": "Fixed-curve search",
    "rank42.lattice": "MW lattice build",
    "rank42.quartic_search": "Quartic search",
}


def _module_from_command(command):
    parts = str(command or "").split()
    for i, part in enumerate(parts[:-1]):
        if part == "-m" and (parts[i + 1].startswith("rank42.") or parts[i + 1].startswith("plugins.")):
            return parts[i + 1]
    return None


def discover_external_jobs(project_root, *, ui_runner_pids=()):
    """Discover terminal-launched Rank Hunter Python jobs in this project.

    UI-launched jobs run in their own process groups, so their scientific child
    processes are excluded by PGID.  The remaining top-level ``python -m
    rank42.*`` processes are surfaced as read-only external work.
    """
    root = Path(project_root).resolve()
    ui_pgids = set()
    for pid in ui_runner_pids:
        try:
            ui_pgids.add(os.getpgid(int(pid)))
        except (ProcessLookupError, PermissionError, OSError, TypeError, ValueError):
            pass

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,etimes=,args="],
            check=False, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    excluded_modules = {
        "rank42.ui_job_runner",
        "rank42.dispatcher",
        "rank42.status",
        "rank42.ui",
    }
    rows = []
    seen = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(None, 3)
        if len(fields) < 4:
            continue
        try:
            pid, ppid, elapsed = int(fields[0]), int(fields[1]), int(fields[2])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        command = fields[3]
        module = _module_from_command(command)
        if not module or module in excluded_modules:
            continue
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        if pgid in ui_pgids:
            continue
        try:
            cwd = Path(f"/proc/{pid}/cwd").resolve()
            cwd.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        # Avoid showing subprocess helpers separately when a rank42 parent is
        # also visible.  Python module workers are normally children of the
        # top-level search command.
        key = (pgid, module, command)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "source": "terminal",
            "pid": pid,
            "ppid": ppid,
            "pgid": pgid,
            "elapsed_seconds": elapsed,
            "module": module,
            "label": _EXTERNAL_MODULE_LABELS.get(module, module.replace("rank42.", "").replace("_", " ")),
            "command": command,
            "cwd": str(cwd),
            "status": "running",
        })
    # One foreground terminal search may spawn several ``rank42.*`` workers in
    # the same process group.  Report that as one piece of active work, choosing
    # the oldest Rank Hunter process as the group representative.
    rows.sort(key=lambda row: (-int(row["elapsed_seconds"]), int(row["pid"])))
    grouped = []
    seen_pgids = set()
    for row in rows:
        if row["pgid"] in seen_pgids:
            continue
        seen_pgids.add(row["pgid"])
        grouped.append(row)
    return grouped


def active_work(db, project_root):
    """Return UI and terminal Rank Hunter work as separate lists."""
    ui = db.execute(
        "SELECT * FROM ui_jobs WHERE status IN ('queued','running','stopping') ORDER BY id DESC"
    ).fetchall()
    external = discover_external_jobs(
        project_root, ui_runner_pids=[row["pid"] for row in ui if row["pid"]]
    )
    return {"ui": ui, "external": external, "count": len(ui) + len(external)}

def count_candidate_done(path):
    """Count every durable completion marker.

    This remains a full-log recovery/finalization primitive. Live UI polling
    must use latest_candidate_done() so refresh cost does not grow with
    historical log size.
    """
    p = Path(path)
    if not p.exists():
        return 0
    rx = re.compile(r"^\[candidate done\]\s+\d+/\d+\s*$")
    count = 0
    with p.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if rx.match(line.rstrip("\n")):
                count += 1
    return count


def latest_candidate_done(path, *, max_bytes=262_144):
    """Return the latest candidate completion index from a bounded log tail.

    Returns 0 when the complete log fits in the bounded window and contains no
    completion marker. Returns None when a larger log has no marker in the
    bounded tail, because reporting zero would be misleading.

    This helper is for frequent UI polling and never reads more than max_bytes.
    """
    p = Path(path)
    if not p.exists():
        return 0
    try:
        size = int(p.stat().st_size)
    except OSError:
        return None
    limit = max(1, int(max_bytes))
    try:
        with p.open("rb") as handle:
            start = max(0, size - limit)
            if start:
                handle.seek(start)
            data = handle.read(limit)
    except OSError:
        return None

    text = data.decode("utf-8", errors="replace")
    matches = list(
        re.finditer(
            r"^\[candidate done\]\s+(\d+)/(\d+)\s*$",
            text,
            flags=re.MULTILINE,
        )
    )
    if matches:
        return int(matches[-1].group(1))
    return 0 if size <= limit else None


def read_log(path, *, max_bytes=120_000):
    p = Path(path)
    if not p.exists():
        return ""
    size = p.stat().st_size
    with p.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            prefix = b"[... earlier log output omitted ...]\n"
        else:
            prefix = b""
        data = f.read()
    return (prefix + data).decode("utf-8", errors="replace")
