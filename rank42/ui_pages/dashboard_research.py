from __future__ import annotations

import html
import json
from fractions import Fraction

import streamlit as st

from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_research_state import list_curve_research_states
from rank42.ui_components import region


def _int_value(value, *, absolute=False):
    if value in (None, ""):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return abs(number) if absolute else number


def _coefficient_height(raw):
    if not raw:
        return None
    try:
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
    sign = "−" if number < 0 else ""
    digits = str(abs(number))
    mantissa = digits[0] + (f".{digits[1:4]}" if len(digits) > 1 else "")
    return f"{sign}{mantissa}e{len(digits) - 1}"


def _compact_text(value, *, limit=14):
    if value in (None, ""):
        return "—"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _heading(title, subtitle):
    return (
        '<div class="rh-dashboard-research-heading">'
        f'<span class="rh-dashboard-eyebrow">{html.escape(title)}</span>'
        f'<small>{html.escape(subtitle)}</small>'
        '</div>'
    )


def _cell(display, *, title=None, classes=""):
    title_attr = f' title="{html.escape(str(title), quote=True)}"' if title not in (None, "") else ""
    class_attr = f' class="{html.escape(classes, quote=True)}"' if classes else ""
    return f'<td{class_attr}{title_attr}>{display}</td>'


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


def _arithmetic_record_rows(db):
    states = [
        state
        for state in list_curve_research_states(db)
        if state["exact_rank"] is not None and not state["rank_inconsistent"]
    ]
    arithmetic = curve_arithmetic_state_map(
        db,
        [int(state["curve_id"]) for state in states],
    )
    by_rank = {}
    for state in states:
        by_rank.setdefault(int(state["exact_rank"]), []).append(state)

    records = []
    for rank in sorted(by_rank, reverse=True)[:8]:
        bucket = by_rank[rank]

        conductor_pairs = []
        discriminant_pairs = []
        height_pairs = []
        for state in bucket:
            curve_id = int(state["curve_id"])
            row = state["curve"]
            arithmetic_state = arithmetic[curve_id]
            conductor = _int_value(arithmetic_state["conductor"], absolute=True)
            discriminant = _int_value(arithmetic_state["discriminant"], absolute=True)
            height = _coefficient_height(row["a_invariants_json"])
            if conductor is not None:
                conductor_pairs.append((conductor, state))
            if discriminant is not None:
                discriminant_pairs.append((discriminant, state))
            if height is not None:
                height_pairs.append((height, state))

        conductor_pick = min(
            conductor_pairs,
            key=lambda item: (item[0], int(item[1]["curve_id"])),
            default=None,
        )
        discriminant_pick = min(
            discriminant_pairs,
            key=lambda item: (item[0], int(item[1]["curve_id"])),
            default=None,
        )
        height_pick = min(
            height_pairs,
            key=lambda item: (item[0], int(item[1]["curve_id"])),
            default=None,
        )

        if conductor_pick is not None:
            representative = conductor_pick[1]
        elif discriminant_pick is not None:
            representative = discriminant_pick[1]
        elif height_pick is not None:
            representative = height_pick[1]
        else:
            representative = min(bucket, key=lambda state: int(state["curve_id"]))

        conductor_source = (
            _arithmetic_source_label(
                arithmetic[int(conductor_pick[1]["curve_id"])],
                "conductor",
            )
            if conductor_pick is not None
            else "—"
        )
        discriminant_source = (
            _arithmetic_source_label(
                arithmetic[int(discriminant_pick[1]["curve_id"])],
                "discriminant",
            )
            if discriminant_pick is not None
            else "—"
        )
        records.append(
            {
                "rank": rank,
                "min_conductor": None if conductor_pick is None else conductor_pick[0],
                "conductor_source": conductor_source,
                "min_discriminant": None if discriminant_pick is None else discriminant_pick[0],
                "discriminant_source": discriminant_source,
                "min_height": None if height_pick is None else height_pick[0],
                "representative": representative["curve"],
            }
        )
    return records


def _exceptional_arithmetic_rows(db, *, high_rank_floor, best_exact):
    states = [
        state
        for state in list_curve_research_states(db)
        if not state["rank_inconsistent"]
        and int(state["specialization_rigorous_lower"] or 0) > 0
    ]
    arithmetic = curve_arithmetic_state_map(
        db,
        [int(state["curve_id"]) for state in states],
    )
    point_counts = {
        int(row["curve_id"]): int(row["independent_points"] or 0)
        for row in db.execute(
            """SELECT curve_id,
                      SUM(CASE WHEN rigorous_independent=1 THEN 1 ELSE 0 END) AS independent_points
               FROM points
               GROUP BY curve_id"""
        ).fetchall()
    }

    record_conductor = {}
    record_discriminant = {}
    for state in states:
        if state["exact_rank"] is None:
            continue
        rank = int(state["exact_rank"])
        arithmetic_state = arithmetic[int(state["curve_id"])]
        conductor = _int_value(arithmetic_state["conductor"], absolute=True)
        discriminant = _int_value(arithmetic_state["discriminant"], absolute=True)
        if conductor is not None:
            record_conductor[rank] = min(record_conductor.get(rank, conductor), conductor)
        if discriminant is not None:
            record_discriminant[rank] = min(record_discriminant.get(rank, discriminant), discriminant)

    data = []
    for state in states:
        row = state["curve"]
        curve_id = int(state["curve_id"])
        arithmetic_state = arithmetic[curve_id]
        lower = int(state["specialization_rigorous_lower"] or 0)
        exact_rank = state["exact_rank"]
        exact = exact_rank is not None
        exact_rank = int(exact_rank) if exact else None
        upper = state["rigorous_upper"]
        upper = int(upper) if upper is not None else None
        gap = max(0, upper - lower) if upper is not None else 0
        conductor = _int_value(arithmetic_state["conductor"], absolute=True)
        discriminant = _int_value(arithmetic_state["discriminant"], absolute=True)

        flags = []
        if exact and best_exact is not None and exact_rank == int(best_exact):
            flags.append("Best exact")
        if not exact and lower >= int(high_rank_floor):
            flags.append("High-rank lower")
        if gap > 0:
            flags.append(f"Open interval +{gap}")
        if exact and conductor is not None and conductor == record_conductor.get(exact_rank):
            flags.append("Conductor record")
        if exact and discriminant is not None and discriminant == record_discriminant.get(exact_rank):
            flags.append("Δ record")

        data.append(
            {
                "row": row,
                "lower": lower,
                "exact": exact,
                "exact_rank": exact_rank,
                "upper": upper,
                "gap": gap,
                "root_number": arithmetic_state["root_number"],
                "conductor": conductor,
                "conductor_source": _arithmetic_source_label(arithmetic_state, "conductor"),
                "discriminant": discriminant,
                "discriminant_source": _arithmetic_source_label(arithmetic_state, "discriminant"),
                "independent_points": point_counts.get(curve_id, 0),
                "flags": flags,
            }
        )

    data.sort(
        key=lambda rec: (
            rec["lower"],
            1 if rec["exact"] else 0,
            rec["gap"],
            int(rec["independent_points"]),
            int(rec["row"]["id"]),
        ),
        reverse=True,
    )
    interesting = [rec for rec in data if rec["flags"]]
    seen = {int(rec["row"]["id"]) for rec in interesting}
    for rec in data:
        if len(interesting) >= 8:
            break
        if int(rec["row"]["id"]) not in seen:
            interesting.append(rec)
            seen.add(int(rec["row"]["id"]))
    return interesting[:8]


def _render_arithmetic_records(db):
    rows = _arithmetic_record_rows(db)

    with region("dashboard-arithmetic-records", border=True):
        st.html(
            _heading(
                "ARITHMETIC RECORDS",
                "Authority-selected extrema by exact specialization rank; external-reference fallback is labeled explicitly.",
            )
        )
        if not rows:
            st.caption("No exact specialization ranks are stored yet.")
            return

        body = []
        for rec in rows:
            representative = rec["representative"]
            rep_text = f'#{int(representative["id"])} · {html.escape(str(representative["family"]))}'
            rep_title = f"parameter {representative['parameter']}"
            body.append(
                "<tr>"
                + _cell(str(rec["rank"]), classes="rh-num")
                + _cell(html.escape(_compact_integer(rec["min_conductor"])), title=rec["min_conductor"], classes="rh-num")
                + _cell(html.escape(rec["conductor_source"]))
                + _cell(html.escape(_compact_integer(rec["min_discriminant"])), title=rec["min_discriminant"], classes="rh-num")
                + _cell(html.escape(rec["discriminant_source"]))
                + _cell(html.escape(_compact_integer(rec["min_height"])), title=rec["min_height"], classes="rh-num")
                + _cell(rep_text, title=rep_title)
                + "</tr>"
            )

        st.html(
            '<div class="rh-dashboard-table-wrap"><table class="rh-dashboard-data-table">'
            '<thead><tr><th>Exact rank</th><th>Smallest conductor</th><th>N source</th>'
            '<th>Smallest |Δ|</th><th>Δ source</th><th>Smallest coeff. height</th>'
            '<th>Representative curve</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )


def _render_exceptional_arithmetic(db, *, high_rank_floor, best_exact):
    rows = _exceptional_arithmetic_rows(
        db,
        high_rank_floor=high_rank_floor,
        best_exact=best_exact,
    )

    with region("dashboard-exceptional-arithmetic", border=True):
        st.html(
            _heading(
                "EXCEPTIONAL ARITHMETIC",
                "High rigorous-rank examples, record-size invariants, and unresolved rigorous intervals from authoritative research/arithmetic state.",
            )
        )
        if not rows:
            st.caption("No rigorous specialization-rank evidence is stored yet.")
            return

        body = []
        for rec in rows:
            row = rec["row"]
            evidence = f"= {rec['exact_rank']}" if rec["exact"] else f"≥ {rec['lower']}"
            root_number = rec["root_number"]
            root = "—" if root_number is None else ("+1" if int(root_number) > 0 else "−1")
            upper = "—" if rec["upper"] is None else str(rec["upper"])
            regulator = _compact_text(row["regulator"], limit=12)
            conductor = _compact_integer(rec["conductor"])
            flag = " · ".join(rec["flags"][:2]) or "High rigorous rank"
            curve_display = (
                f'<strong>#{int(row["id"])}</strong><small>{html.escape(str(row["family"]))}</small>'
            )
            body.append(
                "<tr>"
                + _cell(curve_display, title=f"parameter {row['parameter']}", classes="rh-curve-cell")
                + _cell(html.escape(evidence), classes="rh-num")
                + _cell(root, classes="rh-num")
                + _cell(html.escape(upper), classes="rh-num")
                + _cell(f"{int(rec['independent_points']):,}", classes="rh-num")
                + _cell(html.escape(regulator), title=row["regulator"], classes="rh-num")
                + _cell(html.escape(conductor), title=rec["conductor"], classes="rh-num")
                + _cell(html.escape(rec["conductor_source"]))
                + _cell(html.escape(flag))
                + "</tr>"
            )

        st.html(
            '<div class="rh-dashboard-table-wrap"><table class="rh-dashboard-data-table rh-dashboard-exceptional-table">'
            '<thead><tr><th>Curve</th><th>Evidence</th><th>Root #</th><th>Rigorous upper</th>'
            '<th>Independent pts</th><th>Regulator</th><th>Conductor</th><th>N source</th><th>Flag</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )


def render_post_chart_sections(db, *, high_rank_floor, best_exact):
    _render_arithmetic_records(db)
    _render_exceptional_arithmetic(db, high_rank_floor=high_rank_floor, best_exact=best_exact)
