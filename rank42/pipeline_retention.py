"""Enforce Pipeline inventory floors after UI jobs exit or are stopped.

Run/candidate history stays in the database. Only curves created during the
particular run can be removed; a matching preexisting curve is never owned by
that run's retention policy.
"""
from __future__ import annotations

import json

from rank42.curve_research_state import get_curve_research_state
from rank42.db import discard_unpromoted_search_curve, now


TERMINAL_JOB_STATUSES = frozenset({
    "succeeded", "failed", "interrupted", "stopped", "killed", "cancelled",
})


def cleanup_pipeline_run(db, run_id, *, floor_override=None, dry_run=False):
    run = db.execute(
        "SELECT id,started_at,run_config_json FROM search_pipeline_runs WHERE id=?",
        (int(run_id),),
    ).fetchone()
    if run is None:
        raise ValueError(f"Pipeline run #{run_id} not found")
    config = json.loads(run["run_config_json"] or "{}")
    floor = max(0, int(
        (config.get("retention_floor") if floor_override is None else floor_override)
        or 0
    ))
    result = {
        "run_id": int(run_id), "floor": floor,
        "removed": 0, "preexisting": 0, "active_elsewhere": 0,
        "retained": 0, "curve_ids_removed": [],
    }
    if floor == 0:
        return result

    rows = db.execute(
        """SELECT DISTINCT c.id,c.created_at,pc.score_provenance_json
           FROM search_pipeline_candidates pc
           JOIN curves c ON c.id=pc.curve_id
           WHERE pc.run_id=? ORDER BY c.id""",
        (int(run_id),),
    ).fetchall()
    started_at = str(run["started_at"] or "")
    processed = set()
    for row in rows:
        curve_id = int(row["id"])
        if curve_id in processed:
            continue
        try:
            provenance = json.loads(row["score_provenance_json"] or "{}")
        except (TypeError, ValueError):
            provenance = {}
        if int(provenance.get("created_by_pipeline_run") or 0) != int(run_id):
            continue
        processed.add(curve_id)
        if not started_at or str(row["created_at"] or "") < started_at:
            result["preexisting"] += 1
            continue
        active = db.execute(
            """SELECT 1 FROM search_pipeline_candidates pc
               JOIN search_pipeline_runs r ON r.id=pc.run_id
               WHERE pc.curve_id=? AND pc.run_id<>?
                 AND r.status IN ('queued','running') LIMIT 1""",
            (curve_id, int(run_id)),
        ).fetchone()
        if active is not None:
            result["active_elsewhere"] += 1
            continue
        state = get_curve_research_state(db, curve_id)
        if int(state.get("rigorous_lower") or 0) >= floor:
            result["retained"] += 1
            continue
        if not dry_run:
            if not discard_unpromoted_search_curve(
                db, curve_id,
                created_not_before=started_at,
                retention_floor=floor,
            ):
                result["retained"] += 1
                continue
        result["removed"] += 1
        result["curve_ids_removed"].append(curve_id)
    return result


def cleanup_auto_campaign(db, campaign_id):
    campaign = db.execute(
        "SELECT id,started_at,retention_floor FROM auto_search_campaigns WHERE id=?",
        (int(campaign_id),),
    ).fetchone()
    if campaign is None:
        raise ValueError(f"Auto campaign #{campaign_id} not found")
    floor = max(0, int(campaign["retention_floor"] or 0))
    result = {
        "campaign_id": int(campaign_id), "floor": floor,
        "removed": 0, "preexisting": 0, "active_elsewhere": 0,
        "retained": 0, "curve_ids_removed": [],
    }
    started_at = str(campaign["started_at"] or "")
    if not floor or not started_at:
        return result
    rows = db.execute(
        """SELECT DISTINCT c.id,c.created_at,t.metadata_json
           FROM auto_search_trials t JOIN curves c ON c.id=t.curve_id
           WHERE t.campaign_id=? ORDER BY c.id""",
        (int(campaign_id),),
    ).fetchall()
    processed = set()
    for row in rows:
        curve_id = int(row["id"])
        if curve_id in processed:
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not metadata.get("created_by_campaign"):
            continue
        processed.add(curve_id)
        if str(row["created_at"] or "") < started_at:
            result["preexisting"] += 1
            continue
        active = db.execute(
            """SELECT 1 FROM auto_search_trials t
               JOIN auto_search_campaigns a ON a.id=t.campaign_id
               WHERE t.curve_id=? AND t.campaign_id<>?
                 AND a.status IN ('queued','running') LIMIT 1""",
            (curve_id, int(campaign_id)),
        ).fetchone()
        if active is None:
            active = db.execute(
                """SELECT 1 FROM search_pipeline_candidates pc
                   JOIN search_pipeline_runs r ON r.id=pc.run_id
                   WHERE pc.curve_id=? AND r.status IN ('queued','running') LIMIT 1""",
                (curve_id,),
            ).fetchone()
        if active is not None:
            result["active_elsewhere"] += 1
            continue
        state = get_curve_research_state(db, curve_id)
        if int(state.get("rigorous_lower") or 0) >= floor:
            result["retained"] += 1
            continue
        if discard_unpromoted_search_curve(
            db, curve_id,
            created_not_before=started_at,
            retention_floor=floor,
        ):
            result["removed"] += 1
            result["curve_ids_removed"].append(curve_id)
    return result


def finalize_search_job_retention(db, job_id):
    job = db.execute(
        "SELECT id,kind,status,pid,metadata_json FROM ui_jobs WHERE id=?",
        (int(job_id),),
    ).fetchone()
    if job is None or str(job["status"] or "") not in TERMINAL_JOB_STATUSES:
        return None
    metadata = json.loads(job["metadata_json"] or "{}")
    run_id = metadata.get("pipeline_run_id")
    campaign_id = metadata.get("search_campaign_id")
    if metadata.get("retention_finalized") is not None:
        return None
    if run_id is not None:
        result = cleanup_pipeline_run(db, int(run_id))
    elif campaign_id is not None:
        result = cleanup_auto_campaign(db, int(campaign_id))
    else:
        return None
    metadata["retention_finalized"] = {
        "at": now(),
        "floor": result["floor"],
        "removed": result["removed"],
        "preexisting": result["preexisting"],
        "active_elsewhere": result["active_elsewhere"],
    }
    db.execute(
        "UPDATE ui_jobs SET metadata_json=?,updated_at=? WHERE id=?",
        (json.dumps(metadata, sort_keys=True), now(), int(job_id)),
    )
    db.commit()
    return result
