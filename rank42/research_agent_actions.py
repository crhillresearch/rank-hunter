"""Reviewed, allowlisted management actions for Rank Hunter Agent."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from rank42.ai_search import (continue_ai_campaign, create_ai_campaign,
                              get_ai_campaign, strategy_options, update_ai_campaign)
from rank42.db import get_curve
from rank42.ai_provider import default_model, generate_json, selected_service
from rank42.manage_store import (create_campaign, ensure_manage_schema,
                                 get_campaign, pause_job, resume_job, update_campaign)
from rank42.plugins import (Plugin, discover_plugins, is_archived, is_enabled,
                            archive_plugin, restore_plugin, set_plugin_state)
from rank42.settings_registry import setting_value
from rank42.torsion import MAZUR_TORSION_CHOICES, canonical_torsion_label
from rank42.ui_store import (cancel_queue_job, enqueue_job, get_job,
                             hold_queue_job, release_queue_job, stop_job)


_INTENT = re.compile(r"\b(?:create|make|start|launch|run|enable|disable|activate|restore|"
                     r"archive|sync|refresh|pause|resume|continue|stop|cancel|hold|release|update|rename|set|change|"
                     r"turn\s+(?:on|off))\b", re.I)
_OBJECT = re.compile(r"\b(?:campaign|hunt|search|plugin|extension|family|job|queue|icarm|catalog)\b", re.I)
_ICARM_SYNC = re.compile(r"\b(?:sync|refresh|update)\b.{0,35}\bicarm\b", re.I | re.S)


def _active_icarm_sync(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_jobs'").fetchone() is None:
        return None
    return db.execute(
        """SELECT id FROM ui_jobs WHERE kind='catalog_sync'
           AND label='Sync ICARM catalog'
           AND status IN ('queued','running','stopping')
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()


def _plugins(project_root):
    return [p for p in discover_plugins(project_root) if isinstance(p, Plugin)]


def _plugin_by_name(project_root, value):
    needle = str(value or "").strip().casefold()
    if not needle:
        raise ValueError("Which plugin should I change? Name an installed plugin.")
    plugins = _plugins(project_root)
    matches = [p for p in plugins if needle in {p.id.casefold(), p.name.casefold()}]
    if not matches:
        matches = [p for p in plugins if needle in p.name.casefold() or needle in p.id.casefold()]
    if len(matches) != 1:
        raise ValueError("Plugin name is ambiguous or not installed. Use its exact name from Plugins.")
    return matches[0]


def plan_action(db, project_root, *, message, active_curve_id=None, history=None):
    """Return a reviewable plan; this function never writes or queues work."""
    message = str(message or "").strip()[:1500]
    if not _INTENT.search(message) or not _OBJECT.search(message):
        return None
    if _ICARM_SYNC.search(message) and not re.match(r"^\s*(?:how|why|what|when)\b", message, re.I):
        active = _active_icarm_sync(db)
        if active is not None:
            raise ValueError(f"ICARM catalog sync is already active as job #{int(active['id'])}.")
        snapshot = db.execute(
            "SELECT fetched_at FROM external_catalogs WHERE source='icarm'"
        ).fetchone()
        last_sync = str(snapshot["fetched_at"] or "")[:19] if snapshot else "never"
        return {"kind": "sync_icarm", "preview":
                "Sync the public ICARM catalog into Rank Hunter as a queued job. "
                f"Last local sync: {last_sync}. This updates the external catalog snapshot; "
                "it does not submit or change your curves."}
    plugins = [{"id": p.id, "name": p.name, "type": p.plugin_type,
                "enabled": is_enabled(db, p)} for p in _plugins(project_root)]
    prompt = (
        "Classify the user's requested Rank Hunter action. Return JSON with action "
        "(create_campaign, update_campaign, start_ai_hunt, enable_plugin, "
        "disable_plugin, restore_plugin, archive_plugin, continue_ai_hunt, pause_job, resume_job, stop_job, "
        "hold_job, release_job, cancel_job, or none), "
        "name, objective, notes, plugin, campaign_id, ai_hunt_id, campaign_status "
        "(active/paused/completed/archived), job_id, target_mode "
        "(curve/family/torsion/general), curve_id, variant_id, torsion_group, "
        "goal_rank. Use null for missing values. "
        "Only classify an explicitly requested action. A question about an action is none. "
        "Do not invent a curve, job, campaign, or plugin ID. "
        "For update_campaign, include only fields the user asked to change. "
        "Recent conversation is context only.\n"
        f"Installed plugins: {json.dumps(plugins, ensure_ascii=True)[:6000]}\n"
        f"Active curve ID: {active_curve_id}\n"
        f"Recent chat: {json.dumps((history or [])[-4:], ensure_ascii=True)[:3000]}\n"
        f"User: {json.dumps(message)}"
    )
    raw = generate_json(project_root, prompt, db=db)
    action = str(raw.get("action") or "none")
    if action == "none":
        return None
    if action in {"enable_plugin", "disable_plugin", "restore_plugin", "archive_plugin"}:
        plugin = _plugin_by_name(project_root, raw.get("plugin"))
        if not any(label.casefold() in message.casefold() for label in (plugin.id, plugin.name)):
            raise ValueError("Name the installed plugin you want to change.")
        if action == "enable_plugin" and is_archived(db, plugin):
            raise ValueError(f"{plugin.name} is archived. Restore it on the Plugins page first.")
        if action == "restore_plugin" and not is_archived(db, plugin):
            raise ValueError(f"{plugin.name} is not archived.")
        if action == "archive_plugin" and is_archived(db, plugin):
            raise ValueError(f"{plugin.name} is already archived.")
        enabled = is_enabled(db, plugin)
        if action in {"enable_plugin", "disable_plugin"} and enabled == (action == "enable_plugin"):
            raise ValueError(f"{plugin.name} is already {'enabled' if enabled else 'disabled'}.")
        label = {"enable_plugin": "Validate and enable", "disable_plugin": "Disable",
                 "restore_plugin": "Restore", "archive_plugin": "Archive"}[action]
        return {"kind": action, "plugin_id": plugin.id, "plugin_name": plugin.name,
                "preview": f"{label} **{plugin.name}** ({plugin.plugin_type}). "
                           + ("Validation runs as a queued job; activation follows only if it passes."
                              if action == "enable_plugin" else
                              "It will return to installed but disabled state."
                              if action == "restore_plugin" else
                              "It will be hidden from normal plugin use."
                              if action == "archive_plugin" else
                              "New searches will stop using this plugin.")}
    if action == "create_campaign":
        name = str(raw.get("name") or "").strip()[:100]
        objective = str(raw.get("objective") or "").strip()[:500]
        if not name:
            raise ValueError("Give the campaign a name so I can create it.")
        goal = _goal(raw.get("goal_rank"), optional=True)
        return {"kind": action, "name": name, "objective": objective,
                "goal_rank": goal,
                "preview": f"Create campaign **{name}**" + (f" with rank goal ≥{goal}" if goal else "")
                           + (f". {objective}" if objective else ".")}
    if action == "update_campaign":
        campaign_id = _explicit_id(message, raw.get("campaign_id"), "campaign")
        campaign = get_campaign(db, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} was not found.")
        changes = {}
        for key in ("name", "objective", "notes", "campaign_status", "goal_rank"):
            if raw.get(key) not in (None, ""):
                field = {"campaign_status": "status", "goal_rank": "target_rank"}.get(key, key)
                changes[field] = str(raw[key]).strip()[:1000] if field in {"name", "objective", "notes"} else raw[key]
        if "status" in changes and changes["status"] not in {"active", "paused", "completed", "archived"}:
            raise ValueError("Campaign status must be active, paused, completed, or archived.")
        if "target_rank" in changes:
            changes["target_rank"] = _goal(changes["target_rank"])
        if not changes:
            raise ValueError("Say what should change in the campaign.")
        summary = ", ".join(f"{key.replace('_', ' ')} → {value}" for key, value in changes.items())
        return {"kind": action, "campaign_id": campaign_id, "changes": changes,
                "preview": f"Update campaign **#{campaign_id} · {campaign['name']}**: {summary}."}
    if action == "continue_ai_hunt":
        ai_id = _explicit_id(message, raw.get("ai_hunt_id"), "hunt")
        hunt = get_ai_campaign(db, ai_id)
        if hunt is None or hunt["status"] != "budget_exhausted":
            raise ValueError(f"AI Hunt #{ai_id} has not exhausted its budget or was not found.")
        return {"kind": action, "ai_hunt_id": ai_id,
                "preview": f"Continue **AI Hunt #{ai_id} · {str(hunt['name'])[:100]}** "
                           "for up to 60 more minutes and 2 more Pipelines."}
    if action in {"pause_job", "resume_job", "stop_job", "hold_job", "release_job", "cancel_job"}:
        job_id = _job_target(db, message, raw.get("job_id"), action)
        row = get_job(db, job_id)
        if row is None:
            raise ValueError(f"Job #{job_id} was not found.")
        status = str(row["status"])
        if action in {"pause_job", "stop_job"} and status not in {"queued", "running", "stopping"}:
            raise ValueError(f"Job #{job_id} is already {status}.")
        if action == "resume_job" and status in {"queued", "running", "stopping", "succeeded"}:
            raise ValueError(f"Job #{job_id} cannot be resumed from {status}.")
        if action in {"hold_job", "release_job", "cancel_job"} and status != "queued":
            raise ValueError(f"Job #{job_id} is not queued.")
        labels = {"pause_job": "Pause", "resume_job": "Resume", "stop_job": "Stop",
                  "hold_job": "Hold in queue", "release_job": "Release from hold",
                  "cancel_job": "Cancel queued"}
        return {"kind": action, "job_id": job_id,
                "preview": f"{labels[action]} **job #{job_id} · {str(row['label'])[:100]}** (currently {status})."}
    if action == "start_ai_hunt":
        return _plan_hunt(db, project_root, raw, active_curve_id, message)
    return None


def _goal(value, *, optional=False):
    if value in (None, ""):
        return None if optional else 28
    try:
        goal = int(value)
    except (TypeError, ValueError):
        raise ValueError("The rank goal must be an integer between 1 and 100.") from None
    if not 1 <= goal <= 100:
        raise ValueError("The rank goal must be between 1 and 100.")
    return goal


def _explicit_id(message, value, noun):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Name the {noun} ID.") from None
    if number <= 0 or not re.search(rf"(?:{noun}\s*#?\s*|#){number}\b", message, re.I):
        raise ValueError(f"Name the {noun} ID in your message.")
    return number


def _job_target(db, message, value, action):
    if re.search(r"(?:job\s*#?\s*|#)\d+\b", message, re.I):
        return _explicit_id(message, value, "job")
    if action not in {"pause_job", "stop_job"} or not re.search(r"\b(?:current|active|running)\b", message, re.I):
        raise ValueError("Name the job ID.")
    rows = db.execute(
        "SELECT id FROM ui_jobs WHERE status IN ('queued','running','stopping') ORDER BY id DESC LIMIT 2"
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("More than one active job is present. Name the job ID.")
    job_id = int(rows[0]["id"])
    if value not in (None, "", job_id, str(job_id)):
        raise ValueError("The suggested job does not match the only active job.")
    return job_id


def _plan_hunt(db, project_root, raw, active_curve_id, message):
    mode = str(raw.get("target_mode") or "").lower()
    if mode not in {"curve", "family", "torsion", "general"}:
        raise ValueError("Choose a curve, searchable family, torsion group, or general curves for the hunt.")
    target = {}
    summary = {}
    if mode == "curve":
        curve_id = raw.get("curve_id") or active_curve_id
        try:
            curve_id = int(curve_id)
        except (TypeError, ValueError):
            raise ValueError("Name a stored curve ID for the AI Hunt.") from None
        curve = get_curve(db, curve_id)
        if curve_id != active_curve_id and not re.search(rf"(?:#|\bcurve\s*){curve_id}\b", message, re.I):
            raise ValueError("Name the stored curve ID for this hunt.")
        if curve is None or not curve["a_invariants_json"]:
            raise ValueError(f"Curve #{curve_id} needs a stored exact model before AI Hunt can use it.")
        target = {"curve_id": curve_id}
        summary = {"curve_id": curve_id, "family": str(curve["family"]),
                   "stored_lower": int(curve["descent_lower"] or curve["generic_lower"] or 0)}
        target_label = f"curve #{curve_id}"
    elif mode == "family":
        plugin = _plugin_by_name(project_root, raw.get("plugin"))
        if not any(label.casefold() in message.casefold() for label in (plugin.id, plugin.name)):
            raise ValueError("Name the searchable family for this hunt.")
        if plugin.plugin_type != "family" or "candidate_generation" not in plugin.capabilities or not is_enabled(db, plugin):
            raise ValueError(f"{plugin.name} is not an enabled searchable family.")
        variant_id = str(raw.get("variant_id") or "")
        if not variant_id and len(plugin.variants) == 1:
            variant_id = plugin.variants[0].id
        variant = next((v for v in plugin.variants if v.id == variant_id or v.name.casefold() == variant_id.casefold()), None)
        if variant is None:
            raise ValueError(f"Name a variant of {plugin.name} for the AI Hunt.")
        target = {"plugin_id": plugin.id, "variant_id": variant.id}
        summary = {"family": plugin.name, "variant": variant.name,
                   "plugin_id": plugin.id, "formula_family": str(variant.family_spec).startswith("json:"),
                   "plugin_capabilities": sorted(plugin.capabilities)}
        target_label = f"{plugin.name} · {variant.name}"
    elif mode == "torsion":
        try:
            torsion = canonical_torsion_label(str(raw.get("torsion_group") or ""))
        except ValueError:
            raise ValueError("Name an exact Mazur torsion group for the hunt.") from None
        if torsion not in MAZUR_TORSION_CHOICES:
            raise ValueError("That torsion group is not available in AI Hunt.")
        if not re.search(re.escape(str(raw.get("torsion_group") or "")), message, re.I):
            raise ValueError("Name the torsion group for this hunt.")
        target = {"torsion_group": torsion, "include_secondary_providers": False}
        summary = {"torsion_group": torsion, "secondary_providers": False}
        target_label = torsion
    else:
        target = {"pool_mode": "seeded", "pool_size": 200,
                  "random_seed": 42, "u_min": 1, "u_max": 100,
                  "v_min": 1, "v_max": 100}
        summary = {"pool_mode": "seeded", "pool_size": 200,
                   "ranges": {"u_min": 1, "u_max": 100, "v_min": 1, "v_max": 100}}
        target_label = "general seeded curves"
    if not strategy_options(db, target_mode=mode, target=target):
        raise ValueError(f"No compatible Pipeline is available for {target_label}.")
    goal = _goal(raw.get("goal_rank"))
    objective = str(raw.get("objective") or "Find a higher rigorously certified rank.").strip()[:500]
    if not objective:
        objective = "Find a higher rigorously certified rank."
    name = str(raw.get("name") or f"AI Hunt · {target_label} · rank ≥{goal}").strip()[:100]
    config = {"goal_rank": goal, "max_total_minutes": 60, "max_run_minutes": 20,
              "max_rounds": 2, "max_candidates": 100, "max_scan_parameters": 10000,
              "max_point_height": 1000000, "numerator_limit": 1000,
              "denominator_limit": 100, "retention_floor": min(3, goal),
              "share_candidate_parameters": False, "allow_pipeline_build": True,
              "auto_continue": False, "checkpoint_minutes": 60,
              "checkpoint_rounds": 2, "hard_stop_minutes": 60,
              "hard_stop_rounds": 2, "target_summary": summary}
    return {"kind": "start_ai_hunt", "name": name, "objective": objective,
            "target_mode": mode, "target": target, "goal_rank": goal, "config": config,
            "preview": f"Start **{name}** on **{target_label}**. Goal: rigorous rank ≥{goal}. "
                       f"Objective: {objective} "
                       "Limit: 60 minutes, 2 Pipelines, 100 candidates per Pipeline. "
                       f"Keep new curves at rank ≥{min(3, goal)}; Rank Hunter's lead-preservation rule also applies."}


def execute_action(db, ctx, plan):
    """Execute only a previously reviewed plan, rechecking mutable targets."""
    kind = plan.get("kind")
    if kind == "sync_icarm":
        active = _active_icarm_sync(db)
        if active is not None:
            raise ValueError(f"ICARM sync is already active as job #{int(active['id'])}.")
        detected = getattr(ctx, "detected_science_python", None)
        fallback = detected() if callable(detected) else sys.executable
        python = str(setting_value(db, "science_python", fallback) or fallback)
        job_id = enqueue_job(ctx.db_path, kind="catalog_sync", label="Sync ICARM catalog",
            command=[python, "-m", "rank42.catalog", "--db", str(ctx.db_path),
                     "sync", "--source", "icarm"], cwd=ctx.project_root)
        return f"ICARM catalog sync queued as job #{job_id}."
    if kind in {"enable_plugin", "disable_plugin"}:
        plugin = _plugin_by_name(ctx.project_root, plan.get("plugin_id"))
        if kind == "disable_plugin":
            if not is_enabled(db, plugin):
                raise ValueError("Plugin is already disabled.")
            set_plugin_state(db, plugin.id, enabled=False, status="disabled")
            return f"Disabled {plugin.name}."
        if is_archived(db, plugin) or is_enabled(db, plugin):
            raise ValueError("Plugin state changed. Ask me again to review it.")
        python = (str(setting_value(db, "science_python", sys.executable) or sys.executable)
                  if plugin.plugin_type == "family" else sys.executable)
        job_id = enqueue_job(ctx.db_path, kind="plugin_validate",
            label=f"Activate {plugin.plugin_type} · {plugin.name}",
            command=[python, "-m", "rank42.plugin_validate", "--project-root",
                     str(ctx.project_root), "--db", str(ctx.db_path), "--plugin", plugin.id],
            cwd=ctx.project_root)
        return f"Validation and activation for {plugin.name} queued as job #{job_id}."
    if kind == "restore_plugin":
        plugin = _plugin_by_name(ctx.project_root, plan.get("plugin_id"))
        if not is_archived(db, plugin):
            raise ValueError("Plugin state changed. Ask me again to review it.")
        restore_plugin(db, plugin)
        return f"Restored {plugin.name} in disabled state."
    if kind == "archive_plugin":
        plugin = _plugin_by_name(ctx.project_root, plan.get("plugin_id"))
        if is_archived(db, plugin):
            raise ValueError("Plugin is already archived.")
        archive_plugin(db, plugin)
        return f"Archived {plugin.name}."
    if kind == "create_campaign":
        ensure_manage_schema(db)
        campaign_id = create_campaign(db, name=plan["name"], objective=plan["objective"],
                                      target_rank=plan.get("goal_rank"))
        return f"Created research campaign #{campaign_id}: {plan['name']}."
    if kind == "update_campaign":
        campaign_id = int(plan["campaign_id"])
        if get_campaign(db, campaign_id) is None:
            raise ValueError("Campaign is no longer available.")
        update_campaign(db, campaign_id, **dict(plan["changes"]))
        return f"Updated research campaign #{campaign_id}."
    if kind == "continue_ai_hunt":
        ai_id = int(plan["ai_hunt_id"])
        hunt = get_ai_campaign(db, ai_id)
        if hunt is None or hunt["status"] != "budget_exhausted":
            raise ValueError("AI Hunt state changed. Ask me again to review it.")
        continue_ai_campaign(db, ai_id, minutes=60, rounds=2)
        research_id = int(hunt["research_campaign_id"])
        created = db.execute("SELECT created_at FROM research_campaigns WHERE id=?", (research_id,)).fetchone()[0]
        backend = str(setting_value(db, "ratpoints_backend", "CPU") or "CPU").upper()
        resource = "gpu_ratpoints" if backend == "GPU" else "sage_heavy"
        try:
            job_id = enqueue_job(ctx.db_path, kind="ai_search", label=str(hunt["name"]),
                command=[sys.executable, "-u", "-m", "rank42.ai_search_runner",
                         "--project-root", str(ctx.project_root), "--db", str(ctx.db_path),
                         "--campaign-id", str(ai_id)], cwd=ctx.project_root,
                metadata={"campaign_id": research_id, "ai_search_campaign_id": ai_id,
                          "target_mode": str(hunt["target_mode"]),
                          "queue_resource_class": resource,
                          "campaign_created_at": str(created)}, resource_class=resource)
        except Exception as exc:
            update_ai_campaign(db, ai_id, status="budget_exhausted", error=str(exc)[:500])
            raise
        update_ai_campaign(db, ai_id, job_id=job_id)
        return f"AI Hunt #{ai_id} continued as job #{job_id}."
    if kind in {"pause_job", "resume_job", "stop_job", "hold_job", "release_job", "cancel_job"}:
        job_id = int(plan["job_id"])
        row = get_job(db, job_id)
        if row is None:
            raise ValueError("Job is no longer available.")
        if kind == "pause_job":
            pause_job(db, job_id)
            return f"Paused job #{job_id}."
        if kind == "stop_job":
            stop_job(db, job_id, force=False)
            return f"Stop requested for job #{job_id}."
        if kind == "resume_job":
            new_id = resume_job(db, ctx.db_path, job_id)
            return f"Resumed job #{job_id} as job #{new_id}."
        operation = {"hold_job": hold_queue_job, "release_job": release_queue_job,
                     "cancel_job": cancel_queue_job}[kind]
        if not operation(db, job_id):
            raise ValueError("Queue state changed. Ask me again to review it.")
        return {"hold_job": "Held", "release_job": "Released",
                "cancel_job": "Cancelled"}[kind] + f" job #{job_id}."
    if kind == "start_ai_hunt":
        mode, target = plan["target_mode"], dict(plan["target"])
        if mode == "curve":
            curve = get_curve(db, int(target["curve_id"]))
            if curve is None or not curve["a_invariants_json"]:
                raise ValueError("Target curve is no longer available.")
        elif mode == "family":
            plugin = _plugin_by_name(ctx.project_root, target["plugin_id"])
            if not is_enabled(db, plugin) or not any(v.id == target["variant_id"] for v in plugin.variants):
                raise ValueError("Target family is no longer enabled or its variant changed.")
        if not strategy_options(db, target_mode=mode, target=target):
            raise ValueError("No compatible Pipeline is available now.")
        config = dict(plan["config"])
        config["db_path"] = str(Path(ctx.db_path).resolve())
        ensure_manage_schema(db)
        research_id = create_campaign(db, name=plan["name"], objective=plan["objective"],
            target_rank=plan["goal_rank"], plugin_id=target.get("plugin_id"),
            variant_id=target.get("variant_id"),
            family=config.get("target_summary", {}).get("family"))
        ai_id = create_ai_campaign(db, research_campaign_id=research_id,
            name=plan["name"], objective=plan["objective"], target_mode=mode,
            target=target, config=config, model=default_model(selected_service(db), db=db))
        created = db.execute("SELECT created_at FROM research_campaigns WHERE id=?", (research_id,)).fetchone()[0]
        backend = str(setting_value(db, "ratpoints_backend", "CPU") or "CPU").upper()
        resource = "gpu_ratpoints" if backend == "GPU" else "sage_heavy"
        try:
            job_id = enqueue_job(ctx.db_path, kind="ai_search", label=plan["name"],
                command=[sys.executable, "-u", "-m", "rank42.ai_search_runner",
                         "--project-root", str(ctx.project_root), "--db", str(ctx.db_path),
                         "--campaign-id", str(ai_id)], cwd=ctx.project_root,
                metadata={"campaign_id": research_id, "ai_search_campaign_id": ai_id,
                          "target_mode": mode, "queue_resource_class": resource,
                          "campaign_created_at": str(created)}, resource_class=resource)
        except Exception as exc:
            update_ai_campaign(db, ai_id, status="failed", error=str(exc)[:500])
            update_campaign(db, research_id, status="paused")
            raise
        update_ai_campaign(db, ai_id, job_id=job_id)
        return f"AI Hunt #{ai_id} queued as job #{job_id}; research campaign #{research_id} created."
    raise ValueError("Unsupported Agent action.")


def audit_action(project_root, plan, outcome, detail):
    target = Path(project_root) / ".rank42-ui" / "agent-actions.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(),
              "action": str(plan.get("kind") or "unknown")[:40],
              "target": str(plan.get("plugin_id") or plan.get("name") or "")[:100],
              "outcome": str(outcome)[:30], "detail": str(detail)[:300]}
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=True) + "\n")
