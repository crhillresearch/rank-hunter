from __future__ import annotations

import html
import math
from fractions import Fraction

import streamlit as st

from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_research_state import list_curve_research_states
from rank42.ui_components import region


def _positive_integer(value):
    if value in (None, ""):
        return None
    try:
        number = abs(int(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _coefficient_height(raw):
    if not raw:
        return None
    try:
        import json

        values = json.loads(raw)
    except Exception:
        return None
    heights = []
    for value in values or []:
        try:
            q = Fraction(str(value))
        except (ValueError, ZeroDivisionError):
            return None
        heights.append(max(abs(q.numerator), q.denominator))
    return max(heights) if heights else None


def _compact_integer(value):
    if value is None:
        return "—"
    number = int(value)
    text = f"{number:,}"
    if len(text) <= 14:
        return text
    digits = str(abs(number))
    mantissa = digits[0] + (f".{digits[1:4]}" if len(digits) > 1 else "")
    return f"{mantissa}e{len(digits) - 1}"


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


def _rank_evidence_rows(db):
    states = sorted(
        list_curve_research_states(db),
        key=lambda state: int(state["curve_id"]),
    )

    exact = {}
    rigorous = {}
    for state in states:
        if state["rank_inconsistent"]:
            continue
        exact_rank = state["exact_rank"]
        lower_rank = int(state["specialization_rigorous_lower"] or 0)
        example = state["curve"]

        if lower_rank > 0:
            bucket = rigorous.setdefault(lower_rank, {"count": 0, "example": None})
            bucket["count"] += 1
            if bucket["example"] is None:
                bucket["example"] = example

        if exact_rank is not None:
            exact_rank = int(exact_rank)
            bucket = exact.setdefault(exact_rank, {"count": 0, "example": None})
            bucket["count"] += 1
            if bucket["example"] is None:
                bucket["example"] = example

    ranks = sorted(set(exact) | set(rigorous))
    return [
        {
            "rank": rank,
            "exact": exact.get(rank),
            "rigorous": rigorous.get(rank),
        }
        for rank in ranks
    ]


def _rank_evidence_chart_html(rows):
    if not rows:
        return ""

    rank_min = int(rows[0]["rank"])
    rank_max = int(rows[-1]["rank"])
    rank_span = max(1, rank_max - rank_min)
    label_every = 1 if len(rows) <= 12 else max(2, math.ceil(len(rows) / 10))

    columns = []
    for index, row in enumerate(rows):
        rank = int(row["rank"])
        rise = 10 + round(35 * (rank - rank_min) / rank_span)
        dots = []

        rigorous = row["rigorous"]
        if rigorous:
            example = rigorous["example"]
            title = html.escape(
                f"Rigorous lower bound ≥ {rank} · {rigorous['count']:,} curve(s) · "
                f"example #{int(example['id'])} · {example['family']} · parameter {example['parameter']}",
                quote=True,
            )
            dots.append(
                f'<i class="rh-rank-evidence-dot rh-rank-evidence-rigorous" '
                f'style="bottom:{rise}px" title="{title}"></i>'
            )

        exact = row["exact"]
        if exact:
            example = exact["example"]
            title = html.escape(
                f"Exact rank {rank} · {exact['count']:,} curve(s) · "
                f"example #{int(example['id'])} · {example['family']} · parameter {example['parameter']}",
                quote=True,
            )
            dots.append(
                f'<i class="rh-rank-evidence-dot rh-rank-evidence-exact" '
                f'style="bottom:{rise + 17}px" title="{title}"></i>'
            )

        label = str(rank) if index in (0, len(rows) - 1) or index % label_every == 0 else ""
        columns.append(
            '<span class="rh-rank-evidence-column">'
            f'<span class="rh-rank-evidence-lane">{"".join(dots)}</span>'
            f'<small>{label}</small>'
            '</span>'
        )

    return (
        '<div class="rh-rank-evidence-chart-shell">'
        f'<div class="rh-rank-evidence-track" style="--rh-rank-columns:{len(rows)}" '
        'role="img" aria-label="Compact exact and rigorous rank evidence chart">'
        f'{"".join(columns)}</div>'
        '<div class="rh-rank-evidence-legend">'
        '<span><i class="rh-rank-evidence-legend-exact"></i>Exact rank</span>'
        '<span><i class="rh-rank-evidence-legend-rigorous"></i>Rigorous lower bound</span>'
        '</div></div>'
    )


def render_conductor_frontier(db):
    rows = _rank_evidence_rows(db)
    with region("dashboard-chart-conductor-records", border=True):
        st.html(
            '<div class="rh-dashboard-chart-title">'
            '<strong>Rigorous Rank Evidence</strong>'
            '<span>Compact ICARM-style view of exact ranks and rigorous specialization lower bounds</span>'
            '</div>'
        )
        if rows:
            st.html(_rank_evidence_chart_html(rows))
        else:
            st.caption("No exact or rigorous specialization-rank evidence is stored yet.")


def _record_complexity_rows(db):
    states = [
        state
        for state in list_curve_research_states(db)
        if state["exact_rank"] is not None and not state["rank_inconsistent"]
    ]
    states.sort(key=lambda state: (-int(state["exact_rank"]), int(state["curve_id"])))
    arithmetic = curve_arithmetic_state_map(
        db,
        [int(state["curve_id"]) for state in states],
    )

    by_rank = {}
    for state in states:
        row = dict(state["curve"])
        row["exact_rank"] = int(state["exact_rank"])
        row["_arithmetic"] = arithmetic[int(state["curve_id"])]
        by_rank.setdefault(int(state["exact_rank"]), []).append(row)

    records = []
    metrics = (
        (
            "Smallest conductor",
            lambda row: _positive_integer(row["_arithmetic"]["conductor"]),
            lambda row: _arithmetic_source_label(row["_arithmetic"], "conductor"),
        ),
        (
            "Smallest |Δ|",
            lambda row: _positive_integer(row["_arithmetic"]["discriminant"]),
            lambda row: _arithmetic_source_label(row["_arithmetic"], "discriminant"),
        ),
        (
            "Smallest coefficient height",
            lambda row: _coefficient_height(row["a_invariants_json"]),
            lambda row: "local model",
        ),
    )
    for rank in sorted(by_rank, reverse=True):
        bucket = by_rank[rank]
        for order, (label, getter, source_getter) in enumerate(metrics):
            candidates = []
            for row in bucket:
                value = getter(row)
                if value is not None:
                    candidates.append((value, int(row["id"]), row))
            if not candidates:
                continue
            value, _, row = min(candidates, key=lambda item: (item[0], item[1]))
            records.append(
                {
                    "rank": rank,
                    "order": order,
                    "record": label,
                    "value": value,
                    "source": source_getter(row),
                    "row": row,
                }
            )
    records.sort(key=lambda rec: (-rec["rank"], rec["order"], int(rec["row"]["id"])))
    return records


def render_record_complexity(db):
    records = _record_complexity_rows(db)
    with region("dashboard-record-complexity", border=True):
        st.html('<div class="rh-dashboard-research-heading"><span class="rh-dashboard-eyebrow">RECORD BY COMPLEXITY</span></div>')
        if not records:
            st.caption("No exact-rank curves with authoritative arithmetic-complexity data are stored yet.")
            return
        body = []
        for rec in records:
            row = rec["row"]
            curve = f'<strong>#{int(row["id"])}</strong><small>{html.escape(str(row["family"]))}</small>'
            body.append(
                "<tr>"
                f'<td class="rh-num">{rec["rank"]}</td>'
                f'<td>{html.escape(rec["record"])}</td>'
                f'<td class="rh-num" title="{html.escape(str(rec["value"]), quote=True)}">{html.escape(_compact_integer(rec["value"]))}</td>'
                f'<td>{html.escape(str(rec["source"]))}</td>'
                f'<td class="rh-curve-cell" title="parameter {html.escape(str(row["parameter"]), quote=True)}">{curve}</td>'
                "</tr>"
            )
        st.html(
            '<div class="rh-dashboard-table-wrap rh-dashboard-table-scroll"><table class="rh-dashboard-data-table">'
            '<thead><tr><th>Rank</th><th>Record</th><th>Value</th><th>Source</th><th>Curve</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )