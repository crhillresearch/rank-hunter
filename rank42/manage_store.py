"""Persistent research-management state for Campaigns and scheduled Jobs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rank42.campaign_lineage import (
    ensure_campaign_lineage_schema,
    list_campaign_artifacts,
    pin_curve,
    remove_artifact_links,
    replace_owned_campaign_link,
    sync_owned_campaign_lineage,
    unpin_curve,
)
from rank42.campaign_schema import RESEARCH_CAMPAIGN_SCHEMA
from rank42.ui_store import (
    get_application_state,
    get_job,
    enqueue_job,
    enqueue_job_record,
    infer_resource_class,
    ensure_ui_schema,
    list_jobs,
    now,
    read_log,
    set_application_state,
    stop_job,
    update_job,
)
from rank42.ui_results import summarize_job
from rank42.schema_sql import execute_static_schema_script


MANAGE_SCHEMA = RESEARCH_CAMPAIGN_SCHEMA + """
CREATE TABLE IF NOT EXISTS research_campaign_notes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    entry_type TEXT NOT NULL DEFAULT 'note',
    handoff_state TEXT NOT NULL DEFAULT 'include',
    created_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES research_campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_research_campaign_notes_campaign
    ON research_campaign_notes(campaign_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS research_campaign_note_links(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    object_kind TEXT NOT NULL,
    object_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(note_id, object_kind, object_id),
    FOREIGN KEY(note_id) REFERENCES research_campaign_notes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_research_campaign_note_links_note
    ON research_campaign_note_links(note_id, id);

CREATE TABLE IF NOT EXISTS ui_job_schedules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    command_json TEXT NOT NULL,
    cwd TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    campaign_id INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    recurrence TEXT NOT NULL DEFAULT 'once',
    interval_minutes INTEGER,
    catch_up_policy TEXT NOT NULL DEFAULT 'latest',
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    last_job_id INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES research_campaigns(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_ui_job_schedules_due
    ON ui_job_schedules(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS ui_job_schedule_occurrences(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    scheduled_for TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    job_id INTEGER,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(schedule_id, scheduled_for),
    FOREIGN KEY(schedule_id) REFERENCES ui_job_schedules(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ui_job_schedule_occurrences_pending
    ON ui_job_schedule_occurrences(state, scheduled_for, id);
CREATE INDEX IF NOT EXISTS idx_ui_job_schedule_occurrences_schedule
    ON ui_job_schedule_occurrences(schedule_id, scheduled_for DESC, id DESC);
"""


CAMPAIGN_STATUSES = ("active", "paused", "completed", "archived")
CURRENT_CAMPAIGN_STATE_KEY = "current_campaign_id"
NOTEBOOK_ENTRY_TYPES = (
    "note",
    "hypothesis",
    "observation",
    "decision",
    "failed_approach",
    "next_experiment",
    "reference",
    "milestone",
)
NOTEBOOK_HANDOFF_STATES = ("include", "private", "resolved")
NOTEBOOK_LINK_KINDS = (
    "curve",
    "candidate_pool",
    "pipeline_definition",
    "pipeline_run",
    "job",
    "landscape_analysis",
)
NOTEBOOK_ENTRY_LABELS = {
    "note": "Note",
    "hypothesis": "Hypothesis",
    "observation": "Observation",
    "decision": "Decision",
    "failed_approach": "Failed approach",
    "next_experiment": "Next experiment",
    "reference": "Reference",
    "milestone": "Milestone",
}
NOTEBOOK_LINK_LABELS = {
    "curve": "Curve",
    "candidate_pool": "Candidate Pool",
    "pipeline_definition": "Pipeline",
    "pipeline_run": "Pipeline Run",
    "job": "Job",
    "landscape_analysis": "Landscape analysis",
}
LEGACY_AUTO_JOB_KINDS = frozenset({"auto_search", "torsion_auto_search"})
LEGACY_NATIVE_SEARCH_JOB_KINDS = frozenset({
    "family_search",
    "free_family_search",
    "general_hunt",
    "target_free",
    "target_plugin",
})
RETIRED_SEARCH_JOB_KINDS = frozenset(
    LEGACY_AUTO_JOB_KINDS | LEGACY_NATIVE_SEARCH_JOB_KINDS
)
LEGACY_CORE_SEARCH_MODULES = {
    "free_family_search": "rank42.auto_analyze",
    "general_hunt": "rank42.general_hunt",
    "target_free": "rank42.fixed_curve_search",
}


def legacy_search_campaign_id(metadata):
    """Resolve new separated search identity with pre-migration fallback."""
    metadata = dict(metadata or {})
    value = metadata.get("search_campaign_id")
    if value is None:
        value = metadata.get("campaign_id")
    return None if value is None else int(value)
SCHEDULE_RECURRENCES = ("once", "daily", "weekly", "interval")
SCHEDULE_CATCH_UP_POLICIES = ("latest", "all", "skip")


def ensure_manage_schema(db, *, commit=True):
    required_tables = {
        "research_campaigns",
        "research_campaign_notes",
        "research_campaign_note_links",
        "ui_job_schedules",
        "ui_job_schedule_occurrences",
    }
    tables = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                 'research_campaigns',
                 'research_campaign_notes',
                 'research_campaign_note_links',
                 'ui_job_schedules',
                 'ui_job_schedule_occurrences'
               )"""
        ).fetchall()
    }
    if tables == required_tables:
        indexes = {
            str(row["name"])
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='index' AND name IN (
                     'idx_research_campaigns_status',
                     'idx_research_campaign_notes_campaign',
                     'idx_research_campaign_note_links_note',
                     'idx_ui_job_schedules_due',
                     'idx_ui_job_schedule_occurrences_pending',
                     'idx_ui_job_schedule_occurrences_schedule'
                   )"""
            ).fetchall()
        }
        schedule_cols = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(ui_job_schedules)").fetchall()
        }
        occurrence_cols = {
            str(row["name"])
            for row in db.execute(
                "PRAGMA table_info(ui_job_schedule_occurrences)"
            ).fetchall()
        }
        campaign_cols = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(research_campaigns)").fetchall()
        }
        note_cols = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(research_campaign_notes)").fetchall()
        }
        if (
            {
                "idx_research_campaigns_status",
                "idx_research_campaign_notes_campaign",
                "idx_research_campaign_note_links_note",
                "idx_ui_job_schedules_due",
                "idx_ui_job_schedule_occurrences_pending",
                "idx_ui_job_schedule_occurrences_schedule",
            }.issubset(indexes)
            and {"last_error", "catch_up_policy"}.issubset(schedule_cols)
            and "snapshot_json" in occurrence_cols
            and {"variant_id", "completed_at", "completion_snapshot_json"}.issubset(campaign_cols)
            and {"entry_type", "handoff_state"}.issubset(note_cols)
        ):
            changed = bool(_ensure_campaign_ownership_links(db, commit=False))
            changed = bool(
                ensure_campaign_lineage_schema(db, commit=False)
            ) or changed
            changed = bool(
                sync_owned_campaign_lineage(db, commit=False)
            ) or changed
            if changed and commit:
                db.commit()
            return changed
    execute_static_schema_script(db, MANAGE_SCHEMA)
    schedule_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(ui_job_schedules)").fetchall()
    }
    if "last_error" not in schedule_cols:
        db.execute("ALTER TABLE ui_job_schedules ADD COLUMN last_error TEXT")
    if "catch_up_policy" not in schedule_cols:
        db.execute(
            "ALTER TABLE ui_job_schedules "
            "ADD COLUMN catch_up_policy TEXT NOT NULL DEFAULT 'latest'"
        )
    occurrence_cols = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(ui_job_schedule_occurrences)"
        ).fetchall()
    }
    if "snapshot_json" not in occurrence_cols:
        db.execute(
            "ALTER TABLE ui_job_schedule_occurrences "
            "ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{}'"
        )
    note_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(research_campaign_notes)").fetchall()
    }
    if "entry_type" not in note_cols:
        db.execute(
            "ALTER TABLE research_campaign_notes "
            "ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'note'"
        )
    if "handoff_state" not in note_cols:
        db.execute(
            "ALTER TABLE research_campaign_notes "
            "ADD COLUMN handoff_state TEXT NOT NULL DEFAULT 'include'"
        )
    campaign_cols = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(research_campaigns)").fetchall()
    }
    if "variant_id" not in campaign_cols:
        db.execute("ALTER TABLE research_campaigns ADD COLUMN variant_id TEXT")
    if "completed_at" not in campaign_cols:
        db.execute("ALTER TABLE research_campaigns ADD COLUMN completed_at TEXT")
    if "completion_snapshot_json" not in campaign_cols:
        db.execute("ALTER TABLE research_campaigns ADD COLUMN completion_snapshot_json TEXT")
    _ensure_campaign_ownership_links(db, commit=False)
    ensure_campaign_lineage_schema(db, commit=False)
    sync_owned_campaign_lineage(db, commit=False)
    if commit:
        db.commit()
    return True


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _loads(value, default):
    try:
        out = json.loads(value or "")
    except Exception:
        return default
    return out


def _row_has_field(row, key):
    try:
        return str(key) in row.keys()
    except (AttributeError, TypeError):
        try:
            row[key]
        except (KeyError, IndexError, TypeError):
            return False
        return True


def _metadata_campaign_id(metadata):
    value = dict(metadata or {}).get("campaign_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ensure_campaign_ownership_links(db, *, commit=True):
    """Add/backfill relational Campaign ownership without inventing stale links."""
    changed = bool(ensure_ui_schema(db, commit=False))

    if _table_exists(db, "search_pipeline_runs"):
        from rank42.pipeline_state import ensure_pipeline_schema
        changed = bool(ensure_pipeline_schema(db, commit=False)) or changed

    jobs_columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(ui_jobs)").fetchall()
    } if _table_exists(db, "ui_jobs") else set()
    if "campaign_id" in jobs_columns:
        rows = db.execute(
            """SELECT id,campaign_id,metadata_json,created_at
               FROM ui_jobs
               WHERE campaign_id IS NULL
               ORDER BY id"""
        ).fetchall()
        for row in rows:
            metadata = _loads(row["metadata_json"], {})
            campaign_id = _metadata_campaign_id(metadata)
            if campaign_id is None:
                continue
            campaign = db.execute(
                "SELECT * FROM research_campaigns WHERE id=?",
                (campaign_id,),
            ).fetchone()
            if campaign is None or not _campaign_link_is_current(
                campaign,
                row,
                metadata,
            ):
                continue
            db.execute(
                "UPDATE ui_jobs SET campaign_id=? WHERE id=?",
                (campaign_id, int(row["id"])),
            )
            changed = True

    run_columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(search_pipeline_runs)").fetchall()
    } if _table_exists(db, "search_pipeline_runs") else set()
    if "campaign_id" in run_columns:
        rows = db.execute(
            """SELECT id,campaign_id,run_config_json,created_at
               FROM search_pipeline_runs
               WHERE campaign_id IS NULL
               ORDER BY id"""
        ).fetchall()
        for row in rows:
            config = _loads(row["run_config_json"], {})
            campaign_id = _metadata_campaign_id(config)
            if campaign_id is None:
                continue
            campaign = db.execute(
                "SELECT * FROM research_campaigns WHERE id=?",
                (campaign_id,),
            ).fetchone()
            if campaign is None or not _campaign_link_is_current(
                campaign,
                row,
                config,
            ):
                continue
            db.execute(
                "UPDATE search_pipeline_runs SET campaign_id=? WHERE id=?",
                (campaign_id, int(row["id"])),
            )
            changed = True

    if changed and commit:
        db.commit()
    return changed


def create_campaign(
    db,
    *,
    name,
    objective="",
    target_rank=None,
    plugin_id=None,
    variant_id=None,
    family=None,
    notes="",
    status="active",
):
    status = str(status)
    if status not in CAMPAIGN_STATUSES:
        raise ValueError(f"unsupported campaign status {status!r}")
    ts = now()
    cur = db.execute(
        """INSERT INTO research_campaigns(
               name,objective,status,target_rank,plugin_id,variant_id,family,notes,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            str(name).strip(),
            str(objective or ""),
            status,
            None if target_rank is None else int(target_rank),
            str(plugin_id).strip() if plugin_id else None,
            str(variant_id).strip() if variant_id else None,
            str(family).strip() if family else None,
            str(notes or ""),
            ts,
            ts,
        ),
    )
    db.commit()
    return int(cur.lastrowid)



def attach_pipeline_run_to_campaign(db, run_id, campaign_id=None):
    """Set current Pipeline Run ownership while preserving launch-time snapshots."""
    if not _table_exists(db, "search_pipeline_runs"):
        raise ValueError(f"pipeline run #{int(run_id)} not found")
    row = db.execute(
        "SELECT * FROM search_pipeline_runs WHERE id=?",
        (int(run_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"pipeline run #{int(run_id)} not found")
    config = _loads(row["run_config_json"], {})
    if campaign_id is None:
        config.pop("campaign_id", None)
        owner_id = None
    else:
        campaign = get_campaign(db, int(campaign_id))
        if campaign is None:
            raise ValueError(f"campaign #{int(campaign_id)} not found")
        owner_id = int(campaign_id)
        config["campaign_id"] = owner_id
        config["campaign_name"] = str(campaign["name"])
        config["campaign_created_at"] = str(campaign["created_at"])
    db.execute(
        """UPDATE search_pipeline_runs
           SET campaign_id=?,run_config_json=?,updated_at=?
           WHERE id=?""",
        (
            owner_id,
            json.dumps(config, sort_keys=True),
            now(),
            int(run_id),
        ),
    )
    replace_owned_campaign_link(
        db,
        artifact_kind="pipeline_run",
        artifact_id=int(run_id),
        campaign_id=owner_id,
        commit=False,
    )
    db.commit()


def update_campaign(db, campaign_id, **fields):
    existing = db.execute(
        "SELECT * FROM research_campaigns WHERE id=?",
        (int(campaign_id),),
    ).fetchone()
    allowed = {
        "name", "objective", "status", "target_rank",
        "plugin_id", "variant_id", "family", "notes",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if "status" in values and str(values["status"]) not in CAMPAIGN_STATUSES:
        raise ValueError(f"unsupported campaign status {values['status']!r}")
    if "target_rank" in values:
        values["target_rank"] = (
            None if values["target_rank"] in (None, "") else int(values["target_rank"])
        )
    if not values:
        return

    terminal_status = str(values.get("status") or "")
    if terminal_status in {"completed", "archived"}:
        snapshot = campaign_snapshot(db, int(campaign_id))
        active_work = int(snapshot["running"]) + int(snapshot["queued"])
        if active_work:
            raise ValueError(
                f"campaign #{int(campaign_id)} still has {active_work} active or queued work item(s); "
                f"finish or stop them before marking the campaign {terminal_status}"
            )

    previous_status = "" if existing is None else str(existing["status"] or "")
    if terminal_status == "completed" and previous_status != "completed":
        brief = campaign_research_brief(db, int(campaign_id))
        completion_target = (
            values["target_rank"]
            if "target_rank" in values
            else brief["target"]
        )
        completion_conflict = bool(brief.get("target_blocked_by_conflict"))
        completion_reached = (
            None
            if completion_target is None
            else bool(
                brief["best_curve"] is not None
                and not completion_conflict
                and int(brief["best_lower"] or 0) >= int(completion_target)
            )
        )
        best_state = brief.get("best_state") or {}
        captured_at = now()
        values["completed_at"] = captured_at
        values["completion_snapshot_json"] = json.dumps(
            {
                "captured_at": captured_at,
                "target_rank": completion_target,
                "target_reached": completion_reached,
                "target_blocked_by_conflict": completion_conflict,
                "best_curve_id": (
                    None
                    if brief["best_curve"] is None
                    else int(brief["best_curve"]["id"])
                ),
                "best_lower": int(brief["best_lower"] or 0),
                "rigorous_upper": best_state.get("rigorous_upper"),
                "exact_rank": best_state.get("exact_rank"),
                "inconsistent": bool(best_state.get("inconsistent")),
            },
            sort_keys=True,
        )

    values["updated_at"] = now()
    db.execute(
        "UPDATE research_campaigns SET "
        + ", ".join(f"{key}=?" for key in values)
        + " WHERE id=?",
        [*values.values(), int(campaign_id)],
    )
    db.commit()
    if terminal_status in {"completed", "archived"}:
        db.execute(
            """UPDATE ui_job_schedules
               SET enabled=0,updated_at=?
               WHERE campaign_id=? AND enabled=1""",
            (now(), int(campaign_id)),
        )
        db.commit()



def delete_campaign(db, campaign_id):
    current = get_application_state(db, CURRENT_CAMPAIGN_STATE_KEY, None)
    if current is not None and int(current) == int(campaign_id):
        set_application_state(db, CURRENT_CAMPAIGN_STATE_KEY, None)
    for job in campaign_jobs(db, int(campaign_id), limit=100000):
        attach_job_to_campaign(db, int(job["id"]), None)
    for run in campaign_pipeline_runs(db, int(campaign_id)):
        attach_pipeline_run_to_campaign(db, int(run["id"]), None)
    db.execute(
        "UPDATE ui_job_schedules SET campaign_id=NULL,updated_at=? WHERE campaign_id=?",
        (now(), int(campaign_id)),
    )
    db.execute(
        "DELETE FROM research_campaign_artifacts WHERE campaign_id=?",
        (int(campaign_id),),
    )
    db.execute("DELETE FROM research_campaigns WHERE id=?", (int(campaign_id),))
    db.commit()


def get_campaign(db, campaign_id):
    return db.execute(
        "SELECT * FROM research_campaigns WHERE id=?",
        (int(campaign_id),),
    ).fetchone()


def list_campaigns(db, *, include_archived=True):
    sql = "SELECT * FROM research_campaigns"
    vals = []
    if not include_archived:
        sql += " WHERE status!='archived'"
    sql += " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'completed' THEN 2 ELSE 3 END, updated_at DESC,id DESC"
    return db.execute(sql, vals).fetchall()


def _normalize_notebook_links(links):
    normalized = []
    seen = set()
    for link in links or ():
        if not isinstance(link, dict):
            raise ValueError("notebook link must be an object with kind and id")
        kind = str(link.get("kind") or "").strip().lower()
        if kind not in NOTEBOOK_LINK_KINDS:
            raise ValueError(f"unsupported notebook link kind {kind!r}")
        try:
            object_id = int(link.get("id"))
        except (TypeError, ValueError):
            raise ValueError("notebook link id must be a positive integer") from None
        if object_id <= 0:
            raise ValueError("notebook link id must be a positive integer")
        key = (kind, object_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"kind": kind, "id": object_id})
    return normalized


def add_campaign_note(
    db,
    campaign_id,
    body,
    *,
    entry_type="note",
    handoff_state="include",
    links=None,
):
    campaign_id = int(campaign_id)
    if get_campaign(db, campaign_id) is None:
        raise ValueError(f"campaign #{campaign_id} not found")
    text = str(body or "").strip()
    if not text:
        raise ValueError("campaign note cannot be blank")
    entry_type = str(entry_type or "").strip().lower()
    if entry_type not in NOTEBOOK_ENTRY_TYPES:
        raise ValueError(f"unsupported notebook entry type {entry_type!r}")
    handoff_state = str(handoff_state or "").strip().lower()
    if handoff_state not in NOTEBOOK_HANDOFF_STATES:
        raise ValueError(f"unsupported notebook handoff state {handoff_state!r}")
    normalized_links = _normalize_notebook_links(links)
    ts = now()
    cur = db.execute(
        """INSERT INTO research_campaign_notes(
               campaign_id,body,entry_type,handoff_state,created_at
           ) VALUES(?,?,?,?,?)""",
        (campaign_id, text, entry_type, handoff_state, ts),
    )
    note_id = int(cur.lastrowid)
    for link in normalized_links:
        db.execute(
            """INSERT INTO research_campaign_note_links(
                   note_id,object_kind,object_id,created_at
               ) VALUES(?,?,?,?)""",
            (note_id, link["kind"], int(link["id"]), ts),
        )
    db.execute(
        "UPDATE research_campaigns SET updated_at=? WHERE id=?",
        (ts, campaign_id),
    )
    db.commit()
    return note_id


def campaign_notes(db, campaign_id, *, limit=500):
    return db.execute(
        """SELECT * FROM research_campaign_notes
           WHERE campaign_id=?
           ORDER BY created_at DESC,id DESC
           LIMIT ?""",
        (int(campaign_id), int(limit)),
    ).fetchall()


def campaign_notebook_entries(db, campaign_id, *, limit=500, handoff_state=None):
    params = [int(campaign_id)]
    sql = "SELECT * FROM research_campaign_notes WHERE campaign_id=?"
    if handoff_state is not None:
        state = str(handoff_state or "").strip().lower()
        if state not in NOTEBOOK_HANDOFF_STATES:
            raise ValueError(f"unsupported notebook handoff state {state!r}")
        sql += " AND handoff_state=?"
        params.append(state)
    sql += " ORDER BY created_at DESC,id DESC LIMIT ?"
    params.append(int(limit))
    rows = db.execute(sql, params).fetchall()
    if not rows:
        return []

    note_ids = [int(row["id"]) for row in rows]
    marks = ",".join("?" for _ in note_ids)
    link_rows = db.execute(
        f"""SELECT note_id,object_kind,object_id
            FROM research_campaign_note_links
            WHERE note_id IN ({marks})
            ORDER BY note_id,id""",
        note_ids,
    ).fetchall()
    links_by_note = {note_id: [] for note_id in note_ids}
    for link in link_rows:
        links_by_note[int(link["note_id"])].append(
            {"kind": str(link["object_kind"]), "id": int(link["object_id"])}
        )

    entries = []
    for row in rows:
        entry = dict(row)
        entry["links"] = links_by_note.get(int(row["id"]), [])
        entries.append(entry)
    return entries


def set_current_campaign(db, campaign_id=None):
    """Select the one Campaign UI/research context without changing lifecycle."""
    if campaign_id is None:
        set_application_state(db, CURRENT_CAMPAIGN_STATE_KEY, None)
        return
    row = get_campaign(db, int(campaign_id))
    if row is None:
        raise ValueError(f"campaign #{int(campaign_id)} not found")
    set_application_state(db, CURRENT_CAMPAIGN_STATE_KEY, int(campaign_id))


def current_campaign(db):
    """Return the one selected Campaign context regardless of lifecycle status."""
    campaign_id = get_application_state(db, CURRENT_CAMPAIGN_STATE_KEY, None)
    if campaign_id is None:
        return None
    row = get_campaign(db, int(campaign_id))
    if row is None:
        set_application_state(db, CURRENT_CAMPAIGN_STATE_KEY, None)
        return None
    return row


def current_campaign_readonly(db):
    """Return the selected Campaign context without cleanup writes."""
    tables = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                 'ui_application_state','research_campaigns'
               )"""
        ).fetchall()
    }
    if {"ui_application_state", "research_campaigns"} - tables:
        return None
    row = db.execute(
        """SELECT value_json FROM ui_application_state
           WHERE state_key=?""",
        (CURRENT_CAMPAIGN_STATE_KEY,),
    ).fetchone()
    if row is None:
        return None
    try:
        campaign_id = int(json.loads(row["value_json"]))
    except Exception:
        return None
    return db.execute(
        "SELECT * FROM research_campaigns WHERE id=?",
        (campaign_id,),
    ).fetchone()


def set_active_campaign(db, campaign_id=None):
    """Legacy helper: select a Campaign and ensure lifecycle status is active."""
    if campaign_id is None:
        return set_current_campaign(db, None)
    row = get_campaign(db, int(campaign_id))
    if row is None:
        raise ValueError(f"campaign #{int(campaign_id)} not found")
    if str(row["status"]) != "active":
        update_campaign(db, int(campaign_id), status="active")
    return set_current_campaign(db, int(campaign_id))


def active_campaign(db):
    """Return the selected Campaign only when lifecycle status is active."""
    row = current_campaign(db)
    if row is None or str(row["status"]) != "active":
        return None
    return row


_RUNTIME_JOB_METADATA_KEYS = {
    "cursor_finalized",
    "cursor_completed",
    "cursor_next_offset",
    "cursor_advanced",
    "pool_cursor_finalized",
    "pool_candidates_completed",
    "pool_candidates_planned",
    "pool_next_offset",
    "retry_of_job_id",
    "resume_of_job_id",
    "resumed_as_job_id",
    "pause_requested",
    "paused_at",
    "schedule_id",
}


def _fresh_job_metadata(raw):
    meta = dict(raw or {})
    for key in _RUNTIME_JOB_METADATA_KEYS:
        meta.pop(key, None)
    return meta


def job_campaign_id(row):
    if _row_has_field(row, "campaign_id"):
        value = row["campaign_id"]
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return _metadata_campaign_id(_loads(row["metadata_json"], {}))


def _campaign_link_is_current(campaign, row, metadata):
    """Reject stale numeric-ID collisions while preserving intentional old attachments."""
    marker = metadata.get("campaign_created_at")
    if marker:
        return str(marker) == str(campaign["created_at"])

    try:
        linked_at = datetime.fromisoformat(
            str(row["created_at"]).replace("Z", "+00:00")
        )
        campaign_created = datetime.fromisoformat(
            str(campaign["created_at"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    return linked_at >= campaign_created


def attach_job_to_campaign(db, job_id, campaign_id=None):
    row = get_job(db, int(job_id))
    if row is None:
        raise ValueError(f"job #{int(job_id)} not found")
    meta = _loads(row["metadata_json"], {})
    if campaign_id is None:
        meta.pop("campaign_id", None)
        owner_id = None
    else:
        campaign = get_campaign(db, int(campaign_id))
        if campaign is None:
            raise ValueError(f"campaign #{int(campaign_id)} not found")
        owner_id = int(campaign_id)
        meta["campaign_id"] = owner_id
        meta["campaign_name"] = str(campaign["name"])
        meta["campaign_created_at"] = str(campaign["created_at"])
    update_job(
        db,
        int(job_id),
        campaign_id=owner_id,
        metadata_json=json.dumps(meta, sort_keys=True),
    )


def campaign_jobs(db, campaign_id, *, limit=1000):
    campaign = get_campaign(db, int(campaign_id))
    if campaign is None:
        return []
    return db.execute(
        """SELECT * FROM ui_jobs
           WHERE campaign_id=?
           ORDER BY id DESC
           LIMIT ?""",
        (int(campaign_id), int(limit)),
    ).fetchall()



def pipeline_run_campaign_id(row):
    if _row_has_field(row, "campaign_id"):
        value = row["campaign_id"]
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return _metadata_campaign_id(_loads(row["run_config_json"], {}))


def campaign_pipeline_runs(db, campaign_id):
    if not _table_exists(db, "search_pipeline_runs"):
        return []
    campaign = get_campaign(db, int(campaign_id))
    if campaign is None:
        return []
    return db.execute(
        """SELECT * FROM search_pipeline_runs
           WHERE campaign_id=?
           ORDER BY id DESC""",
        (int(campaign_id),),
    ).fetchall()


def campaign_snapshot(db, campaign_id):
    campaign = get_campaign(db, int(campaign_id))
    if campaign is None:
        raise ValueError(f"campaign #{int(campaign_id)} not found")
    jobs = campaign_jobs(db, int(campaign_id))
    summaries = []
    best_lower = 0
    candidates_done = 0
    running = queued = failed = completed = 0
    represented_pipeline_run_ids = set()
    pipeline_run_ids = {
        int(row["id"]) for row in campaign_pipeline_runs(db, int(campaign_id))
    }
    for row in jobs:
        summary = summarize_job(row, read_log(row["log_path"]))
        summaries.append((row, summary))
        best_lower = max(
            best_lower,
            int(summary.get("best_rigorous_lower") or 0),
        )
        if str(row["kind"]) != "pipeline_search":
            candidates_done += int(
                summary.get("candidates_completed")
                or summary.get("trials_completed")
                or summary.get("shortlist_searched")
                or 0
            )
        status = str(row["status"])
        running += status in {"running", "stopping"}
        queued += status == "queued"
        completed += status == "succeeded"
        failed += status in {"failed", "interrupted", "killed", "stopped"}
        meta = _loads(row["metadata_json"], {})
        if meta.get("pipeline_run_id") is not None:
            try:
                run_id = int(meta["pipeline_run_id"])
                pipeline_run_ids.add(run_id)
                represented_pipeline_run_ids.add(run_id)
            except (TypeError, ValueError):
                pass

    pipeline_runs = []
    if pipeline_run_ids and _table_exists(db, "search_pipeline_runs"):
        ordered_run_ids = sorted(pipeline_run_ids)
        marks = ",".join("?" for _ in ordered_run_ids)
        pipeline_runs = db.execute(
            f"""SELECT * FROM search_pipeline_runs
                WHERE id IN ({marks}) ORDER BY id DESC""",
            ordered_run_ids,
        ).fetchall()
        for run in pipeline_runs:
            best_lower = max(best_lower, int(run["best_lower"] or 0))
            candidates_done += int(run["candidates_done"] or 0)
            if int(run["id"]) in represented_pipeline_run_ids:
                continue
            status = str(run["status"] or "")
            running += status in {"running", "stopping"}
            queued += status == "queued"
            completed += status in {"completed", "target_hit"}
            failed += status in {"failed", "interrupted", "killed"}

    orphan_pipeline_runs = [
        run for run in pipeline_runs
        if int(run["id"]) not in represented_pipeline_run_ids
    ]

    schedules = db.execute(
        """SELECT * FROM ui_job_schedules
           WHERE campaign_id=? ORDER BY enabled DESC,next_run_at,id""",
        (int(campaign_id),),
    ).fetchall()

    return {
        "campaign": campaign,
        "jobs": jobs,
        "summaries": summaries,
        "pipeline_runs": pipeline_runs,
        "orphan_pipeline_runs": orphan_pipeline_runs,
        "schedules": schedules,
        "best_lower": best_lower,
        "candidates_done": candidates_done,
        "running": running,
        "queued": queued,
        "completed": completed,
        "failed": failed,
        "last_activity": max(
            [str(row["updated_at"] or row["created_at"]) for row in jobs]
            + [str(row["updated_at"] or row["created_at"]) for row in pipeline_runs]
            + [str(campaign["updated_at"])],
        ),
    }



def campaign_artifacts(db, campaign_id, *, artifact_kind=None, relation=None):
    if get_campaign(db, int(campaign_id)) is None:
        return []
    return list_campaign_artifacts(
        db,
        int(campaign_id),
        artifact_kind=artifact_kind,
        relation=relation,
    )


def campaign_pinned_curve_ids(db, campaign_id):
    return [
        int(row["artifact_id"])
        for row in campaign_artifacts(
            db,
            int(campaign_id),
            artifact_kind="curve",
            relation="pinned",
        )
    ]


def pin_campaign_curve(db, campaign_id, curve_id):
    if get_campaign(db, int(campaign_id)) is None:
        raise ValueError(f"campaign #{int(campaign_id)} not found")
    curve = db.execute(
        "SELECT id FROM curves WHERE id=?",
        (int(curve_id),),
    ).fetchone()
    if curve is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    pin_curve(
        db,
        campaign_id=int(campaign_id),
        curve_id=int(curve_id),
    )


def unpin_campaign_curve(db, campaign_id, curve_id):
    unpin_curve(
        db,
        campaign_id=int(campaign_id),
        curve_id=int(curve_id),
    )


def campaign_curve_ids_readonly(db, campaign_id):
    """Return Campaign-traceable retained curves using existing schema only.

    This is the read-only ownership projection for consumers such as Analysis.
    It never creates/reconciles lineage tables or repairs application state.
    """
    campaign = get_campaign(db, int(campaign_id))
    if campaign is None:
        return []

    jobs = (
        db.execute(
            """SELECT * FROM ui_jobs
               WHERE campaign_id=? ORDER BY id DESC LIMIT 100000""",
            (int(campaign_id),),
        ).fetchall()
        if _table_exists(db, "ui_jobs")
        and "campaign_id" in {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(ui_jobs)").fetchall()
        }
        else []
    )
    curve_ids = set()
    if _table_exists(db, "research_campaign_artifacts"):
        rows = db.execute(
            """SELECT artifact_id FROM research_campaign_artifacts
               WHERE campaign_id=? AND artifact_kind='curve'
                 AND relation='pinned'""",
            (int(campaign_id),),
        ).fetchall()
        curve_ids.update(int(row["artifact_id"]) for row in rows)

    candidate_ids = set()
    pool_ids = set()
    pipeline_run_ids = set()
    if _table_exists(db, "search_pipeline_runs") and "campaign_id" in {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(search_pipeline_runs)").fetchall()
    }:
        rows = db.execute(
            """SELECT id,best_curve_id FROM search_pipeline_runs
               WHERE campaign_id=?""",
            (int(campaign_id),),
        ).fetchall()
        for row in rows:
            pipeline_run_ids.add(int(row["id"]))
            if row["best_curve_id"] is not None:
                curve_ids.add(int(row["best_curve_id"]))

    for row in jobs:
        meta = _loads(row["metadata_json"], {})
        for key in ("curve_id", "best_curve_id"):
            value = meta.get(key)
            if value is not None:
                try:
                    curve_ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        for value in meta.get("curve_ids") or []:
            try:
                curve_ids.add(int(value))
            except (TypeError, ValueError):
                pass
        for value in meta.get("candidate_ids") or []:
            try:
                candidate_ids.add(int(value))
            except (TypeError, ValueError):
                pass
        if meta.get("pool_id") is not None:
            try:
                pool_ids.add(int(meta["pool_id"]))
            except (TypeError, ValueError):
                pass
        if meta.get("pipeline_run_id") is not None:
            try:
                pipeline_run_ids.add(int(meta["pipeline_run_id"]))
            except (TypeError, ValueError):
                pass

    if _table_exists(db, "candidates"):
        ordered_candidate_ids = sorted(candidate_ids)
        for offset in range(0, len(ordered_candidate_ids), 800):
            chunk = ordered_candidate_ids[offset : offset + 800]
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            rows = db.execute(
                f"SELECT curve_id FROM candidates WHERE id IN ({marks}) AND curve_id IS NOT NULL",
                chunk,
            ).fetchall()
            curve_ids.update(int(row["curve_id"]) for row in rows)

        ordered_pool_ids = sorted(pool_ids)
        for offset in range(0, len(ordered_pool_ids), 800):
            chunk = ordered_pool_ids[offset : offset + 800]
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            rows = db.execute(
                f"SELECT DISTINCT curve_id FROM candidates WHERE pool_id IN ({marks}) AND curve_id IS NOT NULL",
                chunk,
            ).fetchall()
            curve_ids.update(int(row["curve_id"]) for row in rows)

    if pipeline_run_ids and _table_exists(db, "search_pipeline_candidates"):
        ordered_run_ids = sorted(pipeline_run_ids)
        for offset in range(0, len(ordered_run_ids), 800):
            chunk = ordered_run_ids[offset : offset + 800]
            marks = ",".join("?" for _ in chunk)
            rows = db.execute(
                f"""SELECT DISTINCT curve_id FROM search_pipeline_candidates
                    WHERE run_id IN ({marks}) AND curve_id IS NOT NULL""",
                chunk,
            ).fetchall()
            curve_ids.update(int(row["curve_id"]) for row in rows)

    if not curve_ids:
        return []
    existing_ids = []
    ordered_curve_ids = sorted(curve_ids)
    for offset in range(0, len(ordered_curve_ids), 800):
        chunk = ordered_curve_ids[offset : offset + 800]
        marks = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT id FROM curves WHERE id IN ({marks}) ORDER BY id",
            chunk,
        ).fetchall()
        existing_ids.extend(int(row["id"]) for row in rows)
    return sorted(set(existing_ids))


def campaign_curve_ids(db, campaign_id):
    """Return retained curves traceable to work or explicitly pinned here."""
    return campaign_curve_ids_readonly(db, int(campaign_id))


def _campaign_curve_rows(db, campaign_id, *, limit=12):
    """Shortlist campaign curves using the same current rigorous-bound semantics as the reducer."""
    curve_ids = campaign_curve_ids(db, int(campaign_id))
    if not curve_ids:
        return []
    marks = ",".join("?" for _ in curve_ids)
    return db.execute(
        f"""
        WITH evidence AS (
            SELECT curve_id,
                   MAX(
                       CASE
                           WHEN COALESCE(rigorous,0)=1
                                AND rigorous_lower IS NOT NULL
                           THEN rigorous_lower
                       END
                   ) AS evidence_lower,
                   MIN(
                       CASE
                           WHEN COALESCE(rigorous,0)=1
                                AND rigorous_upper IS NOT NULL
                                AND status='completed'
                           THEN rigorous_upper
                       END
                   ) AS evidence_upper
            FROM rank_evidence
            WHERE curve_id IN ({marks})
            GROUP BY curve_id
        ),
        ranked AS (
            SELECT c.*,
                   MAX(
                       COALESCE(c.exact_rank,0),
                       COALESCE(c.descent_lower,0),
                       COALESCE(c.generic_lower,0),
                       COALESCE(e.evidence_lower,0)
                   ) AS rigorous_lower,
                   CASE
                       WHEN COALESCE(c.exact_rank,c.descent_upper) IS NULL
                           THEN e.evidence_upper
                       WHEN e.evidence_upper IS NULL
                           THEN COALESCE(c.exact_rank,c.descent_upper)
                       ELSE MIN(
                           COALESCE(c.exact_rank,c.descent_upper),
                           e.evidence_upper
                       )
                   END AS rigorous_upper,
                   (SELECT COUNT(*) FROM points p
                    WHERE p.curve_id=c.id AND COALESCE(p.exact_verified,0)=1) AS exact_points,
                   (SELECT COUNT(*) FROM points p
                    WHERE p.curve_id=c.id
                      AND COALESCE(p.exact_verified,0)=1
                      AND COALESCE(p.rigorous_independent,0)=0
                      AND COALESCE(p.independence_status,'unknown')
                          IN ('unknown','numerical_novel','inconclusive')) AS actionable_points
            FROM curves c
            LEFT JOIN evidence e ON e.curve_id=c.id
            WHERE c.id IN ({marks})
        )
        SELECT *
        FROM ranked
        ORDER BY
            CASE
                WHEN rigorous_upper IS NOT NULL
                     AND rigorous_upper < rigorous_lower
                THEN 1 ELSE 0
            END,
            rigorous_lower DESC,
            actionable_points DESC,
            exact_points DESC,
            COALESCE(score,-1e99) DESC,
            id DESC
        LIMIT ?
        """,
        [
            *sorted(curve_ids),
            *sorted(curve_ids),
            int(limit),
        ],
    ).fetchall()


def campaign_progress_summary(db, campaign_id):
    """Return a lightweight, proof-safe rank-progress summary for campaign cards."""
    campaign = get_campaign(db, int(campaign_id))
    if campaign is None:
        raise ValueError(f"campaign #{int(campaign_id)} not found")

    target = None if campaign["target_rank"] is None else int(campaign["target_rank"])
    rows = _campaign_curve_rows(db, int(campaign_id), limit=1)
    best = rows[0] if rows else None
    best_lower = 0
    blocked = False
    best_curve_id = None

    if best is not None:
        best_curve_id = int(best["id"])
        best_lower = int(best["rigorous_lower"] or 0)
        upper = best["rigorous_upper"]
        blocked = bool(
            upper is not None and int(upper) < int(best_lower)
        )

    if target is None:
        fraction = None
        label = (
            f"Rigorous frontier ≥{best_lower} · no rank target"
            if best_lower > 0 and not blocked
            else (
                "Evidence conflict · no rank target"
                if blocked
                else "No rank target"
            )
        )
    elif blocked:
        fraction = 0.0
        label = f"Evidence conflict · target ≥{target}"
    else:
        fraction = min(1.0, float(best_lower) / float(max(1, target)))
        label = f"Rigorous ≥{best_lower} / target ≥{target}"

    return {
        "campaign_id": int(campaign_id),
        "target_rank": target,
        "best_curve_id": best_curve_id,
        "best_lower": best_lower,
        "blocked": blocked,
        "fraction": fraction,
        "label": label,
    }



def _campaign_execution_records(snapshot):
    """Normalize campaign jobs into strategy/run records for comparison."""
    run_by_id = {int(row["id"]): row for row in snapshot["pipeline_runs"]}
    summary_by_job = {
        int(row["id"]): summary for row, summary in snapshot["summaries"]
    }
    records = []
    for job in snapshot["jobs"]:
        job_id = int(job["id"])
        meta = _loads(job["metadata_json"], {})
        summary = summary_by_job.get(job_id, {})
        run = None
        run_id = meta.get("pipeline_run_id")
        if run_id is not None:
            try:
                run = run_by_id.get(int(run_id))
            except (TypeError, ValueError):
                run = None

        if run is not None:
            strategy = str(run["pipeline_name"] or "Pipeline")
            best_lower = int(run["best_lower"] or 0)
            candidates = int(run["candidates_done"] or 0)
            activity_at = (
                run["finished_at"]
                or run["updated_at"]
                or job["finished_at"]
                or job["updated_at"]
                or job["created_at"]
            )
        else:
            strategy = str(meta.get("pipeline_name") or job["label"] or job["kind"])
            best_lower = int(summary.get("best_rigorous_lower") or 0)
            candidates = int(
                summary.get("candidates_completed")
                or summary.get("trials_completed")
                or summary.get("shortlist_searched")
                or 0
            )
            activity_at = job["finished_at"] or job["updated_at"] or job["created_at"]

        status = str(job["status"] or "")
        records.append(
            {
                "job_id": job_id,
                "run_id": None if run is None else int(run["id"]),
                "strategy": strategy,
                "kind": str(job["kind"]),
                "status": status,
                "best_lower": best_lower,
                "candidates": candidates,
                "interesting": bool(summary.get("interesting")),
                "activity_at": str(activity_at or ""),
            }
        )
    represented_run_ids = {
        int(rec["run_id"]) for rec in records if rec["run_id"] is not None
    }
    for run in snapshot["pipeline_runs"]:
        run_id = int(run["id"])
        if run_id in represented_run_ids:
            continue
        records.append(
            {
                "job_id": 0,
                "run_id": run_id,
                "strategy": str(run["pipeline_name"] or "Pipeline"),
                "kind": "pipeline_search",
                "status": str(run["status"] or ""),
                "best_lower": int(run["best_lower"] or 0),
                "candidates": int(run["candidates_done"] or 0),
                "interesting": bool(int(run["best_lower"] or 0) > 0),
                "activity_at": str(
                    run["finished_at"] or run["updated_at"] or run["created_at"] or ""
                ),
            }
        )

    records.sort(key=lambda rec: (rec["activity_at"], rec["run_id"] or 0, rec["job_id"]))
    return records


def _campaign_strategy_performance(records):
    grouped = {}
    frontier = 0
    milestones = []
    for rec in records:
        key = str(rec["strategy"])
        stat = grouped.setdefault(
            key,
            {
                "strategy": key,
                "runs": 0,
                "succeeded": 0,
                "failed": 0,
                "active": 0,
                "candidates": 0,
                "best_lower": 0,
                "frontier_gains": 0,
                "interesting_runs": 0,
                "last_activity": "",
            },
        )
        stat["runs"] += 1
        stat["candidates"] += int(rec["candidates"])
        stat["best_lower"] = max(int(stat["best_lower"]), int(rec["best_lower"]))
        stat["interesting_runs"] += int(bool(rec["interesting"]))
        status = str(rec["status"])
        if status in {"succeeded", "completed", "goal_reached"}:
            stat["succeeded"] += 1
        elif status in {"queued", "running", "stopping"}:
            stat["active"] += 1
        elif status in {"failed", "interrupted", "stopped", "killed"}:
            stat["failed"] += 1
        if rec["activity_at"] > stat["last_activity"]:
            stat["last_activity"] = rec["activity_at"]

        if int(rec["best_lower"]) > frontier:
            previous = frontier
            frontier = int(rec["best_lower"])
            stat["frontier_gains"] += 1
            milestones.append(
                {
                    "at": rec["activity_at"],
                    "strategy": key,
                    "job_id": (
                        int(rec["job_id"]) if int(rec["job_id"] or 0) > 0 else None
                    ),
                    "run_id": rec["run_id"],
                    "previous_lower": previous,
                    "new_lower": frontier,
                }
            )

    strategies = sorted(
        grouped.values(),
        key=lambda rec: (
            -int(rec["frontier_gains"]),
            -int(rec["best_lower"]),
            -int(rec["interesting_runs"]),
            -int(rec["candidates"]),
            str(rec["strategy"]).lower(),
        ),
    )
    recent = sorted(
        records,
        key=lambda rec: (rec["activity_at"], rec["job_id"]),
        reverse=True,
    )[:20]
    return strategies, milestones, recent


def _campaign_completion_drift(
    completion_snapshot,
    *,
    target,
    best,
    best_state,
    best_lower,
    target_reached,
    target_blocked_by_conflict,
):
    if not isinstance(completion_snapshot, dict):
        return None

    current = {
        "target_rank": target,
        "target_reached": None if target is None else bool(target_reached),
        "target_blocked_by_conflict": bool(target_blocked_by_conflict),
        "best_curve_id": None if best is None else int(best["id"]),
        "best_lower": int(best_lower or 0),
        "rigorous_upper": None if best_state is None else best_state.get("rigorous_upper"),
        "exact_rank": None if best_state is None else best_state.get("exact_rank"),
        "inconsistent": bool(best_state and best_state.get("inconsistent")),
    }
    fields = [
        "target_rank",
        "target_reached",
        "target_blocked_by_conflict",
        "best_curve_id",
        "best_lower",
        "rigorous_upper",
        "exact_rank",
        "inconsistent",
    ]
    changes = {
        key: {
            "completed": completion_snapshot.get(key),
            "current": current.get(key),
        }
        for key in fields
        if completion_snapshot.get(key) != current.get(key)
    }
    target_changed = "target_rank" in changes
    completion_was_met = completion_snapshot.get("target_reached") is True
    current_is_met = current.get("target_reached") is True
    requires_review = bool(
        target_changed
        or current["inconsistent"]
        or current["target_blocked_by_conflict"]
        or (completion_was_met and not current_is_met)
    )
    return {
        "changed": bool(changes),
        "requires_review": requires_review,
        "changes": changes,
        "current": current,
    }


def campaign_research_brief(db, campaign_id):
    """Build a read-only, proof-safe research brief from campaign-linked state."""
    snapshot = campaign_snapshot(db, int(campaign_id))
    campaign = snapshot["campaign"]
    execution_records = _campaign_execution_records(snapshot)
    strategy_performance, frontier_milestones, recent_activity = (
        _campaign_strategy_performance(execution_records)
    )
    curves = _campaign_curve_rows(db, int(campaign_id), limit=12)
    target = None if campaign["target_rank"] is None else int(campaign["target_rank"])

    detailed = []
    from rank42.analysis_planner import analysis_plan, planner_immediate_actions
    from rank42.analyze_workspace import curve_analysis_snapshot

    for row in curves:
        analysis = curve_analysis_snapshot(db, int(row["id"]))
        detailed.append(
            {
                "curve": row,
                "state": analysis["state"],
                "analysis": analysis,
                "actionable_points": len(analysis["actionable_points"]),
                "witness_gap": int(analysis["witness_gap"] or 0),
                "lattices": len(analysis["lattices"]),
            }
        )

    # Contradictory evidence is never allowed to outrank a consistent curve
    # in the campaign frontier. Keep conflicts visible, but quarantine them
    # from leader/target selection until the evidence is reconciled.
    detailed.sort(
        key=lambda rec: (
            int(bool(rec["state"].get("inconsistent"))),
            -int(rec["state"].get("rigorous_lower") or 0),
            -int(rec["actionable_points"]),
            -int(rec["witness_gap"]),
            -int(rec["curve"]["id"]),
        )
    )
    curves = [rec["curve"] for rec in detailed]
    best_detail = detailed[0] if detailed else None
    best = None if best_detail is None else best_detail["curve"]
    best_state = None if best_detail is None else best_detail["state"]
    best_analysis = None if best_detail is None else best_detail["analysis"]
    best_lower = 0 if best_state is None else int(best_state.get("rigorous_lower") or 0)
    target_blocked_by_conflict = bool(
        best_state is not None and best_state.get("inconsistent")
    )
    target_gap = (
        None
        if target is None or target_blocked_by_conflict
        else max(0, target - best_lower)
    )
    target_reached = bool(
        target is not None
        and best is not None
        and not target_blocked_by_conflict
        and best_lower >= target
    )

    conflicts = [
        rec for rec in detailed
        if rec["state"].get("inconsistent")
    ]
    promising = [
        rec for rec in detailed[:6]
        if rec["state"].get("exact_rank") is None
        or rec["state"].get("inconsistent")
        or rec["actionable_points"] > 0
        or rec["witness_gap"] > 0
    ]

    next_action = None
    if best is not None:
        plan = analysis_plan(best_analysis, target_rank=target)
        planner_actions = planner_immediate_actions(plan)
        if planner_actions:
            action = planner_actions[0]
            next_action = {
                "title": action["title"],
                "why": action["why"],
                "page": action.get("page") or "Curves",
                "planner_kind": action["kind"],
                "planner_version": int(plan["planner_version"]),
            }
            if next_action["page"] != "Pipelines":
                next_action["curve_id"] = int(best["id"])
        else:
            next_action = {
                "title": "Inspect the best campaign curve",
                "why": (
                    f"Curve #{int(best['id'])} currently leads this campaign at "
                    f"rigorous rank ≥{best_lower}."
                ),
                "page": "Curves",
                "curve_id": int(best["id"]),
            }
    else:
        orphan_active = [
            run for run in snapshot["orphan_pipeline_runs"]
            if str(run["status"] or "") in {"queued", "running", "stopping"}
        ]
        orphan_failed = [
            run for run in snapshot["orphan_pipeline_runs"]
            if str(run["status"] or "") in {"failed", "interrupted", "killed"}
        ]
        if orphan_active:
            run = orphan_active[0]
            next_action = {
                "title": "Inspect active pipeline run",
                "why": (
                    f"Pipeline run #{int(run['id'])} is {run['status']} and remains durably "
                    "attached to this campaign even though its UI job row is unavailable."
                ),
                "page": "Pipelines",
                "pipeline_run_id": int(run["id"]),
            }
        elif snapshot["running"] or snapshot["queued"]:
            next_action = {
                "title": "Inspect active computation",
                "why": "The campaign has active jobs but no retained curve with positive evidence yet.",
                "page": "Jobs",
                "jobs_section": "Active",
            }
        elif orphan_failed:
            run = orphan_failed[0]
            next_action = {
                "title": "Review interrupted pipeline run",
                "why": (
                    f"Pipeline run #{int(run['id'])} is {run['status']} and remains durably "
                    "attached to this campaign even though its UI job row is unavailable."
                ),
                "page": "Pipelines",
                "pipeline_run_id": int(run["id"]),
            }
        elif snapshot["failed"]:
            next_action = {
                "title": "Review failed or interrupted work",
                "why": "The campaign has no retained leading curve and at least one failed, stopped, or interrupted job.",
                "page": "Jobs",
                "jobs_section": "History",
            }
        else:
            next_action = {
                "title": "Choose or build the first pipeline",
                "why": "No campaign-linked curve has been retained yet. Start with a reusable strategy under Manage → Pipelines.",
                "page": "Pipelines",
            }
    completion_snapshot = _loads(campaign["completion_snapshot_json"], None)
    completion_drift = _campaign_completion_drift(
        completion_snapshot,
        target=target,
        best=best,
        best_state=best_state,
        best_lower=best_lower,
        target_reached=target_reached,
        target_blocked_by_conflict=target_blocked_by_conflict,
    )

    return {
        "snapshot": snapshot,
        "curves": curves,
        "detailed_curves": detailed,
        "promising_curves": promising,
        "conflicted_curves": conflicts,
        "best_curve": best,
        "best_state": best_state,
        "best_lower": best_lower,
        "target": target,
        "target_gap": target_gap,
        "target_reached": target_reached,
        "target_blocked_by_conflict": target_blocked_by_conflict,
        "completion_snapshot": completion_snapshot,
        "completion_drift": completion_drift,
        "next_action": next_action,
        "strategy_performance": strategy_performance,
        "frontier_milestones": frontier_milestones,
        "recent_activity": recent_activity,
    }


def _campaign_handoff_rank_text(state):
    if not state:
        return "—"
    lower = int(state.get("rigorous_lower") or 0)
    upper = state.get("rigorous_upper")
    if state.get("inconsistent"):
        return (
            f"CONFLICT: ≥{lower}"
            + (f" / ≤{int(upper)}" if upper is not None else "")
        )
    exact = state.get("exact_rank")
    if exact is not None:
        return f"= {int(exact)}"
    if upper is not None:
        return f"≥{lower}, ≤{int(upper)}"
    return f"≥{lower}"


def _campaign_handoff_cell(value):
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def campaign_handoff_markdown(db, campaign_id, *, brief=None):
    """Render a proof-safe Markdown handoff from current durable campaign state."""
    brief = brief or campaign_research_brief(db, int(campaign_id))
    snapshot = brief["snapshot"]
    campaign = snapshot["campaign"]
    best = brief["best_curve"]
    best_state = brief.get("best_state") or {}
    target = brief["target"]
    lines = [
        f"# Campaign Handoff — #{int(campaign['id'])} {campaign['name']}",
        "",
        "> Proof-safe summary rendered from current durable Rank Hunter state. "
        "Current rigorous evidence is kept separate from historical execution metrics.",
        "",
        "## Objective",
        "",
        f"- **Lifecycle status:** {str(campaign['status']).title()}",
        f"- **Target:** {('rank ≥' + str(int(target))) if target is not None else 'No numeric rank target'}",
        f"- **Plugin:** {_campaign_handoff_cell(campaign['plugin_id'])}",
        f"- **Variant:** {_campaign_handoff_cell(campaign['variant_id'])}",
        f"- **Family:** {_campaign_handoff_cell(campaign['family'])}",
        f"- **Last activity:** {_campaign_handoff_cell(snapshot['last_activity'])}",
        "",
        str(campaign["objective"] or "No research objective recorded."),
        "",
        "## Current scientific state",
        "",
    ]

    if best is None:
        lines.extend([
            "- **Leading curve:** None retained yet",
            "- **Current rigorous rank:** —",
        ])
    else:
        lines.extend([
            (
                f"- **Leading curve:** #{int(best['id'])} — "
                f"{_campaign_handoff_cell(best['family'])} at "
                f"t={_campaign_handoff_cell(best['parameter'])}"
            ),
            f"- **Current rigorous rank:** {_campaign_handoff_rank_text(best_state)}",
            f"- **Exact stored points:** {int(best['exact_points'] or 0)}",
            f"- **Unresolved exact points:** {int(best['actionable_points'] or 0)}",
        ])
        best_detail = next(
            (
                rec for rec in brief["detailed_curves"]
                if int(rec["curve"]["id"]) == int(best["id"])
            ),
            None,
        )
        if best_detail is not None:
            lines.append(f"- **Witness gap:** {int(best_detail['witness_gap'] or 0)}")

    if target is None:
        target_outcome = "No numeric rank target; completion is an operator lifecycle decision."
    elif brief.get("target_blocked_by_conflict"):
        target_outcome = "Unresolved because the leading current rank evidence is inconsistent."
    elif brief["target_reached"]:
        target_outcome = f"Met: current consistent rigorous evidence reaches rank ≥{int(target)}."
    elif best is None:
        target_outcome = f"Not yet supported: no retained campaign curve reaches rank ≥{int(target)}."
    else:
        target_outcome = (
            f"Not met: current rigorous frontier is ≥{int(brief['best_lower'] or 0)}; "
            f"gap {int(brief['target_gap'] or 0)}."
        )
    lines.extend([
        f"- **Target outcome:** {target_outcome}",
        f"- **Conflicted campaign curves:** {len(brief.get('conflicted_curves') or [])}",
        "",
        "## Next useful action",
        "",
    ])

    action = brief.get("next_action")
    if action:
        destination = str(action.get("page") or "—")
        if action.get("curve_id") is not None:
            destination += f" · curve #{int(action['curve_id'])}"
        if action.get("pipeline_run_id") is not None:
            destination += f" · pipeline run #{int(action['pipeline_run_id'])}"
        lines.extend([
            f"**{action['title']}**",
            "",
            str(action["why"]),
            "",
            f"- **Open:** {destination}",
        ])
    else:
        lines.append("No next action is currently available.")

    lines.extend(["", "## Strongest unresolved curves", ""])
    promising = brief.get("promising_curves") or []
    if promising:
        lines.extend([
            "| Curve | Current rigorous rank | Family | Parameter | Unresolved points | Witness gap |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ])
        for rec in promising[:5]:
            curve = rec["curve"]
            state = rec["state"]
            lines.append(
                "| "
                + " | ".join([
                    f"#{int(curve['id'])}",
                    _campaign_handoff_cell(_campaign_handoff_rank_text(state)),
                    _campaign_handoff_cell(curve["family"]),
                    _campaign_handoff_cell(curve["parameter"]),
                    str(int(rec["actionable_points"] or 0)),
                    str(int(rec["witness_gap"] or 0)),
                ])
                + " |"
            )
    else:
        lines.append("_No unresolved campaign curves in the current shortlist._")

    lines.extend(["", "## Strategy history", ""])
    strategies = brief.get("strategy_performance") or []
    if strategies:
        lines.extend([
            "| Strategy | Runs | Candidates | Historical best ≥ | Frontier gains | Failures | Active | Last activity |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ])
        for rec in strategies:
            lines.append(
                "| "
                + " | ".join([
                    _campaign_handoff_cell(rec["strategy"]),
                    str(int(rec["runs"] or 0)),
                    str(int(rec["candidates"] or 0)),
                    str(int(rec["best_lower"] or 0)),
                    str(int(rec["frontier_gains"] or 0)),
                    str(int(rec["failed"] or 0)),
                    str(int(rec["active"] or 0)),
                    _campaign_handoff_cell(rec["last_activity"]),
                ])
                + " |"
            )
    else:
        lines.append("_No recorded campaign execution history._")

    lines.extend(["", "### Frontier milestones", ""])
    milestones = brief.get("frontier_milestones") or []
    if milestones:
        for rec in milestones:
            source = []
            if rec.get("job_id") is not None:
                source.append(f"job #{int(rec['job_id'])}")
            if rec.get("run_id") is not None:
                source.append(f"pipeline run #{int(rec['run_id'])}")
            source_text = ", ".join(source) if source else "durable execution record"
            lines.append(
                f"- {rec['at'] or '—'} — **{rec['strategy']}** raised the historical execution "
                f"frontier {int(rec['previous_lower'])} → {int(rec['new_lower'])} ({source_text})."
            )
    else:
        lines.append("_No recorded execution has raised the historical campaign frontier._")

    lines.extend([
        "",
        "## Execution state",
        "",
        f"- **Running:** {int(snapshot['running'])}",
        f"- **Queued:** {int(snapshot['queued'])}",
        f"- **Completed work:** {int(snapshot['completed'])}",
        f"- **Failed / interrupted:** {int(snapshot['failed'])}",
        f"- **Candidates processed:** {int(snapshot['candidates_done']):,}",
        f"- **Linked jobs:** {len(snapshot['jobs'])}",
        f"- **Linked pipeline runs:** {len(snapshot['pipeline_runs'])}",
        f"- **Attached schedules:** {len(snapshot['schedules'])}",
    ])

    recent = brief.get("recent_activity") or []
    lines.extend(["", "### Recent execution", ""])
    if recent:
        lines.extend([
            "| When | Strategy | Status | Kind | Job | Pipeline run | Candidates | Historical best ≥ |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
        ])
        for rec in recent[:8]:
            lines.append(
                "| "
                + " | ".join([
                    _campaign_handoff_cell(rec["activity_at"]),
                    _campaign_handoff_cell(rec["strategy"]),
                    _campaign_handoff_cell(rec["status"]),
                    _campaign_handoff_cell(rec["kind"]),
                    (
                        f"#{int(rec['job_id'])}"
                        if int(rec["job_id"] or 0) > 0
                        else "—"
                    ),
                    (
                        f"#{int(rec['run_id'])}"
                        if rec.get("run_id") is not None
                        else "—"
                    ),
                    str(int(rec["candidates"] or 0)),
                    str(int(rec["best_lower"] or 0)),
                ])
                + " |"
            )
    else:
        lines.append("_No recent execution activity._")

    lines.extend(["", "## Completion baseline", ""])
    completion = brief.get("completion_snapshot")
    drift = brief.get("completion_drift")
    if completion is None:
        lines.append("_No completion-time evidence baseline is recorded._")
    else:
        completion_outcome = completion.get("target_reached")
        if completion_outcome is True:
            completion_outcome_text = "target met"
        elif completion_outcome is False:
            completion_outcome_text = "target not met"
        else:
            completion_outcome_text = "no numeric target"
        lines.extend([
            f"- **Captured:** {_campaign_handoff_cell(completion.get('captured_at') or campaign['completed_at'])}",
            f"- **Target then:** {('rank ≥' + str(int(completion['target_rank']))) if completion.get('target_rank') is not None else 'No numeric target'}",
            f"- **Outcome then:** {completion_outcome_text}",
            f"- **Leader then:** {('#' + str(int(completion['best_curve_id']))) if completion.get('best_curve_id') is not None else '—'}",
            f"- **Rigorous lower then:** {int(completion.get('best_lower') or 0)}",
        ])
        if drift and drift["changed"]:
            lines.append(
                f"- **Evidence drift:** {'REVIEW REQUIRED' if drift['requires_review'] else 'updated but compatible'}"
            )
            for key, change in drift["changes"].items():
                lines.append(
                    f"  - {_campaign_handoff_cell(key.replace('_', ' '))}: "
                    f"{_campaign_handoff_cell(change['completed'])} → "
                    f"{_campaign_handoff_cell(change['current'])}"
                )
        else:
            lines.append("- **Evidence drift:** none detected")

    lines.extend(["", "## Research notebook", ""])
    journal_notes = campaign_notebook_entries(
        db,
        int(campaign["id"]),
        limit=500,
        handoff_state="include",
    )
    legacy_note = str(campaign["notes"] or "").strip()

    if journal_notes:
        for note in journal_notes:
            entry_type = str(note.get("entry_type") or "note")
            label = NOTEBOOK_ENTRY_LABELS.get(
                entry_type,
                entry_type.replace("_", " ").title(),
            )
            lines.extend([
                f"### {label} — {_campaign_handoff_cell(note['created_at'])}",
                "",
                str(note["body"]),
            ])
            links = list(note.get("links") or [])
            if links:
                rendered = []
                for link in links:
                    kind = str(link["kind"])
                    link_label = NOTEBOOK_LINK_LABELS.get(
                        kind,
                        kind.replace("_", " ").title(),
                    )
                    rendered.append(f"{link_label} #{int(link['id'])}")
                lines.extend(["", "- **Links:** " + ", ".join(rendered)])
            lines.append("")

    if legacy_note:
        lines.extend([
            f"### Initial campaign note — {_campaign_handoff_cell(campaign['created_at'])}",
            "",
            legacy_note,
        ])

    if not journal_notes and not legacy_note:
        lines.append("_No operator notes recorded._")

    lines.extend([
        "",
        "## Resume anchors",
        "",
        f"- Campaign ID: #{int(campaign['id'])}",
        (
            f"- Leading curve ID: #{int(best['id'])}"
            if best is not None
            else "- Leading curve ID: —"
        ),
    ])
    active_runs = [
        run for run in snapshot["pipeline_runs"]
        if str(run["status"] or "") in {"queued", "running", "stopping"}
    ]
    if active_runs:
        for run in active_runs:
            lines.append(
                f"- Active pipeline run: #{int(run['id'])} — "
                f"{_campaign_handoff_cell(run['pipeline_name'])} ({run['status']})"
            )
    else:
        lines.append("- Active pipeline run: —")

    lines.extend([
        "",
        "## Evidence policy",
        "",
        "- Current rank statements above come from stored rigorous evidence and the conservative rank reducer.",
        "- Historical pipeline/job best lower bounds are execution history, not substitutes for current proof state.",
        "- Heuristic scores are intentionally omitted from proof claims in this handoff.",
        "",
    ])
    return "\n".join(lines)


def create_schedule(
    db,
    *,
    label,
    kind,
    command,
    cwd,
    next_run_at,
    recurrence="once",
    interval_minutes=None,
    catch_up_policy="latest",
    metadata=None,
    campaign_id=None,
    enabled=True,
):
    if str(kind) in RETIRED_SEARCH_JOB_KINDS:
        raise ValueError(
            "retired native Search jobs cannot be scheduled; use a Pipeline"
        )
    recurrence = str(recurrence)
    if recurrence not in SCHEDULE_RECURRENCES:
        raise ValueError(f"unsupported recurrence {recurrence!r}")
    catch_up_policy = str(catch_up_policy or "latest")
    if catch_up_policy not in SCHEDULE_CATCH_UP_POLICIES:
        raise ValueError(
            f"unsupported catch-up policy {catch_up_policy!r}"
        )
    if recurrence == "interval":
        if interval_minutes is None or int(interval_minutes) < 60:
            raise ValueError("interval schedules require interval_minutes >= 60")
        interval_minutes = int(interval_minutes)
    else:
        interval_minutes = None
    if campaign_id is not None and get_campaign(db, int(campaign_id)) is None:
        raise ValueError(f"campaign #{int(campaign_id)} not found")
    ts = now()
    cur = db.execute(
        """INSERT INTO ui_job_schedules(
               label,kind,command_json,cwd,metadata_json,campaign_id,enabled,
               recurrence,interval_minutes,catch_up_policy,next_run_at,
               created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(label),
            str(kind),
            json.dumps([str(x) for x in command]),
            str(cwd),
            json.dumps(_fresh_job_metadata(metadata), sort_keys=True),
            None if campaign_id is None else int(campaign_id),
            1 if enabled else 0,
            recurrence,
            interval_minutes,
            catch_up_policy,
            str(next_run_at),
            ts,
            ts,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_schedule(db, schedule_id, **fields):
    allowed = {
        "label", "kind", "command_json", "cwd", "metadata_json", "campaign_id",
        "enabled", "recurrence", "interval_minutes", "catch_up_policy",
        "next_run_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if "recurrence" in values and str(values["recurrence"]) not in SCHEDULE_RECURRENCES:
        raise ValueError(f"unsupported recurrence {values['recurrence']!r}")
    if "catch_up_policy" in values:
        values["catch_up_policy"] = str(values["catch_up_policy"] or "latest")
        if values["catch_up_policy"] not in SCHEDULE_CATCH_UP_POLICIES:
            raise ValueError(
                f"unsupported catch-up policy {values['catch_up_policy']!r}"
            )
    if "campaign_id" in values and values["campaign_id"] not in (None, ""):
        values["campaign_id"] = int(values["campaign_id"])
        if get_campaign(db, values["campaign_id"]) is None:
            raise ValueError(f"campaign #{values['campaign_id']} not found")
    if "enabled" in values:
        values["enabled"] = 1 if values["enabled"] else 0
    if not values:
        return
    values["updated_at"] = now()
    db.execute(
        "UPDATE ui_job_schedules SET "
        + ", ".join(f"{key}=?" for key in values)
        + " WHERE id=?",
        [*values.values(), int(schedule_id)],
    )
    db.commit()


def delete_schedule(db, schedule_id):
    db.execute(
        "DELETE FROM ui_job_schedule_occurrences WHERE schedule_id=?",
        (int(schedule_id),),
    )
    db.execute("DELETE FROM ui_job_schedules WHERE id=?", (int(schedule_id),))
    db.commit()


def list_schedules(db, *, include_disabled=True, limit=None):
    sql = "SELECT * FROM ui_job_schedules"
    args = []
    if not include_disabled:
        sql += " WHERE enabled=1"
    sql += " ORDER BY enabled DESC,next_run_at,id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    return db.execute(sql, args).fetchall()


def schedule_status_summary(db):
    """Return exact Scheduler counts without loading Schedule rows."""
    row = db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled
           FROM ui_job_schedules"""
    ).fetchone()
    error_row = db.execute(
        """SELECT COUNT(DISTINCT schedule_id) AS n
           FROM (
               SELECT id AS schedule_id
               FROM ui_job_schedules
               WHERE TRIM(COALESCE(last_error,''))!=''
               UNION
               SELECT schedule_id
               FROM ui_job_schedule_occurrences
               WHERE TRIM(COALESCE(last_error,''))!=''
                  OR state IN ('failed','interrupted','killed')
           )"""
    ).fetchone()
    total = int(row["total"] or 0)
    enabled = int(row["enabled"] or 0)
    return {
        "total": total,
        "enabled": enabled,
        "disabled": max(0, total - enabled),
        "errors": int(error_row["n"] or 0),
    }


def list_schedule_errors(db, *, limit=100):
    """Return bounded Schedules with persisted top-level errors."""
    return db.execute(
        """SELECT * FROM ui_job_schedules
           WHERE TRIM(COALESCE(last_error,''))!=''
           ORDER BY id DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()


def list_schedule_occurrences(db, schedule_id=None, *, limit=200):
    if schedule_id is None:
        return db.execute(
            """SELECT * FROM ui_job_schedule_occurrences
               ORDER BY scheduled_for DESC,id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return db.execute(
        """SELECT * FROM ui_job_schedule_occurrences
           WHERE schedule_id=?
           ORDER BY scheduled_for DESC,id DESC LIMIT ?""",
        (int(schedule_id), int(limit)),
    ).fetchall()


def _normalize_scheduled_for(value):
    return _parse_time(value).astimezone(timezone.utc).isoformat()


def _schedule_step(row):
    recurrence = str(row["recurrence"])
    if recurrence == "once":
        return None
    if recurrence == "daily":
        return timedelta(days=1)
    if recurrence == "weekly":
        return timedelta(days=7)
    return timedelta(minutes=int(row["interval_minutes"] or 60))


def _schedule_snapshot(db, row):
    meta = _fresh_job_metadata(_loads(row["metadata_json"], {}))
    campaign_id = (
        None if row["campaign_id"] is None else int(row["campaign_id"])
    )
    if campaign_id is not None:
        meta["campaign_id"] = campaign_id
        campaign = get_campaign(db, campaign_id)
        if campaign is not None:
            meta["campaign_name"] = str(campaign["name"])
            meta["campaign_created_at"] = str(campaign["created_at"])

    kind = str(row["kind"])
    return {
        "label": str(row["label"]),
        "kind": kind,
        "command": [
            str(value)
            for value in _loads(row["command_json"], [])
        ],
        "cwd": str(row["cwd"]),
        "metadata": meta,
        "campaign_id": campaign_id,
        "resource_class": infer_resource_class(kind, meta),
        "catch_up_policy": str(
            row["catch_up_policy"] or "latest"
        ),
    }


def _catch_up_plan(row, current, *, max_occurrences=20):
    scheduled = _parse_time(row["next_run_at"]).astimezone(timezone.utc)
    current = current.astimezone(timezone.utc)
    if scheduled > current:
        return [], scheduled, 0

    step = _schedule_step(row)
    if step is None:
        return [scheduled], None, 0

    due_count = int((current - scheduled) // step) + 1
    next_future = scheduled + (step * due_count)
    if due_count == 1:
        return [scheduled], next_future, 0

    policy = str(row["catch_up_policy"] or "latest")
    if policy == "skip":
        return [], next_future, due_count
    if policy == "latest":
        latest = scheduled + (step * (due_count - 1))
        return [latest], next_future, due_count - 1

    take = min(max(1, int(max_occurrences)), due_count)
    slots = [scheduled + (step * offset) for offset in range(take)]
    return slots, scheduled + (step * take), 0


def _register_due_occurrences(
    db,
    row,
    current,
    *,
    max_occurrences=20,
):
    """Atomically snapshot due work and advance the schedule clock."""
    schedule_id = int(row["id"])
    original_next = str(row["next_run_at"])
    ts = now()

    db.execute("BEGIN IMMEDIATE")
    try:
        fresh = db.execute(
            "SELECT * FROM ui_job_schedules WHERE id=?",
            (schedule_id,),
        ).fetchone()
        if (
            fresh is None
            or not bool(fresh["enabled"])
            or str(fresh["next_run_at"]) != original_next
            or _parse_time(fresh["next_run_at"]).astimezone(timezone.utc)
            > current.astimezone(timezone.utc)
        ):
            db.rollback()
            return []

        slots, next_time, suppressed = _catch_up_plan(
            fresh,
            current,
            max_occurrences=max_occurrences,
        )
        snapshot = _schedule_snapshot(db, fresh)
        policy = str(fresh["catch_up_policy"] or "latest")
        snapshot["catch_up"] = {
            "policy": policy,
            "suppressed_occurrences": int(suppressed),
        }
        snapshot_json = json.dumps(snapshot, sort_keys=True)
        registered = []

        if policy == "skip" and int(suppressed) > 1:
            step = _schedule_step(fresh)
            skipped_for = (
                next_time - step
                if step is not None and next_time is not None
                else _parse_time(fresh["next_run_at"])
            )
            scheduled_for = skipped_for.astimezone(timezone.utc).isoformat()
            message = (
                f"catch-up policy skipped {int(suppressed)} "
                "overdue occurrence(s)"
            )
            db.execute(
                """INSERT OR IGNORE INTO ui_job_schedule_occurrences(
                       schedule_id,scheduled_for,state,job_id,snapshot_json,
                       last_error,created_at,updated_at
                   ) VALUES(?,?,'skipped',NULL,?,?,?,?)""",
                (
                    schedule_id,
                    scheduled_for,
                    snapshot_json,
                    message,
                    ts,
                    ts,
                ),
            )
            occurrence = db.execute(
                """SELECT * FROM ui_job_schedule_occurrences
                   WHERE schedule_id=? AND scheduled_for=?""",
                (schedule_id, scheduled_for),
            ).fetchone()
            if occurrence is not None:
                registered.append(occurrence)
        else:
            for slot in slots:
                scheduled_for = slot.astimezone(timezone.utc).isoformat()
                db.execute(
                    """INSERT OR IGNORE INTO ui_job_schedule_occurrences(
                           schedule_id,scheduled_for,state,job_id,snapshot_json,
                           created_at,updated_at
                       ) VALUES(?,?,'pending',NULL,?,?,?)""",
                    (
                        schedule_id,
                        scheduled_for,
                        snapshot_json,
                        ts,
                        ts,
                    ),
                )
                occurrence = db.execute(
                    """SELECT * FROM ui_job_schedule_occurrences
                       WHERE schedule_id=? AND scheduled_for=?""",
                    (schedule_id, scheduled_for),
                ).fetchone()
                if occurrence is not None:
                    registered.append(occurrence)

        enabled = 0 if next_time is None else 1
        claimed_next = (
            original_next
            if next_time is None
            else next_time.astimezone(timezone.utc).isoformat()
        )
        cur = db.execute(
            """UPDATE ui_job_schedules
               SET enabled=?,next_run_at=?,updated_at=?
               WHERE id=? AND enabled=1 AND next_run_at=?""",
            (
                enabled,
                claimed_next,
                ts,
                schedule_id,
                original_next,
            ),
        )
        if int(cur.rowcount or 0) != 1:
            db.rollback()
            return []
        db.commit()
        return registered
    except Exception:
        db.rollback()
        raise


def _occurrence_snapshot(db, occurrence):
    snapshot = _loads(occurrence["snapshot_json"], {})
    required = {"label", "kind", "command", "cwd", "metadata"}
    if isinstance(snapshot, dict) and required.issubset(snapshot):
        return snapshot

    # Backward compatibility for pending occurrences created before snapshots
    # existed. New occurrences never take this path.
    row = db.execute(
        "SELECT * FROM ui_job_schedules WHERE id=?",
        (int(occurrence["schedule_id"]),),
    ).fetchone()
    if row is None:
        return None
    return _schedule_snapshot(db, row)


def _enqueue_pending_occurrence(db, db_path, project_root, occurrence):
    """Queue one registered immutable occurrence exactly once."""
    occurrence_id = int(occurrence["id"])
    fresh = db.execute(
        "SELECT * FROM ui_job_schedule_occurrences WHERE id=?",
        (occurrence_id,),
    ).fetchone()
    if fresh is None:
        return None
    if fresh["job_id"] is not None:
        return int(fresh["job_id"])
    if str(fresh["state"]) != "pending":
        return None

    snapshot = _occurrence_snapshot(db, fresh)
    if snapshot is None:
        return None
    if str(snapshot.get("kind")) in RETIRED_SEARCH_JOB_KINDS:
        db.execute(
            """UPDATE ui_job_schedule_occurrences
               SET state='failed',last_error=?,updated_at=? WHERE id=?""",
            (
                "retired native Search schedules cannot run; use a Pipeline",
                now(),
                occurrence_id,
            ),
        )
        db.commit()
        return None

    base_meta = _fresh_job_metadata(
        dict(snapshot.get("metadata") or {})
    )
    base_command = list(snapshot.get("command") or [])
    execution_row = {"kind": str(snapshot["kind"])}
    campaign_id = snapshot.get("campaign_id")

    try:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT * FROM ui_job_schedule_occurrences WHERE id=?",
            (occurrence_id,),
        ).fetchone()
        if current is None:
            db.rollback()
            return None
        if current["job_id"] is not None:
            job_id = int(current["job_id"])
            db.commit()
            return job_id
        if str(current["state"]) != "pending":
            db.rollback()
            return None

        if campaign_id is not None:
            campaign = get_campaign(db, int(campaign_id))
            if (
                campaign is None
                or str(campaign["status"]) != "active"
            ):
                db.rollback()
                return None

        # Pipeline run cloning, Job creation, Queue creation, and occurrence
        # attachment share this transaction. A failure rolls all of them back.
        meta, command = _prepare_cloned_job(
            db,
            execution_row,
            base_meta,
            base_command,
            pipeline_commit=False,
        )
        meta["schedule_id"] = int(current["schedule_id"])
        meta["schedule_occurrence_id"] = occurrence_id
        meta["scheduled_for"] = str(current["scheduled_for"])
        meta["schedule_catch_up_policy"] = str(
            snapshot.get("catch_up_policy") or "latest"
        )

        job_id = enqueue_job_record(
            db,
            kind=str(snapshot["kind"]),
            label=str(snapshot["label"]),
            command=command,
            cwd=str(snapshot.get("cwd") or project_root),
            metadata=meta,
            resource_class=str(
                snapshot.get("resource_class") or "default"
            ),
        )
        stamp = now()
        db.execute(
            """UPDATE ui_job_schedule_occurrences
               SET state='queued',job_id=?,last_error=NULL,updated_at=?
               WHERE id=? AND state='pending' AND job_id IS NULL""",
            (int(job_id), stamp, occurrence_id),
        )
        db.execute(
            """UPDATE ui_job_schedules
               SET last_run_at=?,last_job_id=?,last_error=NULL,updated_at=?
               WHERE id=?""",
            (
                stamp,
                int(job_id),
                stamp,
                int(current["schedule_id"]),
            ),
        )
        db.commit()
        return int(job_id)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        db.execute(
            """UPDATE ui_job_schedule_occurrences
               SET last_error=?,updated_at=?
               WHERE id=? AND state='pending'""",
            (str(exc)[:1000], now(), occurrence_id),
        )
        db.commit()
        return None

def _parse_time(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _next_after(row, scheduled):
    recurrence = str(row["recurrence"])
    if recurrence == "once":
        return None
    if recurrence == "daily":
        return scheduled + timedelta(days=1)
    if recurrence == "weekly":
        return scheduled + timedelta(days=7)
    return scheduled + timedelta(minutes=int(row["interval_minutes"] or 60))


def _replace_command_option(command, option, value):
    parts = [str(x) for x in command]
    try:
        index = parts.index(str(option))
    except ValueError:
        parts += [str(option), str(value)]
    else:
        if index + 1 >= len(parts):
            parts.append(str(value))
        else:
            parts[index + 1] = str(value)
    return parts


def _clone_pipeline_run(
    db,
    source_run_id,
    *,
    campaign_id=None,
    campaign_name=None,
    campaign_created_at=None,
    commit=True,
):
    if not _table_exists(db, "search_pipeline_runs"):
        raise ValueError("Pipeline state is not initialized")
    from rank42.pipeline_state import create_pipeline_run, get_pipeline_run, pipeline_run_payload

    source = get_pipeline_run(db, int(source_run_id))
    if source is None:
        raise ValueError(f"pipeline run #{int(source_run_id)} not found")
    payload = pipeline_run_payload(source)
    run_config = dict(payload.get("run_config") or {})
    if campaign_id is not None:
        campaign = get_campaign(db, int(campaign_id))
        if campaign is None:
            raise ValueError(f"campaign #{int(campaign_id)} not found")
        run_config["campaign_id"] = int(campaign_id)
        run_config["campaign_name"] = str(
            campaign_name or campaign["name"]
        )
        run_config["campaign_created_at"] = str(
            campaign_created_at or campaign["created_at"]
        )
    return create_pipeline_run(
        db,
        pipeline_id=payload.get("pipeline_id"),
        pipeline_name=payload.get("pipeline_name") or "Scheduled pipeline",
        target_mode=payload.get("target_mode") or "family",
        target=payload.get("target") or {},
        stages=payload.get("stages") or [],
        run_config=run_config,
        commit=commit,
    )


def _prepare_cloned_job(
    db,
    row,
    meta,
    command,
    *,
    pipeline_commit=True,
):
    meta = _fresh_job_metadata(meta)
    command = [str(x) for x in command]
    if str(row["kind"]) != "pipeline_search":
        return meta, command
    source_run_id = meta.get("pipeline_run_id")
    if source_run_id is None:
        raise ValueError("pipeline job metadata has no pipeline_run_id")
    campaign_id = meta.get("campaign_id")
    new_run_id = _clone_pipeline_run(
        db,
        int(source_run_id),
        campaign_id=campaign_id,
        campaign_name=meta.get("campaign_name"),
        campaign_created_at=meta.get("campaign_created_at"),
        commit=pipeline_commit,
    )
    meta["pipeline_run_id"] = int(new_run_id)
    command = _replace_command_option(command, "--run-id", int(new_run_id))
    return meta, command


def run_schedule_now(db, db_path, project_root, schedule_id):
    row = db.execute(
        "SELECT * FROM ui_job_schedules WHERE id=?", (int(schedule_id),)
    ).fetchone()
    if row is None:
        raise ValueError(f"schedule #{int(schedule_id)} not found")
    if str(row["kind"]) in RETIRED_SEARCH_JOB_KINDS:
        raise ValueError(
            "retired native Search schedules cannot run; use a Pipeline"
        )
    meta = _fresh_job_metadata(_loads(row["metadata_json"], {}))
    if row["campaign_id"] is not None:
        meta["campaign_id"] = int(row["campaign_id"])
        campaign = get_campaign(db, int(row["campaign_id"]))
        if campaign is not None:
            meta["campaign_name"] = str(campaign["name"])
            meta["campaign_created_at"] = str(campaign["created_at"])
        if campaign is not None and str(campaign["status"]) != "active":
            raise ValueError(
                f"campaign #{int(row['campaign_id'])} is {campaign['status']}; activate it before running this schedule"
            )
    meta, command = _prepare_cloned_job(
        db,
        row,
        meta,
        _loads(row["command_json"], []),
    )
    meta["schedule_id"] = int(row["id"])
    job_id = enqueue_job(
        db_path,
        kind=str(row["kind"]),
        label=str(row["label"]),
        command=command,
        cwd=str(row["cwd"] or project_root),
        metadata=meta,
    )
    db.execute(
        """UPDATE ui_job_schedules
           SET last_run_at=?,last_job_id=?,last_error=NULL,updated_at=? WHERE id=?""",
        (now(), int(job_id), now(), int(row["id"])),
    )
    db.commit()
    return int(job_id)


def dispatch_due_schedules(db, db_path, project_root):
    """Register due schedule occurrences and enqueue each occurrence once."""
    current = datetime.now(timezone.utc)
    due = db.execute(
        """SELECT * FROM ui_job_schedules
           WHERE enabled=1 AND next_run_at<=?
           ORDER BY next_run_at,id LIMIT 20""",
        (current.isoformat(),),
    ).fetchall()

    occurrence_budget = 20
    for row in due:
        if occurrence_budget <= 0:
            break
        if row["campaign_id"] is not None:
            campaign = get_campaign(db, int(row["campaign_id"]))
            if campaign is None or str(campaign["status"]) != "active":
                continue
        registered = _register_due_occurrences(
            db,
            row,
            current,
            max_occurrences=occurrence_budget,
        )
        occurrence_budget -= len(registered)

    pending = db.execute(
        """SELECT * FROM ui_job_schedule_occurrences
           WHERE state='pending'
           ORDER BY scheduled_for,id LIMIT 20"""
    ).fetchall()
    launched = []
    for occurrence in pending:
        try:
            job_id = _enqueue_pending_occurrence(
                db,
                db_path,
                project_root,
                occurrence,
            )
        except Exception as exc:
            db.execute(
                """UPDATE ui_job_schedule_occurrences
                   SET last_error=?,updated_at=?
                   WHERE id=? AND state='pending'""",
                (
                    str(exc)[:1000],
                    now(),
                    int(occurrence["id"]),
                ),
            )
            db.commit()
            continue
        if job_id is not None:
            launched.append(int(job_id))
    return launched

def retry_job(db, db_path, job_id):
    row = get_job(db, int(job_id))
    if row is None:
        raise ValueError(f"job #{int(job_id)} not found")
    if str(row["status"]) in {"queued", "running", "stopping"}:
        raise ValueError("active jobs cannot be retried")
    if str(row["kind"]) in RETIRED_SEARCH_JOB_KINDS:
        raise ValueError(
            "retired native Search jobs may only resume their existing work; "
            "start fresh work through a Pipeline"
        )
    meta, command = _prepare_cloned_job(
        db,
        row,
        _loads(row["metadata_json"], {}),
        _loads(row["command_json"], []),
    )
    meta["retry_of_job_id"] = int(job_id)
    return enqueue_job(
        db_path,
        kind=str(row["kind"]),
        label=str(row["label"]),
        command=command,
        cwd=str(row["cwd"]),
        metadata=meta,
    )


def _replace_existing_command_option(command, option, value):
    parts = [str(x) for x in command]
    try:
        index = parts.index(str(option))
    except ValueError:
        return parts
    if index + 1 < len(parts):
        parts[index + 1] = str(value)
    return parts


def _resume_pool_slice(db, row, source_meta, meta, command):
    pool_id = source_meta.get("pool_id")
    original_ids = [int(x) for x in (source_meta.get("candidate_ids") or [])]
    if not pool_id or not original_ids:
        return meta, command

    from rank42.candidates import candidate_rows, export_rows_jsonl

    available = {
        int(candidate["id"]): candidate
        for candidate in candidate_rows(
            db,
            int(pool_id),
            limit=None,
            offset=0,
            only_unsearched=True,
        )
    }
    remaining = [
        available[candidate_id]
        for candidate_id in original_ids
        if candidate_id in available
    ]
    if not remaining:
        raise ValueError("this candidate slice has no unsearched work remaining")

    cwd = Path(str(row["cwd"])).resolve()
    export_dir = cwd / ".rank42-ui" / "candidate-slices"
    export_dir.mkdir(parents=True, exist_ok=True)
    candidate_file = (
        export_dir
        / f"resume-job-{int(row['id']):06d}-n{len(remaining)}.jsonl"
    )
    export_rows_jsonl(db, int(pool_id), remaining, candidate_file)

    parts = [str(x) for x in command]
    replaced = False
    pool_token = f"pool-{int(pool_id)}-"
    for index, value in enumerate(parts):
        name = Path(value).name
        if value.endswith(".jsonl") and (
            pool_token in name or "candidate-slices" in value
        ):
            parts[index] = str(candidate_file)
            replaced = True
            break
    if not replaced:
        # The metadata still makes the resumed scientific slice exact even for
        # custom adapters.  Known core/plugin launchers pass the JSONL path as a
        # command argument, so this is only a conservative fallback.
        parts = [str(x) for x in command]

    parts = _replace_existing_command_option(parts, "--limit", len(remaining))
    parts = _replace_existing_command_option(parts, "--count", len(remaining))

    rank_orders = [int(candidate["rank_order"]) for candidate in remaining]
    meta["candidate_ids"] = [int(candidate["id"]) for candidate in remaining]
    meta["candidate_rank_orders"] = rank_orders
    meta["count"] = len(remaining)
    if rank_orders:
        meta["offset"] = max(0, min(rank_orders) - 1)
    return meta, parts


def _resume_cursor_command(source_meta, meta, command):
    if source_meta.get("cursor_next_offset") is None:
        return meta, [str(x) for x in command]
    next_offset = int(source_meta.get("cursor_next_offset") or 0)
    completed = int(source_meta.get("cursor_completed") or 0)
    original_count = int(source_meta.get("count") or 0)
    remaining = max(0, original_count - completed) if original_count else 0

    meta["offset"] = next_offset
    if remaining:
        meta["count"] = remaining
    parts = _replace_existing_command_option(command, "--offset", next_offset)
    if remaining:
        parts = _replace_existing_command_option(parts, "--count", remaining)
        parts = _replace_existing_command_option(parts, "--limit", remaining)
    return meta, parts


def _mark_legacy_core_search_resume(kind, command):
    """Authorize only a stored Jobs resume for retired core Search CLIs."""
    parts = [str(value) for value in command]
    module = LEGACY_CORE_SEARCH_MODULES.get(str(kind))
    if module is None or "--legacy-resume" in parts:
        return parts
    for index, value in enumerate(parts[:-1]):
        if value == "-m" and parts[index + 1] == module:
            parts.append("--legacy-resume")
            break
    return parts


def _prepare_resumed_job(db, row):
    source_meta = _loads(row["metadata_json"], {})
    meta = _fresh_job_metadata(source_meta)
    command = [str(x) for x in _loads(row["command_json"], [])]
    kind = str(row["kind"])

    if kind == "pipeline_search":
        run_id = source_meta.get("pipeline_run_id")
        if run_id is None:
            raise ValueError("pipeline job metadata has no pipeline_run_id")
        from rank42.pipeline_state import get_pipeline_run, update_pipeline_run

        run = get_pipeline_run(db, int(run_id))
        if run is None:
            raise ValueError(f"pipeline run #{int(run_id)} not found")
        update_pipeline_run(
            db,
            int(run_id),
            status="queued",
            error=None,
            finished_at=None,
        )
    elif kind in {"auto_search", "torsion_auto_search", "geometry_search"}:
        campaign_id = legacy_search_campaign_id(source_meta)
        if campaign_id is not None:
            from rank42.auto_search_state import (
                get_campaign as get_auto_campaign,
                update_campaign as update_auto_campaign,
            )

            campaign = get_auto_campaign(db, int(campaign_id))
            if campaign is not None:
                update_auto_campaign(
                    db,
                    int(campaign_id),
                    status="queued",
                    error=None,
                    finished_at=None,
                )
                command = _replace_command_option(
                    command, "--campaign-id", int(campaign_id)
                )

    command = _mark_legacy_core_search_resume(kind, command)

    if source_meta.get("pool_id") and source_meta.get("candidate_ids"):
        meta, command = _resume_pool_slice(
            db,
            row,
            source_meta,
            meta,
            command,
        )
    else:
        meta, command = _resume_cursor_command(
            source_meta,
            meta,
            command,
        )

    return source_meta, meta, command


def _pipeline_jobs_for_run(db, run_id):
    """Return Pipeline Jobs for one durable Run, newest first."""
    run_id = int(run_id)
    rows = []
    for row in list_jobs(db, limit=1000):
        if str(row["kind"]) != "pipeline_search":
            continue
        meta = _loads(row["metadata_json"], {})
        try:
            linked_run_id = int(meta.get("pipeline_run_id"))
        except (TypeError, ValueError):
            continue
        if linked_run_id == run_id:
            rows.append(row)
    return rows


def _pipeline_active_job(db, run_id):
    for row in _pipeline_jobs_for_run(db, run_id):
        if str(row["status"]) in {"queued", "running", "stopping"}:
            return row
    return None


def _pipeline_resumable_job(db, run_id):
    for row in _pipeline_jobs_for_run(db, run_id):
        if str(row["status"]) in {"failed", "interrupted", "stopped", "killed"}:
            return row
    return None


def _pipeline_retry_source_job(db, run_id):
    for row in _pipeline_jobs_for_run(db, run_id):
        if str(row["status"]) not in {"queued", "running", "stopping"}:
            return row
    return None


def pause_pipeline_run(db, run_id, *, job_id=None):
    """Pause one durable Pipeline Run through its active process attempt."""
    from rank42.pipeline_state import get_pipeline_run, update_pipeline_run

    run_id = int(run_id)
    if get_pipeline_run(db, run_id) is None:
        raise ValueError(f"pipeline run #{run_id} not found")
    row = get_job(db, int(job_id)) if job_id is not None else _pipeline_active_job(db, run_id)
    if row is None:
        raise ValueError(f"pipeline run #{run_id} has no active job")
    meta = _loads(row["metadata_json"], {})
    if str(row["kind"]) != "pipeline_search" or int(meta.get("pipeline_run_id") or -1) != run_id:
        raise ValueError(f"job #{int(row['id'])} does not own pipeline run #{run_id}")
    update_pipeline_run(
        db,
        run_id,
        status="paused",
        error=None,
        finished_at=None,
    )
    return stop_job(db, int(row["id"]), force=False, pause=True)


def stop_pipeline_run(db, run_id, *, force=False, job_id=None):
    """Stop one durable Pipeline Run and its active process attempt."""
    from rank42.pipeline_state import get_pipeline_run, update_pipeline_run

    run_id = int(run_id)
    if get_pipeline_run(db, run_id) is None:
        raise ValueError(f"pipeline run #{run_id} not found")
    row = get_job(db, int(job_id)) if job_id is not None else _pipeline_active_job(db, run_id)
    if row is None:
        raise ValueError(f"pipeline run #{run_id} has no active job")
    meta = _loads(row["metadata_json"], {})
    if str(row["kind"]) != "pipeline_search" or int(meta.get("pipeline_run_id") or -1) != run_id:
        raise ValueError(f"job #{int(row['id'])} does not own pipeline run #{run_id}")
    update_pipeline_run(
        db,
        run_id,
        status="killed" if force else "paused",
        error=None,
        finished_at=now() if force else None,
    )
    return stop_job(db, int(row["id"]), force=bool(force), pause=False)


def resume_pipeline_run(db, db_path, run_id, *, launch_fallback=None):
    """Resume the same durable Pipeline Run through one lifecycle authority."""
    from rank42.pipeline_state import (
        get_pipeline_run,
        pipeline_run_runtime_drift,
        update_pipeline_run,
    )

    run_id = int(run_id)
    run = get_pipeline_run(db, run_id)
    if run is None:
        raise ValueError(f"pipeline run #{run_id} not found")
    if str(run["status"]) in {"queued", "running"}:
        raise ValueError("active pipeline runs cannot be resumed")
    drift = pipeline_run_runtime_drift(run)
    if drift["blocking"]:
        raise ValueError(
            "pipeline run cannot be resumed with this runtime: "
            + "; ".join(drift["blocking"])
        )

    row = _pipeline_resumable_job(db, run_id)
    if row is not None:
        return resume_job(db, db_path, int(row["id"]))

    if launch_fallback is None:
        raise ValueError(f"pipeline run #{run_id} has no resumable job")
    previous_status = str(run["status"])
    previous_error = run["error"]
    previous_finished_at = run["finished_at"]
    update_pipeline_run(
        db,
        run_id,
        status="queued",
        error=None,
        finished_at=None,
    )
    try:
        return int(launch_fallback(run))
    except Exception:
        update_pipeline_run(
            db,
            run_id,
            status=previous_status,
            error=previous_error,
            finished_at=previous_finished_at,
        )
        raise


def retry_pipeline_run(db, db_path, run_id):
    """Retry one Pipeline Run as a fresh cloned Run + Job."""
    from rank42.pipeline_state import get_pipeline_run

    run_id = int(run_id)
    run = get_pipeline_run(db, run_id)
    if run is None:
        raise ValueError(f"pipeline run #{run_id} not found")
    if str(run["status"]) in {"queued", "running"}:
        raise ValueError("active pipeline runs cannot be retried")
    row = _pipeline_retry_source_job(db, run_id)
    if row is None:
        raise ValueError(f"pipeline run #{run_id} has no retryable job")
    return retry_job(db, db_path, int(row["id"]))


def pause_job(db, job_id):
    """Pause one UI job while keeping its durable scientific owner resumable."""
    row = get_job(db, int(job_id))
    if row is None:
        raise ValueError(f"job #{int(job_id)} not found")
    if str(row["status"]) not in {"queued", "running", "stopping"}:
        raise ValueError("only active jobs can be paused")

    meta = _loads(row["metadata_json"], {})
    kind = str(row["kind"])
    if kind == "pipeline_search" and meta.get("pipeline_run_id") is not None:
        return pause_pipeline_run(
            db,
            int(meta["pipeline_run_id"]),
            job_id=int(job_id),
        )
    elif kind in {"auto_search", "torsion_auto_search", "geometry_search"}:
        campaign_id = legacy_search_campaign_id(meta)
        if campaign_id is not None:
            from rank42.auto_search_state import (
                get_campaign as get_auto_campaign,
                reconcile_child_campaign_lifecycle,
                update_campaign as update_auto_campaign,
            )

            campaign = get_auto_campaign(db, int(campaign_id))
            if campaign is not None:
                update_auto_campaign(
                    db,
                    int(campaign_id),
                    status="paused",
                    error=None,
                )
                reconcile_child_campaign_lifecycle(
                    db,
                    int(campaign_id),
                    parent_status="paused",
                )

    return stop_job(db, int(job_id), force=False, pause=True)


def resume_job(db, db_path, job_id):
    """Resume durable work from a stopped/interrupted UI job.

    Rank Hunter reuses persisted scientific state.  Pipeline/Auto/Geometry jobs
    continue their existing durable run/campaign.  Candidate-pool jobs rebuild
    the original slice from only still-unsearched candidates.  Other jobs
    relaunch the same command, so completed persisted work is retained while an
    uncheckpointed in-flight stage may be repeated.
    """
    row = get_job(db, int(job_id))
    if row is None:
        raise ValueError(f"job #{int(job_id)} not found")
    status = str(row["status"])
    if status in {"queued", "running", "stopping"}:
        raise ValueError("active jobs cannot be resumed")
    if status == "succeeded":
        raise ValueError("completed jobs do not need resume; use Retry for a fresh run")

    source_meta, meta, command = _prepare_resumed_job(db, row)
    meta["resume_of_job_id"] = int(job_id)
    new_id = enqueue_job(
        db_path,
        kind=str(row["kind"]),
        label=str(row["label"]),
        command=command,
        cwd=str(row["cwd"]),
        metadata=meta,
    )

    source_meta.pop("pause_requested", None)
    source_meta.pop("paused_at", None)
    source_meta["resumed_as_job_id"] = int(new_id)
    update_job(
        db,
        int(job_id),
        metadata_json=json.dumps(source_meta, sort_keys=True),
    )
    return int(new_id)


def delete_terminal_job(db, job_id, *, delete_log=True):
    row = get_job(db, int(job_id))
    if row is None:
        return False
    if str(row["status"]) in {"queued", "running", "stopping"}:
        raise ValueError("active jobs cannot be deleted")
    if delete_log:
        try:
            Path(str(row["log_path"])).unlink(missing_ok=True)
        except OSError:
            pass
    if _table_exists(db, "ui_job_queue"):
        db.execute(
            "DELETE FROM ui_job_queue WHERE job_id=?",
            (int(job_id),),
        )
    db.execute("DELETE FROM ui_jobs WHERE id=?", (int(job_id),))
    remove_artifact_links(
        db,
        artifact_kind="job",
        artifact_id=int(job_id),
        commit=False,
    )
    db.commit()
    return True
