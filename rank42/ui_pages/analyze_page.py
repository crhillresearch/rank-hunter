from __future__ import annotations

import json

import streamlit as st

from rank42.analysis_planner import (
    analysis_plan,
    planner_immediate_actions,
    planner_research_paths,
)
from rank42.analysis_case_store import (
    CASE_PRIORITY_PRESETS,
    CASE_RESEARCH_GOALS,
    CASE_WORKFLOW_STATUSES,
    case_priority_label,
    get_analysis_case,
    save_analysis_case,
)
from rank42.analyze_workspace import (
    case_board,
    curve_analysis_snapshot,
    research_inbox,
    research_inbox_default_curve_id,
)
from rank42.curve_research_state import list_curve_research_states
from rank42.manage_store import list_campaigns
from rank42.feature_hooks import render_feature_hook
from rank42.research_timeline import research_timeline
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.ui_components import dataframe, region, rh_key, tabs
from .common import active_curve_selector, curve_label, select_row, title


def _go(page, curve_id, *, family_pool_id=None, point_ids=None):
    curve_id = set_active_curve_id(st.session_state, curve_id)
    if page == "Independence":
        st.session_state.pop("independence-curve-native", None)
        set_active_curve_id(
            st.session_state,
            curve_id,
            "independence_curve_id",
        )
        ids = [int(value) for value in (point_ids or [])]
        if ids:
            st.session_state["independence_point_id"] = ids[0]
            st.session_state["independence_selected_point_ids"] = ids
    elif page == "Lattices & Heights":
        st.session_state.pop("lattice-curve-native", None)
        set_active_curve_id(st.session_state, curve_id, "analysis_curve_id")
    elif page == "Descent":
        st.session_state.pop("descent-curve-native", None)
        set_active_curve_id(st.session_state, curve_id, "descent_curve_id")
    elif page == "Target":
        st.session_state.pop("target-curve-native", None)
        set_active_curve_id(st.session_state, curve_id, "target_curve_id")
    elif page == "Curves":
        st.session_state["curves_detail_id"] = curve_id
    elif page == "Search":
        st.session_state.pop(rh_key("search-main-tabs"), None)
        st.session_state["search_tab"] = "Family"
        if family_pool_id is not None:
            st.session_state.pop(rh_key("family-pool-select"), None)
            st.session_state["family_search_pool_id"] = int(family_pool_id)
    st.session_state["rh_page"] = page


def _focus_tab(name):
    st.session_state["analyze_section_pending"] = str(name)


def _analysis_curve_options(db):
    rows = []
    for state in list_curve_research_states(db):
        row = dict(state["curve"])
        row["rigorous_lower"] = int(state["rigorous_lower"])
        row["rigorous_upper"] = state["rigorous_upper"]
        row["exact_rank"] = state["exact_rank"]
        row["rank_inconsistent"] = bool(state["rank_inconsistent"])
        rows.append(row)
    return rows


def _rank_text(state):
    if state["exact_rank"] is not None:
        return f"= {state['exact_rank']}"
    if state["rigorous_upper"] is not None:
        return f"{state['rigorous_lower']}–{state['rigorous_upper']}"
    return f"≥ {state['rigorous_lower']}"


def _arithmetic_source_label(state, field):
    info = (state.get("provenance") or {}).get(field) or {}
    kind = str(info.get("kind") or "missing")
    source = str(info.get("source") or "").strip()
    if kind == "external_reference":
        label = f"{source.upper()} reference" if source else "external reference"
    elif kind == "external_reference_cached_local":
        label = f"cached {source.upper()} reference" if source else "cached external reference"
    elif kind == "local_computed":
        label = str(info.get("method") or "local computed")
    elif kind == "local_persisted":
        label = "local"
    elif kind == "derived":
        label = "derived"
    else:
        label = "—"
    if field in set(state.get("conflicts") or []):
        label += " · conflict"
    return label


def _research_inbox(inbox, wanted):
    st.subheader("Research Inbox")
    st.caption(
        "Attention queue derived from durable state: active Campaign cases first, "
        "then unresolved/recent work, then the remaining backlog. Curves remains "
        "the passive record inventory."
    )
    if not inbox:
        st.info("No retained research cases are available.")
        return None

    active_campaign_rows = [
        rec for rec in inbox if rec["queue_bucket"] == "active_campaign"
    ]
    recent_unresolved_rows = [
        rec for rec in inbox if rec["queue_bucket"] == "recent_unresolved"
    ]
    backlog_rows = [
        rec for rec in inbox if rec["queue_bucket"] == "backlog"
    ]
    q1, q2, q3 = st.columns(3)
    q1.metric("Active Campaign unresolved", len(active_campaign_rows))
    q2.metric("Recent unresolved", len(recent_unresolved_rows))
    q3.metric("Global backlog", len(backlog_rows))
    if active_campaign_rows:
        campaign = active_campaign_rows[0]
        st.caption(
            f"Active Campaign priority: {campaign['active_campaign_name']} · "
            f"{len(active_campaign_rows)} unresolved retained curve(s) traceable through Manage."
        )

        st.caption(
            "Campaign growth is shown as a rigorous surplus above the stored family-section baseline; "
            "it is not a causal claim that the Campaign created those directions."
        )

    table = []
    for rec in inbox:
        if rec["rank_inconsistent"]:
            rank = f"conflict ≥{rec['rigorous_lower']}"
            if rec["rigorous_upper"] is not None:
                rank += f" / ≤{rec['rigorous_upper']}"
        elif rec["exact_rank"] is not None:
            rank = f"= {rec['exact_rank']}"
        elif rec["rigorous_upper"] is not None:
            rank = f"{rec['rigorous_lower']}–{rec['rigorous_upper']}"
        else:
            rank = f"≥ {rec['rigorous_lower']}"
        table.append({
            "curve": int(rec["curve_id"]),
            "family": rec["family"],
            "parameter": rec["parameter"],
            "rank": rank,
            "family subgroup": (
                "—"
                if rec["family_section_lower"] is None
                else int(rec["family_section_lower"])
            ),
            "extra dirs": (
                "—"
                if rec["extra_directions_lower"] is None
                else f"≥ {int(rec['extra_directions_lower'])}"
            ),
            "unresolved exact": int(rec["unresolved_exact_points"]),
            "hard": int(rec["hard_cases"]),
            "witness gap": int(rec["witness_gap"]),
            "last attempt": rec["last_attempt_at"] or "—",
            "next": rec["next_action"] or "—",
            "campaign": rec["active_campaign_name"] or "—",
            "case": rec["workflow_status"] or "—",
            "priority": rec["workflow_priority_label"],
            "goal": rec["workflow_goal"] or "—",
            "queue": (
                "active campaign"
                if rec["queue_bucket"] == "active_campaign"
                else "recent unresolved"
                if rec["queue_bucket"] == "recent_unresolved"
                else "backlog"
            ),
            "campaign growth": (
                "—"
                if rec["campaign_rank_growth_lower"] is None
                else f"≥ {int(rec['campaign_rank_growth_lower'])}"
            ),
            "campaign gap": (
                "—"
                if rec["campaign_target_gap"] is None
                else int(rec["campaign_target_gap"])
            ),
            "follow-up owner": rec["campaign_followup_owner"] or "—",
            "follow-up exhausted": bool(rec["campaign_followup_exhausted"]),
            "needs rank proof": bool(rec["campaign_needs_rank_proof"]),
            "proof methods exhausted": bool(rec["campaign_proof_methods_exhausted"]),
            "stronger proof budget": bool(rec["campaign_stronger_proof_budget_available"]),
        })
    dataframe(
        table,
        semantic="analysis-research-inbox",
        width="stretch",
        hide_index=True,
    )

    idx = next(
        (
            i for i, rec in enumerate(inbox)
            if wanted and int(rec["curve_id"]) == int(wanted)
        ),
        0,
    )
    selected = select_row(
        "Active research case",
        inbox,
        index=idx,
        format_func=curve_label,
        key="analysis-inbox-case",
    )
    curve_id = set_active_curve_id(
        st.session_state,
        int(selected["curve_id"]),
        "analyze_curve_id",
    )
    if selected["attention_reasons"]:
        st.caption("Needs attention: " + " · ".join(selected["attention_reasons"]))
    else:
        st.caption("This case is currently resolved/low-attention; open the Case Board to review it.")

    if selected["workflow_status"]:
        workflow_line = (
            f"Researcher case: {selected['workflow_status']} · "
            f"{selected['workflow_priority_label']}"
        )
        if selected["workflow_goal"]:
            workflow_line += f" · {selected['workflow_goal']}"
        st.caption(workflow_line)
        if selected["workflow_reason"]:
            st.caption("Researcher reason: " + selected["workflow_reason"])
    else:
        st.caption("No explicit researcher workflow state is saved for this curve.")

    if st.button(
        "Open Case Board",
        type="primary",
        width="stretch",
        key=f"analysis-open-case-{curve_id}",
    ):
        _focus_tab("Case Board")
        st.rerun()
    return curve_id


def _case_state_editor(db, curve_id):
    current = get_analysis_case(db, int(curve_id))
    campaigns = list_campaigns(db, include_archived=True)

    with region("analysis-case-workflow", border=True):
        st.subheader("Researcher case state")
        st.caption(
            "Human workflow only. Saving this form never changes rank bounds, "
            "point independence, proof status, or any other scientific result."
        )
        if current is None:
            st.caption("No explicit case state is saved yet; defaults below are unsaved.")

        current_priority = 100 if current is None else int(current["priority"])
        priority_labels = list(CASE_PRIORITY_PRESETS)
        priority_label = case_priority_label(current_priority)
        priority_index = (
            priority_labels.index(priority_label)
            if priority_label in priority_labels
            else priority_labels.index("Normal")
        )
        current_status = "open" if current is None else str(current["workflow_status"])
        current_goal = "" if current is None else str(current["research_goal"] or "")
        goal_options = list(CASE_RESEARCH_GOALS)
        if current_goal and current_goal not in goal_options:
            goal_options.insert(1, current_goal)

        campaign_options = [None, *campaigns]
        current_campaign_id = None if current is None else current["campaign_id"]
        campaign_index = 0
        if current_campaign_id is not None:
            for index, campaign in enumerate(campaign_options[1:], 1):
                if int(campaign["id"]) == int(current_campaign_id):
                    campaign_index = index
                    break

        with st.form(f"analysis-case-state-{int(curve_id)}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                priority_name = st.selectbox(
                    "Priority",
                    priority_labels,
                    index=priority_index,
                )
            with c2:
                workflow_status = st.selectbox(
                    "Workflow status",
                    list(CASE_WORKFLOW_STATUSES),
                    index=list(CASE_WORKFLOW_STATUSES).index(current_status),
                )
            with c3:
                campaign = st.selectbox(
                    "Campaign context",
                    campaign_options,
                    index=campaign_index,
                    format_func=lambda rec: (
                        "None"
                        if rec is None
                        else f"#{int(rec['id'])} · {rec['name']} · {rec['status']}"
                    ),
                )

            research_goal = st.selectbox(
                "Current research goal",
                goal_options,
                index=goal_options.index(current_goal) if current_goal in goal_options else 0,
                format_func=lambda value: value or "Not selected",
            )
            reason = st.text_input(
                "Reason",
                value="" if current is None else str(current["reason"] or ""),
                help="Why this curve is open, deferred, or resolved as a research case.",
            )
            note = st.text_area(
                "Researcher note",
                value="" if current is None else str(current["note"] or ""),
                height=120,
                help="Workflow notes only; mathematical claims continue to come from the scientific ledgers.",
            )
            submitted = st.form_submit_button(
                "Save researcher case state",
                type="primary",
                width="stretch",
            )

        if submitted:
            saved = save_analysis_case(
                db,
                curve_id=int(curve_id),
                campaign_id=None if campaign is None else int(campaign["id"]),
                priority=int(CASE_PRIORITY_PRESETS[priority_name]),
                workflow_status=workflow_status,
                research_goal=research_goal,
                reason=reason,
                note=note,
            )
            st.success(
                f"Saved case #{int(saved['id'])}: "
                f"{saved['workflow_status']} · {case_priority_label(saved['priority'])}."
            )
            st.rerun()


def _overview(db, snapshot):
    curve = snapshot["curve"]
    state = snapshot["state"]

    _case_state_editor(db, int(curve["id"]))

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Rigorous rank", _rank_text(state))
    c2.metric(
        "Family sections",
        "—" if snapshot["family_section_lower"] is None else f"rank {snapshot['family_section_lower']}",
        help="Rank of the certified subgroup obtained by specializing the family's known sections.",
    )
    c3.metric(
        "Extra directions",
        "—" if snapshot["extra_directions_lower"] is None else f"≥ {snapshot['extra_directions_lower']}",
        help="Rigorous directions known beyond the certified family-section subgroup. This is not automatically a generic-rank jump.",
    )
    c4.metric("Rigorous witnesses", len(snapshot["witnesses"]))
    c5.metric("Unresolved candidates", len(snapshot["actionable_points"]))
    c6.metric("Stored lattices", len(snapshot["lattices"]))

    board = case_board(snapshot)
    b1, b2, b3 = st.columns(3)
    b1.metric("Hard cases", board["material"]["hard_cases"])
    b2.metric("Evidence timeouts", board["attempts"]["evidence_timeouts"])
    upper_history = board["attempts"]["upper_bound_history"]
    b3.metric(
        "Upper-bound methods",
        (
            "not needed"
            if state.get("exact_rank") is not None
            else "exhausted"
            if upper_history.get("all_exhausted_at_budget")
            else f"{len(upper_history.get('available_methods') or [])} available"
        ),
    )
    if board["attempts"]["last_evidence_at"]:
        st.caption(
            f"Last durable rank/proof attempt: {board['attempts']['last_evidence_at']} "
            f"· evidence #{board['attempts']['last_evidence_id']}"
        )

    if state.get("conditional_analytic_upper") is not None:
        st.caption(
            f"Conditional analytic upper: {state['conditional_analytic_upper']} · "
            "kept separate from rigorous algebraic evidence."
        )

    provenance = [
        f"Curve #{curve['id']}",
        str(curve["family"]),
        f"t={curve['parameter']}",
    ]
    if curve["plugin_id"]:
        provenance.append(f"plugin {curve['plugin_id']} v{curve['plugin_version'] or '—'}")
    st.caption(" · ".join(provenance))

    st.markdown("### Research paths")
    st.caption(
        "Once a specialization is interesting, these are the usual mathematical questions: "
        "what is the actual rank, is there another generator, what caused the extra directions, and does the phenomenon repeat?"
    )
    plan = analysis_plan(snapshot)
    paths = planner_research_paths(plan)
    path_cols = st.columns(min(4, len(paths)))
    for index, path in enumerate(paths):
        with path_cols[index % len(path_cols)]:
            with st.container(border=True):
                st.markdown(f"**{path['title']}**")
                st.caption(path["why"])
                if path.get("tab"):
                    if st.button(
                        f"Open {path['tab']}",
                        width="stretch",
                        key=f"analyze-research-{curve['id']}-{path['kind']}",
                    ):
                        _focus_tab(path["tab"])
                        st.rerun()
                elif st.button(
                    f"Open {path['page']}",
                    width="stretch",
                    key=f"analyze-research-{curve['id']}-{path['kind']}",
                ):
                    _go(
                        path["page"],
                        curve["id"],
                        family_pool_id=path.get("family_pool_id"),
                    )
                    st.rerun()

    _render_next_actions(snapshot, plan=plan)

    with region("analyze-proof-boundary", border=True):
        st.markdown("**Proof boundary**")
        if state["exact_rank"] is not None:
            st.success(
                f"Exact rank {state['exact_rank']} is supported by matching rigorous lower and upper bounds."
            )
        elif state["rigorous_upper"] is not None:
            st.info(
                f"Rigorous interval: {state['rigorous_lower']} ≤ rank ≤ {state['rigorous_upper']}."
            )
        else:
            st.info(
                f"Only the lower bound rank ≥ {state['rigorous_lower']} is rigorous right now."
            )
        if snapshot["witness_gap"]:
            st.warning(
                f"The point ledger is missing {snapshot['witness_gap']} replayable witness point(s) "
                "for the stored lower bound."
            )


def _render_next_actions(snapshot, *, plan=None):
    curve = snapshot["curve"]
    plan = analysis_plan(snapshot) if plan is None else plan
    actions = planner_immediate_actions(plan)
    if actions:
        primary = actions[0]
        with region("analyze-next-action", border=True):
            st.subheader("Next useful action")
            st.markdown(f"**{primary['title']}**")
            st.caption(primary["why"])
            if st.button(
                f"Open {primary['page']}",
                type="primary",
                width="stretch",
                key=f"analyze-primary-{curve['id']}-{primary['page']}",
            ):
                _go(primary["page"], curve["id"])
                st.rerun()

        if len(actions) > 1:
            st.markdown("#### Other worthwhile actions")
            cols = st.columns(min(3, len(actions) - 1))
            for i, action in enumerate(actions[1:4]):
                with cols[i]:
                    with st.container(border=True):
                        st.markdown(f"**{action['title']}**")
                        st.caption(action["why"])
                        if st.button(
                            f"Open {action['page']}",
                            width="stretch",
                            key=f"analyze-action-{curve['id']}-{i}-{action['page']}",
                        ):
                            _go(action["page"], curve["id"])
                            st.rerun()


def render_curve_research_workflow(db, curve_id):
    """Render human case state and planner guidance inside Curve detail."""
    snapshot = curve_analysis_snapshot(db, int(curve_id))
    _case_state_editor(db, int(curve_id))
    _render_next_actions(snapshot)
    st.caption(
        "Research status and notes are human workflow context only. Rank, point, "
        "independence, and proof claims remain owned by their scientific ledgers."
    )


def _rank_evidence(snapshot):
    rows = []
    for rec in snapshot["evidence"]:
        rows.append({
            "id": int(rec["id"]),
            "engine": rec["engine"],
            "type": rec["evidence_type"],
            "status": rec["status"],
            "rigorous": bool(rec["rigorous"]),
            "lower": rec["rigorous_lower"],
            "upper": rec["rigorous_upper"],
            "exact": rec["exact_rank"],
            "conditional upper": rec["conditional_analytic_upper"],
            "numerical": rec["numerical_rank_signal"],
            "timeout": bool(rec["timed_out"]),
            "runtime s": rec["elapsed_seconds"],
            "created": rec["created_at"],
        })
    if not rows:
        st.info("No structured rank evidence has been recorded for this curve.")
    else:
        dataframe(rows, semantic=f"analyze-evidence-{snapshot['curve']['id']}", width="stretch", hide_index=True)

    if snapshot["evidence_timeouts"]:
        st.caption(
            f"{snapshot['evidence_timeouts']} evidence attempt(s) timed out. "
            "Timeouts are computationally inconclusive, not negative rank results."
        )

    if st.button("Open specialist Descent tools", width="stretch", key="analyze-open-descent"):
        _go("Descent", snapshot["curve"]["id"])
        st.rerun()


def _points(snapshot):
    rows = []
    for rec in snapshot["points"]:
        rows.append({
            "id": int(rec["id"]),
            "role": rec["role"],
            "source": rec["source"],
            "exact": bool(rec["exact_verified"]),
            "independence": rec["independence_status"],
            "rigorous witness": bool(rec["rigorous_independent"]),
            "x": rec["x"],
            "y": rec["y"],
            "search": rec["search_ref"] or "—",
        })
    if rows:
        dataframe(rows, semantic=f"analyze-points-{snapshot['curve']['id']}", width="stretch", hide_index=True)
    else:
        st.info("No point-ledger rows are stored for this curve.")

    if snapshot["actionable_points"]:
        st.info(
            f"{len(snapshot['actionable_points'])} exact point(s) still need an exact independence decision."
        )
    if st.button("Open Independence Workbench", width="stretch", key="analyze-open-independence"):
        _go(
            "Independence",
            snapshot["curve"]["id"],
            point_ids=[int(rec["id"]) for rec in snapshot["actionable_points"]],
        )
        st.rerun()



def _timeline(db, curve_id):
    rows = research_timeline(db, int(curve_id), limit=500)
    st.subheader("Research Timeline")
    st.caption(
        "Newest-first projection over existing durable research artifacts. "
        "Timeline rows reference their owning records; they are not a second scientific event ledger."
    )
    if not rows:
        st.info("No durable research attempts or artifacts are attached to this curve yet.")
        return

    sources = sorted({str(rec["source"]) for rec in rows})
    selected_sources = st.multiselect(
        "Show sources",
        sources,
        default=sources,
        key=f"analysis-timeline-sources-{int(curve_id)}",
    )
    selected_source_set = set(selected_sources)
    visible = [
        rec for rec in rows
        if str(rec["source"]) in selected_source_set
    ]
    dataframe(
        [
            {
                "time": rec["timestamp"],
                "source": rec["source"],
                "event": rec["kind"].replace("_", " "),
                "status": rec["status"] or "—",
                "artifact": rec["artifact_ref"],
                "summary": rec["summary"],
                "open": rec["route"] or "—",
            }
            for rec in visible
        ],
        semantic=f"analysis-research-timeline-{int(curve_id)}",
        width="stretch",
        hide_index=True,
    )

    with st.expander("Timeline provenance"):
        st.caption(
            "Included sources are projected from Point Ledger / point discoveries, structured rank evidence, "
            "MW lattices, quartic and covering artifacts, Pipeline/Auto/Search state, curve-linked UI Jobs, "
            "researcher case state, hard-case bookmarks, and the curve event log when those stores exist."
        )
        st.json(
            {
                "curve_id": int(curve_id),
                "rows": len(rows),
                "visible_rows": len(visible),
                "sources": sources,
                "artifact_refs": [rec["artifact_ref"] for rec in visible[:100]],
            }
        )


def _rank_jump(snapshot):
    curve = snapshot["curve"]
    state = snapshot["state"]
    family_sections = snapshot["family_section_lower"]
    extra_directions = snapshot["extra_directions_lower"]

    generic_rank_exact = snapshot["generic_rank_exact"]
    rank_jump_lower = snapshot["rank_jump_lower"]
    rank_jump_status = snapshot["rank_jump_status"]
    family_generic_evidence = snapshot["family_generic_evidence"]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(
        "Certified family-section subgroup",
        "—" if family_sections is None else f"rank {family_sections}",
    )
    c2.metric(
        "Exact generic rank",
        "—" if generic_rank_exact is None else int(generic_rank_exact),
    )
    c3.metric("Specialization rank", _rank_text(state))
    c4.metric(
        "Certified rank jump",
        "—" if rank_jump_lower is None else f"≥ {rank_jump_lower}",
    )
    c5.metric(
        "Extra section directions",
        "—" if extra_directions is None else f"≥ {extra_directions}",
    )
    c6.metric("Best retained family lower", f"≥ {snapshot['family_best_lower']}")

    if rank_jump_status == "family_generic_rank_conflict":
        st.error(
            "Stored family-level generic-rank evidence is inconsistent. "
            "No formal specialization rank-jump claim is made until that conflict is resolved."
        )
    elif generic_rank_exact is not None and rank_jump_lower is not None and rank_jump_lower > 0:
        st.success(
            f"The family generic rank is rigorously exact at {generic_rank_exact}, while this "
            f"specialization has rigorous rank ≥{state['rigorous_lower']}. "
            f"Therefore the specialization rank jump is rigorously at least {rank_jump_lower}."
        )
    elif generic_rank_exact is not None:
        st.info(
            f"The family generic rank is rigorously exact at {generic_rank_exact}. "
            f"This specialization currently has rigorous rank ≥{state['rigorous_lower']}, "
            "so no positive rank jump beyond the exact generic rank is certified yet."
        )
    elif family_sections is None:
        st.warning(
            "No certified family-section subgroup is stored for this fiber. "
            "Rank Hunter cannot yet separate ordinary family directions from specialization-specific ones."
        )
    elif extra_directions and extra_directions > 0:
        st.success(
            f"This specialization has at least {extra_directions} rigorous Mordell–Weil direction(s) "
            f"beyond the certified rank-{family_sections} subgroup supplied by the family sections."
        )
        st.warning(
            "This does not automatically certify a generic-rank jump of the same size. "
            "That requires the family's generic rank itself to be known exactly."
        )
    else:
        st.info(
            f"This fiber has not yet been certified beyond its rank-{family_sections} family-section subgroup. "
            "Finding more rational points is not enough; they must add new independent directions."
        )

    evidence_rows = family_generic_evidence.get("evidence") or []
    if evidence_rows:
        with st.expander("Family generic-rank evidence", expanded=False):
            st.caption(
                "Family-level evidence is bound to the stored family spec and, when available, "
                "its family SHA-256. Historical plugin generic-rank metadata is not treated as proof."
            )
            dataframe(
                [
                    {
                        "id": rec["id"],
                        "criterion": rec["criterion"],
                        "status": rec["status"],
                        "generic lower": rec["generic_lower"],
                        "generic upper": rec["generic_upper"],
                        "exact generic rank": rec["exact_generic_rank"],
                        "plugin": rec["plugin_id"] or "—",
                        "plugin version": rec["plugin_version"] or "—",
                        "specialization": rec["specialization_parameter"],
                        "created": rec["created_at"],
                    }
                    for rec in evidence_rows
                ],
                semantic=f"analyze-family-evidence-{curve['id']}",
                width="stretch",
                hide_index=True,
            )
            exact_ids = family_generic_evidence.get("exact_evidence_ids") or []
            if exact_ids:
                st.caption(
                    "Exact generic-rank certificate evidence: "
                    + ", ".join(f"#{int(value)}" for value in exact_ids)
                )
            for rec in evidence_rows[:10]:
                if rec["certificate"]:
                    st.markdown(f"**Evidence #{int(rec['id'])} certificate**")
                    st.json(rec["certificate"])

    family_total = int(snapshot["family_total"])
    st.caption(
        f"Rank Hunter currently retains {family_total} fiber(s) from {curve['family']}. "
        f"{snapshot['family_at_or_above']} have a rigorous lower bound at least as large as this fiber."
    )

    st.markdown("#### Arithmetic fingerprint")
    arithmetic = snapshot["arithmetic"]
    a, b, c, d = st.columns(4)
    root_number = arithmetic["root_number"]
    a.metric("Root number", root_number if root_number is not None else "—")
    bad_primes = arithmetic["bad_primes"]
    b.metric("Bad primes", len(bad_primes) if isinstance(bad_primes, list) else "—")
    c.metric("Conductor", arithmetic["conductor"] if arithmetic["conductor"] is not None else "—")
    d.metric("Nagao score", f"{curve['score']:.4f}" if curve["score"] is not None else "—")
    if isinstance(bad_primes, list):
        st.caption(
            "Bad-prime set: "
            + (", ".join(str(p) for p in bad_primes) if bad_primes else "none")
            + f" · {_arithmetic_source_label(arithmetic, 'bad_primes')}"
        )
    st.caption(
        f"Conductor: {_arithmetic_source_label(arithmetic, 'conductor')} · "
        f"Discriminant: {_arithmetic_source_label(arithmetic, 'discriminant')} · "
        f"Root number: {_arithmetic_source_label(arithmetic, 'root_number')}"
    )
    if arithmetic["discriminant"] is not None:
        with st.expander("Discriminant"):
            st.code(str(arithmetic["discriminant"]), language="text")
            st.caption(f"Source: {_arithmetic_source_label(arithmetic, 'discriminant')}")

    st.markdown("#### Extra rigorous directions")
    if snapshot["extra_witnesses"]:
        dataframe(
            [
                {
                    "point": int(rec["id"]),
                    "role": rec["role"],
                    "source": rec["source"],
                    "x": rec["x"],
                    "y": rec["y"],
                    "search": rec["search_ref"] or "—",
                }
                for rec in snapshot["extra_witnesses"]
            ],
            semantic=f"analyze-extra-directions-{curve['id']}",
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption(
            "No rigorous witness rows are explicitly labeled outside the generic-section basis. "
            "The extra-direction lower bound remains authoritative even when historical point roles are incomplete."
        )

    st.markdown("#### Related retained fibers")
    rows = []
    for rec in snapshot["family_jumps"][:25]:
        rows.append({
            "curve": int(rec["curve_id"]),
            "parameter": rec["parameter"],
            "rigorous lower": rec["rigorous_lower"],
            "family sections": rec["family_section_lower"],
            "extra directions": rec["extra_directions_lower"],
            "exact rank": rec["exact_rank"],
            "root number": rec["root_number"],
            "score": rec["score"],
            "selected": int(rec["curve_id"]) == int(curve["id"]),
        })
    if rows:
        dataframe(
            rows,
            semantic=f"analyze-family-peers-{curve['id']}",
            width="stretch",
            hide_index=True,
        )

    st.markdown("#### What a researcher asks next")
    questions = []
    if state["exact_rank"] is None:
        questions.append("Is the specialization rank actually higher than the current lower bound?")
    if extra_directions is not None and extra_directions > 0:
        if generic_rank_exact is None:
            questions.append("Is the generic rank known exactly, so this surplus can be turned into a certified rank-jump statement?")
        elif rank_jump_lower is not None and rank_jump_lower > 0:
            questions.append(f"What arithmetic or geometric condition creates the certified rank jump ≥ {rank_jump_lower}?")
        questions.append("What arithmetic or geometric condition created the extra Mordell–Weil directions?")
    questions.append("Do fibers with similar parameter or arithmetic fingerprints show the same surplus?")
    questions.append("Can an extra point be explained by a section, base change, covering, isogeny, or hidden subfamily?")
    for question in questions:
        st.markdown(f"- {question}")

    if st.button(
        "Search related fibers",
        type="primary",
        width="stretch",
        key=f"analyze-search-related-{curve['id']}",
    ):
        _go("Search", curve["id"], family_pool_id=snapshot.get("family_pool_id"))
        st.rerun()


def _lattice(snapshot):
    if not snapshot["lattices"]:
        st.info("No stored Mordell–Weil lattice analysis exists for this curve.")
    else:
        rows = []
        for rec in snapshot["lattices"]:
            rows.append({
                "id": int(rec["id"]),
                "source": rec["source"],
                "basis": rec["basis_count"],
                "precision": rec["precision_bits"],
                "determinant": rec["determinant"],
                "min eigenvalue": rec["min_eigenvalue"],
                "positive definite screen": bool(rec["positive_definite_screen"]),
                "status": rec["status"],
                "updated": rec["updated_at"],
            })
        dataframe(rows, semantic=f"analyze-lattices-{snapshot['curve']['id']}", width="stretch", hide_index=True)
        st.caption(
            "Lattice diagnostics are numerical geometry. They help understand and schedule searches; "
            "they do not replace exact independence certificates."
        )

    if st.button("Open Lattices & Heights", width="stretch", key="analyze-open-lattices"):
        _go("Lattices & Heights", snapshot["curve"]["id"])
        st.rerun()


def _search_history(snapshot):
    sections = 0
    if snapshot["pipeline_candidates"]:
        sections += 1
        st.markdown("#### Builder / pipeline history")
        rows = []
        for rec in snapshot["pipeline_candidates"]:
            rows.append({
                "run": int(rec["run_id"]),
                "pipeline": rec["pipeline_name"],
                "run status": rec["run_status"],
                "candidate status": rec["status"],
                "stage": rec["current_stage_id"] or "—",
                "lower": rec["rigorous_lower"],
                "upper": rec["rigorous_upper"],
                "exact": rec["exact_rank"],
                "updated": rec["updated_at"],
            })
        dataframe(rows, semantic=f"analyze-pipeline-history-{snapshot['curve']['id']}", width="stretch", hide_index=True)

    if snapshot["quartic_searches"]:
        sections += 1
        st.markdown("#### Quartic searches")
        rows = []
        for rec in snapshot["quartic_searches"]:
            rows.append({
                "id": int(rec["id"]),
                "hole": rec["hole_label"] or "—",
                "height": rec["height_bound"],
                "denominator": (
                    f"{rec['denominator_low']}–{rec['denominator_high']}"
                    if rec["denominator_low"] is not None else "—"
                ),
                "status": rec["status"],
                "points": rec["point_count"],
                "runtime s": rec["runtime"],
                "updated": rec["updated_at"],
            })
        dataframe(rows, semantic=f"analyze-quartic-history-{snapshot['curve']['id']}", width="stretch", hide_index=True)

    if snapshot["events"]:
        sections += 1
        st.markdown("#### Curve event log")
        rows = [
            {
                "level": rec["level"],
                "message": rec["message"],
                "created": rec["created_at"],
            }
            for rec in snapshot["events"]
        ]
        dataframe(rows, semantic=f"analyze-events-{snapshot['curve']['id']}", width="stretch", hide_index=True)

    if not sections:
        st.info("No stored search history is attached to this curve yet.")

    if st.button("Target this curve", type="primary", width="stretch", key="analyze-open-target"):
        _go("Target", snapshot["curve"]["id"])
        st.rerun()


def _raw(snapshot):
    curve = snapshot["curve"]
    payload = {
        "curve": {key: curve[key] for key in curve.keys()},
        "rank_state": snapshot["state"],
        "counts": {
            "points": len(snapshot["points"]),
            "exact_points": len(snapshot["exact_points"]),
            "rigorous_witnesses": len(snapshot["witnesses"]),
            "actionable_points": len(snapshot["actionable_points"]),
            "lattices": len(snapshot["lattices"]),
            "rank_evidence": len(snapshot["evidence"]),
            "quartic_searches": len(snapshot["quartic_searches"]),
            "pipeline_rows": len(snapshot["pipeline_candidates"]),
        },
    }
    st.code(json.dumps(payload, indent=2, sort_keys=True, default=str), language="json")




def case_board_page(db, ctx):
    """Standalone researcher Case Board; no Campaign membership is required."""
    title(
        "Case Board",
        "Manage per-curve research priority, workflow state, goals, and next actions.",
        "Manage",
        icon="view_kanban",
    )

    rows = _analysis_curve_options(db)
    if not rows:
        st.info("No retained curves are available for Case Board.")
        return

    wanted = get_active_curve_id(
        st.session_state,
        "case_board_curve_id",
        "analyze_curve_id",
        "analysis_curve_id",
    )
    row = active_curve_selector(
        db,
        rows,
        wanted=wanted,
        state_prefix="case_board",
        widget_prefix="case-board-curve",
        compatibility_keys=("case_board_curve_id", "analyze_curve_id"),
    )
    if row is None:
        return

    snapshot = curve_analysis_snapshot(db, int(row["id"]))
    _overview(db, snapshot)
    st.caption(
        "Case Board stores researcher workflow context only. Rank, point, "
        "independence, and proof claims remain owned by their scientific ledgers."
    )

def page(db, ctx):
    title(
        "Analyze",
        "Research Inbox first: what needs attention, why, and what should happen next.",
        "Analysis",
    )
    render_feature_hook(db, ctx, "analysis.work_center.after_header")

    rows = _analysis_curve_options(db)
    if not rows:
        st.info("No retained curves are available to analyze.")
        return

    wanted = get_active_curve_id(
        st.session_state,
        "analyze_curve_id",
        "analysis_curve_id",
    )
    sections = [
        "Research Inbox",
        "Case Board",
        "Timeline",
        "Rank Jump",
    ]
    pending = st.session_state.pop("analyze_section_pending", None)
    if pending == "Overview":
        pending = "Case Board"
    if pending in sections:
        st.session_state["analyze_section"] = pending
        st.session_state.pop(rh_key("analyze-main-tabs"), None)
    current = st.session_state.get("analyze_section") or "Research Inbox"
    if current == "Overview":
        current = "Case Board"
    active = tabs(
        sections,
        value=current if current in sections else "Research Inbox",
        key="analyze-main-tabs",
    )
    st.session_state["analyze_section"] = active
    st.divider()

    if active == "Research Inbox":
        inbox = research_inbox(db)
        active_campaign_id = next(
            (
                int(rec["active_campaign_id"])
                for rec in inbox
                if rec["active_campaign_id"] is not None
            ),
            None,
        )
        seen_campaign_id = st.session_state.get(
            "analysis_inbox_campaign_context_id"
        )
        campaign_changed = seen_campaign_id != active_campaign_id
        if campaign_changed:
            st.session_state["analysis_inbox_campaign_context_id"] = active_campaign_id
        explicit_analyze_curve_id = (
            None
            if campaign_changed
            else st.session_state.get("analyze_curve_id")
        )
        default_curve_id = research_inbox_default_curve_id(
            inbox,
            explicit_analyze_curve_id=explicit_analyze_curve_id,
            semantic_curve_id=wanted,
        )
        _research_inbox(inbox, default_curve_id)
        st.caption(
            "Opening the Work Center is read-only. The Inbox runs database projections "
            "only; no Sage, PARI, mwrank, ratpoints, or proof job starts on page load."
        )
        return

    wanted = get_active_curve_id(
        st.session_state,
        "analyze_curve_id",
        "analysis_curve_id",
    )
    row = active_curve_selector(
        db,
        rows,
        wanted=wanted,
        state_prefix="analyze",
        widget_prefix="analyze-curve",
        compatibility_keys=("analyze_curve_id",),
    )
    if row is None:
        return
    if active == "Timeline":
        _timeline(db, int(row["id"]))
    else:
        snapshot = curve_analysis_snapshot(db, int(row["id"]))
        if active == "Case Board":
            _overview(db, snapshot)
        else:
            _rank_jump(snapshot)

    st.caption(
        "Opening Analyze is read-only. Expensive arithmetic runs only after an explicit researcher action."
    )
