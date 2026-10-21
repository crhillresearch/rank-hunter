from __future__ import annotations

import json

import streamlit as st

from rank42.auto_search_policy import auto_search_policy, default_target_rank
from rank42.auto_search_state import (
    campaign_trials,
    create_campaign,
    ensure_auto_search_schema,
    get_campaign,
    list_campaigns,
    reconcile_child_campaign_lifecycle,
    update_campaign,
)
from rank42.db import now
from rank42.manage_store import pause_job
from rank42.plugins import (
    Plugin,
    discover_plugins,
    family_rank_claim,
    is_enabled,
    variant_torsion_groups,
    variant_torsion_record,
)
from rank42.torsion import MAZUR_TORSION_CHOICES
from rank42.ui_store import list_jobs, reconcile_jobs, stop_job

from .common import launch, launch_with_pipeline, ratpoints_selector, search_pipeline_selector, section_title, setting, title


ACTIVE_JOB_STATES = {"queued", "running", "stopping"}
RESUMABLE = {"paused", "interrupted", "killed", "failed"}


def _lanes(db, ctx, torsion_group=None):
    rows = []
    for plugin in discover_plugins(ctx.project_root):
        if not isinstance(plugin, Plugin) or plugin.plugin_type != "family":
            continue
        if not is_enabled(db, plugin):
            continue
        if "candidate_generation" not in plugin.capabilities:
            continue
        for variant in plugin.variants:
            groups = variant_torsion_groups(plugin, variant)
            if torsion_group and torsion_group not in groups:
                continue
            claim = family_rank_claim(plugin, variant)
            lower = claim["effective_lower"]
            rows.append((
                -(int(lower) if lower is not None else -1),
                plugin.name.lower(),
                variant.name.lower(),
                plugin,
                variant,
                claim,
            ))
    rows.sort(key=lambda rec: rec[:3])
    return rows


def _lane_label(rec):
    _rank_key, _pn, _vn, plugin, variant, claim = rec
    lower = claim["effective_lower"]
    rank = f"generic ≥{int(lower)}" if lower is not None else "generic rank —"
    geometry = [
        name.replace("_", " ")
        for name in ("known_subgroup", "quartic_search", "pgl2_search", "target_search")
        if name in plugin.capabilities
    ]
    suffix = (
        " · plugin: " + ", ".join(geometry)
        if geometry
        else " · core geometry"
    )
    variant_note = f" / {variant.name}" if len(plugin.variants) > 1 else ""
    return f"{plugin.name}{variant_note} · {rank}{suffix}"


def _torsion_record_profile(lanes):
    records = []
    for _rk, _pn, _vn, plugin, variant, _claim in lanes:
        rec = variant_torsion_record(plugin, variant)
        if rec is not None:
            records.append(rec)
    if not records:
        return None
    return max(
        records,
        key=lambda rec: (int(rec["rank_lower"]), int(rec["goal_rank"])),
    )


def _job_meta(job):
    try:
        return json.loads(job["metadata_json"] or "{}")
    except Exception:
        return {}


def _search_campaign_id(metadata):
    """Return the Geometry search-campaign id with legacy job compatibility."""
    metadata = dict(metadata or {})
    value = metadata.get("search_campaign_id")
    if value is None:
        # Pre-Launch-Context native Geometry jobs used generic campaign_id for
        # the Geometry search campaign itself.
        value = metadata.get("campaign_id")
    return None if value is None else int(value)


def _geometry_jobs(db):
    reconcile_jobs(db)
    rows = [
        row for row in list_jobs(db, limit=100)
        if str(row["kind"]) == "geometry_search"
    ]
    terminal_map = {
        "interrupted": "interrupted",
        "stopped": "paused",
        "killed": "killed",
        "failed": "failed",
    }
    for job in rows:
        meta = _job_meta(job)
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


def _command(db, ctx, campaign):
    config = json.loads(campaign["config_json"] or "{}")
    backend = str(config.get("ratpoints_backend") or "GPU").upper()
    ratpoints = config.get("ratpoints") or None
    if not ratpoints:
        from .common import resolve_ratpoints
        ratpoints = resolve_ratpoints(db, backend)
    if not ratpoints:
        return None

    py = setting(db, "science_python", ctx.detected_science_python())
    mode = str(campaign["search_mode"] or "geometry")
    common = [
        "--project-root", str(ctx.project_root),
        "--db", str(ctx.db_path),
        "--campaign-id", str(int(campaign["id"])),
        "--target-rank", str(int(campaign["target_rank"])),
        "--retention-floor", str(int(campaign["retention_floor"] or 0)),
        "--pool-size", str(int(config.get("pool_size", 250))),
        "--deep-keep", str(int(config.get("deep_keep", 24))),
        "--upper-timeout", str(int(config.get("upper_timeout", 5))),
        "--final-upper-timeout", str(int(config.get("final_upper_timeout", 20))),
        "--certificate-timeout", str(int(config.get("certificate_timeout", 120))),
        "--exact-candidates", str(int(config.get("exact_candidates", 48))),
        "--ratpoints", str(ratpoints),
        "--stop-on-target" if bool(campaign["stop_on_target"]) else "--no-stop-on-target",
    ]

    if mode == "geometry_torsion":
        command = [
            str(py), "-m", "rank42.torsion_auto_search",
            "--torsion-group", str(campaign["torsion_group"]),
            "--geometry-first",
            "--geometry-keep", str(int(config.get("geometry_keep", 48))),
            "--geometry-triage-timeout", str(int(config.get("geometry_triage_timeout", 2))),
            "--geometry-timeout", str(int(config.get("geometry_timeout", 180))),
            "--geometry-stage-bounds",
            ",".join(str(x) for x in config.get("stage_bounds", [523, 1979])),
            "--geometry-stage-keeps",
            ",".join(str(x) for x in config.get("stage_keeps", [5000, 250])),
            *common,
        ]
        command.append(
            "--include-secondary-providers"
            if bool(config.get("include_secondary_providers", False))
            else "--no-include-secondary-providers"
        )
        return command

    return [
        str(py), "-m", "rank42.geometry_search",
        "--plugin", str(campaign["plugin_id"]),
        "--variant", str(campaign["variant_id"]),
        "--geometry-keep", str(int(config.get("geometry_keep", 48))),
        "--geometry-triage-timeout", str(int(config.get("geometry_triage_timeout", 2))),
        "--geometry-timeout", str(int(config.get("geometry_timeout", 180))),
        "--stage-bounds",
        ",".join(str(x) for x in config.get("stage_bounds", [523, 1979])),
        "--stage-keeps",
        ",".join(str(x) for x in config.get("stage_keeps", [5000, 250])),
        *common,
    ]


def _launch(db, ctx, campaign, label=None):
    cmd = _command(db, ctx, campaign)
    if not cmd:
        raise ValueError("ratpoints is not configured")
    update_campaign(
        db, int(campaign["id"]),
        status="queued", error=None, finished_at=None,
    )
    mode = str(campaign["search_mode"] or "geometry")
    default_label = (
        f"Geometry · torsion {campaign['torsion_group']}"
        if mode == "geometry_torsion"
        else f"Geometry · {campaign['family_name'] or campaign['plugin_id']}"
    )
    return launch(
        ctx,
        db,
        kind="geometry_search",
        label=label or default_label,
        command=cmd,
        metadata={
            "search_campaign_id": int(campaign["id"]),
            "search_campaign_kind": "geometry",
            "search_mode": str(campaign["search_mode"]),
            "plugin_id": str(campaign["plugin_id"]),
            "plugin_variant": str(campaign["variant_id"]),
            "target_rank": int(campaign["target_rank"]),
            "torsion_group": campaign["torsion_group"],
        },
    )


def _render_running(db):
    active = [j for j in _geometry_jobs(db) if str(j["status"]) in {"queued", "running"}]
    if not active:
        return
    with st.container(border=True):
        section_title(
            "Running Geometry",
            "Pause preserves the candidate pool and per-fiber campaign state.",
        )
        for job in active:
            meta = _job_meta(job)
            cid = _search_campaign_id(meta)
            campaign = get_campaign(db, int(cid)) if cid is not None else None
            st.write(f"**{job['label']}**")
            if campaign is not None:
                unit = (
                    "providers"
                    if str(campaign["search_mode"] or "") == "geometry_torsion"
                    else "fibers"
                )
                st.caption(
                    f"Campaign #{cid} · {int(campaign['candidates_done'])}/"
                    f"{int(campaign['candidates_total'])} {unit} · "
                    f"best rigorous rank ≥{int(campaign['best_lower'] or 0)}"
                )
            a, b = st.columns(2)
            if a.button("Pause", key=f"geometry-pause-{job['id']}", width="stretch"):
                pause_job(db, int(job["id"]))
                st.rerun()
            if b.button("Kill", key=f"geometry-kill-{job['id']}", width="stretch"):
                if cid is not None:
                    update_campaign(db, int(cid), status="killed", finished_at=now())
                    reconcile_child_campaign_lifecycle(
                        db, int(cid), parent_status="killed"
                    )
                stop_job(db, int(job["id"]), force=True, pause=False)
                st.rerun()


def _recent_table(rows):
    return [{
        "id": int(r["id"]),
        "status": str(r["status"]),
        "geometry": str(r["family_name"] or r["plugin_id"]),
        "torsion": str(r["torsion_group"] or "—"),
        "goal": int(r["target_rank"]),
        "done": f"{int(r['candidates_done'])}/{int(r['candidates_total'])}",
        "best ≥": int(r["best_lower"] or 0),
        "best curve": r["best_curve_id"],
    } for r in rows]


def render(db, ctx, *, embedded=False):
    ensure_auto_search_schema(db)
    if not embedded:
        title(
            "Geometry",
            "Geometry-first record hunting for any candidate-generating family: cheap/medium Mestre–Nagao screening, "
            "exact arithmetic triage, core geometric attacks, optional plugin-native acceleration, then certification.",
            "Search",
            icon="route",
        )
    st.caption(
        "“Geometry” is Rank Hunter's name for a geometry-first workflow, not a standard theorem name. "
        "Core supplies family-independent exact search geometry; plugins may add family-specific maps, sections, quartics, PGL₂ charts, coverings, or Selmer logic."
    )

    _render_running(db)
    blocking = any(str(j["status"]) in ACTIVE_JOB_STATES for j in _geometry_jobs(db))

    with st.container(border=True):
        section_title(
            "Research lane",
            "Pick the family parameter space to scan. Core Geometry works for every candidate-generating family; plugin-native geometry is used automatically when available.",
        )
        search_by = st.radio(
            "Search by",
            ["Family", "Torsion group"],
            horizontal=True,
            key="geometry-target-kind",
        )

        torsion = None
        plugin = None
        variant = None
        claim = None
        policy = {}
        record = None
        include_secondary = False

        if search_by == "Family":
            lanes = _lanes(db, ctx)
            if not lanes:
                st.warning("No enabled candidate-generating family is available.")
                return
            lane = st.selectbox(
                "Family / parameter space",
                lanes,
                format_func=_lane_label,
                key="geometry-lane",
            )
            _rk, _pn, _vn, plugin, variant, claim = lane
            policy = auto_search_policy(plugin, variant)
            generic_lower = claim["effective_lower"]
        else:
            torsion = st.selectbox(
                "Exact rational torsion group",
                MAZUR_TORSION_CHOICES,
                key="geometry-torsion",
            )
            direct_lanes = _lanes(db, ctx, torsion)
            if not direct_lanes:
                st.warning(
                    f"No enabled family declares prescribed torsion {torsion}. "
                    "Install/enable a direct torsion provider first."
                )
                return
            record = _torsion_record_profile(direct_lanes)
            generic_lower = None
            st.caption(
                f"Direct prescribed-torsion providers available: **{len(direct_lanes)}**. "
                "Geometry will exact-preflight them and run the geometry pipeline in provider order."
            )
            include_secondary = st.toggle(
                "After direct providers, try unrelated family specializations",
                value=False,
                key=f"geometry-secondary-{str(torsion).replace(' ', '-').replace('×', 'x')}",
            )

        fallback_goal = (int(generic_lower) + 1) if generic_lower is not None else 20
        target_default = (
            int(record["goal_rank"]) if record is not None
            else default_target_rank(policy, fallback_goal)
        )
        floor_default = (
            int(record["rank_lower"]) if record is not None
            else ((int(generic_lower) + 1) if generic_lower is not None else 1)
        )
        floor_default = min(floor_default, target_default)

        control_key = (
            f"family-{plugin.id}-{variant.id}"
            if search_by == "Family"
            else "torsion-" + str(torsion).replace(" ", "-").replace("×", "x")
        )

        c1, c2, c3 = st.columns(3)
        target_rank = int(c1.number_input(
            "Rigorous rank goal",
            min_value=1,
            value=int(target_default),
            step=1,
            key=f"geometry-goal-{control_key}",
        ))
        retention_floor = int(c2.number_input(
            "Keep curves with rank ≥",
            min_value=0,
            max_value=target_rank,
            value=min(int(floor_default), target_rank),
            step=1,
            key=f"geometry-floor-{control_key}",
        ))
        stop_on_target = c3.toggle(
            "Stop on goal",
            value=True,
            key=f"geometry-stop-{control_key}",
        )

        backend, ratpoints = ratpoints_selector(
            db,
            key=f"geometry-ratpoints-{control_key}",
        )

        pipeline_choice = search_pipeline_selector(
            db,
            target_mode="torsion" if search_by == "Torsion group" else "family",
            key=f"geometry-{control_key}",
            fixed_goal=target_rank,
        )

        with st.expander("Research budget", expanded=False):
            st.caption(
                "Default sieve bounds mirror the classic cheap/medium pattern: broad S(523)-scale screening, "
                "then S(1979)-scale rescoring only on survivors."
            )
            b1, b2, b3 = st.columns(3)
            pool_size = int(b1.number_input("Nagao survivors", 20, 5000, 250, 10))
            geometry_keep = int(b2.number_input("Geometry survivors", 1, pool_size, min(48, pool_size), 1))
            deep_keep = int(b3.number_input(
                "Generic fallback keep", 0, geometry_keep, min(24, geometry_keep), 1
            ))
            t1, t2, t3 = st.columns(3)
            triage_timeout = int(t1.number_input("PARI gate seconds", 0, 30, 2, 1))
            geometry_timeout = int(t2.number_input("Seconds per geometry fiber", 15, 3600, 180, 15))
            certificate_timeout = int(t3.number_input("Certificate seconds", 10, 7200, 120, 10))
            exact_candidates = int(st.number_input("Exact candidates per fiber", 1, 512, 48, 1))

        st.markdown(
            "**Pipeline:** family parameter space → Nagao p<523 → survivor rescore p<1979 → "
            "exact torsion/section baseline → short rigorous PARI elimination gate → "
            "global-minimal integral seed → optional plugin-native geometry → point-centered quartics / "
            "known-point coverings → generic affine fallback on the strongest survivors → exact independence certificate."
        )

        start_disabled = blocking or not ratpoints
        if st.button(
            "Start Geometry",
            type="primary",
            width="stretch",
            disabled=start_disabled,
            key="geometry-start",
        ):
            stage_keeps = [max(5000, pool_size * 20), pool_size]
            common_config = {
                "pipeline": "geometry-first-v1",
                "geometry_first": True,
                "stage_bounds": [523, 1979],
                "stage_keeps": stage_keeps,
                "pool_size": pool_size,
                "geometry_keep": geometry_keep,
                "deep_keep": deep_keep,
                "geometry_triage_timeout": triage_timeout,
                "geometry_timeout": geometry_timeout,
                "upper_timeout": 5,
                "final_upper_timeout": 20,
                "certificate_timeout": certificate_timeout,
                "exact_candidates": exact_candidates,
                "ratpoints_backend": backend,
                "ratpoints": ratpoints,
                "include_secondary_providers": bool(include_secondary),
            }
            if pipeline_choice:
                pipeline_target = (
                    {
                        "torsion_group": torsion,
                        "include_secondary_providers": bool(include_secondary),
                    }
                    if search_by == "Torsion group"
                    else {
                        "plugin_id": plugin.id,
                        "variant_id": variant.id,
                    }
                )
                jid = launch_with_pipeline(
                    ctx,
                    db,
                    pipeline_choice=pipeline_choice,
                    target_mode="torsion" if search_by == "Torsion group" else "family",
                    target=pipeline_target,
                    run_config={
                        "target_rank": target_rank,
                        "retention_floor": retention_floor,
                        "certificate_timeout": certificate_timeout,
                        "exact_candidates": exact_candidates,
                        "ratpoints_backend": backend,
                        "ratpoints": ratpoints,
                    },
                    native_kind="geometry_search",
                    native_label="Geometry",
                    native_command=[],
                    native_metadata={
                        "search_mode": "geometry",
                        "plugin_id": None if plugin is None else plugin.id,
                        "plugin_variant": None if variant is None else variant.id,
                        "torsion_group": torsion,
                        "target_rank": target_rank,
                    },
                )
                st.success(f"Started Builder geometry search as job #{jid}.")
                st.rerun()
            else:
                if search_by == "Torsion group":
                    cid = create_campaign(
                        db,
                        plugin_id="__torsion_geometry__",
                        plugin_version=None,
                        variant_id=str(torsion),
                        family_spec=f"torsion:{torsion}",
                        family_name=f"Torsion {torsion}",
                        target_rank=target_rank,
                        search_mode="geometry_torsion",
                        torsion_group=torsion,
                        retention_floor=retention_floor,
                        stop_on_target=stop_on_target,
                        config=common_config,
                    )
                else:
                    cid = create_campaign(
                        db,
                        plugin_id=plugin.id,
                        plugin_version=plugin.version,
                        variant_id=variant.id,
                        family_spec=variant.family_spec,
                        family_name=str(variant.manifest.get("curve_family_name") or plugin.name),
                        target_rank=target_rank,
                        search_mode="geometry",
                        torsion_group=None,
                        retention_floor=retention_floor,
                        stop_on_target=stop_on_target,
                        config=common_config,
                    )
                campaign = get_campaign(db, cid)
                jid = _launch(
                    db, ctx, campaign,
                    label=(
                        f"Geometry · torsion {torsion}"
                        if search_by == "Torsion group"
                        else f"Geometry · {plugin.name} · {variant.name}"
                    ),
                )
                st.success(f"Started Geometry campaign #{cid} as job #{jid}.")
                st.rerun()

    campaigns = [
        r for r in list_campaigns(db, limit=25, top_level_only=True)
        if str(r["search_mode"] or "").startswith("geometry")
    ]
    if campaigns:
        with st.container(border=True):
            section_title(
                "Geometry campaigns",
                "Campaign state is durable; heuristic screening never counts as rank evidence.",
            )
            st.dataframe(_recent_table(campaigns), hide_index=True, width="stretch")
            resumable = [r for r in campaigns if str(r["status"]) in RESUMABLE]
            resumable_by_id = {int(r["id"]): r for r in resumable}
            if resumable and not blocking:
                chosen_id = st.selectbox(
                    "Resume campaign",
                    list(resumable_by_id),
                    format_func=lambda cid: (
                        f"#{cid} · "
                        f"{resumable_by_id[cid]['family_name'] or resumable_by_id[cid]['plugin_id']} · "
                        f"best ≥{int(resumable_by_id[cid]['best_lower'] or 0)} · "
                        f"{resumable_by_id[cid]['status']}"
                    ),
                    key="geometry-resume-select",
                )
                chosen = resumable_by_id[int(chosen_id)]
                if st.button("Resume selected Geometry", width="stretch"):
                    jid = _launch(db, ctx, chosen)
                    st.success(f"Resumed campaign #{int(chosen['id'])} as job #{jid}.")
                    st.rerun()

            campaigns_by_id = {int(r["id"]): r for r in campaigns}
            selected_id = st.selectbox(
                "Inspect campaign",
                list(campaigns_by_id),
                format_func=lambda cid: (
                    f"#{cid} · "
                    f"{campaigns_by_id[cid]['family_name'] or campaigns_by_id[cid]['plugin_id']}"
                ),
                key="geometry-inspect-select",
            )
            selected = campaigns_by_id[int(selected_id)]
            inspect_campaign = selected
            if str(selected["search_mode"] or "") == "geometry_torsion":
                reconcile_child_campaign_lifecycle(db, int(selected["id"]))
                children = list_campaigns(
                    db,
                    limit=250,
                    parent_campaign_id=int(selected["id"]),
                )
                if children:
                    st.caption(
                        f"Provider campaigns for exact {selected['torsion_group']} · "
                        "each provider exact-checks specialized torsion before geometry work"
                    )
                    st.dataframe(
                        [{
                            "provider": f"{r['family_name']} · {r['variant_id']}",
                            "status": r["status"],
                            "done": f"{int(r['candidates_done'])}/{int(r['candidates_total'])}",
                            "best ≥": int(r["best_lower"] or 0),
                            "curve": r["best_curve_id"],
                            "torsion misses": int(r["torsion_mismatch_count"] or 0),
                        } for r in children],
                        hide_index=True,
                        width="stretch",
                    )
                    children_by_id = {int(r["id"]): r for r in children}
                    inspect_child_id = st.selectbox(
                        "Inspect provider fibers",
                        list(children_by_id),
                        format_func=lambda cid: (
                            f"#{cid} · {children_by_id[cid]['family_name']} · "
                            f"best ≥{int(children_by_id[cid]['best_lower'] or 0)}"
                        ),
                        key="geometry-provider-inspect",
                    )
                    inspect_campaign = children_by_id[int(inspect_child_id)]
                else:
                    st.caption("No provider child campaign has started yet.")

            trials = campaign_trials(db, int(inspect_campaign["id"]), limit=100)
            if trials:
                st.dataframe(
                    [{
                        "t": r["parameter"],
                        "status": r["status"],
                        "tier": r["tier"],
                        "score": r["score"],
                        "rank ≥": int(r["rigorous_lower"] or 0),
                        "upper": r["rigorous_upper"],
                        "curve": r["curve_id"],
                    } for r in trials],
                    hide_index=True,
                    width="stretch",
                )




def page(db, ctx):
    return render(db, ctx, embedded=False)
