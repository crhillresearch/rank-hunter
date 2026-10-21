from __future__ import annotations

import json

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.plugins import discover_plugins
from rank42.manage_store import (
    NOTEBOOK_ENTRY_LABELS,
    NOTEBOOK_ENTRY_TYPES,
    NOTEBOOK_HANDOFF_STATES,
    NOTEBOOK_LINK_KINDS,
    NOTEBOOK_LINK_LABELS,
    current_campaign,
    add_campaign_note,
    campaign_notebook_entries,
    campaign_pinned_curve_ids,
    campaign_research_brief,
    campaign_snapshot,
    delete_campaign,
    get_campaign,
    pin_campaign_curve,
    set_current_campaign,
    unpin_campaign_curve,
    update_campaign,
)
from rank42.ui_components import dataframe, region, rh_key, tabs
from .campaign_handoff_panel import render_handoff as _handoff
from .campaigns_landing_panel import (
    CAMPAIGN_MANAGER_COMPONENT_DIR,
    campaign_add_button as _campaign_add_button,
    campaign_component_event as _campaign_component_event,
    campaign_family_choices as _campaign_family_choices,
    campaign_family_selector as _campaign_family_selector,
    campaign_label as _campaign_label,
    campaign_page_slice as _campaign_page_slice,
    campaign_page_style as _campaign_page_style,
    campaign_row as _campaign_row,
    campaign_row_meta as _campaign_row_meta,
    campaign_search_blob as _campaign_search_blob,
    campaign_status_label as _campaign_status_label,
    campaign_status_value as _campaign_status_value,
    create_campaign_dialog as _create_campaign_dialog,
    filtered_campaigns as _filtered_campaigns,
    render_campaign_header as _render_campaign_header,
    render_campaign_landing as _render_campaign_landing,
)


_CAMPAIGN_WORK_PAGES = {
    "Target",
    "Pipelines",
    "Independence",
    "Lattices & Heights",
    "Descent",
}

def _campaign_action_button_state(campaign, current, action):
    """Describe explicit lifecycle/current-context work handoff."""
    page = str(action.get("page") or "")
    current_id = None if current is None else int(current["id"])
    campaign_id = int(campaign["id"])
    status = str(campaign["status"] or "")
    computation = page in _CAMPAIGN_WORK_PAGES
    needs_current = computation and current_id != campaign_id
    needs_lifecycle = computation and status != "active"
    if not (needs_current or needs_lifecycle):
        return f"Open {page}", False

    if status == "paused":
        prefix = "Resume"
    elif status in {"completed", "archived"}:
        prefix = "Reopen"
    else:
        prefix = ""
    if needs_current:
        prefix = f"{prefix}, set current" if prefix else "Set current"
    return f"{prefix} & open {page}", True



def _edit_campaign(db, ctx, row, snapshot):
    with st.expander("Edit campaign", expanded=False):
        with st.form(f"campaign-edit-{int(row['id'])}"):
            name = st.text_input("Name", value=str(row["name"]))
            objective = st.text_area("Research objective", value=str(row["objective"] or ""))
            c1, c2, c3 = st.columns([0.7, 0.7, 2.2])
            status_options = ["active", "paused", "completed", "archived"]
            status = c1.selectbox(
                "Status",
                status_options,
                index=status_options.index(str(row["status"])),
                format_func=_campaign_status_label,
            )
            target_rank = c2.number_input(
                "Target rank",
                min_value=0,
                value=int(row["target_rank"] or 0),
                step=1,
                help="Use 0 when the campaign has no numeric rank target.",
            )
            with c3:
                family_choice = _campaign_family_selector(
                    ctx,
                    key=f"campaign-edit-family-{int(row['id'])}",
                    current_plugin_id=row["plugin_id"],
                    current_variant_id=row["variant_id"],
                    current_family=row["family"],
                )
            submitted = st.form_submit_button("Save changes", width="stretch")
        if submitted:
            if not name.strip():
                st.error("Campaign name is required.")
            elif (
                status in {"completed", "archived"}
                and (int(snapshot["running"]) + int(snapshot["queued"])) > 0
            ):
                st.error(
                    "Finish or stop active/queued campaign work before completing or archiving it."
                )
            else:
                update_campaign(
                    db,
                    int(row["id"]),
                    name=name.strip(),
                    objective=objective,
                    status=status,
                    target_rank=None if int(target_rank) <= 0 else int(target_rank),
                    plugin_id=family_choice["plugin_id"],
                    variant_id=family_choice["variant_id"],
                    family=family_choice["family"],
                )
                st.success("Campaign updated.")
                st.rerun()


def _job_rows(snapshot):
    out = []
    summaries = {int(row["id"]): summary for row, summary in snapshot["summaries"]}
    for row in snapshot["jobs"]:
        summary = summaries.get(int(row["id"]), {})
        out.append({
            "job": int(row["id"]),
            "status": row["status"],
            "kind": row["kind"],
            "label": row["label"],
            "best rigorous": summary.get("best_rigorous_lower"),
            "completed": (
                summary.get("candidates_completed")
                or summary.get("trials_completed")
                or summary.get("shortlist_searched")
            ),
            "created": row["created_at"],
            "finished": row["finished_at"],
        })
    return out



def _go_campaign_action(action):
    page = str(action.get("page") or "")
    curve_id = action.get("curve_id")
    if curve_id is not None:
        curve_id = int(curve_id)
        if page in {"Analyze", "Case Board", "Curves"}:
            st.session_state["curves_detail_id"] = curve_id
            page = "Curves"
        elif page == "Independence":
            st.session_state.pop(rh_key("independence-curve"), None)
            st.session_state["independence_curve_id"] = curve_id
        elif page == "Lattices & Heights":
            st.session_state.pop(rh_key("lattice-curve-select"), None)
            st.session_state["analysis_curve_id"] = curve_id
        elif page == "Descent":
            st.session_state.pop(rh_key("descent-curve-select"), None)
            st.session_state["descent_curve_id"] = curve_id
        elif page == "Target":
            st.session_state.pop(rh_key("target-curve-select"), None)
            st.session_state["target_curve_id"] = curve_id
    if page == "Jobs" and action.get("jobs_section"):
        st.session_state["jobs_section_pending"] = str(action["jobs_section"])
    if page == "Pipelines" and action.get("pipeline_run_id") is not None:
        st.session_state["byo_focus_run_id"] = int(action["pipeline_run_id"])
        st.session_state["builder_main_view"] = "Editor"
    st.session_state["rh_page"] = page


def _campaign_pinned_curves(db, campaign_id):
    pinned_ids = campaign_pinned_curve_ids(db, int(campaign_id))
    rows = []
    if pinned_ids:
        marks = ",".join("?" for _ in pinned_ids)
        rows = db.execute(
            f"""SELECT id,family,parameter FROM curves
                WHERE id IN ({marks}) ORDER BY id""",
            pinned_ids,
        ).fetchall()

    with st.expander(
        f"Pinned curves ({len(rows)})",
        expanded=bool(rows),
    ):
        st.caption(
            "Pins record Campaign research association only. "
            "Rank, points, evidence, and arithmetic remain owned by the Curve record."
        )
        if rows:
            dataframe(
                [
                    {
                        "curve": f"#{int(row['id'])}",
                        "family": str(row["family"] or "—"),
                        "parameter": str(row["parameter"] or "—"),
                    }
                    for row in rows
                ],
                semantic=f"campaign-pinned-curves-{int(campaign_id)}",
                width="stretch",
                hide_index=True,
            )

        pin_col, action_col = st.columns(
            [4.5, 1.5],
            vertical_alignment="bottom",
        )
        curve_id = pin_col.number_input(
            "Curve ID",
            min_value=1,
            step=1,
            key=f"campaign-pin-curve-id-{int(campaign_id)}",
        )
        if action_col.button(
            "Pin curve",
            width="stretch",
            icon=":material/push_pin:",
            key=f"campaign-pin-curve-{int(campaign_id)}",
        ):
            try:
                pin_campaign_curve(db, int(campaign_id), int(curve_id))
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.toast(f"Pinned curve #{int(curve_id)} to this campaign.")
                st.rerun()

        if rows:
            unpin_col, remove_col = st.columns(
                [4.5, 1.5],
                vertical_alignment="bottom",
            )
            selected = unpin_col.selectbox(
                "Pinned curve",
                [int(row["id"]) for row in rows],
                format_func=lambda value: f"Curve #{int(value)}",
                key=f"campaign-unpin-curve-id-{int(campaign_id)}",
            )
            if remove_col.button(
                "Unpin",
                width="stretch",
                icon=":material/link_off:",
                key=f"campaign-unpin-curve-{int(campaign_id)}",
            ):
                unpin_campaign_curve(db, int(campaign_id), int(selected))
                st.toast(f"Unpinned curve #{int(selected)}.")
                st.rerun()


def _research_brief(db, brief):
    campaign = brief["snapshot"]["campaign"]
    best = brief["best_curve"]
    best_state = brief.get("best_state")
    action = brief["next_action"]
    current = current_campaign(db)

    with region("campaign-research-brief", border=True):
        st.subheader("Research brief")
        st.caption(
            "Read-only summary of evidence traceable to this campaign. "
            "Rank statements below come from stored rigorous evidence, not heuristic scores."
        )

        if best is None:
            st.info(
                "No retained curve is traceable to this campaign yet. "
                "The campaign may still have running jobs or completed searches that produced no retained positive-rank curve."
            )
        else:
            if best_state and best_state.get("inconsistent"):
                upper = best_state.get("rigorous_upper")
                state_text = (
                    f"conflict: ≥{int(best_state['rigorous_lower'] or 0)}"
                    + (f" / ≤{int(upper)}" if upper is not None else "")
                )
            elif best_state and best_state.get("exact_rank") is not None:
                state_text = f"= {int(best_state['exact_rank'])}"
            else:
                state_text = f"≥ {int((best_state or {}).get('rigorous_lower') or 0)}"
            gap_text = (
                "conflict"
                if brief.get("target_blocked_by_conflict")
                else (
                    "—"
                    if brief["target_gap"] is None
                    else ("met" if brief["target_reached"] else str(int(brief["target_gap"])))
                )
            )
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Leading curve", f"#{int(best['id'])}")
            c2.metric("Rigorous rank", state_text)
            c3.metric("Target gap", gap_text)
            c4.metric("Unresolved exact points", int(best["actionable_points"] or 0))
            st.caption(
                f"{best['family']} · t={best['parameter']} · "
                f"{int(best['exact_points'] or 0)} exact stored point(s)"
            )

        conflicts = brief.get("conflicted_curves") or []
        if conflicts:
            count = len(conflicts)
            noun = "curve has" if count == 1 else "curves have"
            st.warning(
                f"{count} campaign {noun} conflicting rigorous rank bounds. "
                "Conflicted curves are excluded from campaign leadership and target completion "
                "until their evidence is reconciled."
            )

        _campaign_pinned_curves(db, int(campaign["id"]))

        if action:
            st.markdown("#### Next useful action")
            st.markdown(f"**{action['title']}**")
            st.caption(str(action["why"]))
            action_label, needs_activation = _campaign_action_button_state(
                campaign,
                current,
                action,
            )
            if needs_activation:
                lifecycle = str(campaign["status"] or "")
                current_id = None if current is None else int(current["id"])
                context_note = (
                    "set this as the current campaign"
                    if current_id != int(campaign["id"])
                    else "use this current campaign"
                )
                lifecycle_note = (
                    " It will also resume/reopen the campaign first."
                    if lifecycle != "active"
                    else ""
                )
                st.caption(
                    "This is a computation-bearing workspace. Opening it from this campaign "
                    f"will {context_note} so any new launch is attributed here."
                    + lifecycle_note
                )
            if st.button(
                action_label,
                type="primary",
                width="stretch",
                key=f"campaign-next-{int(campaign['id'])}-{action['page']}",
            ):
                if needs_activation:
                    if str(campaign["status"] or "") != "active":
                        update_campaign(db, int(campaign["id"]), status="active")
                    set_current_campaign(db, int(campaign["id"]))
                _go_campaign_action(action)
                st.rerun()

        promising = brief["promising_curves"]
        if promising:
            st.markdown("#### Strongest unresolved campaign curves")
            rows = []
            for rec in promising[:5]:
                curve = rec["curve"]
                state = rec["state"]
                if state.get("inconsistent"):
                    upper = state.get("rigorous_upper")
                    rank = (
                        f"conflict ≥{int(state['rigorous_lower'] or 0)}"
                        + (f" / ≤{int(upper)}" if upper is not None else "")
                    )
                elif state.get("exact_rank") is not None:
                    rank = f"= {int(state['exact_rank'])}"
                else:
                    rank = f"≥ {int(state['rigorous_lower'] or 0)}"
                rows.append({
                    "curve": f"#{int(curve['id'])}",
                    "rank": rank,
                    "family": str(curve["family"]),
                    "parameter": str(curve["parameter"]),
                    "unresolved points": int(rec["actionable_points"]),
                    "witness gap": int(rec["witness_gap"]),
                    "score": "—" if curve["score"] is None else f"{float(curve['score']):.4f}",
                })
            dataframe(
                rows,
                semantic=f"campaign-research-curves-{int(campaign['id'])}",
                width="stretch",
                hide_index=True,
            )


def _overview(db, ctx, snapshot, brief):
    row = snapshot["campaign"]
    target = row["target_rank"]
    best_state = brief.get("best_state")
    current_lower = int(brief.get("best_lower") or 0)
    current_text = "—"
    if best_state is not None:
        if best_state.get("inconsistent"):
            current_text = "conflict"
        elif best_state.get("exact_rank") is not None:
            current_text = f"= {int(best_state['exact_rank'])}"
        elif current_lower > 0:
            current_text = f"≥ {current_lower}"

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Status", str(row["status"]).title())
    c2.metric("Current rigorous", current_text)
    c3.metric("Target", f"≥ {int(target)}" if target is not None else "—")
    c4.metric("Running", int(snapshot["running"]))
    c5.metric("Completed work", int(snapshot["completed"]))
    c6.metric("Candidates done", f"{int(snapshot['candidates_done']):,}")

    if target is not None and int(target) > 0:
        if brief.get("target_blocked_by_conflict"):
            st.warning(
                "Target progress is suspended because the leading campaign curve has "
                "conflicting rigorous rank bounds. Resolve the evidence conflict first."
            )
        else:
            st.progress(
                min(1.0, float(current_lower) / float(target)),
                text=f"Current rigorous lower {current_lower} / target {int(target)}",
            )

    if target is None:
        outcome = "No numeric rank target; completion is an operator lifecycle decision."
    elif brief.get("target_blocked_by_conflict"):
        outcome = "Target outcome unresolved because rigorous evidence conflicts."
    elif brief["target_reached"]:
        outcome = f"Rank target ≥{int(target)} met by current consistent rigorous evidence."
    elif brief["best_curve"] is None:
        outcome = f"Rank target ≥{int(target)} not yet supported by a retained campaign curve."
    else:
        outcome = (
            f"Rank target ≥{int(target)} not met; current rigorous frontier is ≥{current_lower} "
            f"(gap {int(brief['target_gap'])})."
        )
    st.caption(
        f"Research outcome: {outcome} Campaign status is lifecycle state, not a rank-proof claim."
    )

    completion = brief.get("completion_snapshot")
    drift = brief.get("completion_drift")
    if completion is not None:
        completed_target = completion.get("target_rank")
        completed_target_text = (
            "no numeric target"
            if completed_target is None
            else f"target ≥{int(completed_target)}"
        )
        completed_curve = completion.get("best_curve_id")
        completed_curve_text = (
            "no retained leader"
            if completed_curve is None
            else f"leader #{int(completed_curve)}"
        )
        st.caption(
            f"Completion snapshot {completion.get('captured_at') or row['completed_at'] or '—'} · "
            f"{completed_target_text} · {completed_curve_text} · "
            f"rigorous lower {int(completion.get('best_lower') or 0)}."
        )
        if drift and drift["changed"]:
            changed = ", ".join(
                str(key).replace("_", " ")
                for key in drift["changes"]
            )
            message = (
                "Current research evidence differs from the completion snapshot"
                + (f" ({changed})." if changed else ".")
            )
            if drift["requires_review"]:
                st.warning(
                    message
                    + " Review the current evidence before treating the historical completion state as scientifically current."
                )
            else:
                st.info(
                    message
                    + f" The lifecycle status remains {_campaign_status_label(row['status'])}, but the research outcome has been updated."
                )
        else:
            st.caption("Completion snapshot still matches the current research evidence.")
    elif str(row["status"]) == "completed":
        st.caption(
            "This campaign predates completion snapshots, so no completion-time evidence baseline is available."
        )

    if row["objective"]:
        with region("campaign-objective", border=True):
            st.markdown("**Research objective**")
            st.write(str(row["objective"]))

    _research_brief(db, brief)

    bits = []
    if row["plugin_id"]:
        bits.append(f"plugin `{row['plugin_id']}`")
    if row["family"]:
        bits.append(f"family `{row['family']}`")
    bits.append(f"last activity {snapshot['last_activity']}")
    st.caption(" · ".join(bits))

    current = current_campaign(db)
    is_current = current is not None and int(current["id"]) == int(row["id"])
    active_work = int(snapshot["running"]) + int(snapshot["queued"])
    if active_work:
        st.caption(
            f"{active_work} active/queued work item(s) must finish or be stopped before this campaign can be completed or archived."
        )
    a, b, c, d = st.columns(4)
    current_label = "Current campaign" if is_current else "Set current"
    if a.button(
        current_label,
        type="primary" if is_current else "secondary",
        disabled=is_current,
        width="stretch",
        key=f"campaign-current-{row['id']}",
    ):
        set_current_campaign(db, int(row["id"]))
        st.rerun()

    lifecycle_status = str(row["status"])
    if lifecycle_status == "active":
        lifecycle_label = "Pause"
        lifecycle_next = "paused"
    elif lifecycle_status == "paused":
        lifecycle_label = "Resume"
        lifecycle_next = "active"
    else:
        lifecycle_label = "Reopen"
        lifecycle_next = "active"
    if b.button(
        lifecycle_label,
        width="stretch",
        key=f"campaign-lifecycle-{row['id']}",
    ):
        update_campaign(db, int(row["id"]), status=lifecycle_next)
        st.rerun()
    if c.button(
        "Mark completed",
        disabled=str(row["status"]) == "completed" or active_work > 0,
        width="stretch",
        key=f"campaign-complete-{row['id']}",
    ):
        update_campaign(db, int(row["id"]), status="completed")
        st.rerun()
    if d.button(
        "Archive",
        disabled=str(row["status"]) == "archived" or active_work > 0,
        width="stretch",
        key=f"campaign-archive-{row['id']}",
    ):
        update_campaign(db, int(row["id"]), status="archived")
        st.rerun()

    _edit_campaign(db, ctx, row, snapshot)



def _strategy_history(brief):
    strategies = brief.get("strategy_performance") or []
    milestones = brief.get("frontier_milestones") or []
    activity = brief.get("recent_activity") or []
    campaign_id = int(brief["snapshot"]["campaign"]["id"])

    with st.container(
        border=True,
        key=rh_key("campaign-progress-strategies"),
    ):
        heading, count = st.columns(
            [5.6, 1.0],
            vertical_alignment="center",
        )
        with heading:
            st.markdown("#### Strategy performance")
            st.caption(
                "Historical execution results by strategy. Frontier gains count "
                "runs that raised the rigorous lower bound at that point in time."
            )
        with count:
            st.metric("Strategies", len(strategies))

        if strategies:
            dataframe(
                [
                    {
                        "strategy": str(rec["strategy"]),
                        "runs": int(rec["runs"]),
                        "candidates": int(rec["candidates"]),
                        "historical best ≥": int(rec["best_lower"]),
                        "frontier gains": int(rec["frontier_gains"]),
                        "interesting": int(rec["interesting_runs"]),
                        "failures": int(rec["failed"]),
                        "active": int(rec["active"]),
                        "last activity": str(rec["last_activity"] or "—"),
                    }
                    for rec in strategies
                ],
                semantic=f"campaign-strategies-{campaign_id}",
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("No campaign execution history is available yet.")

    frontier_col, activity_col = st.columns(
        [0.9, 1.1],
        gap="large",
    )
    with frontier_col:
        with st.container(
            border=True,
            key=rh_key("campaign-progress-frontier"),
        ):
            heading, count = st.columns(
                [4.0, 1.0],
                vertical_alignment="center",
            )
            with heading:
                st.markdown("#### Frontier history")
                st.caption("Recorded rigorous-lower-bound improvements.")
            with count:
                st.metric("Gains", len(milestones))

            if milestones:
                dataframe(
                    [
                        {
                            "when": str(rec["at"] or "—"),
                            "strategy": str(rec["strategy"]),
                            "source": (
                                f"job #{int(rec['job_id'])}"
                                if rec["job_id"] is not None
                                else (
                                    f"run #{int(rec['run_id'])}"
                                    if rec["run_id"] is not None
                                    else "—"
                                )
                            ),
                            "rigorous lower": (
                                f"{int(rec['previous_lower'])} → "
                                f"{int(rec['new_lower'])}"
                            ),
                        }
                        for rec in milestones
                    ],
                    semantic=f"campaign-frontier-{campaign_id}",
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption(
                    "No recorded execution has raised the rigorous frontier yet."
                )

    with activity_col:
        with st.container(
            border=True,
            key=rh_key("campaign-progress-activity"),
        ):
            heading, count = st.columns(
                [4.0, 1.0],
                vertical_alignment="center",
            )
            with heading:
                st.markdown("#### Recent execution")
                st.caption("Latest campaign-linked search activity.")
            with count:
                st.metric("Events", len(activity))

            if activity:
                dataframe(
                    [
                        {
                            "when": str(rec["activity_at"] or "—"),
                            "strategy": str(rec["strategy"]),
                            "status": str(rec["status"] or "—"),
                            "source": (
                                f"run #{int(rec['run_id'])}"
                                if rec["run_id"] is not None
                                else (
                                    f"job #{int(rec['job_id'])}"
                                    if int(rec["job_id"] or 0) > 0
                                    else "—"
                                )
                            ),
                            "candidates": int(rec["candidates"] or 0),
                            "historical best ≥": int(rec["best_lower"] or 0),
                        }
                        for rec in activity[:12]
                    ],
                    semantic=f"campaign-recent-{campaign_id}",
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No campaign execution activity is available yet.")


def _progress(snapshot, brief):
    campaign = snapshot["campaign"]
    campaign_id = int(campaign["id"])
    target = brief.get("target")
    current_lower = int(brief.get("best_lower") or 0)
    best_state = brief.get("best_state") or {}
    blocked = bool(brief.get("target_blocked_by_conflict"))
    active_work = int(snapshot["running"]) + int(snapshot["queued"])

    if best_state.get("inconsistent"):
        frontier_text = "Conflict"
    elif best_state.get("exact_rank") is not None:
        frontier_text = f"= {int(best_state['exact_rank'])}"
    elif current_lower > 0:
        frontier_text = f"≥ {current_lower}"
    else:
        frontier_text = "—"

    target_text = "—" if target is None else f"≥ {int(target)}"
    if target is None:
        gap_text = "—"
    elif blocked:
        gap_text = "Blocked"
    elif brief.get("target_reached"):
        gap_text = "Met"
    else:
        gap_text = str(int(brief.get("target_gap") or 0))

    with st.container(
        border=True,
        key=rh_key("campaign-progress-summary"),
    ):
        st.markdown("### Campaign progress")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Rigorous frontier", frontier_text)
        m2.metric("Target", target_text)
        m3.metric("Gap", gap_text)
        m4.metric("Candidates", f"{int(snapshot['candidates_done']):,}")
        m5.metric("Active work", active_work)
        m6.metric("Pipeline runs", len(snapshot["pipeline_runs"]))

        if target is not None and int(target) > 0:
            if blocked:
                st.warning(
                    "Target progress is blocked by conflicting rigorous evidence."
                )
            else:
                st.progress(
                    min(1.0, float(current_lower) / float(target)),
                    text=(
                        f"Rigorous lower {current_lower} / target {int(target)}"
                        if not brief.get("target_reached")
                        else f"Target ≥{int(target)} reached"
                    ),
                )

    _strategy_history(brief)

    with st.container(
        border=True,
        key=rh_key("campaign-progress-pipelines"),
    ):
        heading, count = st.columns(
            [5.6, 1.0],
            vertical_alignment="center",
        )
        with heading:
            st.markdown("#### Pipeline runs")
            st.caption(
                "Saved pipeline execution attached to this campaign."
            )
        with count:
            st.metric("Runs", len(snapshot["pipeline_runs"]))

        if snapshot["pipeline_runs"]:
            dataframe(
                [
                    {
                        "run": int(run["id"]),
                        "pipeline": run["pipeline_name"],
                        "status": run["status"],
                        "stage": run["current_stage_id"] or "—",
                        "candidates": (
                            f"{int(run['candidates_done'])}/"
                            f"{int(run['candidates_total'])}"
                        ),
                        "run best ≥": int(run["best_lower"] or 0),
                        "best curve": (
                            f"#{int(run['best_curve_id'])}"
                            if run["best_curve_id"] is not None
                            else "—"
                        ),
                        "updated": run["updated_at"],
                    }
                    for run in snapshot["pipeline_runs"]
                ],
                semantic=f"campaign-pipelines-{campaign_id}",
                width="stretch",
                hide_index=True,
            )
            pipeline_by_id = {
                int(run["id"]): run for run in snapshot["pipeline_runs"]
            }
            select_col, open_col = st.columns(
                [5.5, 1.3],
                vertical_alignment="bottom",
            )
            selected_run_id = select_col.selectbox(
                "Pipeline run",
                list(pipeline_by_id),
                format_func=lambda rid: (
                    f"#{rid} · {pipeline_by_id[rid]['pipeline_name']} · "
                    f"{pipeline_by_id[rid]['status']}"
                ),
                key=f"campaign-open-pipeline-{campaign_id}",
            )
            if open_col.button(
                "Open in Pipelines",
                width="stretch",
                key=f"campaign-open-pipeline-button-{campaign_id}",
            ):
                st.session_state["byo_focus_run_id"] = int(selected_run_id)
                st.session_state["builder_main_view"] = "Editor"
                st.session_state["rh_page"] = "Pipelines"
                st.rerun()
        else:
            st.caption("No pipeline runs are linked to this campaign yet.")

    rows = _job_rows(snapshot)
    with st.container(
        border=True,
        key=rh_key("campaign-progress-jobs"),
    ):
        heading, count = st.columns(
            [5.6, 1.0],
            vertical_alignment="center",
        )
        with heading:
            st.markdown("#### Jobs")
            st.caption("Direct jobs and searches attached to this campaign.")
        with count:
            st.metric("Jobs", len(rows))

        if rows:
            dataframe(
                rows,
                semantic=f"campaign-jobs-{campaign_id}",
                width="stretch",
                hide_index=True,
            )
            job_ids = [row["job"] for row in rows]
            select_col, open_col = st.columns(
                [5.5, 1.3],
                vertical_alignment="bottom",
            )
            selected = select_col.selectbox(
                "Job",
                job_ids,
                format_func=lambda jid: next(
                    f"#{rec['job']} · {rec['status']} · {rec['label']}"
                    for rec in rows if rec["job"] == jid
                ),
                key=f"campaign-open-job-{campaign_id}",
            )
            if open_col.button(
                "Open in Jobs",
                width="stretch",
                key=f"campaign-open-job-button-{campaign_id}",
            ):
                st.session_state["manage_job_id"] = int(selected)
                st.session_state["rh_page"] = "Jobs"
                st.rerun()
        else:
            st.info(
                "No jobs are linked yet. Set this as the current campaign, then "
                "launch Auto/Search/Target/Pipelines work; new UI jobs will "
                "attach automatically."
            )

def _schedules(snapshot):
    rows = snapshot["schedules"]
    if not rows:
        st.info("No scheduled jobs are attached to this campaign.")
        return
    dataframe(
        [
            {
                "schedule": int(row["id"]),
                "enabled": bool(row["enabled"]),
                "label": row["label"],
                "recurrence": row["recurrence"],
                "next run": row["next_run_at"],
                "last run": row["last_run_at"] or "—",
                "last job": (
                    f"#{int(row['last_job_id'])}"
                    if row["last_job_id"] is not None
                    else "—"
                ),
            }
            for row in rows
        ],
        semantic=f"campaign-schedules-{snapshot['campaign']['id']}",
        width="stretch",
        hide_index=True,
    )
    if st.button("Manage schedules", width="stretch", key=f"campaign-manage-schedules-{snapshot['campaign']['id']}"):
        st.session_state["jobs_section_pending"] = "Scheduled"
        st.session_state["rh_page"] = "Jobs"
        st.rerun()


def _campaign_note_time(value):
    text = str(value or "").strip()
    if not text:
        return "—"
    return text.replace("T", " · ").replace("+00:00", " UTC").replace("Z", " UTC")


def _notes(db, snapshot):
    row = snapshot["campaign"]
    campaign_id = int(row["id"])

    st.markdown("#### Research Notebook")
    st.caption(
        "Record typed research reasoning inside this Campaign. Notebook entries are "
        "human research context only and never become mathematical evidence automatically."
    )

    handoff_labels = {
        "include": "Include in handoff",
        "private": "Private scratch",
        "resolved": "Resolved / obsolete",
    }
    link_options = ["none", *NOTEBOOK_LINK_KINDS]

    with st.form(f"campaign-add-note-{campaign_id}", clear_on_submit=True):
        c1, c2 = st.columns(2)
        entry_type = c1.selectbox(
            "Entry type",
            NOTEBOOK_ENTRY_TYPES,
            format_func=lambda value: NOTEBOOK_ENTRY_LABELS[value],
        )
        handoff_state = c2.selectbox(
            "Handoff state",
            NOTEBOOK_HANDOFF_STATES,
            format_func=lambda value: handoff_labels[value],
        )
        body = st.text_area(
            "Add notebook entry",
            placeholder=(
                "Record a hypothesis, observation, decision, failed approach, "
                "next experiment, reference, or milestone…"
            ),
            height=120,
        )
        l1, l2 = st.columns([1.5, 1])
        link_kind = l1.selectbox(
            "Linked object",
            link_options,
            format_func=lambda value: (
                "None"
                if value == "none"
                else NOTEBOOK_LINK_LABELS[value]
            ),
        )
        link_id = l2.number_input(
            "Object ID",
            min_value=0,
            value=0,
            step=1,
            disabled=(link_kind == "none"),
            help="Typed Rank Hunter object reference; use 0 only when no object is linked.",
        )
        submitted = st.form_submit_button(
            "Add notebook entry",
            icon=":material/add_comment:",
            type="primary",
            width="stretch",
        )

    if submitted:
        links = []
        if link_kind != "none":
            links = [{"kind": link_kind, "id": int(link_id)}]
        try:
            add_campaign_note(
                db,
                campaign_id,
                body,
                entry_type=entry_type,
                handoff_state=handoff_state,
                links=links,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.toast("Research notebook entry added.")
            st.rerun()

    entries = campaign_notebook_entries(db, campaign_id, limit=500)
    if entries:
        st.markdown("#### Notebook history")
        for note in entries:
            entry_type = str(note.get("entry_type") or "note")
            handoff_state = str(note.get("handoff_state") or "include")
            with st.container(
                border=True,
                key=f"campaign-note-{int(note['id'])}",
            ):
                st.caption(
                    f"{NOTEBOOK_ENTRY_LABELS.get(entry_type, entry_type.title())} · "
                    f"{handoff_labels.get(handoff_state, handoff_state.title())} · "
                    f"{_campaign_note_time(note['created_at'])} · "
                    f"entry #{int(note['id'])}"
                )
                st.write(str(note["body"]))
                links = list(note.get("links") or [])
                if links:
                    st.caption(
                        "Links · "
                        + " · ".join(
                            f"{NOTEBOOK_LINK_LABELS.get(str(link['kind']), str(link['kind']).title())} "
                            f"#{int(link['id'])}"
                            for link in links
                        )
                    )

    if row["notes"]:
        st.markdown("#### Initial campaign note")
        with st.container(border=True):
            st.caption(
                f"{_campaign_note_time(row['created_at'])} · "
                "legacy campaign note · included in handoff for compatibility"
            )
            st.write(str(row["notes"]))

    if not entries and not row["notes"]:
        st.caption("No notebook entries yet.")


def _danger(db, row):
    with st.expander("Danger zone", expanded=False):
        st.warning(
            "Deleting a campaign removes only the campaign container. Existing curves, evidence, jobs, logs, "
            "pipeline runs, and mathematical results are not deleted. Attached schedules become unassigned."
        )
        confirm = st.checkbox(
            f"I understand — delete campaign #{int(row['id'])}",
            key=f"campaign-delete-confirm-{row['id']}",
        )
        if st.button(
            "Delete campaign",
            disabled=not confirm,
            width="stretch",
            key=f"campaign-delete-{row['id']}",
        ):
            delete_campaign(db, int(row["id"]))
            st.session_state.pop("manage_campaign_id", None)
            st.rerun()


def _render_campaign_detail(db, ctx, row):
    back_col, title_col = st.columns([1.35, 8.65], vertical_alignment="center")
    with back_col:
        if st.button(
            "All campaigns",
            icon=":material/arrow_back:",
            width="stretch",
            key=f"campaign-back-{int(row['id'])}",
        ):
            st.session_state.pop("manage_campaign_id", None)
            st.rerun()
    with title_col:
        st.subheader(f"#{int(row['id'])} · {row['name']}")
        detail = [str(row["status"]).title()]
        if row["target_rank"] is not None:
            detail.append(f"target ≥{int(row['target_rank'])}")
        if row["family"]:
            detail.append(str(row["family"]))
        st.caption(" · ".join(detail))

    campaign_id = int(row["id"])
    with st.container(key=rh_key("campaign-detail-tabs")):
        current = str(
            st.session_state.get(f"campaign-detail-tab-{campaign_id}")
            or "Overview"
        )
        if current == "Notes":
            current = "Notebook"
        choice = tabs(
            ["Overview", "Progress", "Schedules", "Notebook", "Handoff"],
            value=(
                current
                if current
                in {"Overview", "Progress", "Schedules", "Notebook", "Handoff"}
                else "Overview"
            ),
            key=f"campaign-detail-tabs-{campaign_id}",
        )
        st.session_state[f"campaign-detail-tab-{campaign_id}"] = choice

        if choice == "Overview":
            brief = campaign_research_brief(db, campaign_id)
            snapshot = brief["snapshot"]
            _overview(db, ctx, snapshot, brief)
            _danger(db, row)
        elif choice == "Progress":
            brief = campaign_research_brief(db, campaign_id)
            _progress(brief["snapshot"], brief)
        elif choice == "Schedules":
            _schedules(campaign_snapshot(db, campaign_id))
        elif choice == "Notebook":
            _notes(db, campaign_snapshot(db, campaign_id))
        else:
            brief = campaign_research_brief(db, campaign_id)
            _handoff(db, brief)


def page(db, ctx):
    _campaign_page_style()
    _render_campaign_header(db, ctx)
    render_feature_hook(db, ctx, "manage.campaigns.after_header")

    selected_id = st.session_state.get("manage_campaign_id")
    if selected_id is not None:
        try:
            row = get_campaign(db, int(selected_id))
        except (TypeError, ValueError):
            row = None
        if row is not None:
            _render_campaign_detail(db, ctx, row)
            return
        st.session_state.pop("manage_campaign_id", None)

    _render_campaign_landing(db, ctx)
