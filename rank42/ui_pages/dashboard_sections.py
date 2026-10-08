from __future__ import annotations

import html

import streamlit as st

from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_research_state import curve_research_state_map, list_curve_research_states
from rank42.torsion import MAZUR_TORSION_CHOICES
from rank42.ui_components import region
from .dashboard_complexity import render_record_complexity
from .dashboard_research import _render_exceptional_arithmetic


def _heading(title):
    return (
        '<div class="rh-dashboard-section-heading">'
        f'<span class="rh-dashboard-section-title">{html.escape(title)}</span></div>'
    )


def _cell(display, *, title=None, classes=""):
    title_attr = f' title="{html.escape(str(title), quote=True)}"' if title not in (None, "") else ""
    class_attr = f' class="{html.escape(classes, quote=True)}"' if classes else ""
    return f'<td{class_attr}{title_attr}>{display}</td>'


def _compact_text(value, limit=16):
    if value in (None, ""):
        return "—"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


_TORSION_ORDER = {
    label: index
    for index, label in enumerate(MAZUR_TORSION_CHOICES)
}


def _torsion_sort_key(label, order=0):
    return (
        _TORSION_ORDER.get(str(label), len(_TORSION_ORDER)),
        int(order or 0),
        str(label),
    )


def _torsion_research_data(db):
    """Project persisted torsion + authoritative rank state once for Dashboard."""
    states = list_curve_research_states(db)
    curve_ids = [int(state["curve_id"]) for state in states]
    arithmetic = curve_arithmetic_state_map(db, curve_ids)

    exact_counts = {}
    records = {}
    distribution = {}
    failure_rows = []
    known = 0
    timeouts = 0

    for state in states:
        curve_id = int(state["curve_id"])
        arithmetic_state = arithmetic[curve_id]
        curve = dict(arithmetic_state.get("curve") or state.get("curve") or {})
        error = str(curve.get("torsion_error") or "").strip()
        if error:
            failure_rows.append(
                {
                    "curve_id": curve_id,
                    "family": state["family"],
                    "parameter": state["parameter"],
                    "error": error,
                    "timeout": "timeout" in error.lower(),
                }
            )
            if "timeout" in error.lower():
                timeouts += 1

        torsion = arithmetic_state["torsion"]
        if not isinstance(torsion, dict) or torsion.get("label") is None:
            continue

        known += 1
        label = str(torsion["label"])
        order = int(torsion.get("order") or 0)
        key = (label, order)

        rec = records.setdefault(
            key,
            {
                "torsion_label": label,
                "torsion_order": order,
                "retained_curves": 0,
                "best_exact": None,
                "best_lower": 0,
                "best_lower_only": 0,
                "best_lower_only_curve_id": None,
                "rank_conflicts": 0,
            },
        )
        dist = distribution.setdefault(
            key,
            {
                "torsion_label": label,
                "torsion_order": order,
                "curve_count": 0,
                "exact_count": 0,
                "lower_only_count": 0,
                "rank_conflicts": 0,
            },
        )

        rec["retained_curves"] += 1
        dist["curve_count"] += 1

        if state["rank_inconsistent"]:
            rec["rank_conflicts"] += 1
            dist["rank_conflicts"] += 1
            continue

        lower = int(state["rigorous_lower"] or 0)
        exact = state["exact_rank"]
        rec["best_lower"] = max(int(rec["best_lower"] or 0), lower)

        if exact is not None:
            exact = int(exact)
            rec["best_exact"] = (
                exact
                if rec["best_exact"] is None
                else max(int(rec["best_exact"]), exact)
            )
            dist["exact_count"] += 1
            exact_counts[(label, order, exact)] = (
                exact_counts.get((label, order, exact), 0) + 1
            )
        elif lower > 0:
            dist["lower_only_count"] += 1
            incumbent = int(rec["best_lower_only"] or 0)
            incumbent_curve = rec["best_lower_only_curve_id"]
            if lower > incumbent or (
                lower == incumbent
                and (
                    incumbent_curve is None
                    or curve_id > int(incumbent_curve)
                )
            ):
                rec["best_lower_only"] = lower
                rec["best_lower_only_curve_id"] = curve_id

    exact_rows = [
        {
            "torsion_label": label,
            "torsion_order": order,
            "exact_rank": exact,
            "curve_count": count,
        }
        for (label, order, exact), count in sorted(
            exact_counts.items(),
            key=lambda item: (
                *_torsion_sort_key(item[0][0], item[0][1]),
                item[0][2],
            ),
        )
    ]
    record_rows = [
        records[key]
        for key in sorted(
            records,
            key=lambda item: _torsion_sort_key(item[0], item[1]),
        )
    ]
    distribution_rows = [
        distribution[key]
        for key in sorted(
            distribution,
            key=lambda item: _torsion_sort_key(item[0], item[1]),
        )
    ]

    total = len(states)
    missing = max(0, total - known)
    overview = {
        "curve_count": total,
        "known_torsion": known,
        "coverage_pct": 0.0 if total == 0 else 100.0 * known / total,
        "distinct_groups": len(distribution_rows),
        "missing_torsion": missing,
        "torsion_errors": len(failure_rows),
        "torsion_timeouts": timeouts,
        "rank_conflicts": sum(
            int(row["rank_conflicts"] or 0)
            for row in record_rows
        ),
    }

    rare_groups = [
        row for row in distribution_rows
        if int(row["curve_count"] or 0) <= 2
    ]
    lower_only_leaders = sorted(
        [
            row for row in record_rows
            if int(row["best_lower_only"] or 0) > 0
        ],
        key=lambda row: (
            -int(row["best_lower_only"] or 0),
            _torsion_sort_key(
                row["torsion_label"],
                row["torsion_order"],
            ),
        ),
    )
    conflict_groups = [
        row for row in record_rows
        if int(row["rank_conflicts"] or 0) > 0
    ]

    return {
        "exact_rows": exact_rows,
        "record_rows": record_rows,
        "distribution_rows": distribution_rows,
        "overview": overview,
        "signals": {
            "rare_groups": rare_groups,
            "lower_only_leaders": lower_only_leaders,
            "conflict_groups": conflict_groups,
            "failure_rows": failure_rows,
        },
    }


def _torsion_data(db):
    data = _torsion_research_data(db)
    return data["exact_rows"], data["record_rows"]


def _render_torsion_overview(data):
    overview = data["overview"]
    with region("dashboard-torsion-overview", border=True):
        st.html(_heading("TORSION RESEARCH"))
        st.html(
            '<div class="rh-dashboard-pipeline-stats rh-dashboard-torsion-overview-stats">'
            f'<div><span>Known torsion</span><strong>{int(overview["known_torsion"]):,}</strong></div>'
            f'<div><span>Coverage</span><strong>{float(overview["coverage_pct"]):.1f}%</strong></div>'
            f'<div><span>Mazur groups</span><strong>{int(overview["distinct_groups"]):,}</strong></div>'
            f'<div><span>Missing</span><strong>{int(overview["missing_torsion"]):,}</strong></div>'
            f'<div><span>Compute issues</span><strong>{int(overview["torsion_errors"]):,}</strong></div>'
            f'<div><span>Rank conflicts</span><strong>{int(overview["rank_conflicts"]):,}</strong></div>'
            '</div>'
        )
        timeout_count = int(overview["torsion_timeouts"] or 0)
        issue_suffix = (
            f" · {timeout_count:,} timeout{'s' if timeout_count != 1 else ''}"
            if timeout_count
            else ""
        )
        st.caption(
            "Local persisted rational torsion only · "
            f'{int(overview["curve_count"]):,} local curves total{issue_suffix}. '
            "No torsion is computed while rendering Dashboard."
        )


def _render_torsion_distribution(data):
    rows = data["distribution_rows"]
    with region("dashboard-torsion-distribution", border=True, height="stretch"):
        st.html(_heading("TORSION DISTRIBUTION"))
        if not rows:
            st.caption(
                "No local curves have persisted rational torsion yet. "
                "Run the arithmetic/torsion backfill from Curves to populate this view."
            )
            return

        chart_rows = []
        for row in rows:
            total = int(row["curve_count"] or 0)
            exact = int(row["exact_count"] or 0)
            lower = int(row["lower_only_count"] or 0)
            conflicts = int(row["rank_conflicts"] or 0)
            other = max(0, total - exact - lower - conflicts)
            denom = max(total, 1)

            segments = []
            for cls, value, label in (
                ("exact", exact, "Exact rank"),
                ("lower", lower, "Lower-bound only"),
                ("conflict", conflicts, "Rank conflict"),
                ("other", other, "Other / no retained rank evidence"),
            ):
                if value <= 0:
                    continue
                pct = 100.0 * value / denom
                segments.append(
                    f'<i class="rh-torsion-dist-segment rh-torsion-dist-{cls}" '
                    f'style="width:{pct:.4f}%" '
                    f'title="{html.escape(label, quote=True)}: {value:,}"></i>'
                )

            chart_rows.append(
                '<div class="rh-torsion-dist-row">'
                f'<div class="rh-torsion-dist-label"><strong>{html.escape(str(row["torsion_label"]))}</strong>'
                f'<span>{total:,}</span></div>'
                f'<div class="rh-torsion-dist-track">{"".join(segments)}</div>'
                '</div>'
            )

        st.html(
            '<div class="rh-torsion-distribution-chart" '
            'role="img" aria-label="Local torsion distribution by rank evidence">'
            f'{"".join(chart_rows)}'
            '<div class="rh-torsion-dist-legend">'
            '<span><i class="rh-torsion-dist-exact"></i>Exact rank</span>'
            '<span><i class="rh-torsion-dist-lower"></i>Lower-bound only</span>'
            '<span><i class="rh-torsion-dist-conflict"></i>Rank conflict</span>'
            '<span><i class="rh-torsion-dist-other"></i>Other</span>'
            '</div></div>'
        )
        st.caption(
            "Bar length is local curve count for each torsion group. "
            "Segments use authoritative reduced rank state; unknown torsion is "
            "reported in Coverage rather than this chart."
        )



def _render_torsion_rank(db, *, exact_rows=None, record_rows=None):
    if exact_rows is None or record_rows is None:
        exact_rows, record_rows = _torsion_data(db)

    with region("dashboard-torsion-rank", border=True, height="stretch"):
        st.html(_heading("TORSION × EXACT RANK"))
        if not record_rows:
            st.caption(
                "No local curves have persisted rational torsion yet. "
                "Run the arithmetic/torsion backfill from Curves to populate this view."
            )
            return

        ranks = sorted({int(row["exact_rank"]) for row in exact_rows})
        structures = [
            (str(row["torsion_label"]), int(row["torsion_order"] or 0))
            for row in record_rows
        ]
        counts = {
            (str(row["torsion_label"]), int(row["exact_rank"])): int(
                row["curve_count"] or 0
            )
            for row in exact_rows
        }
        if not ranks:
            st.caption(
                "Torsion is stored, but no exact-rank curves are available "
                "for the matrix yet."
            )
            return

        head = "".join(f"<th>r={rank}</th>" for rank in ranks)
        body = []
        for label, _order in structures:
            cells = "".join(
                _cell(
                    str(counts.get((label, rank), 0)),
                    classes="rh-num",
                )
                for rank in ranks
            )
            body.append(
                f"<tr><td><strong>{html.escape(label)}</strong></td>{cells}</tr>"
            )
        st.html(
            '<div class="rh-dashboard-table-wrap rh-dashboard-table-scroll">'
            '<table class="rh-dashboard-data-table rh-torsion-rank-table">'
            f'<thead><tr><th>Torsion</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )
        st.caption(
            "Matrix counts exact specialization ranks only; lower-bound-only "
            "curves never enter these cells."
        )


def _render_torsion_records(db, *, record_rows=None):
    if record_rows is None:
        _exact_rows, record_rows = _torsion_data(db)

    with region("dashboard-torsion-records", border=True, height="stretch"):
        st.html(_heading("TORSION RECORDS"))
        if not record_rows:
            st.caption(
                "No local curves have persisted rational torsion yet. "
                "Run the arithmetic/torsion backfill from Curves to populate this view."
            )
            return

        rows_html = []
        for row in record_rows:
            best_exact = (
                "—"
                if row["best_exact"] is None
                else str(int(row["best_exact"]))
            )
            best_lower_value = int(row["best_lower"] or 0)
            best_lower = (
                "—" if best_lower_value <= 0 else f"≥ {best_lower_value}"
            )
            lower_only_value = int(row.get("best_lower_only") or 0)
            best_lower_only = (
                "—" if lower_only_value <= 0 else f"≥ {lower_only_value}"
            )
            rows_html.append(
                "<tr>"
                + _cell(
                    f"<strong>{html.escape(str(row['torsion_label']))}</strong>"
                )
                + _cell(best_exact, classes="rh-num")
                + _cell(best_lower, classes="rh-num")
                + _cell(best_lower_only, classes="rh-num")
                + _cell(
                    f"{int(row['retained_curves'] or 0):,}",
                    classes="rh-num",
                )
                + _cell(
                    f"{int(row['rank_conflicts'] or 0):,}",
                    classes="rh-num",
                )
                + "</tr>"
            )
        st.html(
            '<div class="rh-dashboard-table-wrap rh-dashboard-table-scroll">'
            '<table class="rh-dashboard-data-table rh-torsion-records-table">'
            '<thead><tr><th>Torsion</th><th>Best exact</th>'
            '<th>Best rigorous ≥</th><th>Best LB-only ≥</th>'
            '<th>Known curves</th><th>Rank conflicts</th></tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div>'
        )


def _render_torsion_signals(data):
    signals = data["signals"]
    with region("dashboard-torsion-signals", border=True, height="stretch"):
        st.html(_heading("TORSION FOLLOW-UP"))

        rows = []
        for rec in signals["conflict_groups"]:
            rows.append(
                {
                    "priority": 0,
                    "signal": "Rank conflict",
                    "subject": str(rec["torsion_label"]),
                    "detail": (
                        f'{int(rec["rank_conflicts"]):,} curve'
                        f'{"s" if int(rec["rank_conflicts"]) != 1 else ""} '
                        "with inconsistent rigorous rank bounds"
                    ),
                }
            )

        for rec in signals["failure_rows"][:5]:
            rows.append(
                {
                    "priority": 1,
                    "signal": (
                        "Torsion timeout"
                        if rec["timeout"]
                        else "Torsion compute issue"
                    ),
                    "subject": f'Curve #{int(rec["curve_id"])}',
                    "detail": (
                        f'{rec["family"]} · parameter {rec["parameter"]} · '
                        f'{_compact_text(rec["error"], 72)}'
                    ),
                }
            )

        rare = list(signals["rare_groups"])
        if rare:
            rare_text = " · ".join(
                f'{row["torsion_label"]} ({int(row["curve_count"])})'
                for row in rare[:8]
            )
            if len(rare) > 8:
                rare_text += f" · +{len(rare) - 8} more"
            rows.append(
                {
                    "priority": 2,
                    "signal": "Rare local groups",
                    "subject": f"{len(rare)} group{'s' if len(rare) != 1 else ''}",
                    "detail": rare_text,
                }
            )

        for rec in signals["lower_only_leaders"][:5]:
            rows.append(
                {
                    "priority": 3,
                    "signal": "Strong LB-only",
                    "subject": str(rec["torsion_label"]),
                    "detail": (
                        f'Curve #{int(rec["best_lower_only_curve_id"])} '
                        f'has rigorous lower ≥{int(rec["best_lower_only"])} '
                        "without an exact rank"
                    ),
                }
            )

        if not rows:
            st.caption(
                "No torsion/rank follow-up signals are present in persisted state."
            )
            return

        rows.sort(key=lambda row: (row["priority"], row["signal"], row["subject"]))
        body = "".join(
            "<tr>"
            + _cell(f"<strong>{html.escape(row['signal'])}</strong>")
            + _cell(html.escape(row["subject"]))
            + _cell(html.escape(row["detail"]))
            + "</tr>"
            for row in rows[:14]
        )
        st.html(
            '<div class="rh-dashboard-table-wrap rh-dashboard-table-scroll">'
            '<table class="rh-dashboard-data-table rh-torsion-signals-table">'
            '<thead><tr><th>Signal</th><th>Torsion / curve</th><th>Why inspect</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>'
        )
        st.caption(
            "Follow-up signals are descriptive only; they do not promote "
            "rank or torsion evidence."
        )



def _unresolved_data(db):
    states = [
        state
        for state in list_curve_research_states(db)
        if not state["rank_inconsistent"]
        and state["exact_rank"] is None
        and int(state["rigorous_lower"] or 0) > 0
    ]
    arithmetic = curve_arithmetic_state_map(
        db,
        [int(state["curve_id"]) for state in states],
    )
    data = []
    for state in states:
        lower = int(state["rigorous_lower"] or 0)
        upper = state["rigorous_upper"]
        upper = int(upper) if upper is not None else None
        if upper is not None and upper <= lower:
            continue
        gap = None if upper is None else upper - lower
        curve_id = int(state["curve_id"])
        arithmetic_state = arithmetic[curve_id]
        data.append(
            {
                "curve_id": curve_id,
                "family": state["family"],
                "parameter": state["parameter"],
                "lower": lower,
                "upper": upper,
                "gap": gap,
                "conductor": arithmetic_state["conductor"],
                "conductor_source": _arithmetic_source_label(arithmetic_state, "conductor"),
            }
        )
    data.sort(
        key=lambda rec: (
            rec["gap"] if rec["gap"] is not None else 10**9,
            rec["lower"],
            rec["curve_id"],
        ),
        reverse=True,
    )
    return data


def _render_unresolved(db):
    data = _unresolved_data(db)

    with region("dashboard-unresolved", border=True):
        st.html(_heading("UNRESOLVED"))
        if not data:
            st.caption("No unresolved rigorous specialization intervals are stored.")
            return
        body = []
        for rec in data[:25]:
            curve = f'<strong>#{rec["curve_id"]}</strong><small>{html.escape(str(rec["family"]))}</small>'
            upper = "—" if rec["upper"] is None else str(rec["upper"])
            conductor = "—" if rec["conductor"] is None else html.escape(str(rec["conductor"]))
            body.append(
                "<tr>"
                + _cell(curve, title=f"parameter {rec['parameter']}", classes="rh-curve-cell")
                + _cell(f"≥ {rec['lower']}", classes="rh-num")
                + _cell(upper, classes="rh-num")
                + _cell("open" if rec["gap"] is None else str(rec["gap"]), classes="rh-num")
                + _cell(conductor, classes="rh-num")
                + _cell(html.escape(rec["conductor_source"]))
                + "</tr>"
            )
        st.html(
            '<div class="rh-dashboard-table-wrap rh-dashboard-table-scroll"><table class="rh-dashboard-data-table">'
            '<thead><tr><th>Curve</th><th>Rigorous lower</th><th>Rigorous upper</th><th>Gap</th><th>Conductor</th><th>N source</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )

def _lattice_data(db):
    rows = db.execute(
        """SELECT curve_id, basis_count, determinant, min_eigenvalue, status, updated_at, id
           FROM mw_lattices
           ORDER BY basis_count DESC, updated_at DESC, id DESC
           LIMIT 25"""
    ).fetchall()
    if not rows:
        return []

    states = curve_research_state_map(
        db,
        curve_ids=[int(row["curve_id"]) for row in rows],
    )
    data = []
    for row in rows:
        curve_id = int(row["curve_id"])
        state = states.get(curve_id)
        if state is None:
            continue
        lower = int(state["rigorous_lower"] or 0)
        upper = state["rigorous_upper"]
        if state["rank_inconsistent"]:
            evidence = f"conflict ≥ {lower}"
            if upper is not None:
                evidence += f" / ≤ {int(upper)}"
        elif state["exact_rank"] is not None:
            evidence = f"= {int(state['exact_rank'])}"
        elif lower > 0:
            evidence = f"≥ {lower}"
        else:
            evidence = "—"
        data.append(
            {
                "curve_id": curve_id,
                "family": state["family"],
                "parameter": state["parameter"],
                "evidence": evidence,
                "basis_count": int(row["basis_count"] or 0),
                "determinant": row["determinant"],
                "min_eigenvalue": row["min_eigenvalue"],
                "status": row["status"],
            }
        )
    return data


def _render_lattices(db):
    rows = _lattice_data(db)
    with region("dashboard-lattices", border=True):
        st.html(_heading("LATTICES"))
        if not rows:
            st.caption("No Mordell–Weil lattices are stored yet.")
            return
        body = []
        for row in rows:
            curve = f'<strong>#{row["curve_id"]}</strong><small>{html.escape(str(row["family"]))}</small>'
            body.append(
                "<tr>"
                + _cell(curve, title=f"parameter {row['parameter']}", classes="rh-curve-cell")
                + _cell(html.escape(row["evidence"]), classes="rh-num")
                + _cell(f"{row['basis_count']:,}", classes="rh-num")
                + _cell(html.escape(_compact_text(row["determinant"])), title=row["determinant"], classes="rh-num")
                + _cell(html.escape(_compact_text(row["min_eigenvalue"])), title=row["min_eigenvalue"], classes="rh-num")
                + _cell(html.escape(str(row["status"] or "—")))
                + "</tr>"
            )
        st.html(
            '<div class="rh-dashboard-table-wrap rh-dashboard-table-scroll"><table class="rh-dashboard-data-table">'
            '<thead><tr><th>Curve</th><th>Rank evidence</th><th>Basis</th><th>Determinant</th><th>Min eigenvalue</th><th>Status</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )

def render_torsion_landscape(db):
    """Render first-class torsion research from persisted authoritative state."""
    data = _torsion_research_data(db)
    exact_rows = data["exact_rows"]
    record_rows = data["record_rows"]

    _render_torsion_overview(data)

    left, right = st.columns(2, gap="medium")
    with left:
        _render_torsion_distribution(data)
    with right:
        _render_torsion_signals(data)

    _render_torsion_records(db, record_rows=record_rows)
    _render_torsion_rank(
        db,
        exact_rows=exact_rows,
        record_rows=record_rows,
    )
    return exact_rows, record_rows


def render_landscape_comparisons(db, *, high_rank_floor, best_exact):
    """Render comparative population analytics owned by Landscape."""
    render_torsion_landscape(db)

    left, right = st.columns(2, gap="medium")
    with left:
        render_record_complexity(db)
    with right:
        _render_exceptional_arithmetic(
            db,
            high_rank_floor=high_rank_floor,
            best_exact=best_exact,
        )


def render_chart_followups(db, *, high_rank_floor, best_exact):
    """Compatibility wrapper for pre-Landscape callers.

    Lattices and unresolved-work tables no longer belong in comparative
    Dashboard/Landscape analytics. Their owner pages remain Lattices & Heights
    and Analyze respectively.
    """
    return render_landscape_comparisons(
        db,
        high_rank_floor=high_rank_floor,
        best_exact=best_exact,
    )

def render_research_sections(db, *, high_rank_floor=None, best_exact=None):
    """Legacy dashboard hook retained for source compatibility.

    v0.8.8 moves Lattices into ``render_chart_followups`` so all research
    diagnostics sit directly beneath the chart grid.
    """
    return None
