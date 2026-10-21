"""Core Twists and Isogenies research views inside Tools."""
from __future__ import annotations

import html
import math

import streamlit as st

from rank42 import arithmetic_neighborhood as B
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.ui_handoffs import (
    normalize_research_handoff,
    route_curve_to_target,
    route_selection_to_pipelines,
    set_research_handoff,
)
from .common import setting


def _curve_label(record):
    rank = record.rigorous_lower
    score = f" · Nagao {record.score:.3f}" if record.score is not None else ""
    conflict = " · evidence conflict" if record.rank_inconsistent else ""
    return (
        f"#{record.curve_id} · {record.family} · t={record.parameter or '—'} · "
        f"{'≥' + str(rank) if rank is not None else 'rank ?'}{score}{conflict}"
    )


def _open_curve(record, *, handoff=None):
    if handoff is not None:
        set_research_handoff(st.session_state, handoff)
    set_active_curve_id(
        st.session_state,
        record.curve_id,
        "curves_selected_id",
    )
    st.session_state["curves_selected_id"] = record.curve_id
    st.session_state["curves-filter"] = ""
    st.session_state["curves-minrank"] = 0
    st.session_state["curves-family"] = "All"
    st.session_state["curves-evidence"] = "Any"
    st.session_state["curves-status"] = "Any"
    st.session_state["rh_page"] = "Curves"
    st.rerun()



def _retained_handoff_records(records):
    unique = {}
    for record in records:
        unique[int(record.curve_id)] = record
    ordered = [unique[key] for key in sorted(unique)]
    curve_ids = [record.curve_id for record in ordered]
    parameters = []
    for record in ordered:
        item = {
            "curve_id": record.curve_id,
            "parameter": record.parameter or "—",
        }
        if record.rigorous_lower is not None:
            item["rigorous_lower"] = record.rigorous_lower
        if record.generic_lower is not None:
            item["generic_lower"] = record.generic_lower
        if record.score is not None:
            item["score"] = record.score
        if record.root_number is not None:
            item["root_number"] = record.root_number
        parameters.append(item)
    return curve_ids, parameters


def _research_handoff(source_record, *, kind, items, retained_records=(), extra=None):
    curve_ids, parameters = _retained_handoff_records(retained_records)
    selection = {
        "workspace": "Arithmetic Neighborhood",
        "source_curve": {
            "curve_id": source_record.curve_id,
            "family": source_record.family,
            "parameter": source_record.parameter,
            "a_invariants": list(source_record.ainvs),
        },
        "item_count": len(items),
        "items": items,
    }
    if extra:
        selection.update(extra)
    return {
        "source": "rank42_arithmetic_neighborhood",
        "kind": kind,
        "family": source_record.family,
        "curve_ids": curve_ids,
        "parameters": parameters,
        "selection": selection,
    }


def _render_pipeline_handoff(payload, *, key, title="Selection handoff"):
    normalized = normalize_research_handoff(payload)
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(
            f"{len(normalized['selection'].get('items') or []):,} selected item"
            f"{'' if len(normalized['selection'].get('items') or []) == 1 else 's'} · "
            f"{len(normalized['curve_ids']):,} retained curve match"
            f"{'' if len(normalized['curve_ids']) == 1 else 'es'} · "
            f"hash: `{normalized['selection_hash'][:16]}`"
        )
        with st.expander("Inspect normalized handoff", expanded=False):
            st.json(normalized)
        if st.button(
            "Open in Pipelines",
            type="primary",
            use_container_width=True,
            key=f"{key}-pipelines",
        ):
            route_selection_to_pipelines(st.session_state, payload)
            st.rerun()
    return normalized


def _single_item_handoff(source_record, *, kind, item, retained_records=(), extra=None):
    return _research_handoff(
        source_record,
        kind=kind,
        items=[item],
        retained_records=retained_records,
        extra=extra,
    )


def _section_banner(step, title, detail):
    st.markdown(
        (
            "<div class='rh-neighborhood-stage'>"
            f"<div class='rh-neighborhood-stage-index'>{html.escape(str(step))}</div>"
            "<div class='rh-neighborhood-stage-copy'>"
            f"<div class='rh-neighborhood-stage-title'>{html.escape(str(title))}</div>"
            f"<div class='rh-neighborhood-stage-detail'>{html.escape(str(detail))}</div>"
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )


def _render_source_brief(source_record):
    conductor_preview, conductor_digits = _compact_integer(source_record.conductor)
    discriminant_preview, discriminant_digits = _compact_integer(
        source_record.discriminant
    )
    with st.container(border=True):
        if source_record.rank_inconsistent:
            st.warning("This curve has conflicting stored rank evidence. Review its Curves record before using a rank claim.")
        st.markdown(
            f"### Curve #{source_record.curve_id} · {source_record.family}"
        )
        st.caption(
            "Source arithmetic brief · "
            f"t={source_record.parameter or '—'} · retained Rank Hunter curve"
        )
        brief_cols = st.columns(4)
        brief_cols[0].metric(
            "Stored lower",
            "—"
            if source_record.rigorous_lower is None
            else f"≥{source_record.rigorous_lower}",
        )
        brief_cols[1].metric(
            "Conductor",
            conductor_preview
            if conductor_digits is not None
            else (source_record.conductor or "—"),
        )
        brief_cols[2].metric(
            "Discriminant",
            discriminant_preview
            if discriminant_digits is not None
            else (source_record.discriminant or "—"),
        )
        brief_cols[3].metric(
            "Root / Nagao",
            (
                (
                    f"{source_record.root_number:+d}"
                    if source_record.root_number in (-1, 1)
                    else "—"
                )
                + " / "
                + (
                    f"{source_record.score:.3f}"
                    if source_record.score is not None
                    else "—"
                )
            ),
        )
        st.code(
            "a-invariants = [" + ", ".join(source_record.ainvs) + "]",
            language="text",
        )


def _render_mode_boundary(research_view):
    if research_view == "Quadratic twists":
        exact = "Exact twist/minimal models, conductor, discriminant, bad primes, root number, j, torsion."
        stored = "Rank/Nagao appears only when Rank Hunter already retains a matching curve."
        heuristic = "Using twist arithmetic patterns to prioritize later searches is heuristic."
    else:
        exact = "Completed rational-isogeny computations and completed prime-degree source probes."
        stored = "Rank/Nagao is projected only from retained Rank Hunter curves and is not recomputed here."
        heuristic = "Timeouts and unfinished prime-degree checks remain unknown, never negative evidence."

    st.caption(f"Exact in this view: {exact}")
    st.markdown(
        (
            "<div class='rh-neighborhood-boundary-grid'>"
            "<div><span class='rh-neighborhood-boundary-label'>EXACT</span>"
            f"<span>{html.escape(exact)}</span></div>"
            "<div><span class='rh-neighborhood-boundary-label'>STORED</span>"
            f"<span>{html.escape(stored)}</span></div>"
            "<div><span class='rh-neighborhood-boundary-label'>INTERPRETATION</span>"
            f"<span>{html.escape(heuristic)}</span></div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

def _graph_html(payload, stored_index, source_record):
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    source = int(payload.get("source_index") or 0)
    if not nodes:
        return "<div>No isogeny nodes were returned.</div>"

    width, height = 900, 470
    cx, cy = width / 2, height / 2
    radius = min(175.0, 65.0 + 24.0 * max(1, len(nodes)))
    positions = {}
    if len(nodes) == 1:
        positions[0] = (cx, cy)
    else:
        for idx in range(len(nodes)):
            theta = -math.pi / 2 + 2 * math.pi * idx / len(nodes)
            positions[idx] = (
                cx + radius * math.cos(theta),
                cy + radius * math.sin(theta),
            )

    parts = [
        "<div class='rh-neighborhood-graph-shell'><svg viewBox='0 0 900 470' "
        "role='img' aria-label='Rational isogeny graph'>"
    ]
    for edge in edges:
        i, j = int(edge["source"]), int(edge["target"])
        x1, y1 = positions[i]
        x2, y2 = positions[j]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        parts.append(
            f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' "
            "stroke='var(--rh-border-strong)' stroke-width='3'/>"
        )
        parts.append(
            f"<text x='{mx:.1f}' y='{my - 7:.1f}' text-anchor='middle' "
            "fill='var(--rh-text-secondary)' font-size='13' font-weight='700'>"
            f"{int(edge['degree'])}</text>"
        )

    for node in nodes:
        idx = int(node["index"])
        x, y = positions[idx]
        key = B.model_key(node.get("ainvs") or [])
        stored = stored_index.get(key) or []
        fill = "var(--rh-primary)" if idx == source else (
            "var(--rh-success)" if stored else "var(--rh-card-2)"
        )
        text_fill = "var(--rh-text-on-accent)" if idx == source else "var(--rh-text)"
        source_note = (
            f" · selected source #{source_record.curve_id} (Q-isomorphic)"
            if idx == source
            else ""
        )
        tooltip = html.escape(
            f"E{idx} · conductor {node.get('conductor')} · "
            f"root {node.get('root_number')} · exact stored matches "
            f"{','.join('#'+str(r.curve_id) for r in stored) or 'none'}"
            f"{source_note}"
        )
        parts.append(
            f"<g><title>{tooltip}</title>"
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='31' fill='{fill}' "
            "stroke='var(--rh-border-strong)' stroke-width='2'/>"
            f"<text x='{x:.1f}' y='{y + 5:.1f}' text-anchor='middle' "
            f"fill='{text_fill}' font-size='14' font-weight='800'>E{idx}</text></g>"
        )
    parts.append("</svg></div>")
    return "".join(parts)


def _node_rows(payload, stored_index, source_record):
    source = int(payload.get("source_index") or 0)
    rows = []
    for node in payload.get("nodes") or []:
        index = int(node["index"])
        key = B.model_key(node.get("ainvs") or [])
        exact = stored_index.get(key) or []
        retained = list(exact)
        match_kind = "exact model" if exact else None
        if index == source and all(
            record.curve_id != source_record.curve_id for record in retained
        ):
            retained.insert(0, source_record)
            match_kind = "source Q-isomorphic" if not exact else "exact model + source"
        lowers = [
            record.rigorous_lower
            for record in retained
            if record.rigorous_lower is not None
        ]
        exact_ranks = [
            record.exact_rank
            for record in retained
            if record.exact_rank is not None
        ]
        scores = [
            record.score
            for record in retained
            if record.score is not None
        ]
        rows.append(
            {
                "node": f"E{index}",
                "source": index == source,
                "retained match": (
                    ", ".join(f"#{record.curve_id}" for record in retained) or None
                ),
                "match type": match_kind,
                "stored rigorous lower": max(lowers) if lowers else None,
                "stored exact rank": max(exact_ranks) if exact_ranks else None,
                "stored Nagao": max(scores) if scores else None,
                "a-invariants": "[" + ", ".join(node.get("ainvs") or []) + "]",
                "conductor": node.get("conductor"),
                "discriminant": node.get("discriminant"),
                "j-invariant": node.get("j_invariant"),
                "root": node.get("root_number"),
                "torsion invariants": str(node.get("torsion_invariants") or []),
            }
        )
    return rows



def _twist_matches(item, stored_index, source_record):
    matches = []
    kinds = []
    for field, label in (
        ("ainvs", "exact twist model"),
        ("minimal_ainvs", "exact minimal model"),
    ):
        values = item.get(field) or []
        if len(values) != 5:
            continue
        for record in stored_index.get(B.model_key(values), []) or []:
            if all(existing.curve_id != record.curve_id for existing in matches):
                matches.append(record)
                kinds.append(label)

    if int(item.get("d") or 0) == 1 and all(
        record.curve_id != source_record.curve_id for record in matches
    ):
        matches.insert(0, source_record)
        kinds.insert(0, "source Q-isomorphic")
    return matches, " + ".join(dict.fromkeys(kinds)) if kinds else None



def _isogeny_handoff_item(node, retained, *, relation):
    return {
        "node_index": int(node["index"]),
        "relation": relation,
        "a_invariants": list(node.get("ainvs") or []),
        "conductor": node.get("conductor"),
        "discriminant": node.get("discriminant"),
        "j_invariant": node.get("j_invariant"),
        "root_number": node.get("root_number"),
        "torsion_invariants": list(node.get("torsion_invariants") or []),
        "retained_curve_ids": sorted({int(record.curve_id) for record in retained}),
    }

def _compact_integer(value, *, head=8):
    text = str(value or "").strip()
    if not text:
        return "—", None
    sign = "-" if text.startswith("-") else ""
    digits = text.lstrip("+-")
    if not digits.isdigit():
        return text, None
    size = len(digits)
    if size <= head + 2:
        return sign + digits, size
    return f"{sign}{digits[:head]}… ({size} digits)", size


def _compact_primes(values, *, limit=8):
    primes = [int(value) for value in values or []]
    shown = ", ".join(str(p) for p in primes[:limit])
    if len(primes) > limit:
        shown += f", … (+{len(primes) - limit})"
    return shown or "—"



def _twist_handoff_item(item, retained):
    return {
        "d": int(item["d"]),
        "a_invariants": list(item.get("ainvs") or []),
        "minimal_a_invariants": list(item.get("minimal_ainvs") or []),
        "conductor": item.get("conductor"),
        "discriminant": item.get("discriminant"),
        "j_invariant": item.get("j_invariant"),
        "root_number": item.get("root_number"),
        "torsion_invariants": list(item.get("torsion_invariants") or []),
        "bad_primes": [int(p) for p in item.get("bad_primes") or []],
        "retained_curve_ids": sorted({int(record.curve_id) for record in retained}),
    }

def _twist_rows(payload, stored_index, source_record):
    source_item = next(
        (
            item for item in payload.get("twists") or []
            if int(item.get("d") or 0) == 1
        ),
        None,
    )
    source_bad = set(int(p) for p in (source_item or {}).get("bad_primes") or [])
    rows = []
    for item in payload.get("twists") or []:
        retained, match_type = _twist_matches(item, stored_index, source_record)
        lowers = [
            record.rigorous_lower
            for record in retained
            if record.rigorous_lower is not None
        ]
        exact_ranks = [
            record.exact_rank
            for record in retained
            if record.exact_rank is not None
        ]
        scores = [
            record.score
            for record in retained
            if record.score is not None
        ]
        bad = [int(p) for p in item.get("bad_primes") or []]
        bad_set = set(bad)
        added = sorted(bad_set - source_bad)
        removed = sorted(source_bad - bad_set)
        delta_parts = []
        if added:
            delta_parts.append("+" + ",".join(str(p) for p in added[:5]) + ("…" if len(added) > 5 else ""))
        if removed:
            delta_parts.append("-" + ",".join(str(p) for p in removed[:5]) + ("…" if len(removed) > 5 else ""))
        conductor_text, conductor_digits = _compact_integer(item.get("conductor"))
        try:
            conductor_sort = abs(int(str(item.get("conductor"))))
        except Exception:
            conductor_sort = None
        rows.append(
            {
                "d": int(item["d"]),
                "root": item.get("root_number"),
                "conductor": conductor_text,
                "N digits": conductor_digits,
                "ω(N)": len(bad),
                "bad primes": _compact_primes(bad),
                "support Δ": "; ".join(delta_parts) or "same",
                "retained match": (
                    ", ".join(f"#{record.curve_id}" for record in retained) or None
                ),
                "match type": match_type,
                "stored rigorous lower": max(lowers) if lowers else None,
                "stored exact rank": max(exact_ranks) if exact_ranks else None,
                "stored Nagao": max(scores) if scores else None,
                "_conductor_sort": conductor_sort,
            }
        )
    return rows


def _render_twist_view(source_record, records, by_id, science_python):
    st.caption(
        "Quadratic twists are enumerated by squarefree d with a bounded |d|. "
        "Conductor, discriminant, bad primes, and root number are exact Sage arithmetic. "
        "Any visible association with stored rank or Nagao data is a research signal, not proof."
    )

    bound = st.select_slider(
        "Squarefree |d| bound",
        options=[25, 50, 100, 150, 250],
        value=50,
        key="rh-neighborhood-twist-bound",
        help="Includes both signs and d=1. The hard cap is 250.",
    )
    cache_key = "rh-neighborhood-twists::" + B.twist_cache_key(source_record, bound)
    payload = st.session_state.get(cache_key)

    cols = st.columns([0.28, 0.72])
    with cols[0]:
        compute = st.button(
            "Compute quadratic twists",
            type="primary",
            use_container_width=True,
            key="rh-neighborhood-compute-twists",
        )
    with cols[1]:
        st.caption(
            "Runs only on request with a hard timeout. Session cache only — recompute after "
            "a Rank Hunter restart, browser-session reset, or a page reset."
        )

    if compute:
        if not science_python:
            st.error("Scientific Python is not configured in Rank Hunter Settings.")
            return
        try:
            with st.spinner("Computing bounded exact quadratic-twist neighborhood…"):
                payload = B.run_twist_worker(
                    science_python=science_python,
                    ainvs=source_record.ainvs,
                    bound=bound,
                    timeout=120,
                )
            st.session_state[cache_key] = payload
        except Exception as exc:
            st.error(str(exc))
            return

    if payload is None:
        st.info("Compute the bounded quadratic-twist neighborhood to inspect nearby squareclasses.")
        return

    stored_index = B.stored_model_index(records)
    all_rows = _twist_rows(payload, stored_index, source_record)
    roots_plus = sum(row["root"] == 1 for row in all_rows)
    roots_minus = sum(row["root"] == -1 for row in all_rows)
    stored_count = sum(bool(row["retained match"]) for row in all_rows)

    cards = st.columns(4)
    cards[0].metric("Squarefree twists", len(all_rows))
    cards[1].metric("Root +1", roots_plus)
    cards[2].metric("Root -1", roots_minus)
    cards[3].metric("Retained matches", stored_count)

    filter_cols = st.columns(3)
    with filter_cols[0]:
        root_filter = st.selectbox(
            "Root number",
            ["All", "+1", "-1", "Unknown"],
            key="rh-neighborhood-root-filter",
        )
    with filter_cols[1]:
        stored_filter = st.selectbox(
            "Retained",
            ["All", "Stored only", "Unstored only"],
            key="rh-neighborhood-stored-filter",
        )
    with filter_cols[2]:
        sort_mode = st.selectbox(
            "Sort",
            ["|d|", "Stored rigorous lower", "Stored Nagao", "Conductor"],
            key="rh-neighborhood-twist-sort",
        )

    rows = list(all_rows)
    if root_filter == "+1":
        rows = [row for row in rows if row["root"] == 1]
    elif root_filter == "-1":
        rows = [row for row in rows if row["root"] == -1]
    elif root_filter == "Unknown":
        rows = [row for row in rows if row["root"] not in (-1, 1)]

    if stored_filter == "Stored only":
        rows = [row for row in rows if row["retained match"]]
    elif stored_filter == "Unstored only":
        rows = [row for row in rows if not row["retained match"]]

    if sort_mode == "Stored rigorous lower":
        rows.sort(
            key=lambda row: (
                row["stored rigorous lower"] is None,
                -row["stored rigorous lower"] if row["stored rigorous lower"] is not None else 0,
                abs(row["d"]),
                row["d"],
            )
        )
    elif sort_mode == "Stored Nagao":
        rows.sort(
            key=lambda row: (
                row["stored Nagao"] is None,
                -row["stored Nagao"] if row["stored Nagao"] is not None else 0,
                abs(row["d"]),
                row["d"],
            )
        )
    elif sort_mode == "Conductor":
        rows.sort(
            key=lambda row: (
                row["_conductor_sort"] is None,
                row["_conductor_sort"] or 0,
                abs(row["d"]),
                row["d"],
            )
        )
    else:
        rows.sort(key=lambda row: (abs(row["d"]), 0 if row["d"] > 0 else 1, row["d"]))

    if not rows:
        st.info("No quadratic twists match the current filters.")
        return

    d_values = [row["d"] for row in rows]
    _section_banner(
        "2",
        "Selected twist",
        "Inspect one exact computed squareclass before scanning the full neighborhood table.",
    )
    with st.container(border=True):
        st.markdown("**Inspect one computed twist**")
        chosen_d = st.selectbox(
            "Quadratic twist",
            d_values,
            format_func=lambda d: "d=1 · source squareclass" if int(d) == 1 else f"d={d}",
            key="rh-neighborhood-inspect-twist",
        )
        item = next(
            item
            for item in payload.get("twists") or []
            if int(item["d"]) == int(chosen_d)
        )
        retained, match_type = _twist_matches(item, stored_index, source_record)

        conductor_preview, conductor_digits = _compact_integer(item.get("conductor"))
        bad_primes = [int(p) for p in item.get("bad_primes") or []]
        detail_cols = st.columns(4)
        detail_cols[0].metric(
            "Root number",
            "—" if item.get("root_number") not in (-1, 1) else f"{int(item['root_number']):+d}",
        )
        detail_cols[1].metric(
            "Conductor size",
            "—" if conductor_digits is None else f"{conductor_digits} digits",
        )
        detail_cols[2].metric("Bad-prime count", len(bad_primes))
        detail_cols[3].metric(
            "Retained",
            "yes" if retained else "no",
        )
        st.caption(
            f"Twist squareclass d={int(chosen_d)} · match: {match_type or 'not retained'} · "
            f"conductor {conductor_preview}"
        )
        with st.expander("Exact twist arithmetic", expanded=False):
            st.code(
                "conductor = " + str(item.get("conductor") or "—") + "\n"
                "discriminant = " + str(item.get("discriminant") or "—") + "\n"
                "j-invariant = " + str(item.get("j_invariant") or "—") + "\n"
                "bad primes = [" + ", ".join(str(p) for p in bad_primes) + "]\n"
                "torsion invariants = " + str(item.get("torsion_invariants") or []) + "\n"
                "minimal a-invariants = [" + ", ".join(item.get("minimal_ainvs") or []) + "]",
                language="text",
            )

    _section_banner(
        "3",
        "Twist neighborhood",
        "Compare the filtered computed squareclasses, then choose any exact items to hand off.",
    )
    st.markdown("**Computed twist neighborhood**")
    st.caption(
        f"Showing {len(rows):,} of {len(all_rows):,} computed squarefree twist classes. "
        "Stored rank/Nagao columns appear only when an exact retained model match exists "
        "or for the explicitly identified d=1 source."
    )
    display_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    st.dataframe(
        display_rows,
        hide_index=True,
        width="stretch",
        height=min(620, 70 + 35 * min(len(display_rows), 15)),
    )

    selected_ds = st.multiselect(
        "Select twist squareclasses for research handoff",
        d_values,
        default=[chosen_d],
        format_func=lambda d: "d=1 · source squareclass" if int(d) == 1 else f"d={d}",
        key="rh-neighborhood-handoff-twists",
    )
    if len(selected_ds) > 64:
        st.warning("Twist research handoffs are capped at 64 explicit squareclasses.")
        selected_ds = selected_ds[:64]

    selected_items = []
    selected_retained = []
    selected_set = {int(d) for d in selected_ds}
    for selected_item in payload.get("twists") or []:
        if int(selected_item["d"]) not in selected_set:
            continue
        selected_matches, _ = _twist_matches(
            selected_item,
            stored_index,
            source_record,
        )
        selected_retained.extend(selected_matches)
        selected_items.append(
            _twist_handoff_item(selected_item, selected_matches)
        )
    selected_items.sort(key=lambda rec: (abs(int(rec["d"])), int(rec["d"])))

    if selected_items:
        twist_handoff = _research_handoff(
            source_record,
            kind="quadratic_twist_selection",
            items=selected_items,
            retained_records=selected_retained,
            extra={
                "bound": int(bound),
                "root_filter": root_filter,
                "retained_filter": stored_filter,
                "sort": sort_mode,
            },
        )
        _render_pipeline_handoff(
            twist_handoff,
            key="rh-neighborhood-twist-selection",
            title="Quadratic-twist selection handoff",
        )

    current_handoff = _single_item_handoff(
        source_record,
        kind="quadratic_twist",
        item=_twist_handoff_item(item, retained),
        retained_records=retained,
        extra={"bound": int(bound)},
    )

    if retained:
        match_ids = [record.curve_id for record in retained]
        target_id = st.selectbox(
            "Retained curve match",
            match_ids,
            format_func=lambda curve_id: _curve_label(by_id[int(curve_id)]),
            key="rh-neighborhood-twist-retained-match",
        )
        target_record = by_id[int(target_id)]
        actions = st.columns(2)
        with actions[0]:
            if st.button(
                "Open retained curve",
                use_container_width=True,
                key="rh-neighborhood-open-twist-match",
            ):
                _open_curve(target_record, handoff=current_handoff)
        with actions[1]:
            if st.button(
                "Target Search",
                type="primary",
                use_container_width=True,
                key="rh-neighborhood-target-twist-match",
            ):
                route_curve_to_target(
                    st.session_state,
                    target_record.curve_id,
                    handoff=current_handoff,
                )
                st.rerun()
    else:
        st.caption(
            "This twist is not an exact retained-model match. Viewing it does not insert "
            "a curve or rank evidence into Rank Hunter."
        )


def _render_isogeny_probe(source_record, records, science_python, *, expanded=False):
    with st.expander("Large-curve exact isogeny probe", expanded=expanded):
        st.caption(
            "Exact one-hop source probes by rational prime isogeny degree. "
            "Completed degrees are rigorous exact checks; a timed-out degree remains unknown. "
            "This is partial source information, not a replacement for the complete isogeny class."
        )
        profile = st.selectbox(
            "Probe degrees",
            ["Common ≤13", "All rational prime degrees"],
            key="rh-neighborhood-probe-profile",
        )
        primes = (
            (2, 3, 5, 7, 11, 13)
            if profile == "Common ≤13"
            else B.RATIONAL_PRIME_ISOGENY_DEGREES
        )
        per_degree_timeout = st.selectbox(
            "Per-degree timeout (seconds)",
            [5, 10, 20, 45],
            index=1,
            key="rh-neighborhood-probe-timeout",
        )
        cache_key = "rh-neighborhood-probe::" + B.probe_cache_key(
            source_record,
            primes,
            per_degree_timeout,
        )
        result = st.session_state.get(cache_key)

        if st.button(
            "Run exact source probe",
            use_container_width=True,
            key="rh-neighborhood-run-probe",
        ):
            if not science_python:
                st.error("Scientific Python is not configured in Rank Hunter Settings.")
                return
            try:
                with st.spinner("Checking prime-degree source isogenies independently…"):
                    result = B.run_isogeny_probe(
                        science_python=science_python,
                        ainvs=source_record.ainvs,
                        primes=primes,
                        per_degree_timeout=per_degree_timeout,
                    )
                st.session_state[cache_key] = result
            except Exception as exc:
                st.error(str(exc))
                return

        if result is None:
            return

        hits = result.get("hits") or []
        checked = [int(p) for p in result.get("checked_degrees") or []]
        timed_out = [int(p) for p in result.get("timed_out_degrees") or []]
        errors = result.get("errors") or []
        hit_degrees = sorted({int(item["degree"]) for item in hits})
        no_hit = [p for p in checked if p not in hit_degrees]

        cards = st.columns(4)
        cards[0].metric("Exact hits", len(hits))
        cards[1].metric("Degrees checked", len(checked))
        cards[2].metric("No-hit degrees", len(no_hit))
        cards[3].metric("Timed out", len(timed_out))

        st.caption(
            "Checked/no-hit means Sage completed that exact prime-degree test. "
            "Timed-out or errored degrees are inconclusive and are never treated as no-isogeny results."
        )
        status_rows = []
        for degree in result.get("requested_degrees") or []:
            degree = int(degree)
            if degree in hit_degrees:
                status = "exact hit"
            elif degree in no_hit:
                status = "exact no hit"
            elif degree in timed_out:
                status = "timeout / unknown"
            else:
                status = "error / unknown"
            status_rows.append({"degree": degree, "status": status})
        st.dataframe(status_rows, hide_index=True, width="stretch")

        if hits:
            stored_index = B.stored_model_index(records)
            rows = []
            for item in hits:
                matches = stored_index.get(
                    B.model_key(item.get("codomain_ainvs") or [])
                ) or []
                rows.append(
                    {
                        "degree": int(item["degree"]),
                        "retained codomain": (
                            ", ".join(f"#{r.curve_id}" for r in matches) or None
                        ),
                        "codomain a-invariants": (
                            "[" + ", ".join(item.get("codomain_ainvs") or []) + "]"
                        ),
                        "conductor": item.get("codomain_conductor"),
                        "discriminant": item.get("codomain_discriminant"),
                        "j-invariant": item.get("codomain_j_invariant"),
                    }
                )
            st.markdown("**Exact source isogenies found**")
            st.dataframe(rows, hide_index=True, width="stretch")

            hit_options = list(range(len(hits)))
            selected_hits = st.multiselect(
                "Select exact probe hits for research handoff",
                hit_options,
                default=hit_options[:1],
                format_func=lambda idx: (
                    f"ℓ={int(hits[int(idx)]['degree'])} · hit #{int(idx) + 1}"
                ),
                key="rh-neighborhood-probe-handoff-hits",
            )
            selected_items = []
            selected_retained = []
            for hit_index in selected_hits[:32]:
                hit = hits[int(hit_index)]
                matches = stored_index.get(
                    B.model_key(hit.get("codomain_ainvs") or [])
                ) or []
                selected_retained.extend(matches)
                selected_items.append(
                    {
                        "probe_hit_index": int(hit_index),
                        "degree": int(hit["degree"]),
                        "relation": "exact prime-degree source isogeny",
                        "a_invariants": list(hit.get("codomain_ainvs") or []),
                        "conductor": hit.get("codomain_conductor"),
                        "discriminant": hit.get("codomain_discriminant"),
                        "j_invariant": hit.get("codomain_j_invariant"),
                        "retained_curve_ids": sorted(
                            {int(record.curve_id) for record in matches}
                        ),
                    }
                )
            if selected_items:
                probe_handoff = _research_handoff(
                    source_record,
                    kind="isogeny_probe_selection",
                    items=selected_items,
                    retained_records=selected_retained,
                    extra={
                        "class_complete": False,
                        "requested_degrees": list(
                            result.get("requested_degrees") or []
                        ),
                        "checked_degrees": checked,
                        "timed_out_degrees": timed_out,
                        "per_degree_timeout": int(per_degree_timeout),
                    },
                )
                _render_pipeline_handoff(
                    probe_handoff,
                    key="rh-neighborhood-probe-selection",
                    title="Exact source-isogeny probe handoff",
                )

        if errors:
            st.caption(
                "Worker errors: "
                + "; ".join(
                    f"ℓ={item.get('degree')}: {item.get('detail')}"
                    for item in errors
                )
            )

def render(db, science_python, research_view):
    is_twists = research_view == "Quadratic twists"
    heading = "Quadratic twists" if is_twists else "Rational isogenies"
    detail = (
        "Explore bounded squarefree twists around a retained curve. Exact arithmetic "
        "comes from Sage; stored rank evidence is shown only for retained matches."
        if is_twists else
        "Explore the exact rational isogeny class of a retained curve, with "
        "bounded prime-degree probes when a full-class computation is too expensive."
    )
    st.html(
        "<div class='rh-neighborhood-hero'><div class='rh-neighborhood-eyebrow'>CORE RESEARCH TOOL</div>"
        f"<div class='rh-neighborhood-hero-title'>{html.escape(heading)}</div>"
        f"<div class='rh-neighborhood-hero-copy'>{html.escape(detail)}</div></div>"
    )

    records = B.list_stored_curves(db)
    if not records:
        st.info("No retained curves with stored a-invariants are available.")
        return

    by_id = {record.curve_id: record for record in records}
    active = get_active_curve_id(
        st.session_state,
        "curves_selected_id",
        "target_curve_id",
    )
    options = [record.curve_id for record in records]
    index = options.index(active) if active in by_id else 0
    source_key = "rh-neighborhood-source-curve"
    if active in by_id and active != st.session_state.get("_rh_neighborhood_active_curve_id"):
        st.session_state[source_key] = active
    _section_banner(
        "1",
        "Source curve",
        "Choose the retained Rank Hunter curve whose arithmetic neighborhood you want to inspect.",
    )
    selected_id = st.selectbox(
        "Source curve",
        options,
        index=index,
        format_func=lambda curve_id: _curve_label(by_id[int(curve_id)]),
        key=source_key,
    )
    set_active_curve_id(st.session_state, int(selected_id))
    st.session_state["_rh_neighborhood_active_curve_id"] = int(selected_id)
    source_record = by_id[int(selected_id)]
    _render_source_brief(source_record)

    science_python = str(science_python or "").strip()

    _render_mode_boundary(research_view)
    if research_view == "Quadratic twists":
        _render_twist_view(
            source_record,
            records,
            by_id,
            science_python,
        )
        return

    cache_key = "rh-neighborhood-cache::" + B.worker_cache_key(source_record)
    timeout_key = "rh-neighborhood-timeout::" + B.worker_cache_key(source_record)
    payload = st.session_state.get(cache_key)
    timed_out_message = st.session_state.get(timeout_key)

    controls = st.columns([0.24, 0.20, 0.56])
    with controls[0]:
        compute = st.button(
            "Compute full isogeny class",
            type="primary",
            use_container_width=True,
            key="rh-neighborhood-compute",
        )
    with controls[1]:
        full_timeout = st.selectbox(
            "Full-class timeout",
            [30, 90, 300, 900],
            index=1,
            key="rh-neighborhood-full-timeout",
            format_func=lambda value: f"Timeout · {value}s",
            label_visibility="collapsed",
        )
    with controls[2]:
        st.caption(
            "A timeout is inconclusive, not evidence of a trivial isogeny class. "
            "Large-coefficient curves can use the exact per-degree fallback below. "
            "Computed class data is session-cached only."
        )

    if compute:
        if not science_python:
            st.error("Scientific Python is not configured in Rank Hunter Settings.")
            return
        try:
            with st.spinner("Computing exact rational isogeny class…"):
                payload = B.run_isogeny_worker(
                    science_python=science_python,
                    ainvs=source_record.ainvs,
                    timeout=full_timeout,
                )
            st.session_state[cache_key] = payload
            st.session_state.pop(timeout_key, None)
            timed_out_message = None
        except B.WorkerTimeout as exc:
            timed_out_message = str(exc)
            st.session_state[timeout_key] = timed_out_message
        except Exception as exc:
            st.error(str(exc))
            return

    if timed_out_message:
        st.warning(
            timed_out_message
            + ". The complete class is still unknown; use the exact source probe "
            "to salvage independently completed prime-degree information."
        )

    _render_isogeny_probe(
        source_record,
        records,
        science_python,
        expanded=bool(timed_out_message),
    )

    if payload is None:
        st.info(
            "Compute the full rational isogeny class, or use the large-curve exact "
            "source probe for partial prime-degree information."
        )
        return

    stored_index = B.stored_model_index(records)
    node_count = len(payload.get("nodes") or [])
    edge_count = len(payload.get("edges") or [])
    stored_matches = sum(
        1
        for node in payload.get("nodes") or []
        if stored_index.get(B.model_key(node.get("ainvs") or []))
    )

    cards = st.columns(3)
    cards[0].metric("Isogeny-class curves", node_count)
    cards[1].metric("Direct prime-degree edges", edge_count)
    cards[2].metric("Exact stored-model matches", stored_matches)

    node_indices = [int(node["index"]) for node in payload.get("nodes") or []]
    _section_banner(
        "2",
        "Selected isogeny node",
        "Inspect one exact class node before reading the graph and complete class data.",
    )
    with st.container(border=True):
        st.markdown("**Inspect one isogeny-class node**")
        chosen = st.selectbox(
            "Isogeny node",
            node_indices,
            format_func=lambda idx: f"E{idx}",
            key="rh-neighborhood-node",
        )
        node = next(
            node for node in payload.get("nodes") or [] if int(node["index"]) == int(chosen)
        )

        exact_matches = stored_index.get(B.model_key(node.get("ainvs") or [])) or []
        source_index = int(payload.get("source_index") or 0)
        source_isomorphic = int(chosen) == source_index
        retained_matches = list(exact_matches)
        if source_isomorphic and all(
            record.curve_id != source_record.curve_id for record in retained_matches
        ):
            retained_matches.insert(0, source_record)

        current_relation = (
            "exact model + source Q-isomorphic"
            if source_isomorphic and exact_matches
            else "source Q-isomorphic"
            if source_isomorphic
            else "exact model"
            if exact_matches
            else "unstored isogeny-class node"
        )

        node_cols = st.columns(4)
        node_cols[0].metric("Node", f"E{int(chosen)}")
        node_cols[1].metric(
            "Root number",
            "—" if node.get("root_number") not in (-1, 1) else f"{int(node['root_number']):+d}",
        )
        node_cols[2].metric("Conductor", _compact_integer(node.get("conductor"))[0])
        node_cols[3].metric("Retained", "yes" if retained_matches else "no")
        st.caption(f"Relation to source/storage: {current_relation}")
        with st.expander("Exact node arithmetic", expanded=False):
            st.code(
                "a-invariants = [" + ", ".join(node.get("ainvs") or []) + "]\n"
                "conductor = " + str(node.get("conductor") or "—") + "\n"
                "discriminant = " + str(node.get("discriminant") or "—") + "\n"
                "j-invariant = " + str(node.get("j_invariant") or "—") + "\n"
                "torsion invariants = " + str(node.get("torsion_invariants") or []),
                language="text",
            )
        if source_isomorphic and not exact_matches:
            st.caption(
                f"E{source_index} is exactly Q-isomorphic to retained source "
                f"curve #{source_record.curve_id}. Its displayed Sage model differs from "
                "the stored coefficients, so the relationship is labeled source-isomorphic "
                "rather than an exact-model match."
            )

    current_handoff = _single_item_handoff(
        source_record,
        kind="isogeny_node",
        item=_isogeny_handoff_item(
            node,
            retained_matches,
            relation=current_relation,
        ),
        retained_records=retained_matches,
        extra={
            "source_index": source_index,
            "class_complete": True,
            "class_edges": list(payload.get("edges") or []),
        },
    )

    _section_banner(
        "3",
        "Isogeny neighborhood",
        "Read the exact rational-isogeny graph and complete class data after inspecting one node.",
    )
    st.markdown("**Rational isogeny graph**")
    st.caption(
        "Edge labels are prime isogeny degrees. The blue node is the Sage isogeny-class "
        "representative isomorphic to the selected source; green nodes have an exact "
        "stored-model coefficient match in Rank Hunter."
    )
    st.markdown(
        _graph_html(payload, stored_index, source_record),
        unsafe_allow_html=True,
    )

    with st.expander("Exact isogeny-class data", expanded=False):
        st.dataframe(
            _node_rows(payload, stored_index, source_record),
            hide_index=True,
            width="stretch",
        )
        if payload.get("edges"):
            st.markdown("**Prime-degree edges**")
            st.dataframe(payload["edges"], hide_index=True, width="stretch")

    selected_nodes = st.multiselect(
        "Select isogeny nodes for research handoff",
        node_indices,
        default=[chosen],
        format_func=lambda idx: f"E{idx}",
        key="rh-neighborhood-handoff-nodes",
    )
    if len(selected_nodes) > 32:
        st.warning("Isogeny research handoffs are capped at 32 explicit nodes.")
        selected_nodes = selected_nodes[:32]

    selected_items = []
    selected_retained = []
    selected_set = {int(idx) for idx in selected_nodes}
    for selected_node in payload.get("nodes") or []:
        selected_index = int(selected_node["index"])
        if selected_index not in selected_set:
            continue
        selected_exact = (
            stored_index.get(B.model_key(selected_node.get("ainvs") or [])) or []
        )
        selected_matches = list(selected_exact)
        selected_source = selected_index == source_index
        if selected_source and all(
            record.curve_id != source_record.curve_id
            for record in selected_matches
        ):
            selected_matches.insert(0, source_record)
        relation = (
            "exact model + source Q-isomorphic"
            if selected_source and selected_exact
            else "source Q-isomorphic"
            if selected_source
            else "exact model"
            if selected_exact
            else "unstored isogeny-class node"
        )
        selected_retained.extend(selected_matches)
        selected_items.append(
            _isogeny_handoff_item(
                selected_node,
                selected_matches,
                relation=relation,
            )
        )
    selected_items.sort(key=lambda rec: int(rec["node_index"]))

    if selected_items:
        isogeny_handoff = _research_handoff(
            source_record,
            kind="isogeny_class_selection",
            items=selected_items,
            retained_records=selected_retained,
            extra={
                "source_index": source_index,
                "class_complete": True,
                "class_edges": list(payload.get("edges") or []),
            },
        )
        _render_pipeline_handoff(
            isogeny_handoff,
            key="rh-neighborhood-node-selection",
            title="Isogeny-class selection handoff",
        )

    if retained_matches:
        match_ids = [record.curve_id for record in retained_matches]
        target_id = st.selectbox(
            "Retained curve match",
            match_ids,
            format_func=lambda curve_id: _curve_label(by_id[int(curve_id)]),
            key="rh-neighborhood-stored-match",
        )
        target_record = by_id[int(target_id)]
        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button(
                "Open stored curve",
                use_container_width=True,
                key="rh-neighborhood-open-stored",
            ):
                _open_curve(target_record, handoff=current_handoff)
        with action_cols[1]:
            if st.button(
                "Target Search",
                type="primary",
                use_container_width=True,
                key="rh-neighborhood-target-stored",
            ):
                route_curve_to_target(
                    st.session_state,
                    target_record.curve_id,
                    handoff=current_handoff,
                )
                st.rerun()
    else:
        st.caption(
            "No exact stored-model match for this node. Viewing an isogenous curve does not insert it into the authoritative Rank Hunter database."
        )


def twists_page(db, ctx):
    science_python = setting(db, "science_python", ctx.detected_science_python())
    render(db, science_python, "Quadratic twists")


def isogenies_page(db, ctx):
    science_python = setting(db, "science_python", ctx.detected_science_python())
    render(db, science_python, "Isogeny class")
