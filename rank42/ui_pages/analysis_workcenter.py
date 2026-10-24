"""Curve-centered research casebook and launch point for analysis workflows."""
from __future__ import annotations

import html

import streamlit as st

from rank42.analysis_case_store import (
    CASE_ENTRY_OUTCOMES, CASE_ENTRY_TYPES, CASE_PRIORITY_PRESETS,
    CASE_RESEARCH_GOALS, CASE_WORKFLOW_STATUSES,
    add_case_entry, list_case_entries, set_case_entry_outcome,
    analysis_case_schema_ready, case_priority_label, get_analysis_case,
    save_analysis_case,
)
from rank42.analysis_planner import analysis_plan, planner_immediate_actions
from rank42.analyze_workspace import curve_analysis_snapshot, research_inbox
from rank42.manage_store import list_campaigns
from rank42.research_timeline import research_timeline
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.ui_handoffs import route_curve_to_target, route_pool_to_search
from rank42.ui_components import rh_key, tabs
from .common import title


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
                st.session_state["rh-casebook-section"] = "Family"
                st.rerun()


def _render_evidence(snapshot):
    state = snapshot["state"]
    with st.container(key=rh_key("casebook-evidence-rank"), border=True):
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

    with st.container(key=rh_key("casebook-evidence-points"), border=True):
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
    records = research_timeline(db, int(curve_id), limit=200)
    with st.container(key=rh_key("casebook-history"), border=True):
        st.subheader("Research history")
        if not records:
            st.info("No durable research activity is recorded for this curve yet.")
            return
        sources = sorted({str(rec["source"]) for rec in records})
        chosen = st.selectbox("Source", ["All activity", *sources],
                              key=f"casebook-history-source-{curve_id}")
        visible = [rec for rec in records if chosen == "All activity" or rec["source"] == chosen]
        st.caption(f"{len(visible)} event(s) · newest first")
        for index, rec in enumerate(visible[:20]):
            with st.container(key=f"casebook-history-item-{curve_id}-{index}"):
                st.markdown(f"**{rec['source']}** · {rec['summary']}")
                st.caption(f"{str(rec['timestamp'])[:19]} · {rec['artifact_ref']}"
                           + (f" · {rec['status']}" if rec["status"] else ""))
        if len(visible) > 20:
            with st.expander(f"All {len(visible)} events"):
                st.dataframe([
                    {"time": r["timestamp"], "source": r["source"],
                     "status": r["status"] or "—", "artifact": r["artifact_ref"],
                     "summary": r["summary"]}
                    for r in visible
                ], hide_index=True, width="stretch")


def _render_case_notes(db, curve_id):
    st.subheader("Case details")
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
        a, b, c, d = st.columns([1, 1, 1.35, 1.65], gap="small")
        priority = a.selectbox("Priority", priority_names, index=priority_names.index(priority_label) if priority_label in priority_names else 1)
        status = b.selectbox("Case status", list(CASE_WORKFLOW_STATUSES), index=list(CASE_WORKFLOW_STATUSES).index(current_status) if current_status in CASE_WORKFLOW_STATUSES else 0)
        campaign = c.selectbox(
            "Campaign", choices, index=campaign_index,
            format_func=lambda item: "None" if item is None else f"#{int(item['id'])} · {item['name']} · {item['status']}",
        )
        goal = d.selectbox("Research goal", goals, index=goals.index(current_goal) if current_goal in goals else 0, format_func=lambda value: value or "Follow planner recommendation")
        reason_col, note_col = st.columns([1, 2], gap="small")
        reason = reason_col.text_input("Research question", value="" if current is None else str(current["reason"] or ""))
        note = note_col.text_area("Case summary", value="" if current is None else str(current["note"] or ""), height=100,
                                  help="Summarize the current position. Record dated experiments in the Journal.")
        submitted = st.form_submit_button("Save case", type="primary", icon=":material/save:")
    if submitted:
        saved = save_analysis_case(
            db, curve_id=int(curve_id), campaign_id=None if campaign is None else int(campaign["id"]),
            priority=int(CASE_PRIORITY_PRESETS[priority]), workflow_status=status,
            research_goal=goal, reason=reason, note=note,
        )
        st.success(f"Saved {saved['workflow_status']} case · {case_priority_label(saved['priority'])} priority.")
        st.rerun()


def _render_case_header(snapshot, case):
    curve, state = snapshot["curve"], snapshot["state"]
    arithmetic = snapshot["arithmetic"]
    torsion = (arithmetic.get("torsion") or {}).get("label") or "Unknown torsion"
    status = str(case["workflow_status"]).title() if case is not None else "Unfiled"
    priority = case_priority_label(case["priority"]) if case is not None else "Normal"
    goal = str(case["research_goal"] or "") if case is not None else ""
    esc = html.escape
    facts = (
        ("WITNESSES", str(len(snapshot["witnesses"]))),
        ("POINTS TO REVIEW", str(len(snapshot["actionable_points"]))),
        ("RANK EVIDENCE", str(len(snapshot["evidence"]))),
        ("LATTICES", str(len(snapshot["lattices"]))),
    )
    fact_html = "".join(
        f'<div><span>{name}</span><strong>{value}</strong></div>' for name, value in facts
    )
    st.html(
        '<section class="rh-casebook-cover" aria-label="Selected research case">'
        '<div class="rh-casebook-cover-top"><div>'
        '<span class="rh-casebook-eyebrow">RESEARCH CASE FILE</span>'
        f'<h2>Curve #{int(curve["id"])} <span>{esc(status)} · {esc(priority)} priority</span></h2>'
        f'<p>{esc(str(curve["family"]))} · t={esc(str(curve["parameter"]))} · {esc(str(torsion))}</p>'
        '</div>'
        f'<div class="rh-casebook-cover-rank"><span>CERTIFIED RANK</span><strong>{esc(_rank_label(state))}</strong></div>'
        '</div>'
        f'<div class="rh-casebook-cover-goal"><span>RESEARCH GOAL</span><strong>{esc(goal or "Set a goal in Case details")}</strong></div>'
        f'<div class="rh-casebook-cover-facts">{fact_html}</div></section>'
    )


def _render_case_file(db, snapshot, plan, case):
    curve_id = int(snapshot["curve"]["id"])
    with st.container(key=rh_key("casebook-details"), border=True):
        _render_case_notes(db, curve_id)
        st.download_button(
            "Download case", _casebook_markdown(snapshot, case, list_case_entries(db, curve_id)),
            file_name=f"rank-hunter-case-{curve_id}.md", mime="text/markdown",
            icon=":material/download:",
        )

    brief, plan_col = st.columns([1, 1], gap="medium", vertical_alignment="top")
    with brief:
        with st.container(key=rh_key("casebook-brief"), border=True):
            st.subheader("Research brief")
            if case is None:
                st.caption("No research question has been recorded. Set one in Case details below.")
            else:
                st.markdown(f"**Question** · {case['reason'] or 'No question recorded'}")
                summary = str(case["note"] or "")
                if len(summary) > 420:
                    st.write(summary[:420].rsplit(" ", 1)[0] + "…")
                    with st.expander("Read full case summary"):
                        st.write(summary)
                else:
                    st.write(summary or "No case summary recorded yet.")
            state = snapshot["state"]
            lower = int(state["rigorous_lower"])
            upper = state["rigorous_upper"]
            if state["inconsistent"]:
                st.error("Rigorous bounds conflict. Resolve the evidence before drawing conclusions.")
            elif state["exact_rank"] is not None:
                st.success(f"Exact rank = {state['exact_rank']} is certified.")
            else:
                st.info(f"Rigorous rank ≥{lower}" +
                        (f" and ≤{upper}." if upper is not None else "; upper bound open."))
            if snapshot["witness_gap"]:
                st.warning(f"{snapshot['witness_gap']} replayable witness point(s) are missing from the point ledger.")
    with plan_col:
        _render_recommendation(snapshot, plan, case)

    actions = list(planner_immediate_actions(plan))[:4]
    activity = research_timeline(db, curve_id, limit=8)
    questions_col, recent_col = st.columns(2, gap="medium", vertical_alignment="top")
    with questions_col:
        with st.container(key=rh_key("casebook-open-questions"), border=True):
            st.subheader("Open lines of inquiry")
            if not actions:
                st.caption("No immediate open question follows from the stored evidence.")
            for index, action in enumerate(actions[:2], 1):
                st.markdown(f"**{index}. {action['title']}**")
                st.caption(action["why"])
            if len(actions) > 2:
                with st.expander(f"{len(actions) - 2} more lines of inquiry"):
                    for index, action in enumerate(actions[2:], 3):
                        st.markdown(f"**{index}. {action['title']}**")
                        st.caption(f"{action['why']} · Method: {action['recommended_method']}")
    with recent_col:
        with st.container(key=rh_key("casebook-recent"), border=True):
            st.subheader("Recent work")
            if not activity:
                st.caption("No recorded searches, points, or proof attempts for this curve yet.")
            for event in activity[:2]:
                st.markdown(f"**{event['source']}** · {event['summary']}")
                st.caption(str(event["timestamp"])[:19] + (f" · {event['status']}" if event["status"] else ""))
            if len(activity) > 2:
                with st.expander(f"{len(activity) - 2} more recent events"):
                    for event in activity[2:]:
                        st.markdown(f"**{event['source']}** · {event['summary']}")
                        st.caption(str(event["timestamp"])[:19] + (f" · {event['status']}" if event["status"] else ""))


def _render_journal(db, curve_id):
    st.caption("Record hypotheses, experiments, decisions, and follow-up work. Journal outcomes are researcher notes, not mathematical certificates.")
    compose, entries_col = st.columns([.85, 1.15], gap="medium", vertical_alignment="top")
    with compose:
        with st.container(key=rh_key("casebook-journal-compose"), border=True):
            st.subheader("New entry")
            with st.form(f"casebook-entry-{curve_id}", clear_on_submit=True):
                entry_type = st.selectbox("Type", CASE_ENTRY_TYPES)
                title_text = st.text_input("Title", max_chars=160, placeholder="What did you test or notice?")
                body = st.text_area("Details", height=170, max_chars=8000,
                                    placeholder="Inputs, method, observations, and a reproducible next step…")
                outcome = st.selectbox("Current outcome", CASE_ENTRY_OUTCOMES)
                artifact_ref = st.text_input("Artifact reference · optional", max_chars=160,
                                             placeholder="e.g. job:94, point:123, paper DOI")
                submitted = st.form_submit_button("Add entry", type="primary", icon=":material/add:")
            if submitted:
                try:
                    add_case_entry(db, curve_id=curve_id, entry_type=entry_type,
                                   title=title_text, body=body, outcome=outcome,
                                   artifact_ref=artifact_ref)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
    with entries_col:
        with st.container(key=rh_key("casebook-journal-entries"), border=True):
            entries = list_case_entries(db, curve_id)
            st.subheader(f"Journal · {len(entries)}")
            if not entries:
                st.caption("No journal entries yet. Start with the question you want to answer.")
            else:
                kind = st.selectbox("Show", ["All entries", *CASE_ENTRY_TYPES],
                                    key=f"casebook-entry-filter-{curve_id}")
                visible = [row for row in entries if kind == "All entries" or row["entry_type"] == kind]
                if not visible:
                    st.caption("No entries in this category.")
                for row in visible[:20]:
                    with st.container(key=f"casebook-entry-{int(row['id'])}"):
                        st.markdown(f"**{row['title']}** · {row['entry_type']}")
                        st.caption(f"{str(row['created_at'])[:19]} · {row['outcome']}"
                                   + (f" · {row['artifact_ref']}" if row["artifact_ref"] else ""))
                        if row["body"]:
                            st.write(row["body"])
                if len(visible) > 20:
                    st.caption(f"Showing 20 of {len(visible)} entries. Download the case file for the full journal.")
                st.divider()
                entry_ids = [int(row["id"]) for row in entries]
                chosen_id = st.selectbox("Update an entry", entry_ids,
                                         format_func=lambda value: next(
                                             (f"#{value} · {row['title']}" for row in entries if int(row["id"]) == value), str(value)
                                         ), key=f"casebook-update-entry-{curve_id}")
                chosen = next(row for row in entries if int(row["id"]) == chosen_id)
                chosen_outcome = st.selectbox("Outcome", CASE_ENTRY_OUTCOMES,
                                              index=CASE_ENTRY_OUTCOMES.index(chosen["outcome"]),
                                              key=f"casebook-update-outcome-{curve_id}-{chosen_id}")
                if st.button("Update outcome", key=f"casebook-update-button-{curve_id}",
                             disabled=chosen_outcome == chosen["outcome"]):
                    set_case_entry_outcome(db, chosen_id, curve_id=curve_id,
                                           outcome=chosen_outcome)
                    st.rerun()


def _casebook_markdown(snapshot, case, entries):
    curve, state = snapshot["curve"], snapshot["state"]
    lines = [
        f"# Casebook · Curve #{int(curve['id'])}", "",
        f"Family: {curve['family']}", f"Parameter: {curve['parameter']}",
        f"Rigorous rank: {_rank_label(state)}", "",
        "## Research brief", "",
        f"Status: {case['workflow_status'] if case else 'unfiled'}",
        f"Priority: {case_priority_label(case['priority']) if case else 'Normal'}",
        f"Goal: {case['research_goal'] if case else ''}", "",
        f"Question: {case['reason'] if case else ''}", "",
        str(case["note"] if case else ""), "",
        "## Journal", "",
    ]
    for row in entries:
        lines.extend([
            f"### {row['title']}",
            f"{row['created_at']} · {row['entry_type']} · {row['outcome']}"
            + (f" · {row['artifact_ref']}" if row["artifact_ref"] else ""),
            "", str(row["body"]), "",
        ])
    lines.extend(["## Evidence summary", "",
                  f"Rigorous witnesses: {len(snapshot['witnesses'])}",
                  f"Exact points awaiting review: {len(snapshot['actionable_points'])}",
                  f"Rank evidence records: {len(snapshot['evidence'])}", "",
                  "### Independent witness points", ""])
    for point in snapshot["witnesses"]:
        lines.append(f"- Point #{int(point['id'])}: x={point['x']}, y={point['y']} · {point['source']}")
    lines.extend(["", "### Rank evidence records", ""])
    for row in snapshot["evidence"]:
        lines.append(
            f"- Evidence #{int(row['id'])}: {row['engine']} · {row['status']} · "
            f"rigorous={bool(row['rigorous'])} · lower={row['rigorous_lower']} · "
            f"upper={row['rigorous_upper']} · exact={row['exact_rank']}"
        )
    lines.extend(["", "Journal conclusions are researcher notes; only Rank Hunter's evidence ledger certifies ranks and independence.", ""])
    return "\n".join(lines)


def page(db, ctx):
    sections = ["Case file", "Journal", "Evidence", "Family", "History"]
    if (
        "rh-casebook-section" not in st.session_state
        and st.session_state.get("casebook-section") in sections
    ):
        st.session_state["rh-casebook-section"] = st.session_state["casebook-section"]
    title("Casebook")
    section = tabs(
        sections,
        value=str(st.session_state.get("rh-casebook-section") or "Case file"),
        key="casebook-section", variant="line", width="content",
    )
    queue = research_inbox(db, limit=10000)
    by_id = {int(item["curve_id"]): item for item in queue}
    current_curve_id = get_active_curve_id(st.session_state, "analysis_workcenter_curve_id")
    st.subheader("Select a research case")
    with st.container(key=rh_key("casebook-chooser"), border=True):
        view_col, search_col, case_col = st.columns(
            [1.3, 1.5, 3.2], gap="small", vertical_alignment="bottom"
        )
        view = view_col.selectbox(
            "Queue", ["Needs attention", "Open cases", "Deferred cases", "Resolved cases", "All curves"],
            key="analysis-workcenter-queue-view",
        )
        query = search_col.text_input(
            "Search", placeholder="ID, family, parameter…",
            key="analysis-workcenter-queue-search",
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
            case_col.caption("No matches. Try All curves or clear the search.")
            return

        candidate_ids = [int(item["curve_id"]) for item in candidates]
        widget_key = "analysis-workcenter-queue-curve"
        wanted = current_curve_id
        if st.session_state.get(widget_key) not in candidate_ids:
            st.session_state[widget_key] = wanted if wanted in candidate_ids else candidate_ids[0]
        selected_id = case_col.selectbox(
            "Curve", candidate_ids, key=widget_key,
            format_func=lambda value: _queue_label(by_id[int(value)]),
            help=f"{matching_count:,} matching curves; showing the first {len(candidates):,} by priority.",
        )
    set_active_curve_id(
        st.session_state, int(selected_id), "analysis_workcenter_curve_id",
        "analyze_curve_id", "analysis_curve_id", "independence_curve_id",
        "descent_curve_id", "target_curve_id", "points_curve_id",
    )
    queue_item = by_id[int(selected_id)]
    if queue_item.get("attention_reasons"):
        st.caption("Attention · " + " · ".join(queue_item["attention_reasons"]))

    curve_id = int(selected_id)
    snapshot = curve_analysis_snapshot(db, curve_id)
    plan = analysis_plan(snapshot)
    case = get_analysis_case(db, curve_id)
    _render_case_header(snapshot, case)
    if section == "Case file":
        _render_case_file(db, snapshot, plan, case)
    elif section == "Journal":
        _render_journal(db, curve_id)
    elif section == "Evidence":
        _render_evidence(snapshot)
    elif section == "Family":
        _render_family(snapshot)
    elif section == "History":
        _render_activity(db, curve_id)
