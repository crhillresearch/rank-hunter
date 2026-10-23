"""Set a research goal and let Gemini direct bounded Rank Hunter Pipelines."""

from __future__ import annotations

import hashlib
import html
import json
import sys

import streamlit as st

from rank42.ai_search import (
    continue_ai_campaign,
    create_ai_campaign,
    ensure_ai_schema,
    list_ai_campaigns,
    list_ai_rounds,
    plan_strategy,
    reconcile_ai_campaigns,
    strategy_options,
    update_ai_campaign,
)
from rank42.db import connect_existing
from rank42.gemini_api import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    GeminiAPIError,
    key_source,
)
from rank42.manage_store import create_campaign, update_campaign
from rank42.plugins import Plugin, discover_plugins, is_enabled
from rank42.torsion import MAZUR_TORSION_CHOICES
from rank42.settings_registry import setting_value
from rank42.ui_store import enqueue_job, get_job

from .common import PAGE_ICONS, title


_MODES = {
    "Curve": "curve",
    "Family": "family",
    "Torsion group": "torsion",
    "General curves": "general",
}


def _display_campaign_name(name):
    return str(name).replace("AI Search ·", "AI Hunt ·", 1)


def _target_controls(db, ctx, mode):
    if mode == "curve":
        top = db.execute(
            """SELECT id,family,parameter,COALESCE(descent_lower,generic_lower,0) AS lower
               FROM curves WHERE a_invariants_json IS NOT NULL
               ORDER BY lower DESC,id DESC LIMIT 1"""
        ).fetchone()
        if top is None:
            st.warning("Save a curve before starting a curve campaign.")
            return None, None
        curve_id = int(st.number_input("Stored curve ID", min_value=1,
                                       value=int(top["id"]), step=1,
                                       key="ai-search-curve-id"))
        row = db.execute(
            """SELECT id,family,parameter,COALESCE(descent_lower,generic_lower,0) AS lower
               FROM curves WHERE id=? AND a_invariants_json IS NOT NULL""",
            (curve_id,),
        ).fetchone()
        if row is None:
            st.error("That curve is not stored with an exact model.")
            return None, None
        st.caption(f"#{curve_id} · {row['family']} · t={row['parameter']} · "
                   f"stored lower bound ≥{int(row['lower'] or 0)}")
        return {"curve_id": curve_id}, {
            "curve_id": curve_id, "family": str(row["family"]),
            "stored_lower": int(row["lower"] or 0),
        }
    if mode == "family":
        lanes = []
        for plugin in discover_plugins(ctx.project_root):
            if (not isinstance(plugin, Plugin) or plugin.plugin_type != "family"
                    or "candidate_generation" not in plugin.capabilities
                    or not is_enabled(db, plugin)):
                continue
            for variant in plugin.variants:
                lanes.append((plugin, variant))
        if not lanes:
            st.warning("No enabled searchable family is installed.")
            return None, None
        selected = st.selectbox(
            "Searchable family", range(len(lanes)),
            format_func=lambda index: (
                f"{lanes[index][0].name} · {lanes[index][1].name}"
            ),
            key="ai-search-family",
        )
        plugin, variant = lanes[int(selected)]
        return {"plugin_id": plugin.id, "variant_id": variant.id}, {
            "family": plugin.name, "variant": variant.name,
            "plugin_id": plugin.id,
        }
    if mode == "torsion":
        torsion = st.selectbox("Exact torsion group", MAZUR_TORSION_CHOICES,
                               key="ai-search-torsion")
        secondary = st.toggle("Include secondary family providers", value=False,
                              key="ai-search-secondary-providers")
        return {"torsion_group": torsion,
                "include_secondary_providers": bool(secondary)}, {
                    "torsion_group": torsion,
                    "secondary_providers": bool(secondary),
                }
    pool_mode = st.selectbox("General curve pool", ["seeded", "open"],
                             key="ai-search-general-mode")
    pool_size = int(st.number_input("Pool size", min_value=10, max_value=100000,
                                    value=2000, step=100,
                                    key="ai-search-general-pool"))
    target = {"pool_mode": pool_mode, "pool_size": pool_size, "random_seed": 42}
    if pool_mode == "seeded":
        left, right = st.columns(2)
        bound = int(left.number_input("Seed range", min_value=1, max_value=100000,
                                       value=250, key="ai-search-seed-range"))
        target.update(u_min=1, u_max=bound, v_min=1, v_max=bound)
    else:
        left, right = st.columns(2)
        a_bound = int(left.number_input("A range", min_value=1, max_value=1000000,
                                         value=5000, key="ai-search-a-range"))
        b_bound = int(right.number_input("B range", min_value=1, max_value=1000000,
                                          value=5000, key="ai-search-b-range"))
        target.update(a_min=-a_bound, a_max=a_bound,
                      b_min=-b_bound, b_max=b_bound)
    return target, {"pool_mode": pool_mode, "pool_size": pool_size,
                    "ranges": {key: value for key, value in target.items()
                               if key.endswith(("_min", "_max"))}}


def _budget_controls(goal_rank):
    time_col, run_col, rounds_col = st.columns(3)
    total_minutes = int(time_col.number_input(
        "Campaign minutes", min_value=10, max_value=1440, value=240,
        step=30, key="ai-search-total-minutes"))
    run_minutes = int(run_col.number_input(
        "Minutes per Pipeline", min_value=5, max_value=240, value=45,
        step=5, key="ai-search-run-minutes"))
    rounds = int(rounds_col.number_input(
        "Maximum Pipelines", min_value=1, max_value=16, value=5,
        key="ai-search-rounds"))
    floor_key = "ai-search-retention"
    if int(st.session_state.get(floor_key, 0)) > int(goal_rank):
        st.session_state[floor_key] = min(3, int(goal_rank))
    retention = int(st.number_input(
        "Keep curves with rigorous rank ≥", min_value=0, max_value=int(goal_rank),
        value=min(3, int(goal_rank)), key=floor_key,
        help="Curves created by this hunt below this floor are removed after "
             "each Pipeline finishes. Existing curves and certified results "
             "within 10 ranks of the local best are preserved.",
    ))
    auto_continue = st.toggle(
        "Keep hunting after each checkpoint",
        value=False, key="ai-search-auto-continue",
        help="Continue automatically until the rank goal, overall time cap, "
             "overall Pipeline cap, or a manual stop.",
    )
    hard_stop_minutes = total_minutes
    hard_stop_rounds = rounds
    if auto_continue:
        hard_col, cap_col = st.columns(2)
        hard_stop_hours = int(hard_col.number_input(
            "Overall hunt time cap (hours)", min_value=1, max_value=168,
            value=max(24, (total_minutes + 59) // 60),
            key="ai-search-hard-stop-hours",
        ))
        hard_stop_rounds = int(cap_col.number_input(
            "Overall Pipeline cap", min_value=rounds, max_value=256,
            value=max(32, rounds), key="ai-search-hard-stop-rounds",
        ))
        hard_stop_minutes = max(total_minutes, hard_stop_hours * 60)
    with st.expander("Advanced limits", expanded=False, icon=":material/tune:"):
        candidate_col, scan_col, height_col = st.columns(3)
        candidates = int(candidate_col.number_input(
            "Candidates per Pipeline", min_value=1, max_value=10000,
            value=250, step=50, key="ai-search-candidates"))
        scan = int(scan_col.number_input(
            "Parameters screened", min_value=100, max_value=1000000,
            value=50000, step=10000, key="ai-search-parameters"))
        point_height = int(height_col.number_input(
            "Point height ceiling", min_value=1000, max_value=100000000,
            value=4000000, step=1000000, key="ai-search-point-height"))
        numerator_col, denominator_col = st.columns(2)
        numerator = int(numerator_col.number_input(
            "Numerator limit", min_value=10, max_value=100000,
            value=3000, step=100, key="ai-search-numerator"))
        denominator = int(denominator_col.number_input(
            "Denominator limit", min_value=10, max_value=100000,
            value=300, step=50, key="ai-search-denominator"))
        share_parameters = st.toggle(
            "Send candidate parameter values to Gemini when replanning",
            value=False, key="ai-search-share-parameters",
        )
    return {
        "goal_rank": int(goal_rank), "max_total_minutes": total_minutes,
        "max_run_minutes": min(run_minutes, total_minutes),
        "max_rounds": rounds, "max_candidates": candidates,
        "max_scan_parameters": scan, "max_point_height": point_height,
        "numerator_limit": numerator, "denominator_limit": denominator,
        "retention_floor": retention,
        "share_candidate_parameters": bool(share_parameters),
        "auto_continue": bool(auto_continue),
        "checkpoint_minutes": total_minutes,
        "checkpoint_rounds": rounds,
        "hard_stop_minutes": hard_stop_minutes,
        "hard_stop_rounds": hard_stop_rounds,
    }


def _queue_ai_job(ctx, *, ai_id, research_id, name, mode, created_at, backend):
    resource = "gpu_ratpoints" if backend == "GPU" else "sage_heavy"
    return enqueue_job(
        ctx.db_path, kind="ai_search", label=name,
        command=[sys.executable, "-u", "-m", "rank42.ai_search_runner",
                 "--project-root", str(ctx.project_root),
                 "--db", str(ctx.db_path), "--campaign-id", str(ai_id)],
        cwd=ctx.project_root,
        metadata={"campaign_id": research_id, "ai_search_campaign_id": ai_id,
                  "target_mode": mode, "queue_resource_class": resource,
                  "campaign_created_at": created_at},
        resource_class=resource,
    )


def _overview(db, campaigns, source):
    active = next((row for row in campaigns if row["status"] in {"running", "queued"}), None)
    featured = active or (campaigns[0] if campaigns else None)
    esc = html.escape
    if featured is None:
        heading = "Set a goal. Let the search adapt."
        detail = "Gemini chooses the next Pipeline from real search results. Rank Hunter verifies every rank claim."
        badge = "READY" if source else "SETUP NEEDED"
        best = "—"
        goal = "Choose a target"
        round_text = "No hunts yet"
        tone = "ready"
    else:
        config = json.loads(featured["config_json"])
        rounds = list_ai_rounds(db, int(featured["id"]))
        status = str(featured["status"])
        heading = _display_campaign_name(featured["name"])
        detail = {
            "running": "The hunt is running. Open its job to follow the work and exact evidence.",
            "queued": "The hunt is queued and will start when a worker is available.",
            "goal_met": "The rigorous rank goal was reached. Review the saved runs and evidence.",
            "killed": "The hunt was force stopped. Its completed results remain saved.",
            "stopped": "The hunt was stopped. Its completed results remain saved.",
            "budget_exhausted": "The selected limits were reached. Review the next move or continue the hunt.",
            "paused": "The hunt is paused. Its completed results remain saved.",
        }.get(status, "Review the job and saved Pipeline runs for details.")
        badge = status.replace("_", " ").upper()
        best = f"≥{int(featured['best_lower'] or 0)}"
        goal = f"≥{int(config['goal_rank'])}"
        round_text = f"{len(rounds)} / {int(config['max_rounds'])} Pipelines"
        tone = _campaign_tone(status)
    st.html(
        f'<section class="rh-ai-hero rh-ai-hero--{tone}" aria-label="AI Hunt overview">'
        '<div class="rh-ai-hero-main"><div>'
        '<span class="rh-ai-eyebrow">AUTONOMOUS RANK HUNT</span>'
        f'<h2>{esc(heading)}</h2><p>{esc(detail)}</p></div>'
        f'<span class="rh-ai-status">{esc(badge)}</span></div>'
        '<div class="rh-ai-hero-facts">'
        f'<div><span>BEST CERTIFIED</span><strong>{esc(best)}</strong></div>'
        f'<div><span>RANK GOAL</span><strong>{esc(goal)}</strong></div>'
        f'<div><span>PROGRESS</span><strong>{esc(round_text)}</strong></div>'
        '</div></section>'
    )


def _campaign_tone(status):
    if status == "goal_met":
        return "success"
    if status in {"killed", "stopped", "failed", "interrupted"}:
        return "danger"
    if status in {"paused", "budget_exhausted"}:
        return "warning"
    return "active"


def _recent_campaigns(ctx):
    connection = connect_existing(ctx.db_path)
    try:
        rows = list_ai_campaigns(connection, limit=8)
        heading, refresh = st.columns([5, 1], vertical_alignment="center")
        heading.markdown("### Your hunts")
        refresh.button("Refresh", icon=":material/refresh:",
                       key="ai-search-refresh-campaigns", width="stretch")
        if not rows:
            st.info("No hunts yet. Create one in New hunt to start searching.")
            return
        for row in rows:
            with st.container(key=f"rh-ai-campaign-{int(row['id'])}", border=True):
                head, open_col = st.columns([5, 1], vertical_alignment="center")
                head.markdown(f"**#{int(row['id'])} · {_display_campaign_name(row['name'])}**")
                if row["job_id"] and open_col.button(
                    "Open job", icon=":material/terminal:",
                    key=f"ai-search-open-job-{int(row['id'])}", width="stretch",
                ):
                    job = get_job(connection, int(row["job_id"]))
                    status = str(job["status"]) if job else ""
                    st.session_state["manage_job_id"] = int(row["job_id"])
                    st.session_state["jobs_section_pending"] = (
                        "Queue" if status == "queued" else
                        "Active" if status in {"running", "stopping", "paused"} else
                        "History"
                    )
                    st.session_state["rh_page"] = "Jobs"
                    st.rerun()
                rounds = list_ai_rounds(connection, int(row["id"]))
                config = json.loads(row["config_json"])
                status = str(row["status"]).replace("_", " ").title()
                tone = _campaign_tone(str(row["status"]))
                st.html(
                    '<div class="rh-ai-campaign-facts">'
                    f'<span class="rh-ai-campaign-status rh-ai-campaign-status--{tone}">'
                    f'{html.escape(status)}</span>'
                    f'<span>Best certified <strong>≥{int(row["best_lower"] or 0)}</strong></span>'
                    f'<span>Goal <strong>≥{int(config["goal_rank"])}</strong></span>'
                    f'<span>Pipelines <strong>{len(rounds)} / {int(config["max_rounds"])}</strong></span>'
                    '</div>'
                )
                if rounds:
                    latest = rounds[-1]
                    decision = json.loads(latest["decision_json"])
                    recipe = str(decision["recipe_id"]).split(":", 1)[-1].replace("_", " ").title()
                    st.caption(
                        f"Latest · Round {latest['round_number']} · {recipe} · "
                        f"{decision['reason']}"
                    )
                if row["next_decision_json"]:
                    next_move = json.loads(row["next_decision_json"])
                    st.info(
                        f"Next move · {next_move['recipe_id']} · "
                        f"{next_move['reason']}"
                    )
                if row["status"] == "budget_exhausted":
                    if st.button("Continue hunt · +4 hours / 5 Pipelines",
                                 key=f"ai-search-continue-{int(row['id'])}",
                                 icon=":material/play_arrow:", type="primary"):
                        try:
                            continue_ai_campaign(connection, int(row["id"]))
                            research = connection.execute(
                                "SELECT created_at FROM research_campaigns WHERE id=?",
                                (int(row["research_campaign_id"]),),
                            ).fetchone()
                            backend = str(setting_value(
                                connection, "ratpoints_backend", "CPU") or "CPU"
                            ).upper()
                            job_id = _queue_ai_job(
                                ctx, ai_id=int(row["id"]),
                                research_id=int(row["research_campaign_id"]),
                                name=str(row["name"]), mode=str(row["target_mode"]),
                                created_at=str(research["created_at"]), backend=backend,
                            )
                        except Exception as exc:
                            update_ai_campaign(connection, int(row["id"]),
                                               status="budget_exhausted",
                                               error=str(exc)[:500])
                            st.error(f"Could not continue hunt: {exc}")
                        else:
                            update_ai_campaign(connection, int(row["id"]), job_id=job_id)
                            st.rerun()
                if row["error"]:
                    st.error(str(row["error"]))
    finally:
        connection.close()


def page(db, ctx):
    ensure_ai_schema(db)
    reconcile_ai_campaigns(db)
    source = key_source(ctx.project_root)
    campaigns = list_ai_campaigns(db, limit=8)
    st.session_state.setdefault(
        "ai-search-view-v2", "Campaigns" if campaigns else "New hunt"
    )
    title("Autonomous Rank Hunt", "Plan and run an adaptive rank hunt.",
          "Search", icon=PAGE_ICONS["AI Hunt"])
    _overview(db, campaigns, source)
    view = st.segmented_control(
        "AI Hunt view", ["Campaigns", "New hunt"],
        default=None,
        key="ai-search-view-v2",
    ) or "New hunt"
    if view == "Campaigns":
        _recent_campaigns(ctx)
        return

    if not source:
        with st.container(key="rh-ai-setup", border=True):
            st.warning("Add a Gemini API key in Settings → Services to launch a hunt.")
            if st.button("Open Services", icon=":material/settings:",
                         key="ai-search-open-services"):
                st.session_state["rh_page"] = "Settings"
                st.session_state["settings_view"] = "Services"
                st.rerun()

    with st.container(key="rh-ai-target-card", border=True):
        st.markdown("### Choose a target")
        st.caption("Start from a saved curve, a searchable family, an exact torsion group, or a general pool.")
        mode_label = st.segmented_control(
            "Research target", list(_MODES), default="Curve",
            key="ai-search-target-mode-v2",
        ) or "Curve"
        mode = _MODES[mode_label]
        target, summary = _target_controls(db, ctx, mode)
    if target is None:
        return

    with st.container(key="rh-ai-plan-card", border=True):
        st.markdown("### Set the goal")
        prompt_presets = {
            "Custom": None,
            "Raise certified rank": "Find a curve with a higher rigorously certified rank. Change strategies when searches stop finding independent points.",
            "Find independent points": "Search for exact rational points outside the known subgroup. Certify independence before reporting rank growth, and change search charts when a region is exhausted.",
            "Find a rank jump": "Search specializations of this family where new exact rational points raise the rigorous rank above the specialized known sections. Verify every claimed jump.",
            "Explore transformations": "Try compatible twists, base changes, and geometric constructions that could expose new points. Compare the results with the original curve or family using exact evidence.",
        }
        preset = st.selectbox(
            "Research prompt", list(prompt_presets), index=0,
            key="ai-search-prompt-preset",
            help="Choose a starting goal or write your own below.",
        )
        if preset != st.session_state.get("ai-search-last-prompt-preset"):
            if preset != "Custom":
                st.session_state["ai-search-objective"] = prompt_presets[preset]
            st.session_state["ai-search-last-prompt-preset"] = preset
        objective = st.text_area(
            "What should Gemini try to discover?",
            value="Find a curve with a higher rigorously certified rank. Change "
                  "strategies when searches stop finding independent points.",
            height=84, key="ai-search-objective",
        ).strip()
        goal_col, model_col = st.columns(2)
        goal_rank = int(goal_col.number_input(
            "Rigorous rank goal", min_value=1, max_value=100, value=28,
            key="ai-search-rank-goal",
        ))
        model = model_col.selectbox(
            "Gemini model", AVAILABLE_MODELS,
            index=AVAILABLE_MODELS.index(DEFAULT_MODEL),
            key="ai-search-model-v2",
        )
        st.caption("Gemini chooses a strategy. Rank Hunter checks points and rank with exact evidence.")

    with st.container(key="rh-ai-limits-card", border=True):
        st.markdown("### Set the limits")
        st.caption("A checkpoint limits each block of work. The hunt stops at its overall cap or rank goal.")
        config = _budget_controls(goal_rank)
        config["target_summary"] = summary

    options = strategy_options(db, target_mode=mode, target=target)
    if not options:
        st.error("No compatible Rank Hunter Pipeline is available for this target.")
        return
    with st.container(key="rh-ai-launch-card", border=True):
        st.markdown("### Launch the hunt")
        st.caption(f"{len(options)} compatible Pipelines are available. Preview the first choice or start now.")
        fingerprint = hashlib.sha256(json.dumps(
            {"mode": mode, "target": target, "objective": objective,
             "config": config, "model": model}, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        preview = st.session_state.get("ai-search-preview")
        if preview and preview.get("fingerprint") != fingerprint:
            preview = None
            st.session_state.pop("ai-search-preview", None)
        preview_col, start_col = st.columns([1, 2], vertical_alignment="bottom")
        if preview_col.button("Preview Gemini strategy", icon=":material/auto_awesome:",
                              width="stretch", disabled=not source or not objective,
                              key="ai-search-preview-button"):
            try:
                with st.spinner("Asking Gemini to choose the first Pipeline…"):
                    decision = plan_strategy(
                        ctx.project_root, model=model, objective=objective,
                        target_mode=mode, target_summary=summary,
                        goal_rank=goal_rank, config=config, options=options,
                    )
            except (GeminiAPIError, ValueError) as exc:
                st.error(str(exc))
            else:
                preview = {"fingerprint": fingerprint, "decision": decision}
                st.session_state["ai-search-preview"] = preview
        if preview:
            decision = preview["decision"]
            chosen = next(rec for rec in options if rec["id"] == decision["recipe_id"])
            model_used = decision.get("model_used", model)
            st.info(
                f"First strategy: **{chosen['name']}** · depth {decision['depth']} "
                f"· {decision['window']} window · up to {decision['candidate_limit']} candidates. "
                f"{decision['reason']}"
                + (f" Gemini 3.8 Flash was unavailable; {model_used} answered."
                   if model_used != model else "")
            )
        if start_col.button("Start AI campaign", type="primary",
                            icon=":material/auto_awesome:", width="stretch",
                            disabled=not source or not objective,
                            key="ai-search-start-button"):
            name = f"AI Hunt · {mode_label} · rank ≥{goal_rank}"
            config["db_path"] = str(ctx.db_path.resolve())
            if preview:
                config["initial_decision"] = preview["decision"]
            research_id = create_campaign(
                db, name=name, objective=objective, target_rank=goal_rank,
                plugin_id=target.get("plugin_id"),
                variant_id=target.get("variant_id"),
                family=summary.get("family"),
            )
            ai_id = create_ai_campaign(
                db, research_campaign_id=research_id, name=name,
                objective=objective, target_mode=mode, target=target,
                config=config, model=model,
            )
            backend = str(setting_value(db, "ratpoints_backend", "CPU") or "CPU").upper()
            try:
                job_id = _queue_ai_job(
                    ctx, ai_id=ai_id, research_id=research_id, name=name, mode=mode,
                    created_at=str(db.execute(
                        "SELECT created_at FROM research_campaigns WHERE id=?",
                        (research_id,),
                    ).fetchone()["created_at"]), backend=backend,
                )
            except Exception as exc:
                update_ai_campaign(db, ai_id, status="failed", error=str(exc)[:500])
                update_campaign(db, research_id, status="paused")
                st.error(f"Could not queue AI Hunt: {exc}")
            else:
                update_ai_campaign(db, ai_id, job_id=job_id)
                st.session_state.pop("ai-search-preview", None)
                st.success(f"AI campaign #{ai_id} queued as job #{job_id}.")
                st.rerun()

