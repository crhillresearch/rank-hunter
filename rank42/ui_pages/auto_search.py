from __future__ import annotations

import json

import streamlit as st

from rank42.auto_search_state import (
    ensure_auto_search_schema,
    get_campaign,
    reconcile_child_campaign_lifecycle,
    update_campaign,
)
from rank42.db import now
from rank42.manage_store import active_campaign, create_schedule
from rank42.plugins import (
    Plugin,
    discover_plugins,
    family_rank_claim,
    get_variant,
    is_enabled,
    variant_torsion_groups,
    variant_torsion_provider_role,
    variant_torsion_record,
)
from rank42.search_config import pipeline_search_config
from rank42.ui_components import tabs
from rank42.ui_store import list_jobs, reconcile_jobs
from rank42.torsion import MAZUR_TORSION_CHOICES

from .common import (
    launch_with_pipeline,
    prepare_pipeline_launch,
    resolve_ratpoints,
    section_title,
    setting,
    title,
)
from .jobs_schedule_panel import CATCH_UP_LABELS, schedule_time_inputs


BLOCKING_JOB_STATES = {"queued", "running", "stopping"}


def _families(db, ctx):
    return [
        plugin
        for plugin in discover_plugins(ctx.project_root)
        if isinstance(plugin, Plugin)
        and plugin.plugin_type == "family"
        and is_enabled(db, plugin)
        and "candidate_generation" in plugin.capabilities
    ]


def _torsion_profile(plugins, target):
    """Return the strongest direct prescribed-torsion routing profile."""
    matches = []
    for plugin in plugins:
        for variant in plugin.variants:
            groups = variant_torsion_groups(plugin, variant)
            if target not in groups:
                continue
            role = variant_torsion_provider_role(plugin, variant)
            record = variant_torsion_record(plugin, variant)
            priority = {
                "canonical_universal": 0,
                "prescribed_subfamily": 1,
                "general": 2,
                None: 3,
            }.get(role, 3)
            matches.append((
                priority,
                plugin.name.lower(),
                variant.name.lower(),
                plugin.id,
                variant.id,
                plugin,
                variant,
                record,
                role,
            ))
    if not matches:
        return None
    _, _, _, _, _, plugin, variant, record, role = sorted(matches, key=lambda rec: rec[:5])[0]
    profile = pipeline_search_config(plugin, variant)
    return {
        "plugin": plugin,
        "variant": variant,
        "record": record,
        "role": role,
        "profile": profile,
    }


def _campaign_value(campaign, key, default=None):
    if campaign is None:
        return default
    try:
        value = campaign[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _campaign_context_token(campaign):
    if campaign is None:
        return None
    return ":".join(
        [
            str(_campaign_value(campaign, "id", "")),
            str(_campaign_value(campaign, "plugin_id", "") or ""),
            str(_campaign_value(campaign, "variant_id", "") or ""),
            str(_campaign_value(campaign, "family", "") or ""),
            str(_campaign_value(campaign, "target_rank", "") or ""),
        ]
    )


def _campaign_family_target(campaign, plugins):
    """Resolve one active Campaign to an enabled exact family/variant lane."""
    if campaign is None:
        return None
    wanted_plugin = str(_campaign_value(campaign, "plugin_id", "") or "").strip()
    if not wanted_plugin:
        return None

    plugin = next(
        (rec for rec in plugins if str(rec.id) == wanted_plugin),
        None,
    )
    if plugin is None:
        return None

    wanted_variant = str(_campaign_value(campaign, "variant_id", "") or "").strip()
    if wanted_variant:
        variant = next(
            (
                rec for rec in plugin.variants
                if str(rec.id) == wanted_variant
            ),
            None,
        )
        if variant is not None:
            return plugin, variant

    wanted_family = str(_campaign_value(campaign, "family", "") or "").strip()
    if wanted_family:
        matches = [
            rec for rec in plugin.variants
            if str(getattr(rec, "curve_family_name", "") or "").strip()
            == wanted_family
        ]
        if len(matches) == 1:
            return plugin, matches[0]

    if len(plugin.variants) == 1:
        return plugin, plugin.variants[0]
    return None


def _auto_campaign_control_key(campaign_target):
    if campaign_target is None:
        return None
    plugin, variant = campaign_target
    return f"family-{plugin.id}-{variant.id}"


def _reset_auto_campaign_widgets(
    plugins,
    campaign_target,
    *,
    previous_control_key=None,
    state=None,
):
    """Release Campaign-owned Auto defaults without disturbing unrelated controls."""
    state = st.session_state if state is None else state
    state.pop("auto-search-mode", None)
    state.pop("auto-search-plugin", None)
    for plugin in plugins:
        state.pop(f"auto-search-variant-{plugin.id}", None)

    control_keys = {
        value
        for value in (
            previous_control_key,
            _auto_campaign_control_key(campaign_target),
        )
        if value
    }
    for control_key in control_keys:
        state.pop(f"auto-search-target-{control_key}", None)
        state.pop(f"auto-{control_key}-pipeline-strategy", None)


def _sync_auto_campaign_context(
    plugins,
    campaign,
    campaign_target,
    *,
    state=None,
):
    """Apply active Campaign target once, and release it when no Campaign is active."""
    state = st.session_state if state is None else state
    campaign_token = _campaign_context_token(campaign)
    previous_token = state.get("auto_campaign_context")
    previous_control_key = state.get("auto_campaign_control_key")

    if campaign_token is None:
        if previous_token is not None or previous_control_key is not None:
            _reset_auto_campaign_widgets(
                plugins,
                None,
                previous_control_key=previous_control_key,
                state=state,
            )
        state.pop("auto_campaign_context", None)
        state.pop("auto_campaign_control_key", None)
        return

    if previous_token == campaign_token:
        return

    _reset_auto_campaign_widgets(
        plugins,
        campaign_target,
        previous_control_key=previous_control_key,
        state=state,
    )
    state["auto_campaign_context"] = campaign_token
    control_key = _auto_campaign_control_key(campaign_target)
    if control_key is None:
        state.pop("auto_campaign_control_key", None)
    else:
        state["auto_campaign_control_key"] = control_key


def _recommended_auto_preset(
    *,
    target_mode,
    target_rank,
    plugin=None,
    variant=None,
    profile=None,
):
    """Choose a built-in Pipeline strategy for Auto's current research intent."""
    mode = str(target_mode)
    goal = max(1, int(target_rank))
    profile = dict(profile or {})
    requested = str(profile.get("recommended_preset") or "").strip()
    if requested:
        from rank42.pipeline_catalog import pipeline_preset_options

        valid = {
            str(rec["id"])
            for rec in pipeline_preset_options(mode)
        }
        if requested in valid:
            return requested

    if mode == "family" and plugin is not None and variant is not None:
        try:
            claim = family_rank_claim(plugin, variant)
            baseline = claim.get("effective_lower")
        except Exception:
            baseline = None
        if baseline is not None and int(baseline) >= 15:
            return "high_baseline_rank_hunter"

    if goal >= 25:
        return "frontiermath_record_breaker"

    geometry_caps = {
        "known_subgroup",
        "quartic_search",
        "pgl2_search",
        "target_search",
    }
    capabilities = set(getattr(plugin, "capabilities", ()) or ())
    if mode == "family" and bool(capabilities & geometry_caps):
        return "geometry_grinder"

    if goal >= 18:
        return "aggressive_rank_hunter"
    return "auto"


def _auto_pipeline_options(db, *, target_mode, recommended_preset):
    """Return compatible built-in and saved Pipeline choices for Auto."""
    from rank42.pipeline_catalog import (
        normalize_pipeline,
        pipeline_preset_options,
        template_stages,
        validate_pipeline,
    )
    from rank42.pipeline_state import (
        ensure_pipeline_schema,
        list_pipelines,
        pipeline_payload,
    )

    ensure_pipeline_schema(db)
    mode = str(target_mode)
    options = []

    for rec in pipeline_preset_options(mode):
        stages = template_stages(rec["id"], mode)
        if not stages or validate_pipeline(mode, stages):
            continue
        options.append({
            "key": f"preset:{rec['id']}",
            "kind": "preset",
            "preset_id": str(rec["id"]),
            "label": str(rec["label"]),
            "description": str(rec["description"]),
            "recommended": str(rec["id"]) == str(recommended_preset),
            "pipeline": {
                "id": None,
                "name": str(rec["label"]),
                "target_mode": mode,
                "stages": stages,
                "config": {},
            },
        })

    for row in list_pipelines(db, limit=250):
        if str(row["target_mode"]) != mode:
            continue
        payload = pipeline_payload(row)
        stages = normalize_pipeline(payload.get("stages") or [])
        if not stages or validate_pipeline(mode, stages):
            continue
        options.append({
            "key": f"saved:{int(row['id'])}",
            "kind": "saved",
            "preset_id": None,
            "label": str(row["name"]),
            "description": "Saved Pipeline definition",
            "recommended": False,
            "pipeline": payload,
        })

    options.sort(
        key=lambda rec: (
            0 if rec["recommended"] else 1,
            0 if rec["kind"] == "preset" else 1,
            str(rec["label"]).lower(),
        )
    )
    return options


def _auto_pipeline_selector(
    db,
    *,
    target_mode,
    target_rank,
    key,
    recommended_preset,
):
    options = _auto_pipeline_options(
        db,
        target_mode=target_mode,
        recommended_preset=recommended_preset,
    )
    if not options:
        raise RuntimeError("Auto has no compatible Pipeline strategy")

    by_key = {str(rec["key"]): rec for rec in options}
    keys = list(by_key)
    selected = st.selectbox(
        "Execution strategy",
        keys,
        index=0,
        format_func=lambda value: (
            (
                "Recommended · "
                if by_key[str(value)]["recommended"]
                else (
                    "Built-in · "
                    if by_key[str(value)]["kind"] == "preset"
                    else "Saved · "
                )
            )
            + str(by_key[str(value)]["label"])
        ),
        key=f"{key}-pipeline-strategy",
        help=(
            "Auto always runs through the Pipeline engine. Built-in strategies "
            "are standard recipes; saved Pipelines appear here as overrides."
        ),
    )
    chosen = by_key[str(selected)]
    st.caption(str(chosen["description"]))
    return {
        "pipeline": dict(chosen["pipeline"]),
        "target_rank": int(target_rank),
        "strategy_key": str(chosen["key"]),
        "strategy_label": str(chosen["label"]),
        "strategy_kind": str(chosen["kind"]),
    }


def _auto_apply_stop_policy(pipeline_choice, *, stop_on_target):
    """Apply Auto's stop toggle to an execution copy of the Pipeline."""
    from rank42.pipeline_catalog import normalize_pipeline, validate_pipeline

    choice = dict(pipeline_choice)
    payload = dict(choice["pipeline"])
    mode = str(payload["target_mode"])
    stages = [
        rec for rec in normalize_pipeline(payload.get("stages") or [])
        if str(rec["id"]) != "stop_goal"
    ]
    if bool(stop_on_target):
        stages.append({"id": "stop_goal", "config": {}})
    errors = validate_pipeline(mode, stages)
    if errors:
        raise ValueError("Auto strategy became incompatible: " + "; ".join(errors))
    payload["stages"] = stages
    choice["pipeline"] = payload
    return choice


def _job_metadata(row):
    try:
        return json.loads(row["metadata_json"] or "{}")
    except Exception:
        return {}


def _search_campaign_id(metadata):
    """Return the Auto search-campaign id with legacy job compatibility."""
    metadata = dict(metadata or {})
    value = metadata.get("search_campaign_id")
    if value is None:
        # Pre-Launch-Context native Auto jobs used generic campaign_id for the
        # Auto search campaign itself.
        value = metadata.get("campaign_id")
    return None if value is None else int(value)


def _auto_jobs(db):
    reconcile_jobs(db)
    rows = [
        row
        for row in list_jobs(db, limit=100)
        if str(row["kind"]) in {"auto_search", "torsion_auto_search"}
    ]
    terminal_map = {
        "interrupted": "interrupted",
        "stopped": "paused",
        "killed": "killed",
        "failed": "failed",
    }
    for job in rows:
        meta = _job_metadata(job)
        campaign_id = _search_campaign_id(meta)
        if campaign_id is None:
            continue
        campaign = get_campaign(db, int(campaign_id))
        if campaign is None:
            continue
        job_status = str(job["status"] or "")
        campaign_status = str(campaign["status"] or "")
        mapped = terminal_map.get(job_status)
        if mapped is not None and campaign_status in {"queued", "running"}:
            fields = {"status": mapped}
            if mapped in {"killed", "failed"}:
                fields["finished_at"] = now()
            update_campaign(db, int(campaign_id), **fields)
            campaign_status = mapped
        reconcile_child_campaign_lifecycle(
            db,
            int(campaign_id),
            parent_status=campaign_status,
        )
    return rows


def _auto_pipeline_jobs(db):
    reconcile_jobs(db)
    return [
        row
        for row in list_jobs(db, limit=100)
        if str(row["kind"]) == "pipeline_search"
        and bool(_job_metadata(row).get("auto_entrypoint"))
    ]


def _blocking_auto_jobs(db):
    rows = [*_auto_jobs(db), *_auto_pipeline_jobs(db)]
    return [row for row in rows if str(row["status"]) in BLOCKING_JOB_STATES]


def _toggle_auto_schedule():
    state_key = "auto-schedule-active"
    st.session_state[state_key] = not bool(st.session_state.get(state_key, False))


def _auto_schedule_enabled():
    """Render Auto's inline schedule-mode toggle; durable ownership stays in Jobs."""
    enabled = bool(st.session_state.get("auto-schedule-active", False))
    st.button(
        "Schedule",
        icon=":material/schedule:",
        help="Enable or disable Auto scheduling",
        key="auto-schedule-toggle",
        type="primary" if enabled else "secondary",
        width="stretch",
        on_click=_toggle_auto_schedule,
    )
    return enabled


def _auto_schedule_settings():
    """Render Auto's inline schedule draft; durable ownership stays in Jobs."""
    recurrence = st.selectbox(
        "Recurrence",
        ["once", "daily", "weekly", "interval"],
        key="auto-schedule-recurrence",
    )
    catch_up_policy = st.selectbox(
        "After downtime",
        list(CATCH_UP_LABELS),
        format_func=lambda value: CATCH_UP_LABELS[value],
        disabled=recurrence == "once",
        key="auto-schedule-catch-up",
    )
    interval_hours = None
    if recurrence == "interval":
        interval_hours = int(
            st.number_input(
                "Every N hours",
                min_value=1,
                value=6,
                step=1,
                key="auto-schedule-interval-hours",
            )
        )
    next_run_at = schedule_time_inputs("auto-schedule")
    st.caption(
        "Jobs owns this schedule. Each occurrence clones the frozen "
        "Pipeline snapshot captured when you press Schedule Auto Search."
    )
    return {
        "enabled": True,
        "recurrence": str(recurrence),
        "catch_up_policy": (
            "latest" if recurrence == "once" else str(catch_up_policy)
        ),
        "interval_minutes": (
            None if recurrence != "interval" else int(interval_hours) * 60
        ),
        "next_run_at": str(next_run_at),
    }


def _schedule_auto_pipeline(
    ctx,
    db,
    *,
    pipeline_choice,
    target_mode,
    target,
    run_config,
    native_metadata,
    schedule,
):
    """Freeze Auto's Pipeline now and hand durable execution to Jobs Scheduler."""
    schedule = dict(schedule or {})
    if not schedule.get("enabled"):
        raise ValueError("Auto scheduling is not enabled")

    schedule_provenance = {
        "scheduled_entrypoint": "auto",
        "schedule_recurrence": str(schedule["recurrence"]),
        "schedule_catch_up_policy": str(schedule["catch_up_policy"]),
        "schedule_interval_minutes": schedule.get("interval_minutes"),
        "schedule_initial_next_run_at": str(schedule["next_run_at"]),
    }
    frozen_run_config = {**dict(run_config or {}), **schedule_provenance}
    frozen_metadata = {**dict(native_metadata or {}), **schedule_provenance}

    try:
        prepared = prepare_pipeline_launch(
            ctx,
            db,
            pipeline_choice=pipeline_choice,
            target_mode=target_mode,
            target=target,
            run_config=frozen_run_config,
            native_kind="auto_search",
            native_label="Auto",
            native_metadata=frozen_metadata,
            commit=False,
        )
        run_id = int(prepared["pipeline_run_id"])
        db.execute(
            """UPDATE search_pipeline_runs
               SET status='scheduled',updated_at=?
               WHERE id=?""",
            (now(), run_id),
        )
        campaign_id = prepared["metadata"].get("campaign_id")
        schedule_id = create_schedule(
            db,
            label=f"Auto · {pipeline_choice['strategy_label']}",
            kind=prepared["kind"],
            command=prepared["command"],
            cwd=prepared["cwd"],
            next_run_at=schedule["next_run_at"],
            recurrence=schedule["recurrence"],
            interval_minutes=schedule.get("interval_minutes"),
            catch_up_policy=schedule["catch_up_policy"],
            metadata=prepared["metadata"],
            campaign_id=(
                None if campaign_id in (None, "") else int(campaign_id)
            ),
            enabled=True,
        )
    except Exception:
        db.rollback()
        raise
    return int(schedule_id), int(run_id)


def _render_auto(db, ctx):
    ensure_auto_search_schema(db)

    blocking_jobs = _blocking_auto_jobs(db)
    plugins = _families(db, ctx)
    if not plugins:
        with st.container(border=True):
            section_title(
                "No searchable family is active",
                "Enable a Family plugin with candidate generation before starting Auto.",
            )
            if st.button("Open Family Plugins", type="primary", width="stretch"):
                st.session_state["rh_page"] = "Plugin Families"
                st.rerun()
        return

    campaign = active_campaign(db)
    campaign_target = _campaign_family_target(campaign, plugins)
    _sync_auto_campaign_context(
        plugins,
        campaign,
        campaign_target,
    )

    if campaign is not None and campaign["plugin_id"] and campaign_target is None:
        st.warning(
            f"Active campaign #{int(campaign['id'])} names a family/variant "
            "that is not currently available to Auto. Auto remains manual."
        )

    _, main_col, _ = st.columns([0.6, 2.8, 0.6])
    with main_col:
        section_title(
            "Auto Search",
            "Choose a family or torsion target and a rigorous rank goal.",
        )
        search_by = st.radio(
            "Search by",
            ["Family", "Torsion"],
            horizontal=True,
            key="auto-search-mode",
        )

        plugin = None
        variant = None
        torsion_group = None
        torsion_profile = None
        torsion_record = None
        include_secondary_providers = False
        profile = {}
        if search_by == "Family":
            campaign_plugin = (
                campaign_target[0] if campaign_target is not None else None
            )
            plugin_index = (
                plugins.index(campaign_plugin)
                if campaign_plugin in plugins
                else 0
            )
            family_col, variant_col = st.columns(
                [1.0, 1.0],
                gap="small",
                vertical_alignment="bottom",
            )
            with family_col:
                plugin = st.selectbox(
                    "Family",
                    plugins,
                    index=plugin_index,
                    format_func=lambda p: p.name,
                    key="auto-search-plugin",
                )

            campaign_variant = (
                campaign_target[1]
                if (
                    campaign_target is not None
                    and str(campaign_target[0].id) == str(plugin.id)
                )
                else None
            )
            variant_options = list(plugin.variants)
            variant = campaign_variant or get_variant(plugin)
            variant_index = (
                variant_options.index(campaign_variant)
                if campaign_variant in variant_options
                else variant_options.index(variant)
            )
            with variant_col:
                variant = st.selectbox(
                    "Variant",
                    variant_options,
                    index=variant_index,
                    format_func=lambda v: v.name,
                    key=f"auto-search-variant-{plugin.id}",
                    disabled=len(variant_options) == 1,
                )
            profile = pipeline_search_config(plugin, variant)
            control_key = f"family-{plugin.id}-{variant.id}"
            campaign_matches_lane = bool(
                campaign is not None
                and campaign_target is not None
                and str(campaign_target[0].id) == str(plugin.id)
                and str(campaign_target[1].id) == str(variant.id)
            )
            target_default = (
                int(campaign["target_rank"])
                if (
                    campaign_matches_lane
                    and campaign["target_rank"] is not None
                )
                else int(profile.get("target_rank") or 20)
            )
            if campaign_matches_lane:
                target_bits = [
                    f"Active campaign #{int(campaign['id'])}",
                    str(campaign["name"]),
                    f"{plugin.name} / {variant.name}",
                ]
                if campaign["target_rank"] is not None:
                    target_bits.append(f"target ≥{int(campaign['target_rank'])}")
                st.caption("Campaign defaults · " + " · ".join(target_bits))
        else:
            torsion_group = st.selectbox(
                "Exact rational torsion group",
                MAZUR_TORSION_CHOICES,
                key="auto-search-torsion-group",
            )
            control_key = "torsion-" + str(torsion_group).replace(" ", "-").replace("×", "x")
            torsion_profile = _torsion_profile(plugins, torsion_group)
            if torsion_profile is not None:
                torsion_record = torsion_profile["record"]
                profile = dict(torsion_profile["profile"])
                target_default = (
                    int(torsion_record["goal_rank"])
                    if torsion_record is not None
                    else int(profile.get("target_rank") or 20)
                )
                provider_name = (
                    f"{torsion_profile['plugin'].name} · "
                    f"{torsion_profile['variant'].name}"
                )
                if torsion_record is not None:
                    source = torsion_record.get("source") or {}
                    as_of = source.get("as_of")
                    record_note = (
                        f"Best-known record lower: **rank ≥{int(torsion_record['rank_lower'])}**; "
                        f"default goal: **{int(torsion_record['goal_rank'])}**"
                    )
                    if as_of:
                        record_note += f" · record metadata as of {as_of}"
                    st.caption(record_note)
                st.caption(
                    f"Direct prescribed-torsion lane: **{provider_name}**. "
                    "Every specialization is exact-checked for the selected torsion group before rank-search evidence counts."
                )
            else:
                target_default = 20
                st.warning(
                    "No enabled direct prescribed-torsion provider is installed for this group. "
                    "Install/enable a torsion-family provider before starting a direct record hunt."
                )

        floor_default = (
            int(torsion_record["rank_lower"])
            if search_by == "Torsion" and torsion_record is not None
            else int(profile.get("retention_floor") or 0)
        )
        floor_key = f"auto-search-retention-{control_key}"

        goal_col, floor_col = st.columns(
            [1.0, 1.0],
            gap="small",
            vertical_alignment="bottom",
        )
        with goal_col:
            target_rank = int(
                st.number_input(
                    "Goal rank",
                    min_value=1,
                    max_value=100,
                    value=int(target_default),
                    step=1,
                    key=f"auto-search-target-{control_key}",
                    help="Stop criterion uses the rigorous certified lower bound, not Nagao or a numerical rank signal.",
                )
            )

        stored_floor = int(st.session_state.get(floor_key, floor_default) or 0)
        if stored_floor > int(target_rank):
            st.session_state[floor_key] = int(target_rank)

        with floor_col:
            retention_floor = int(
                st.number_input(
                    "Retention floor",
                    min_value=0,
                    max_value=100,
                    value=int(floor_default),
                    step=1,
                    key=floor_key,
                    help="Fresh curves below this rigorous lower bound stay in campaign history but are not kept in Curves. 0 preserves the normal promotion policy.",
                )
            )
        if retention_floor > target_rank:
            st.warning(
                "Retention floor cannot exceed the rank goal; it will be clamped to the goal."
            )
            retention_floor = int(target_rank)

        stop_on_target = bool(
            st.checkbox(
                "Stop when goal is reached",
                value=True,
                key=f"auto-search-stop-target-{control_key}",
                help="Stop after the first curve reaches the rigorous lower-bound goal. Turn this off to scan the full configured queue.",
            )
        )

        certificate_timeout = int(profile.get("certificate_timeout") or 120)
        exact_candidates = int(profile.get("exact_candidates") or 48)

        target_mode = "torsion" if search_by == "Torsion" else "family"
        recommended_preset = _recommended_auto_preset(
            target_mode=target_mode,
            target_rank=target_rank,
            plugin=plugin,
            variant=variant,
            profile=profile,
        )

        needs_provider_fallback = (
            search_by == "Torsion" and torsion_profile is None
        )
        if needs_provider_fallback:
            st.caption(
                "No direct prescribed-torsion provider is available. Enable the fallback below "
                "only if you want Auto to try sparse unrelated-family specializations."
            )
            include_secondary_providers = bool(
                st.checkbox(
                    "Use unrelated-family fallback",
                    value=False,
                    key=f"auto-search-secondary-providers-{control_key}",
                )
            )

        with st.expander("Advanced options", expanded=False):
            st.caption(
                "Fine-tune exact certification budgets, provider fallback, "
                "or override Auto's recommended Pipeline strategy."
            )

            c1, c2 = st.columns(2)
            certificate_timeout = int(
                c1.number_input(
                    "Exact certificate timeout",
                    min_value=1,
                    max_value=7200,
                    value=int(certificate_timeout),
                    step=30,
                    key=f"auto-search-cert-timeout-{control_key}",
                )
            )
            exact_candidates = int(
                c2.number_input(
                    "Exact point candidates per certification pass",
                    min_value=1,
                    max_value=512,
                    value=int(exact_candidates),
                    step=8,
                    key=f"auto-search-exact-candidates-{control_key}",
                )
            )

            if search_by == "Torsion" and not needs_provider_fallback:
                include_secondary_providers = bool(
                    st.checkbox(
                        "After direct torsion families, try unrelated family specializations",
                        value=False,
                        key=f"auto-search-secondary-providers-{control_key}",
                        help=(
                            "Off by default. Direct prescribed-torsion parameterizations are the "
                            "primary lane; unrelated families require exceptional torsion growth."
                        ),
                    )
                )

            pipeline_choice = _auto_pipeline_selector(
                db,
                target_mode=target_mode,
                target_rank=target_rank,
                key=f"auto-{control_key}",
                recommended_preset=recommended_preset,
            )
            if profile.get("strategy_note"):
                st.caption(str(profile["strategy_note"]))

        pipeline_choice = _auto_apply_stop_policy(
            pipeline_choice,
            stop_on_target=stop_on_target,
        )

        backend = str(setting(db, "ratpoints_backend", "GPU") or "GPU").upper()
        if backend not in {"CPU", "GPU"}:
            backend = "GPU"
        ratpoints = resolve_ratpoints(db, backend)

        target_note = f" · exact torsion **{torsion_group}**" if torsion_group else ""
        lane_note = ""
        if torsion_group:
            lane_note = (
                " · **direct prescribed-torsion lane first**"
                + (" · unrelated-family fallback enabled" if include_secondary_providers else "")
            )
        floor_note = (
            f" · keep fresh curves only at **rank ≥{retention_floor}**"
            if retention_floor
            else " · default promotion retention"
        )
        stop_note = (
            " · **stop on first goal hit**"
            if stop_on_target
            else " · scan full configured queue"
        )
        st.caption(
            f"Goal: **rigorous rank ≥{target_rank}**{target_note}{floor_note}"
            f"{stop_note}{lane_note} · strategy: "
            f"**{pipeline_choice['strategy_label']}** · "
            f"point engine: **{backend} ratpoints**."
        )
        if not ratpoints:
            st.warning(
                f"{backend} ratpoints is not configured. Set it in Settings before starting Auto."
            )

        torsion_provider_ready = (
            search_by != "Torsion"
            or torsion_profile is not None
            or include_secondary_providers
        )
        if search_by == "Torsion" and torsion_profile is None and include_secondary_providers:
            st.warning(
                "No direct prescribed-torsion provider is available; this run would use only "
                "sparse unrelated-family fallback specializations."
            )

        pipeline_target = (
            {
                "torsion_group": torsion_group,
                "include_secondary_providers": bool(include_secondary_providers),
            }
            if search_by == "Torsion"
            else {
                "plugin_id": plugin.id,
                "variant_id": variant.id,
            }
        )
        auto_run_config = {
            "target_rank": target_rank,
            "retention_floor": retention_floor,
            "certificate_timeout": certificate_timeout,
            "exact_candidates": exact_candidates,
            "ratpoints_backend": backend,
            "ratpoints": ratpoints,
            "stop_on_target": bool(stop_on_target),
            "auto_entrypoint": True,
            "auto_strategy": pipeline_choice["strategy_label"],
        }
        auto_metadata = {
            "auto_entrypoint": True,
            "auto_strategy": pipeline_choice["strategy_label"],
            "search_mode": (
                "torsion" if search_by == "Torsion" else "family"
            ),
            "plugin_id": None if plugin is None else plugin.id,
            "plugin_variant": None if variant is None else variant.id,
            "torsion_group": torsion_group,
            "target_rank": target_rank,
            "retention_floor": retention_floor,
            "ratpoints_backend": backend,
        }

        launch_col, schedule_col = st.columns(
            [4.8, 1.2],
            gap="small",
            vertical_alignment="center",
        )
        with schedule_col:
            scheduled = _auto_schedule_enabled()

        with launch_col:
            launch_pressed = st.button(
                "Schedule Auto Search" if scheduled else "Start Auto Search",
                type="primary",
                width="stretch",
                disabled=(
                    not bool(ratpoints)
                    or (bool(blocking_jobs) and not scheduled)
                    or not torsion_provider_ready
                ),
                icon=(
                    ":material/schedule:"
                    if scheduled
                    else ":material/rocket_launch:"
                ),
            )

        schedule = {"enabled": False}
        if scheduled:
            with st.container(
                key="auto-schedule-settings",
                border=True,
            ):
                schedule = _auto_schedule_settings()

        if launch_pressed:
            if scheduled:
                schedule_id, template_run_id = _schedule_auto_pipeline(
                    ctx,
                    db,
                    pipeline_choice=pipeline_choice,
                    target_mode=target_mode,
                    target=pipeline_target,
                    run_config=auto_run_config,
                    native_metadata=auto_metadata,
                    schedule=schedule,
                )
                st.session_state["manage_schedule_id"] = int(schedule_id)
                st.success(
                    f"Scheduled Auto with {pipeline_choice['strategy_label']} "
                    f"as Jobs schedule #{schedule_id} · frozen Pipeline Run "
                    f"#{template_run_id}."
                )
            else:
                jid = launch_with_pipeline(
                    ctx,
                    db,
                    pipeline_choice=pipeline_choice,
                    target_mode=target_mode,
                    target=pipeline_target,
                    run_config=auto_run_config,
                    native_kind="auto_search",
                    native_label="Auto",
                    native_command=[],
                    native_metadata=auto_metadata,
                )
                st.success(
                    f"Started Auto with {pipeline_choice['strategy_label']} "
                    f"as Pipeline job #{jid}."
                )
            st.rerun()


def page(db, ctx):
    title(
        "Auto",
        "Pipeline-native automatic family and torsion hunting. Choose the research target; Auto chooses a strategy and the Pipeline engine executes it.",
        "Search",
        icon="rocket_launch",
    )
    # SEARCH-R10: Legacy Auto campaign history moved to the read-only
    # Diagnostics compatibility archive. New Auto work is Pipeline-only.
    st.session_state.pop("auto-main-tab", None)
    st.session_state.pop("auto-main-tabs", None)
    return _render_auto(db, ctx)
