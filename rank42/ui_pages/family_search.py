from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from rank42.candidates import (
    candidate_rows_from_pool_offset,
    list_pools,
    pool_resume_offset,
)
from rank42.plugins import (
    family_rank_claim,
    get_plugin,
    get_variant,
    is_enabled,
    load_adapter,
    plugin_fingerprints,
    search_presets_for_variant,
    variant_for_family_spec,
    variant_torsion_groups,
    variant_torsion_record,
)
from rank42.pipeline_catalog import normalize_pipeline, pipeline_preset_options, template_stages, validate_pipeline
from rank42.search_config import adapter_search_option_defs, pipeline_search_config, resolve_plugin_search_config, search_preset_provenance
from .common import (
    launch_with_pipeline,
    ratpoints_selector,
    search_pipeline_selector,
    section_title,
    select_row,
    setting,
    title,
)


def _widget_default_kwargs(key, *, value=None, index=None):
    """Avoid Streamlit's default+Session-State warning after preset callbacks."""
    if key in st.session_state:
        return {}
    if index is not None:
        return {"index": int(index)}
    return {"value": value}


def _render_option(rec, keyprefix):
    key_name = rec["key"]
    typ = rec.get("type", "str")
    label = rec.get("label", key_name)
    default = rec.get("default")
    help_text = str(rec.get("help") or "").strip() or None
    key = f"{keyprefix}-{key_name}"
    if typ == "int":
        return st.number_input(
            label,
            min_value=rec.get("min"),
            max_value=rec.get("max"),
            step=1,
            key=key,
            help=help_text,
            **_widget_default_kwargs(key, value=int(default or 0)),
        )
    if typ == "bool":
        return st.checkbox(
            label,
            key=key,
            help=help_text,
            **_widget_default_kwargs(key, value=bool(default)),
        )
    if typ == "choice":
        choices = rec.get("choices") or []
        idx = choices.index(default) if default in choices else 0
        return st.selectbox(
            label,
            choices,
            key=key,
            help=help_text,
            **_widget_default_kwargs(key, index=idx),
        )
    return st.text_input(
        label,
        key=key,
        help=help_text,
        **_widget_default_kwargs(key, value=str(default or "")),
    )


def _group_option_defs(defs):
    """Return non-empty plugin option groups in stable adapter order."""
    groups = []
    by_name = {}
    for rec in defs or []:
        name = str(rec.get("group") or "Search options").strip() or "Search options"
        group = by_name.get(name)
        if group is None:
            group = {
                "name": name,
                "help": str(rec.get("group_help") or "").strip(),
                "columns": max(1, min(4, int(rec.get("group_columns") or 2))),
                "expanded": bool(rec.get("group_expanded", True)),
                "records": [],
            }
            by_name[name] = group
            groups.append(group)
        elif not group["help"] and rec.get("group_help"):
            group["help"] = str(rec.get("group_help") or "").strip()
        group["records"].append(rec)
    return [group for group in groups if group["records"]]


def _render_option_groups(defs, keyprefix, *, border=True, allow_expanders=True):
    """Render grouped plugin controls and return their current values."""
    values = {}
    for group in _group_option_defs(defs):
        use_expander = allow_expanders and not bool(group["expanded"])
        target = (
            st.expander(group["name"], expanded=False)
            if use_expander
            else st.container(border=border)
        )
        with target:
            if not use_expander:
                st.markdown(f"**{group['name']}**")
            if group["help"]:
                st.caption(group["help"])
            records = group["records"]
            col_count = min(int(group["columns"]), max(1, len(records)))
            cols = st.columns(col_count)
            for i, rec in enumerate(records):
                with cols[i % col_count]:
                    values[rec["key"]] = _render_option(rec, keyprefix)
    return values

def _preset_option_value(rec, value):
    typ = rec.get("type", "str")
    if typ == "int":
        return int(value)
    if typ == "bool":
        return bool(value)
    return str(value)


def _apply_family_preset(plugin_id, pool_id, preset, defs, total, compact):
    settings = dict(preset.get("family") or {})
    by_key = {str(rec.get("key")): rec for rec in defs}
    for name, value in settings.items():
        if name == "limit":
            count_key = (
                f"dashboard-family-count-{pool_id}"
                if compact
                else f"family-count-{pool_id}"
            )
            st.session_state[count_key] = min(max(1, int(value)), max(1, int(total)))
            continue
        rec = by_key.get(str(name))
        if rec is None:
            raise ValueError(
                f"preset contains unknown Family search option {name!r}"
            )
        st.session_state[f"fam-{plugin_id}-{name}"] = _preset_option_value(rec, value)
    st.session_state[f"family-preset-{pool_id}"] = str(preset.get("id") or "")


def _family_preset_modified(preset, opts, count, defs, total):
    expected = dict(preset.get("family") or {})
    known = {str(rec.get("key")) for rec in defs}
    for name, value in expected.items():
        if name == "limit":
            expected_limit = min(max(1, int(value)), max(1, int(total)))
            if int(count) != expected_limit:
                return True
        elif name in known and str(opts.get(name)) != str(value):
            return True
    return False


def _family_pipeline_choice(name, goal_rank, stages):
    stages = normalize_pipeline(stages)
    errors = validate_pipeline("family", stages)
    if errors:
        raise ValueError("family search pipeline is incompatible: " + "; ".join(errors))
    return {
        "pipeline": {
            "id": None,
            "name": str(name),
            "target_mode": "family",
            "stages": stages,
            "config": {},
        },
        "target_rank": max(1, int(goal_rank)),
    }


def _plugin_family_pipeline_choice(
    plugin_name,
    options,
    goal_rank,
    *,
    resolved_config=None,
):
    """Translate Plugin Family controls into the Pipeline-owned adapter stage."""
    options = dict(options or {})
    stage_options = {
        key: value for key, value in options.items()
        if key not in {
            "limit", "ratpoints", "ratpoints_backend",
            "plugin_variant", "family_spec",
        }
    }
    stage_options["target_lower"] = max(1, int(goal_rank))
    certificate_timeout = max(
        1, int(options.get("certificate_timeout") or 120)
    )
    exact_candidates = max(1, int(options.get("exact_candidates") or 64))
    return _family_pipeline_choice(
        f"{plugin_name} · Plugin family search",
        goal_rank,
        [
            {"id": "nagao_screen"},
            {
                "id": "family_baseline",
                "config": {
                    "certificate_timeout": certificate_timeout,
                    "exact_candidates": exact_candidates,
                },
            },
            {
                "id": "plugin_family_search",
                "config": {
                    "options": stage_options,
                    "resolved_profile": {
                        key: resolved_config.get(key)
                        for key in (
                            "plugin_id", "plugin_version", "variant_id",
                            "family_spec", "context", "preset_id", "config_hash",
                        )
                    } if resolved_config else {},
                    "timeout": 3600,
                    "retry_policy": "manual",
                    "retry_timeout": 7200,
                    "certificate_timeout": certificate_timeout,
                    "exact_candidates": exact_candidates,
                },
            },
            {
                "id": "independence",
                "config": {
                    "certificate_timeout": certificate_timeout,
                    "max_candidates": exact_candidates,
                },
            },
            "stop_goal",
        ],
    )


def _free_family_pipeline_choice(
    *,
    quick_timeout,
    strong_timeout,
    depth,
    goal_rank,
):
    """Translate the visible FREE Family controls into a Pipeline recipe."""
    quick_timeout = max(1, int(quick_timeout))
    strong_timeout = max(1, int(strong_timeout))
    stages = [
        {"id": "nagao_screen"},
        {
            "id": "pari_upper_gate",
            "config": {
                "timeout": quick_timeout,
                "eliminate_below_goal": True,
            },
        },
    ]
    if str(depth) != "PARI screen only":
        stages.extend([
            {
                "id": "mwrank_covering",
                "config": {
                    "timeout": strong_timeout,
                    "retry_policy": "manual",
                    "retry_timeout": max(strong_timeout, 3 * strong_timeout),
                    "first_limit": 40,
                    "second_limit": 20,
                    "exact_candidates": 64,
                },
            },
            {
                "id": "independence",
                "config": {
                    "certificate_timeout": 120,
                    "max_candidates": 64,
                },
            },
            "stop_goal",
        ])
    return _family_pipeline_choice(
        "FREE family · Current controls",
        goal_rank,
        stages,
    )


def _torsion_pipeline_options():
    options = []
    for rec in pipeline_preset_options("torsion"):
        stages = template_stages(rec["id"], "torsion")
        if not stages:
            continue
        if validate_pipeline("torsion", stages):
            continue
        options.append({
            "id": str(rec["id"]),
            "label": str(rec["label"]),
            "description": str(rec["description"]),
            "stages": stages,
        })
    return options


def _torsion_pipeline_choice(preset_id, goal_rank):
    options = {rec["id"]: rec for rec in _torsion_pipeline_options()}
    rec = options.get(str(preset_id)) or options.get("auto")
    if rec is None:
        raise ValueError("no valid torsion-aware pipeline preset is available")
    return {
        "pipeline": {
            "id": None,
            "name": rec["label"],
            "target_mode": "torsion",
            "stages": rec["stages"],
            "config": {},
        },
        "target_rank": max(1, int(goal_rank)),
    }


def render(db, ctx, *, embedded=False, compact=False, only_unsearched=None):
    if not embedded:
        title(
            "Family Search",
            "Operate on a stored candidate pool. Torsion mode exact-checks prescribed torsion before rank work; "
            "Plugin mode uses family-native search, while FREE mode screens the specialized Weierstrass model.",
            "Search",
        )

    pools = list_pools(db, limit=200)
    if not pools:
        with st.container(border=not compact):
            if compact:
                st.caption("No candidate pools yet.")
            else:
                section_title("No candidate pools yet", "Generate a Nagao-ranked pool before launching a family search.")
            if st.button("Generate candidates", type="primary", width="stretch"):
                st.session_state["rh_page"] = "Candidates"
                st.rerun()
        return

    default_id = st.session_state.get("family_search_pool_id")
    idx = (
        next((i for i, p in enumerate(pools) if int(p["id"]) == int(default_id)), 0)
        if default_id
        else 0
    )
    pool = select_row(
        "Candidate pool",
        pools,
        index=idx,
        format_func=lambda p: f"#{p['id']} · {p['name']} · {p['candidate_count']} candidates",
        key="family-pool-select",
        label_visibility="collapsed" if compact and embedded else "visible",
    )
    st.session_state["family_search_pool_id"] = int(pool["id"])
    try:
        plugin = get_plugin(ctx.project_root, pool["plugin_id"])
    except Exception as exc:
        st.error(f"Pool plugin unavailable: {exc}")
        return
    if plugin.plugin_type != "family":
        st.error("This pool references an Extension plugin, not a Family plugin.")
        return

    enabled = is_enabled(db, plugin)
    variant = variant_for_family_spec(plugin, pool["family_spec"]) or get_variant(plugin)
    current_fp = plugin_fingerprints(plugin, variant)
    presets = tuple(p for p in search_presets_for_variant(plugin, variant) if p.get("family"))
    torsion_groups = tuple(variant_torsion_groups(plugin, variant))
    torsion_record = variant_torsion_record(plugin, variant)
    drift = []
    if str(pool["plugin_version"] or "") != str(plugin.version or ""):
        drift.append(f"plugin version {pool['plugin_version'] or '—'} → {plugin.version}")
    for col, label in [
        ("family_sha256", "family"),
        ("adapter_sha256", "adapter"),
        ("plugin_manifest_sha256", "manifest"),
    ]:
        old = pool[col] if col in pool.keys() else None
        new = current_fp.get(col)
        if old and new and old != new:
            drift.append(f"{label} SHA-256 changed")

    resume = pool_resume_offset(db, pool["id"])
    total = int(pool["candidate_count"])
    remaining = max(0, total - int(resume))

    if not compact:
        claim = family_rank_claim(plugin, variant)
        metrics = st.columns(4)
        metrics[0].metric("Pool candidates", total)
        metrics[1].metric("Resume offset", int(resume), help="First pool position not durably completed.")
        metrics[2].metric("Remaining", remaining)
        if claim["effective_lower"] is not None:
            rank_metric = "Verified generic lower" if claim["verified_lower"] is not None else "Generic rank"
            metrics[3].metric(rank_metric, f"≥ {claim['effective_lower']}")
        elif claim["historical_lower"] is not None:
            metrics[3].metric("Historical generic claim", f"≥ {claim['historical_lower']}")
            st.caption(claim["status"] or "Rank Hunter verification pending")
        else:
            metrics[3].metric("Generic rank", "—")

    if compact:
        launch_col = st.container()
        guide_col = None
    else:
        launch_col, guide_col = st.columns([1.12, 0.88])

    with launch_col:
        with st.container(border=not compact):
            if not compact:
                section_title("Search setup", "Choose backend, candidate slice, and the exact search strategy for this stored pool.")
                st.caption(
                    f"Detected: **{plugin.name} v{plugin.version}**"
                    f"{f' · {variant.name} (`{variant.id}`)' if len(plugin.variants) > 1 else ''} · "
                    f"{'enabled' if enabled else 'disabled'}"
                )

            drift_ok = True
            if drift:
                st.warning("This pool was generated with different plugin content: " + " · ".join(drift))
                drift_ok = st.checkbox(
                    "I understand this run will not exactly reproduce the original pool environment.",
                    key=f"pool-drift-{pool['id']}",
                )
            elif not compact:
                if pool["family_sha256"] if "family_sha256" in pool.keys() else None:
                    st.success("Installed family/adapter fingerprints match this pool.")
                else:
                    st.info(
                        "Legacy pool: no content fingerprint is stored; only plugin/version metadata can be compared."
                    )

            mode_key = f"family-search-mode-{int(pool['id'])}"
            modes = []
            if torsion_groups:
                modes.append("Torsion")
            if "family_search" in plugin.capabilities:
                modes.append("Plugin")
            modes.append("FREE")
            if st.session_state.get(mode_key) not in modes:
                st.session_state[mode_key] = modes[0]
            if compact:
                m1, m2 = st.columns(2, gap="small")
                with m1:
                    search_mode = st.selectbox("Backend", modes, key=mode_key)
                with m2:
                    rp_backend, rp_exec = ratpoints_selector(db, key=f"family-ratpoints-{pool['id']}", label="Point engine")
                s1, s2 = st.columns([1.0, 1.15], gap="small", vertical_alignment="bottom")
                with s1:
                    count_key = f"dashboard-family-count-{pool['id']}"
                    count = int(
                        st.number_input(
                            "Candidates",
                            min_value=1,
                            max_value=max(1, total),
                            step=1,
                            key=count_key,
                            **_widget_default_kwargs(
                                count_key,
                                value=min(20, max(1, remaining or total)),
                            ),
                        )
                    )
                with s2:
                    unsearched = st.checkbox(
                        "Only unsearched",
                        value=True if only_unsearched is None else bool(only_unsearched),
                        key=f"dashboard-family-unsearched-{pool['id']}",
                    )
                max_offset = max(0, total - 1)
                default_offset = min(resume, max_offset)
                offset = default_offset
            else:
                search_mode = st.radio("Backend", modes, horizontal=True, key=mode_key)
                c1, c2, c3 = st.columns(3)
                max_offset = max(0, total - 1)
                default_offset = min(resume, max_offset)
                offset = int(
                    c1.number_input(
                        "Pool offset",
                        min_value=0,
                        max_value=max_offset,
                        value=default_offset,
                        step=1,
                        help="Absolute zero-based position. Defaults to first uncompleted candidate.",
                    )
                )
                count_key = f"family-count-{pool['id']}"
                count = int(
                    c2.number_input(
                        "Candidates",
                        min_value=1,
                        max_value=max(1, total),
                        step=1,
                        key=count_key,
                        **_widget_default_kwargs(
                            count_key,
                            value=min(20, max(1, total)),
                        ),
                    )
                )
                unsearched = c3.checkbox(
                    "Only unsearched",
                    value=True,
                    key=f"family-unsearched-{pool['id']}",
                )
                rp_backend, rp_exec = ratpoints_selector(db, key=f"family-ratpoints-{pool['id']}")

            opts = {}
            adapter = None
            defs = []
            # SYSTEM-16: these settings seed editable FREE-mode budgets
            # only. Plugin/Pipeline-owned timeout policy remains independent.
            quick_timeout = int(setting(db, "pari_rank_timeout", 300))
            strong_timeout = int(setting(db, "deep_cert_timeout", 900))
            depth = "PARI + strong mwrank descent"
            torsion_group = torsion_groups[0] if torsion_groups else None
            torsion_goal = (
                int(torsion_record["goal_rank"])
                if torsion_record is not None
                else 1
            )
            torsion_strategy = "auto"

            if search_mode == "Plugin":
                if not enabled:
                    st.warning("This plugin is disabled. Activate it in Plugins or use FREE mode.")
                elif "family_search" not in plugin.capabilities:
                    st.warning("This plugin does not declare a family-search adapter.")
                else:
                    adapter = load_adapter(plugin)
                    defs = adapter_search_option_defs(
                        plugin,
                        variant,
                        adapter,
                        context="family",
                    )
                    # The shared Candidates control above owns the launch limit.
                    # Suppress adapter-level `limit` metadata so plugins cannot
                    # render a second Candidates input for the same value.
                    defs = [rec for rec in defs if str(rec.get("key") or "") != "limit"]
                    if defs:
                        if compact:
                            with st.expander("Search options", expanded=False):
                                opts.update(
                                    _render_option_groups(
                                        defs,
                                        f"fam-{plugin.id}",
                                        border=False,
                                        allow_expanders=False,
                                    )
                                )
                                offset = int(
                                    st.number_input(
                                        "Pool offset",
                                        min_value=0,
                                        max_value=max_offset,
                                        value=default_offset,
                                        step=1,
                                        key=f"dashboard-family-offset-{pool['id']}",
                                    )
                                )
                        else:
                            st.caption("Plugin search controls")
                            opts.update(_render_option_groups(defs, f"fam-{plugin.id}"))
                    elif compact:
                        with st.expander("Advanced", expanded=False):
                            offset = int(
                                st.number_input(
                                    "Pool offset",
                                    min_value=0,
                                    max_value=max_offset,
                                    value=default_offset,
                                    step=1,
                                    key=f"dashboard-family-offset-{pool['id']}",
                                )
                            )
                    opts["limit"] = count
                    opts["ratpoints"] = rp_exec
                    opts["ratpoints_backend"] = rp_backend
                    opts["plugin_variant"] = variant.id
                    opts["family_spec"] = pool["family_spec"]
                    if not compact:
                        adapter_path = (
                            Path(plugin.adapter_path).resolve()
                            if plugin.adapter_path is not None
                            else None
                        )
                        st.caption(
                            "Resolved dispatch: **Plugin adapter**"
                            + (f" · `{adapter_path.name}`" if adapter_path else "")
                        )
            elif search_mode == "Torsion":
                if not enabled:
                    st.warning("This torsion-family plugin is disabled. Activate it in Plugins or use FREE mode.")
                elif not torsion_groups:
                    st.warning("This pool's family/variant does not declare a torsion group.")
                else:
                    if len(torsion_groups) > 1:
                        torsion_group = st.selectbox(
                            "Exact torsion group",
                            list(torsion_groups),
                            key=f"family-torsion-group-{pool['id']}",
                        )
                    else:
                        torsion_group = torsion_groups[0]
                        st.caption(f"Exact torsion target: **{torsion_group}**")
                    strategy_options = _torsion_pipeline_options()
                    strategy_ids = [rec["id"] for rec in strategy_options]
                    default_strategy = "auto" if "auto" in strategy_ids else strategy_ids[0]
                    torsion_strategy = st.selectbox(
                        "Torsion pipeline",
                        strategy_ids,
                        index=strategy_ids.index(default_strategy),
                        format_func=lambda value: next(
                            rec["label"] for rec in strategy_options if rec["id"] == value
                        ),
                        key=f"family-torsion-pipeline-{pool['id']}",
                        help="Runs Rank Hunter's torsion-mode Pipeline: Exact Torsion is a hard gate before point/rank work.",
                    )
                    goal_default = (
                        int(torsion_record["goal_rank"])
                        if torsion_record is not None
                        else 1
                    )
                    torsion_goal = int(
                        st.number_input(
                            "Rigorous rank goal",
                            min_value=1,
                            value=max(1, goal_default),
                            step=1,
                            key=f"family-torsion-goal-{pool['id']}",
                        )
                    )
                    st.caption(
                        "Every selected specialization is exact-checked for the requested torsion group before rank-search evidence counts."
                    )
            else:
                if compact:
                    with st.expander("Search options", expanded=False):
                        offset = int(
                            st.number_input(
                                "Pool offset",
                                min_value=0,
                                max_value=max_offset,
                                value=default_offset,
                                step=1,
                                key=f"dashboard-family-free-offset-{pool['id']}",
                            )
                        )
                        quick_timeout = int(
                            st.number_input(
                                "PARI quick timeout",
                                min_value=1,
                                value=quick_timeout,
                                step=10,
                                key=f"dashboard-family-quick-{pool['id']}",
                            )
                        )
                        strong_timeout = int(
                            st.number_input(
                                "Strong mwrank timeout",
                                min_value=1,
                                value=strong_timeout,
                                step=30,
                                key=f"dashboard-family-strong-{pool['id']}",
                            )
                        )
                        depth = st.selectbox(
                            "Depth",
                            ["PARI screen only", "PARI + strong mwrank descent"],
                            index=1,
                            key=f"dashboard-family-depth-{pool['id']}",
                        )
                else:
                    st.caption("Resolved dispatch: **FREE / Pipeline-native generic search**")
                    f1, f2, f3 = st.columns(3)
                    quick_timeout = int(
                        f1.number_input(
                            "PARI quick timeout",
                            min_value=1,
                            value=quick_timeout,
                            step=10,
                        )
                    )
                    strong_timeout = int(
                        f2.number_input(
                            "Strong mwrank timeout",
                            min_value=1,
                            value=strong_timeout,
                            step=30,
                        )
                    )
                    depth = f3.selectbox(
                        "Depth",
                        ["PARI screen only", "PARI + strong mwrank descent"],
                    )

            if resume >= total:
                st.caption("Pool complete — no unsearched candidates remain.")
            elif resume and not compact:
                st.caption(
                    f"Resume cursor is candidate **{resume + 1}**. Cancelled or in-flight candidates do not advance it."
                )

            claim = family_rank_claim(plugin, variant)
            generic_lower = claim.get("effective_lower")
            suggested_goal=(int(generic_lower) + 1) if generic_lower is not None else 8
            if search_mode == "Plugin":
                profile_goal = pipeline_search_config(
                    plugin,
                    variant,
                ).get("target_rank")
                if profile_goal is not None:
                    suggested_goal = max(1, int(profile_goal))
            if search_mode == "Torsion":
                pipeline_choice = _torsion_pipeline_choice(
                    torsion_strategy,
                    torsion_goal,
                )
            elif search_mode == "FREE":
                pipeline_choice = search_pipeline_selector(
                    db,
                    target_mode="family",
                    key=f"family-search-{pool['id']}",
                    suggested_goal=suggested_goal,
                    builtin_label="Built-in Pipeline · Current FREE controls",
                )
            else:
                pipeline_choice = search_pipeline_selector(
                    db,
                    target_mode="family",
                    key=f"family-search-{pool['id']}",
                    suggested_goal=suggested_goal,
                    builtin_label="Built-in Pipeline · Plugin family search",
                )

            launch_disabled = (
                (not drift_ok)
                or (search_mode == "Plugin" and (not enabled or adapter is None))
                or (search_mode == "Torsion" and (not enabled or not torsion_group))
                or (rp_backend == "GPU" and not rp_exec)
            )
            if compact:
                search_clicked = st.button(
                    "Start Search",
                    type="primary",
                    width="stretch",
                    disabled=launch_disabled,
                    key=f"family-launch-{pool['id']}-compact",
                )
            else:
                search_clicked = st.button(
                    "Launch search",
                    type="primary",
                    width="stretch",
                    disabled=launch_disabled,
                    key=f"family-launch-{pool['id']}-full",
                )
            if search_clicked:
                selected_rows = candidate_rows_from_pool_offset(
                    db,
                    pool["id"],
                    limit=count,
                    offset=offset,
                    only_unsearched=unsearched,
                )
                if not selected_rows:
                    st.warning("No candidates match this slice.")
                    return
                launch_mode = str(st.session_state.get(mode_key, search_mode))
                preset_provenance = None
                if launch_mode == "Plugin":
                    cmd = []
                    kind = "family_search"
                    if pipeline_choice.get("builtin"):
                        active_preset_id = str(
                            st.session_state.get(
                                f"family-preset-{pool['id']}"
                            ) or ""
                        ) or None
                        resolved_config = resolve_plugin_search_config(
                            plugin,
                            variant,
                            adapter,
                            context="family",
                            preset_id=active_preset_id,
                            overrides=opts,
                        )
                        effective_options = {
                            **resolved_config["options"],
                            **resolved_config["controls"],
                        }
                        active_preset = next(
                            (
                                rec for rec in presets
                                if str(rec.get("id") or "")
                                == str(active_preset_id or "")
                            ),
                            None,
                        )
                        preset_provenance = search_preset_provenance(
                            plugin,
                            variant,
                            context="family",
                            preset_id=active_preset_id,
                            effective_settings={
                                **effective_options,
                                "limit": int(count),
                                "ratpoints_backend": str(rp_backend),
                            },
                            modified=(
                                _family_preset_modified(
                                    active_preset,
                                    opts,
                                    count,
                                    defs,
                                    total,
                                )
                                if active_preset is not None
                                else False
                            ),
                        )
                        pipeline_choice = _plugin_family_pipeline_choice(
                            plugin.name,
                            effective_options,
                            pipeline_choice["target_rank"],
                            resolved_config=resolved_config,
                        )
                elif launch_mode == "Torsion":
                    cmd = []
                    kind = "torsion_pool_search"
                else:
                    cmd = []
                    kind = "free_family_search"
                    if pipeline_choice.get("builtin"):
                        pipeline_choice = _free_family_pipeline_choice(
                            quick_timeout=quick_timeout,
                            strong_timeout=strong_timeout,
                            depth=depth,
                            goal_rank=pipeline_choice["target_rank"],
                        )
                adapter_file = (
                    str(Path(plugin.adapter_path).resolve())
                    if launch_mode == "Plugin"
                    and plugin.adapter_path is not None
                    else None
                )
                generation = json.loads(pool["generation_json"] or "{}")
                launch_metadata = {
                    "pool_id": int(pool["id"]),
                    "plugin_id": plugin.id,
                    "plugin_variant": variant.id,
                    "chart_id": generation.get("chart_id"),
                    "native_family_key": generation.get("native_family_key"),
                    "chart_map_fingerprint": generation.get("chart_map_fingerprint"),
                    "search_mode": launch_mode,
                    "adapter_file": adapter_file,
                    "offset": offset,
                    "count": len(selected_rows),
                    "candidate_ids": [int(r["id"]) for r in selected_rows],
                    "candidate_rank_orders": [int(r["rank_order"]) for r in selected_rows],
                    "ratpoints_backend": rp_backend,
                    "torsion_group": torsion_group if launch_mode == "Torsion" else None,
                    "pool_family_sha256": pool["family_sha256"] if "family_sha256" in pool.keys() else None,
                    "installed_family_sha256": current_fp.get("family_sha256"),
                }
                if preset_provenance is not None:
                    launch_metadata["search_preset"] = preset_provenance
                launch_target_mode = "torsion" if launch_mode == "Torsion" else "family"
                launch_target = {
                    "plugin_id": plugin.id,
                    "variant_id": variant.id,
                    "candidate_pool_id": int(pool["id"]),
                    "candidate_ids": [int(r["id"]) for r in selected_rows],
                    "only_unsearched": bool(unsearched),
                }
                if launch_mode == "Torsion":
                    launch_target["torsion_group"] = str(torsion_group)
                    # Provider discovery may classify this exact source variant
                    # as secondary. Include it, then pipeline_runner restricts the
                    # provider list back to this pool's plugin/variant.
                    launch_target["include_secondary_providers"] = True
                run_config = {
                    "ratpoints_backend": rp_backend,
                    "ratpoints": rp_exec,
                    "certificate_timeout": 120,
                    "exact_candidates": 64,
                }
                if preset_provenance is not None:
                    run_config["search_preset"] = preset_provenance
                jid = launch_with_pipeline(
                    ctx,
                    db,
                    pipeline_choice=pipeline_choice,
                    target_mode=launch_target_mode,
                    target=launch_target,
                    run_config=run_config,
                    native_kind=kind,
                    native_label=f"{launch_mode} search · {pool['name']}",
                    native_command=cmd,
                    native_metadata=launch_metadata,
                    require_pipeline=True,
                )
                st.success(f"Started search job #{jid}.")

    if compact:
        return

    with guide_col:
        with st.container(border=True):
            section_title(
                "Search style",
                "Like Target Search, the left side owns execution controls. Family-owned presets fill the visible controls; torsion mode explains its exact-gated Pipeline here.",
            )
            if search_mode == "Torsion":
                strategy = next(
                    (
                        rec
                        for rec in _torsion_pipeline_options()
                        if rec["id"] == str(torsion_strategy)
                    ),
                    None,
                )
                st.markdown(f"**Exact torsion:** `{torsion_group}`")
                if torsion_record is not None:
                    st.caption(
                        f"Known record lower ≥{int(torsion_record['rank_lower'])} · "
                        f"default goal {int(torsion_record['goal_rank'])}"
                    )
                if strategy is not None:
                    st.markdown(f"**Pipeline:** {strategy['label']}")
                    st.caption(strategy["description"])
                st.info(
                    "This uses the existing torsion-mode Pipeline on the selected stored pool. "
                    "Exact Torsion is a hard gate; mismatching specializations are rejected before point/rank stages."
                )
            elif search_mode == "Plugin" and presets:
                pcols = st.columns(2)
                for i, preset in enumerate(presets):
                    with pcols[i % 2]:
                        st.button(
                            str(preset.get("label") or preset.get("id") or "Preset"),
                            key=f"family-preset-button-{pool['id']}-{preset.get('id')}",
                            width="stretch",
                            disabled=not enabled,
                            on_click=_apply_family_preset,
                            args=(plugin.id, int(pool["id"]), preset, defs, total, False),
                            help=str(preset.get("description") or "") or None,
                        )
                active_id = str(st.session_state.get(f"family-preset-{pool['id']}") or "")
                active = next((p for p in presets if str(p.get("id")) == active_id), None)
                if active is not None:
                    modified = _family_preset_modified(active, opts, count, defs, total)
                    label = str(active.get("label") or active.get("id"))
                    st.caption(f"Preset: **{label}**" + (" · Modified" if modified else ""))
                    if active.get("description"):
                        st.caption(str(active["description"]))
                    if active.get("candidate"):
                        st.caption("This preset also has family-specific a/b generation settings on **Candidates → Generate**.")
            elif search_mode == "Plugin":
                st.caption("This adapter has no family-owned search presets; use its visible controls on the left.")
            else:
                st.caption(
                    "FREE mode ignores family-specific subgroup/search geometry and runs the generic exact-curve search path."
                )
        with st.container(border=True):
            section_title(
                "How this search works",
                "The UI is only orchestration; the plugin/backend owns the mathematics.",
            )
            st.markdown(
                """
1. **Nagao order** prioritizes specializations; it does not predict a rank theorem.
2. **Torsion mode** reuses the Pipeline torsion gate on this exact stored pool; exact torsion must match before rank work counts.
3. **Plugin mode** delegates to the family adapter—native quartic/PGL₂ or whatever the plugin actually implements.
4. **Cheap misses finish early.** Exact model construction should be deferred until a hit when the active backend supports that workflow.
5. **Hits are mapped exactly** and checked against the known subgroup when that subgroup is actually available.
6. **Numerical novelty is scheduling evidence.** Rigorous lower bounds move only after an exact certificate.
7. **Resume is durable.** `[candidate done] i/n` advances the pool cursor only after candidate writes finish.
"""
            )
            st.divider()
            st.caption("Pool family specification")
            st.code(str(pool["family_spec"]), language="text")
            if len(plugin.variants) > 1:
                st.caption(f"Active variant: {variant.name} (`{variant.id}`)")
            if not enabled:
                if st.button("Open Plugins", width="stretch"):
                    st.session_state["rh_page"] = "Plugin Families"
                    st.rerun()


def page(db, ctx):
    return render(db, ctx, embedded=False)
