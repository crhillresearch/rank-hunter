"""Authoritative read-only Dashboard snapshots.

Dashboard is a composition surface. These helpers gather state from the owning
Campaign, Analysis, Curve Research State, Jobs/Queue, Pipeline, and Plugin
services so UI renderers do not recreate application or scientific truth.
"""
from __future__ import annotations

import json
import sqlite3

from rank42.dashboard_rank_state import (
    dashboard_frontier_rows,
    dashboard_rank_certification,
    dashboard_rank_summary,
)
from rank42.curve_research_state import list_curve_research_states
from rank42.manage_store import (
    campaign_progress_summary,
    campaign_research_brief,
    current_campaign,
    list_schedule_errors,
    list_schedule_occurrences,
    schedule_status_summary,
)
from rank42.pipeline_state import (
    list_pipeline_runs_readonly,
    pipeline_run_status_counts_readonly,
)
from rank42.plugins import (
    Plugin,
    discover_plugins,
    is_archived,
    is_enabled,
    plugin_state,
    validation_freshness,
)
from rank42.ui_store import (
    dispatcher_status,
    get_application_state,
    job_status_counts,
    list_jobs,
    now,
    queue_counts,
    resolve_dispatcher_health,
    set_application_state,
)


_ACTIVE_JOB_STATUSES = {"queued", "running", "stopping"}
_FAILED_JOB_STATUSES = {"failed", "interrupted", "killed", "stopped"}
_ATTENTION_JOB_STATUSES = {"failed", "interrupted"}
_ACTIVE_RUN_STATUSES = {"queued", "running", "stopping"}
_FAILED_RUN_STATUSES = {"failed", "interrupted", "killed", "error"}
_ATTENTION_RUN_STATUSES = {"failed", "interrupted", "error"}
_ATTENTION_ACK_STATE_KEY = "dashboard_attention_acknowledgements_v1"
_LOAD_CAMPAIGN = object()


def _dispatcher_health_snapshot(db, project_root=None, db_path=None):
    """Resolve Jobs-owned heartbeat/service state for read-only consumers."""
    heartbeat = dispatcher_status(db)
    service = None
    runtime = None
    if project_root is not None and db_path is not None:
        try:
            from rank42.dispatcher_service import (
                runtime_heartbeat_status,
                service_status,
            )
            service = service_status(project_root, db_path)
            runtime = runtime_heartbeat_status(project_root, db_path)
        except Exception:
            # Heartbeat state remains useful when service-manager inspection is
            # unavailable; do not make Dashboard rendering depend on systemd.
            service = None
            runtime = None
    return resolve_dispatcher_health(heartbeat, service, runtime)


def _row_value(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _attention_acknowledgements(db):
    try:
        raw = get_application_state(db, _ATTENTION_ACK_STATE_KEY, {})
    except (AttributeError, sqlite3.Error):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def acknowledge_dashboard_attention(db, item):
    """Acknowledge one immutable terminal operational alert without rewriting it."""
    if not bool(item.get("acknowledgeable")) or not item.get("attention_key"):
        raise ValueError("this attention item must be resolved by its owning page")
    acknowledgements = _attention_acknowledgements(db)
    acknowledgements[str(item["attention_key"])] = now()
    if len(acknowledgements) > 500:
        acknowledgements = dict(
            sorted(acknowledgements.items(), key=lambda pair: str(pair[1]))[-500:]
        )
    set_application_state(db, _ATTENTION_ACK_STATE_KEY, acknowledgements)


def _campaign_snapshot(db):
    campaign = current_campaign(db)
    if campaign is None:
        return None

    campaign_id = int(campaign["id"])
    progress = campaign_progress_summary(db, campaign_id)
    brief = campaign_research_brief(db, campaign_id)
    milestone = None
    milestones = list(brief.get("frontier_milestones") or [])
    if milestones:
        milestone = milestones[0]

    analysis_issues = []
    seen_curve_ids = set()
    for rec in [
        *(brief.get("conflicted_curves") or []),
        *(brief.get("promising_curves") or []),
    ]:
        curve = rec.get("curve")
        if curve is None:
            continue
        curve_id = int(curve["id"])
        if curve_id in seen_curve_ids:
            continue
        seen_curve_ids.add(curve_id)
        state = dict(rec.get("state") or {})
        analysis_issues.append({
            "curve_id": curve_id,
            "rigorous_lower": int(state.get("rigorous_lower") or 0),
            "inconsistent": bool(state.get("inconsistent")),
            "actionable_points": int(rec.get("actionable_points") or 0),
            "witness_gap": int(rec.get("witness_gap") or 0),
        })

    return {
        "id": campaign_id,
        "name": str(campaign["name"]),
        "objective": str(campaign["objective"] or ""),
        "family": campaign["family"],
        "plugin_id": campaign["plugin_id"],
        "variant_id": campaign["variant_id"],
        "target_rank": (
            None if campaign["target_rank"] is None else int(campaign["target_rank"])
        ),
        "progress": progress,
        "next_action": brief.get("next_action"),
        "latest_milestone": milestone,
        "target_reached": bool(brief.get("target_reached")),
        "target_blocked_by_conflict": bool(
            brief.get("target_blocked_by_conflict")
        ),
        "analysis_issues": analysis_issues,
    }


def dashboard_research_snapshot(db):
    """Return authoritative Dashboard research state with one rank reduction pass."""
    states = list_curve_research_states(db)
    point_count = int(
        db.execute("SELECT COUNT(*) AS n FROM points").fetchone()["n"] or 0
    )
    searched_candidates = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE status!='unsearched'"
        ).fetchone()["n"]
        or 0
    )
    rank_conflicts = [
        {
            "curve_id": int(state["curve_id"]),
            "rigorous_lower": int(state["rigorous_lower"] or 0),
            "rigorous_upper": state["rigorous_upper"],
        }
        for state in states
        if state["rank_inconsistent"]
    ]
    return {
        "rank": dashboard_rank_summary(db, states=states),
        "certification": dashboard_rank_certification(db, states=states),
        "frontier": dashboard_frontier_rows(db, states=states),
        "rank_conflicts": rank_conflicts,
        "point_count": point_count,
        "searched_candidates": searched_candidates,
    }


def dashboard_overview_snapshot(db):
    """Return concise Overview state from Campaign + research authorities."""
    return {
        "campaign": _campaign_snapshot(db),
        "research": dashboard_research_snapshot(db),
    }


def _plugin_health_snapshot(db, project_root):
    records = discover_plugins(project_root)
    archived_ids = {
        str(row["plugin_id"])
        for row in db.execute(
            "SELECT plugin_id FROM plugin_states WHERE status='archived'"
        ).fetchall()
    }
    installed_plugins = [record for record in records if isinstance(record, Plugin)]
    invalid_manifests = [
        record
        for record in records
        if not isinstance(record, Plugin)
        and not (
            isinstance(record, dict)
            and str(record.get("id") or record.get("plugin_id") or "") in archived_ids
        )
    ]

    # Archived plugins are intentionally absent from Dashboard operational health.
    # Archive is the near-deletion lifecycle state: preserve provenance/history in
    # the plugin manager, but do not nag the operator about validation or health.
    plugins = [
        plugin for plugin in installed_plugins
        if not is_archived(db, plugin)
    ]
    active = [plugin for plugin in plugins if is_enabled(db, plugin)]
    invalid_states = []
    needs_revalidation = []
    for plugin in plugins:
        state = plugin_state(db, plugin.id)
        if state is not None and str(state["status"] or "").lower() == "invalid":
            invalid_states.append(plugin)
            continue
        freshness = validation_freshness(db, plugin)
        if freshness.get("state") == "needs_revalidation":
            needs_revalidation.append(plugin)

    counts = {}
    for plugin_type in ("family", "feature", "extension"):
        counts[plugin_type] = {
            "installed": sum(
                1 for plugin in plugins if plugin.plugin_type == plugin_type
            ),
            "enabled": sum(
                1 for plugin in active if plugin.plugin_type == plugin_type
            ),
        }

    preview = sorted(
        active,
        key=lambda plugin: (
            {"family": 0, "feature": 1, "extension": 2}.get(
                plugin.plugin_type, 99
            ),
            plugin.name.lower(),
            plugin.id,
        ),
    )[:3]

    issues = []
    for index, record in enumerate(invalid_manifests, start=1):
        if isinstance(record, dict):
            object_id = (
                record.get("id")
                or record.get("plugin_id")
                or record.get("path")
                or f"manifest-{index}"
            )
            reason = str(record.get("error") or "Plugin manifest is invalid.")
        else:
            object_id = f"manifest-{index}"
            reason = str(record)
        issues.append({
            "kind": "invalid_manifest",
            "plugin_id": str(object_id),
            "plugin_type": "unknown",
            "name": str(object_id),
            "reason": reason,
        })
    for plugin in invalid_states:
        issues.append({
            "kind": "invalid_state",
            "plugin_id": plugin.id,
            "plugin_type": plugin.plugin_type,
            "name": plugin.name,
            "reason": "Persisted plugin lifecycle state is invalid.",
        })
    for plugin in needs_revalidation:
        issues.append({
            "kind": "needs_revalidation",
            "plugin_id": plugin.id,
            "plugin_type": plugin.plugin_type,
            "name": plugin.name,
            "reason": "Installed plugin content changed since its last successful validation.",
        })

    return {
        "counts": counts,
        "installed": len(plugins),
        "enabled": len(active),
        "invalid_manifests": len(invalid_manifests),
        "invalid_states": len(invalid_states),
        "needs_revalidation": len(needs_revalidation),
        "attention": len(issues),
        "issues": issues,
        "active_preview": [
            {
                "id": plugin.id,
                "name": plugin.name,
                "type": plugin.plugin_type,
            }
            for plugin in preview
        ],
    }


def _cached_diagnostics_snapshot(result):
    if not isinstance(result, dict):
        return None
    counts = dict(result.get("counts") or {})
    return {
        "generated_at": result.get("generated_at"),
        "overall": result.get("overall"),
        "verification_mode": result.get("verification_mode"),
        "deep_checks_complete": bool(result.get("deep_checks_complete")),
        "counts": {
            "pass": int(counts.get("pass") or 0),
            "warn": int(counts.get("warn") or 0),
            "fail": int(counts.get("fail") or 0),
        },
    }


def _attention_item(
    severity,
    title,
    reason,
    page,
    object_type,
    object_id,
    *,
    kind,
    acknowledgeable=False,
    revision=None,
):
    item = {
        "severity": str(severity),
        "title": str(title),
        "reason": str(reason),
        "page": str(page),
        "object_type": str(object_type),
        "object_id": str(object_id),
        "kind": str(kind),
        "acknowledgeable": bool(acknowledgeable),
    }
    if acknowledgeable:
        item["attention_key"] = ":".join(
            (
                str(kind),
                str(object_type),
                str(object_id),
                str(revision or "current"),
            )
        )
    return item


def dashboard_attention_snapshot(
    db,
    project_root,
    *,
    campaign=_LOAD_CAMPAIGN,
    diagnostics_result=None,
    operations=None,
    rank_conflicts=None,
    limit=12,
):
    """Aggregate actionable Dashboard attention from owning authorities."""
    campaign = _campaign_snapshot(db) if campaign is _LOAD_CAMPAIGN else campaign
    diagnostics = _cached_diagnostics_snapshot(diagnostics_result)

    if operations is None:
        jobs = list_jobs(db, limit=100)
        runs = list_pipeline_runs_readonly(db, limit=100)
        queue = queue_counts(db)
        dispatcher = _dispatcher_health_snapshot(db, project_root)
        plugins = _plugin_health_snapshot(db, project_root)
    else:
        jobs = list(operations.get("jobs") or [])
        runs = list(operations.get("pipeline_runs") or [])
        queue = dict(operations.get("queue_counts") or {})
        dispatcher = dict(operations.get("dispatcher") or {})
        plugins = dict(operations.get("plugins") or {})
        schedules = list(operations.get("schedules") or [])
        occurrences = list(operations.get("schedule_occurrences") or [])
        if diagnostics is None:
            diagnostics = operations.get("diagnostics")

    items = []

    # Curve Research State is the authority for contradictory rigorous bounds.
    if rank_conflicts is None:
        rank_conflicts = [
            {
                "curve_id": int(state["curve_id"]),
                "rigorous_lower": int(state.get("rigorous_lower") or 0),
                "rigorous_upper": state.get("rigorous_upper"),
            }
            for state in list_curve_research_states(db)
            if state.get("rank_inconsistent")
        ]
    for state in rank_conflicts:
        curve_id = int(state["curve_id"])
        lower = int(state.get("rigorous_lower") or 0)
        upper = state.get("rigorous_upper")
        items.append(_attention_item(
            "critical",
            "Conflicting rank evidence",
            (
                f"Curve #{curve_id} has incompatible rigorous bounds"
                + (
                    f" ≥{lower} and ≤{int(upper)}."
                    if upper is not None
                    else "."
                )
            ),
            "Descent",
            "curve",
            curve_id,
            kind="evidence_conflict",
        ))

    # Campaign research brief already consumes the Analysis Planner and detailed
    # curve-analysis authority; Dashboard only surfaces its actionable state.
    if campaign is not None:
        campaign_id = int(campaign["id"])
        if campaign.get("target_reached"):
            target = campaign.get("target_rank")
            items.append(_attention_item(
                "warning",
                "Campaign target reached — review required",
                (
                    f'Campaign "{campaign["name"]}" has reached'
                    + (f" rigorous rank ≥{int(target)}" if target is not None else " its target")
                    + " and is awaiting research review."
                ),
                "Campaigns",
                "campaign",
                campaign_id,
                kind="campaign_target_review",
            ))
        for issue in campaign.get("analysis_issues") or []:
            curve_id = int(issue["curve_id"])
            if issue.get("inconsistent"):
                continue
            witness_gap = int(issue.get("witness_gap") or 0)
            actionable_points = int(issue.get("actionable_points") or 0)
            if witness_gap > 0:
                items.append(_attention_item(
                    "warning",
                    "Witness basis is incomplete",
                    (
                        f"Curve #{curve_id} is short {witness_gap} rigorous witness"
                        + ("es" if witness_gap != 1 else "")
                        + " relative to its current rigorous lower bound."
                    ),
                    "Independence",
                    "curve",
                    curve_id,
                    kind="witness_gap",
                ))
            if actionable_points > 0:
                items.append(_attention_item(
                    "warning",
                    "Exact points need independence review",
                    (
                        f"Curve #{curve_id} has {actionable_points} exact point"
                        + ("s" if actionable_points != 1 else "")
                        + " awaiting an actionable independence decision."
                    ),
                    "Independence",
                    "curve",
                    curve_id,
                    kind="unresolved_exact_points",
                ))

    for job in jobs:
        status = str(job["status"] or "")
        if status not in _ATTENTION_JOB_STATUSES:
            continue
        try:
            metadata = json.loads(_row_value(job, "metadata_json", "{}") or "{}")
        except Exception:
            metadata = {}
        if metadata.get("stop_requested_at"):
            continue
        job_id = int(job["id"])
        items.append(_attention_item(
            "critical",
            f"Job #{job_id} {status}",
            str(job["label"] or job["kind"] or "Background work") + " needs review.",
            "Jobs",
            "job",
            job_id,
            kind="job_failure",
            acknowledgeable=True,
            revision=_row_value(job, "updated_at", status),
        ))

    for run in runs:
        status = str(run["status"] or "")
        run_id = int(run["id"])
        if status in _ATTENTION_RUN_STATUSES:
            items.append(_attention_item(
                "critical",
                f"Pipeline Run #{run_id} {status}",
                f'{str(run["pipeline_name"] or "Pipeline")} stopped unsuccessfully and needs review.',
                "Pipelines",
                "pipeline_run",
                run_id,
                kind="pipeline_failure",
                acknowledgeable=True,
                revision=_row_value(run, "updated_at", status),
            ))

    waiting = sum(
        int(queue.get(state) or 0)
        for state in ("queued", "held", "claimed")
    )
    dispatcher_state = str(dispatcher.get("state") or "")
    dispatcher_available = bool(dispatcher.get("healthy")) or dispatcher_state == "starting"
    if waiting > 0 and not dispatcher_available:
        items.append(_attention_item(
            "critical",
            "Queued work has no healthy dispatcher",
            f"{waiting} queued/held/claimed item(s) are waiting while the dispatcher is unhealthy.",
            "Jobs",
            "dispatcher",
            dispatcher.get("worker_id") or dispatcher.get("pid") or "dispatcher",
            kind="dispatcher_unhealthy",
        ))

    for issue in plugins.get("issues") or []:
        plugin_id = issue.get("plugin_id") or "unknown"
        plugin_type = str(issue.get("plugin_type") or "")
        page = "Plugin Extensions" if plugin_type == "extension" else "Plugin Families"
        title = (
            "Plugin needs revalidation"
            if issue.get("kind") == "needs_revalidation"
            else "Plugin requires attention"
        )
        items.append(_attention_item(
            "warning" if issue.get("kind") == "needs_revalidation" else "critical",
            title,
            f'{issue.get("name") or plugin_id}: {issue.get("reason") or "Plugin health issue."}',
            page,
            "plugin",
            plugin_id,
            kind=str(issue.get("kind") or "plugin_issue"),
        ))

    if operations is None:
        schedules = list_schedule_errors(db, limit=100)
        occurrences = list_schedule_occurrences(db, limit=100)

    for schedule in schedules:
        if not schedule["last_error"]:
            continue
        schedule_id = int(schedule["id"])
        items.append(_attention_item(
            "critical",
            f"Schedule #{schedule_id} reported an error",
            str(schedule["last_error"]),
            "Jobs",
            "schedule",
            schedule_id,
            kind="schedule_failure",
        ))

    seen_schedule_ids = {
        item["object_id"]
        for item in items
        if item["object_type"] == "schedule"
    }
    for occurrence in occurrences:
        state = str(occurrence["state"] or "")
        error = str(occurrence["last_error"] or "").strip()
        if not error and state not in {"failed", "interrupted", "killed"}:
            continue
        schedule_id = str(int(occurrence["schedule_id"]))
        if schedule_id in seen_schedule_ids:
            continue
        seen_schedule_ids.add(schedule_id)
        items.append(_attention_item(
            "critical",
            f"Schedule #{schedule_id} occurrence needs review",
            error or f"Latest occurrence is {state}.",
            "Jobs",
            "schedule",
            schedule_id,
            kind="schedule_failure",
        ))

    if diagnostics is not None and int(diagnostics["counts"].get("fail") or 0) > 0:
        items.append(_attention_item(
            "critical",
            "Diagnostics reports failures",
            (
                f'{int(diagnostics["counts"].get("fail") or 0)} diagnostic check(s) failed'
                f' in the {diagnostics.get("verification_mode") or "latest"} snapshot.'
            ),
            "Diagnostics",
            "diagnostics",
            diagnostics.get("generated_at") or "latest",
            kind="diagnostics_failure",
        ))

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    acknowledgements = _attention_acknowledgements(db)
    deduped = []
    seen = set()
    for item in sorted(
        items,
        key=lambda item: (
            severity_order.get(item["severity"], 9),
            item["kind"],
            item["object_type"],
            item["object_id"],
        ),
    ):
        key = (item["kind"], item["object_type"], item["object_id"])
        if key in seen:
            continue
        seen.add(key)
        if str(item.get("attention_key") or "") in acknowledgements:
            continue
        deduped.append(item)
        if len(deduped) >= max(1, int(limit)):
            break
    return deduped


def dashboard_operations_snapshot(
    db,
    project_root,
    *,
    diagnostics_result=None,
    db_path=None,
):
    """Return operational state from Jobs/Queue/Pipeline/Scheduler authorities."""
    jobs = list_jobs(db, limit=100)
    job_statuses = job_status_counts(db)
    runs = list_pipeline_runs_readonly(db, limit=100)
    run_statuses = pipeline_run_status_counts_readonly(db)
    queue = queue_counts(db)
    dispatcher = _dispatcher_health_snapshot(
        db,
        project_root,
        db_path,
    )
    schedules = list_schedule_errors(db, limit=100)
    occurrences = list_schedule_occurrences(db, limit=100)
    scheduler = schedule_status_summary(db)

    unsearched = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE status='unsearched'"
        ).fetchone()["n"]
        or 0
    )
    searched = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE status!='unsearched'"
        ).fetchone()["n"]
        or 0
    )
    queue_waiting = sum(
        int(queue.get(state) or 0)
        for state in ("queued", "held")
    )

    pool_count = int(
        db.execute("SELECT COUNT(*) AS n FROM candidate_pools").fetchone()["n"] or 0
    )

    return {
        "jobs": jobs,
        "recent_jobs": jobs[:3],
        "job_counts": {
            "active": sum(int(job_statuses.get(status) or 0) for status in _ACTIVE_JOB_STATUSES),
            "completed": int(job_statuses.get("succeeded") or 0),
            "failed": sum(int(job_statuses.get(status) or 0) for status in _FAILED_JOB_STATUSES),
        },
        "pipeline_runs": runs,
        "recent_pipeline_runs": runs[:3],
        "pipeline_run_counts": {
            "active": sum(int(run_statuses.get(status) or 0) for status in _ACTIVE_RUN_STATUSES),
            "paused": int(run_statuses.get("paused") or 0),
            "completed": int(run_statuses.get("succeeded") or 0),
            "failed": sum(int(run_statuses.get(status) or 0) for status in _FAILED_RUN_STATUSES),
        },
        "queue_counts": queue,
        "queue_waiting": queue_waiting,
        "queue_claimed": int(queue.get("claimed") or 0),
        "queue_running": int(queue.get("running") or 0),
        "dispatcher": dispatcher,
        "schedules": schedules,
        "schedule_occurrences": occurrences,
        "scheduler": scheduler,
        "candidate_counts": {
            "total": unsearched + searched,
            "backlog": unsearched,
            "searched": searched,
        },
        "pool_count": pool_count,
        "plugins": _plugin_health_snapshot(db, project_root),
        "diagnostics": _cached_diagnostics_snapshot(diagnostics_result),
    }
