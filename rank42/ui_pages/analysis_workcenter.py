"""Curve-centered research casebook and launch point for analysis workflows."""
from __future__ import annotations

import streamlit as st

from rank42.analysis_case_store import (
    CASE_PRIORITY_PRESETS, CASE_RESEARCH_GOALS, CASE_WORKFLOW_STATUSES,
    analysis_case_schema_ready, case_priority_label, get_analysis_case,
    save_analysis_case,
)
from rank42.analysis_planner import analysis_plan, planner_immediate_actions
from rank42.analyze_workspace import curve_analysis_snapshot, research_inbox
from rank42.manage_store import list_campaigns
from rank42.research_timeline import research_timeline
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.ui_handoffs import route_curve_to_target, route_pool_to_search
from rank42.ui_pages.common import title
from rank42.ui_components import rh_key


_FOCUS_ACTION_KINDS = {
    "Close the rigorous rank interval": ("descent",),
    "Resolve candidate point independence": ("independence",),
    "Find another independent point": ("search",),
    "Understand Mordell–Weil geometry": ("lattice",),
    "Push quartic / covering geometry": ("quartics",),
    "Explain family / specialization structure": ("study_jump",),
    "Compare related fibers": ("search_related",),
}


def _open_curve_tool(route: str, curve_id: int, *, family_pool_id=None) -> None:
    if route == "Target":
        route_curve_to_target(st.session_state, int(curve_id))
        st.rerun()
        return
    if route == "Search":
        set_active_curve_id(st.session_state, int(curve_id), "analysis_workcenter_curve_id")
        if family_pool_id is not None:
            route_pool_to_search(st.session_state, int(family_pool_id))
        else:
            st.session_state["new_search_view"] = "Family"
            st.session_state["new-search-family-workflow"] = "Pool Search"
            st.session_state["rh_page"] = "Search"
        st.rerun()
        return
    set_active_curve_id(
        st.session_state, int(curve_id), "analyze_curve_id", "analysis_curve_id",
        "independence_curve_id", "descent_curve_id", "target_curve_id",
        "points_curve_id", "analysis_workcenter_curve_id",
    )
    if route == "Curves":
        st.session_state["curves_detail_id"] = int(curve_id)
    st.session_state["rh_page"] = route
    st.rerun()


def _rank_label(state):
    if state["exact_rank"] is not None:
        return f"= {int(state['exact_rank'])}"
    if state["rigorous_upper"] is not None:
        return f"{int(state['rigorous_lower'])}–{int(state['rigorous_upper'])}"
    return f"≥ {int(state['rigorous_lower'])}"


def _queue_label(item):
    curve_id = int(item["curve_id"])
    if item["exact_rank"] is not None:
        rank = f"rank = {int(item['exact_rank'])}"
    elif item["rigorous_upper"] is not None:
        rank = f"rank {int(item['rigorous_lower'])}–{int(item['rigorous_upper'])}"
    else:
        rank = f"rank ≥{int(item['rigorous_lower'])}"
    context = "Campaign" if item["active_campaign"] else (
        str(item["workflow_status"]).title()
        if item.get("workflow_status") else "Needs review"
    )
    family = str(item["family"])
    if len(family) > 36:
        family = family[:33] + "…"
    return f"{context} · #{curve_id} · {family} · t={item['parameter']} · {rank}"


def _goal_action(plan, goal):
    actions = list(plan.get("actions") or [])
    if not goal or goal not in _FOCUS_ACTION_KINDS:
        return next(iter(planner_immediate_actions(plan)), None)
    kinds = _FOCUS_ACTION_KINDS.get(str(goal), ())
    return next((action for action in actions if action["kind"] in kinds), None)


def _render_recommendation(snapshot, plan, case):
    curve_id = int(snapshot["curve"]["id"])
    goal = "" if case is None else str(case["research_goal"] or "")
    action = _goal_action(plan, goal)
    with st.container(key=rh_key("casebook-next-step"), border=True):
        st.subheader("Recommended next step" if not goal else f"Next step · {goal}")
        if action is None:
            if goal and goal in _FOCUS_ACTION_KINDS:
                st.info("No matching action follows from the stored evidence. Review the evidence below or change the research goal.")
            else:
                st.info("No immediate action is recommended from the current evidence.")
            return
        st.markdown(f"**{action['title']}**")
        st.write(action["why"])
        st.caption(
            f"Suggested method: {action['recommended_method']}"
            + (" · current budget has been exhausted" if action.get("exhausted") else "")
        )
        if action.get("page"):
            if st.button(
                f"Open {action['page']}", type="primary", icon=":material/arrow_forward:",
                key=f"analysis-workcenter-next-{curve_id}-{action['kind']}", width="stretch",
            ):
                _open_curve_tool(
                    action["page"], curve_id,
                    family_pool_id=action.get("family_pool_id"),
                )
        elif action.get("tab") == "Rank Jump":
            if st.button(
                "Review family analysis", type="primary",
                key=f"analysis-workcenter-family-next-{curve_id}", width="stretch",
            ):
                st.session_state["casebook-family-open"] = True
                st.rerun()


def _render_overview(snapshot):
    curve, state, arithmetic = snapshot["curve"], snapshot["state"], snapshot["arithmetic"]
    torsion = arithmetic.get("torsion") or {}
    conductor = arithmetic.get("conductor")
    with st.container(key=rh_key("casebook-curve-info"), border=True):
        st.subheader(f"Curve #{int(curve['id'])}")
        st.caption(
            f"{curve['family']} · t={curve['parameter']} · {curve['plugin_id'] or 'external/imported'}"
            + (f" v{curve['plugin_version']}" if curve["plugin_version"] else "")
        )
        a, b, c = st.columns(3)
        a.metric("Rigorous rank", _rank_label(state))
        b.metric("Torsion", torsion.get("label") or "Unknown")
        c.metric("Conductor", f"{int(conductor):,}" if conductor else "—")
        st.caption(
            f"{len(snapshot['witnesses'])} independent witnesses · "
            f"{len(snapshot['actionable_points'])} points to review"
        )
        if state["inconsistent"]:
            st.error("Stored rigorous bounds conflict. Review the evidence before treating this rank as settled.")
        if state["conditional_analytic_upper"] is not None:
            st.caption(f"Conditional analytic upper bound: {int(state['conditional_analytic_upper'])} (separate from rigorous bounds).")


def _render_evidence(snapshot):
    state = snapshot["state"]
    st.subheader("Rank evidence")
    if state["inconsistent"]:
        st.error("Stored rigorous bounds are inconsistent; inspect the evidence records below.")
    elif state["exact_rank"] is not None:
        st.success(f"Exact rank = {int(state['exact_rank'])}.")
    elif state["rigorous_upper"] is not None:
        st.info(f"Rigorous interval {int(state['rigorous_lower'])}–{int(state['rigorous_upper'])}.")
    else:
        st.info(f"Rigorous lower bound ≥{int(state['rigorous_lower'])}; upper bound unknown.")
    rows = [
        {
            "engine": r["engine"], "type": r["evidence_type"], "status": r["status"],
            "rigorous": bool(r["rigorous"]), "lower": r["rigorous_lower"],
            "upper": r["rigorous_upper"], "exact": r["exact_rank"],
            "conditional upper": r["conditional_analytic_upper"],
            "timeout": bool(r["timed_out"]), "created": r["created_at"],
        }
        for r in snapshot["evidence"][:30]
    ]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.caption("No structured rank evidence has been recorded for this curve.")

    st.subheader("Point basis and candidates")
    st.caption(
        f"{len(snapshot['witnesses'])} rigorous witnesses · {len(snapshot['actionable_points'])} exact candidates awaiting independence review · "
        f"{int(snapshot['witness_gap'])} witness(es) missing from the replayable basis."
    )
    points = [
        {
            "id": int(r["id"]), "role": r["role"], "source": r["source"],
            "exact": bool(r["exact_verified"]), "independence": r["independence_status"],
            "witness": bool(r["rigorous_independent"]), "x": r["x"], "y": r["y"],
        }
        for r in snapshot["points"][:40]
    ]
    if points:
        st.dataframe(points, hide_index=True, width="stretch")
    else:
        st.caption("No Point Ledger rows are stored for this curve.")


def _render_family(snapshot):
    st.subheader("Family and specialization")
    a, b, c, d = st.columns(4)
    a.metric("Related fibers", f"{int(snapshot['family_total']):,}")
    b.metric("Best family lower bound", f"≥ {int(snapshot['family_best_lower'])}")
    base = snapshot["family_section_lower"]
    c.metric("Certified family sections", "—" if base is None else f"≥ {int(base)}")
    jump = snapshot["rank_jump_lower"]
    d.metric("Certified rank jump", "—" if jump is None else f"≥ {int(jump)}")
    generic = snapshot["generic_rank_exact"]
    if generic is not None:
        st.info(f"Exact generic rank: {int(generic)}.")
    elif snapshot["rank_jump_status"] == "family_generic_rank_conflict":
        st.error("Family generic-rank evidence conflicts; no formal jump claim is made.")
    elif base is None:
        st.caption("No certified family-section subgroup is stored for this specialization.")
    else:
        st.caption("The family subgroup is known, but generic rank is not exact; extra directions alone do not prove a generic-rank jump.")
    siblings = sorted(snapshot["family_jumps"], key=lambda r: (-int(r["rigorous_lower"]), str(r["parameter"])))[:30]
    if siblings:
        st.markdown("#### Strongest stored specializations in this family")
        st.dataframe(
            [
                {
                    "curve": f"#{int(r['curve_id'])}", "parameter": r["parameter"],
                    "rank ≥": int(r["rigorous_lower"]), "exact": r["exact_rank"],
                    "family sections ≥": r["family_section_lower"],
                    "extra directions ≥": r["extra_directions_lower"],
                }
                for r in siblings
            ], hide_index=True, width="stretch",
        )
    else:
        st.caption("No sibling specializations are stored for this family yet.")


def _render_activity(db, curve_id):
    st.subheader("Research activity")
    records = research_timeline(db, int(curve_id), limit=200)
    if not records:
        st.info("No durable research activity is recorded for this curve yet.")
        return
    st.dataframe(
        [
            {
                "time": r["timestamp"], "source": r["source"],
                "event": r["kind"].replace("_", " "), "status": r["status"] or "—",
                "artifact": r["artifact_ref"], "summary": r["summary"],
            }
            for r in records[:40]
        ], hide_index=True, width="stretch",
    )


def _render_case_notes(db, curve_id):
    st.subheader("Research notes")
    if not analysis_case_schema_ready(db):
        st.info("Research case notes are not available in this database yet.")
        return
    current = get_analysis_case(db, int(curve_id))
    campaigns = list_campaigns(db, include_archived=True)
    priority_names = list(CASE_PRIORITY_PRESETS)
    current_priority = 100 if current is None else int(current["priority"])
    priority_label = case_priority_label(current_priority)
    current_status = "open" if current is None else str(current["workflow_status"])
    current_goal = "" if current is None else str(current["research_goal"] or "")
    goals = list(CASE_RESEARCH_GOALS)
    if current_goal and current_goal not in goals:
        goals.insert(1, current_goal)
    choices = [None, *campaigns]
    campaign_index = 0
    if current is not None and current["campaign_id"] is not None:
        for i, campaign in enumerate(choices[1:], 1):
            if int(campaign["id"]) == int(current["campaign_id"]):
                campaign_index = i
                break
    with st.form(f"analysis-workcenter-case-{int(curve_id)}"):
        a, b = st.columns(2)
        priority = a.selectbox("Priority", priority_names, index=priority_names.index(priority_label) if priority_label in priority_names else 1)
        status = b.selectbox("Case status", list(CASE_WORKFLOW_STATUSES), index=list(CASE_WORKFLOW_STATUSES).index(current_status) if current_status in CASE_WORKFLOW_STATUSES else 0)
        campaign = st.selectbox(
            "Campaign", choices, index=campaign_index,
            format_func=lambda item: "None" if item is None else f"#{int(item['id'])} · {item['name']} · {item['status']}",
        )
        goal = st.selectbox("Research goal", goals, index=goals.index(current_goal) if current_goal in goals else 0, format_func=lambda value: value or "Follow planner recommendation")
        reason = st.text_input("Why is this curve a research case?", value="" if current is None else str(current["reason"] or ""))
        note = st.text_area("Working notes", value="" if current is None else str(current["note"] or ""), height=180)
        submitted = st.form_submit_button("Save case", type="primary", width="stretch")
    if submitted:
        saved = save_analysis_case(
            db, curve_id=int(curve_id), campaign_id=None if campaign is None else int(campaign["id"]),
            priority=int(CASE_PRIORITY_PRESETS[priority]), workflow_status=status,
            research_goal=goal, reason=reason, note=note,
        )
        st.success(f"Saved {saved['workflow_status']} case · {case_priority_label(saved['priority'])} priority.")
        st.rerun()


def page(db, ctx):
    title("Casebook", icon=":material/menu_book:")
    queue = research_inbox(db, limit=10000)
    by_id = {int(item["curve_id"]): item for item in queue}
    current_curve_id = get_active_curve_id(st.session_state, "analysis_workcenter_curve_id")
    with st.container(key=rh_key("casebook-queue"), border=True):
        st.markdown("#### Research queue")
        filter_cols = st.columns([1.1, 1.5, 3], gap="small")
        view = filter_cols[0].selectbox(
            "Queue", ["Needs attention", "Open cases", "Deferred cases", "Resolved cases", "All curves"],
            key="analysis-workcenter-queue-view",
            label_visibility="collapsed",
        )
        query = filter_cols[1].text_input(
            "Find a curve", placeholder="ID, family, parameter…",
            key="analysis-workcenter-queue-search",
            label_visibility="collapsed",
        ).strip().casefold()

        candidates = []
        for item in queue:
            status = item.get("workflow_status")
            if view == "Needs attention":
                useful_case = (
                    int(item["rigorous_lower"] or 0) > 0
                    or int(item["unresolved_exact_points"] or 0) > 0
                    or bool(item["active_campaign"])
                    or item.get("workflow_case_id") is not None
                )
                if not item["needs_attention"] or not useful_case or status == "resolved":
                    continue
            elif view == "Open cases" and status != "open":
                continue
            elif view == "Deferred cases" and status != "deferred":
                continue
            elif view == "Resolved cases" and status != "resolved":
                continue
            haystack = " ".join((
                str(item["curve_id"]), str(item["family"]),
                str(item["parameter"]), str(item.get("active_campaign_name") or ""),
            )).casefold()
            if query and query not in haystack:
                continue
            candidates.append(item)

        # Preserve the exact curve handed off from Curves/Search even when it
        # does not yet meet the default queue's attention rules.
        if (
            view == "Needs attention"
            and current_curve_id in by_id
            and all(int(item["curve_id"]) != int(current_curve_id) for item in candidates)
        ):
            item = by_id[int(current_curve_id)]
            haystack = " ".join((
                str(item["curve_id"]), str(item["family"]),
                str(item["parameter"]), str(item.get("active_campaign_name") or ""),
            )).casefold()
            if not query or query in haystack:
                candidates.insert(0, item)

        matching_count = len(candidates)
        candidates = candidates[:50]
        if not candidates:
            filter_cols[2].caption("No matches. Try All curves or clear the search.")
            return

        candidate_ids = [int(item["curve_id"]) for item in candidates]
        widget_key = "analysis-workcenter-queue-curve"
        wanted = current_curve_id
        if st.session_state.get(widget_key) not in candidate_ids:
            st.session_state[widget_key] = wanted if wanted in candidate_ids else candidate_ids[0]
        selected_id = filter_cols[2].selectbox(
            "Select a research case", candidate_ids, key=widget_key,
            format_func=lambda value: _queue_label(by_id[int(value)]),
            label_visibility="collapsed",
            help=f"{matching_count:,} matching curves; showing the first {len(candidates):,} by priority.",
        )
        st.caption(f"{matching_count:,} matching cases · showing the first {len(candidates):,}")
    set_active_curve_id(
        st.session_state, int(selected_id), "analysis_workcenter_curve_id",
        "analyze_curve_id", "analysis_curve_id", "independence_curve_id",
        "descent_curve_id", "target_curve_id", "points_curve_id",
    )
    queue_item = by_id[int(selected_id)]
    status_bits = []
    if queue_item["active_campaign"]:
        status_bits.append(f"Active campaign: {queue_item['active_campaign_name']}")
    if queue_item.get("workflow_case_id") is not None:
        status_bits.append(str(queue_item["workflow_status"]).title())
        status_bits.append(f"{case_priority_label(queue_item['workflow_priority'])} priority")
    if queue_item.get("workflow_goal"):
        status_bits.append(f"Goal: {queue_item['workflow_goal']}")
    if queue_item.get("attention_reasons"):
        status_bits.extend(queue_item["attention_reasons"])
    elif queue_item.get("next_action"):
        status_bits.append(f"Planner focus: {queue_item['next_action']}")
    if status_bits:
        st.caption(" · ".join(status_bits))

    curve_id = int(selected_id)
    snapshot = curve_analysis_snapshot(db, curve_id)
    plan = analysis_plan(snapshot)
    case = get_analysis_case(db, curve_id)
    summary_col, notes_col = st.columns([.9, 1.1], gap="medium", vertical_alignment="top")
    with summary_col:
        _render_overview(snapshot)
        _render_recommendation(snapshot, plan, case)
    with notes_col:
        with st.container(key=rh_key("casebook-notes"), border=True):
            _render_case_notes(db, curve_id)

    with st.expander("Rank evidence and points", expanded=bool(snapshot["state"]["inconsistent"])):
        _render_evidence(snapshot)
    with st.expander("Family context", expanded=bool(st.session_state.pop("casebook-family-open", False))):
        _render_family(snapshot)
    with st.expander("Research history"):
        _render_activity(db, curve_id)
