from __future__ import annotations

import json
import re

import streamlit as st

from rank42.curve_research_state import curve_research_state_map
from rank42.feature_hooks import render_feature_hook
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.points import list_actionable_points, list_points
from rank42.ui_components import (
    metric_card,
    rh_key,
    selectable_dataframe,
    selectable_dataframe_rows,
    tabs,
)
from .common import curve_label, title


ACTIONABLE_STATUSES = {"unknown", "numerical_novel", "inconclusive"}
STATUS_LABELS = {
    "unknown": "Untested",
    "numerical_novel": "Promising — needs proof",
    "inconclusive": "Inconclusive — retry",
    "dependent": "Dependent",
    "rigorous_independent": "Rigorous witness",
}


def _field(record, key, default=None):
    try:
        if hasattr(record, "keys") and key in record.keys():
            return record[key]
    except Exception:
        pass
    if isinstance(record, dict):
        return record.get(key, default)
    return default


def _status_label(record_or_status):
    status = (
        str(_field(record_or_status, "independence_status", "unknown") or "unknown")
        if not isinstance(record_or_status, str)
        else str(record_or_status or "unknown")
    )
    return STATUS_LABELS.get(status, status.replace("_", " ").title())


def _needs_attention(point):
    if not bool(_field(point, "exact_verified", 0)):
        return True
    if bool(_field(point, "rigorous_independent", 0)):
        return False
    return str(_field(point, "independence_status", "unknown") or "unknown") in ACTIONABLE_STATUSES


def _exact_actionable(point):
    return (
        bool(_field(point, "exact_verified", 0))
        and not bool(_field(point, "rigorous_independent", 0))
        and str(_field(point, "independence_status", "unknown") or "unknown") in ACTIONABLE_STATUSES
    )


def _potential_label(point):
    if bool(_field(point, "rigorous_independent", 0)):
        lower = _field(point, "rigorous_lower")
        return f"Supports ≥{int(lower)}" if lower is not None else "Rigorous witness"
    if not bool(_field(point, "exact_verified", 0)):
        return "Verify first"

    status = str(_field(point, "independence_status", "unknown") or "unknown")
    if status == "dependent":
        return "Closed"
    if status not in ACTIONABLE_STATUSES:
        return "—"

    if _field(point, "curve_exact_rank") is not None:
        return "Consistency review"

    lower = _field(point, "rigorous_lower")
    if lower is None:
        return "Possible rank growth"
    return f"Candidate for ≥{int(lower) + 1}"


def _workflow_counts(db):
    row = db.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(exact_verified=0) AS needs_exact,
               SUM(
                   exact_verified=1
                   AND rigorous_independent=0
                   AND independence_status IN ('unknown','numerical_novel','inconclusive')
               ) AS needs_independence,
               SUM(
                   rigorous_independent=0
                   AND independence_status='numerical_novel'
               ) AS numerical_novel,
               SUM(rigorous_independent=1) AS rigorous_independent,
               SUM(independence_status='dependent') AS dependent
        FROM points
        """
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def _curve_rollup(db):
    aggregates = db.execute(
        """
        SELECT p.curve_id,
               COUNT(*) AS total,
               SUM(p.exact_verified=0) AS needs_exact,
               SUM(
                   p.exact_verified=1
                   AND p.rigorous_independent=0
                   AND p.independence_status IN ('unknown','numerical_novel','inconclusive')
               ) AS actionable,
               SUM(
                   p.rigorous_independent=0
                   AND p.independence_status='numerical_novel'
               ) AS promising,
               SUM(p.rigorous_independent=1) AS rigorous,
               SUM(p.independence_status='dependent') AS dependent
        FROM points p
        GROUP BY p.curve_id
        """
    ).fetchall()
    if not aggregates:
        return []

    ids = [int(row["curve_id"]) for row in aggregates]
    states = curve_research_state_map(db, curve_ids=ids)
    rows = []
    for aggregate in aggregates:
        curve_id = int(aggregate["curve_id"])
        state = states[curve_id]
        rows.append(
            {
                "curve_id": curve_id,
                "family": state["family"],
                "parameter": state["parameter"],
                "exact_rank": state["exact_rank"],
                "rigorous_lower": (
                    int(state["rigorous_lower"])
                    if int(state["rigorous_lower"]) > 0 or state["exact_rank"] is not None
                    else None
                ),
                "rigorous_upper": state["rigorous_upper"],
                "rank_inconsistent": bool(state["rank_inconsistent"]),
                "total": int(aggregate["total"] or 0),
                "needs_exact": int(aggregate["needs_exact"] or 0),
                "actionable": int(aggregate["actionable"] or 0),
                "promising": int(aggregate["promising"] or 0),
                "rigorous": int(aggregate["rigorous"] or 0),
                "dependent": int(aggregate["dependent"] or 0),
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row["actionable"]),
            -int(row["needs_exact"]),
            -int(row["promising"]),
            -int(row["rigorous_lower"] or 0),
            -int(row["total"]),
            -int(row["curve_id"]),
        )
    )
    return rows


def _table_row(point):
    family = str(_field(point, "family", "") or "")
    parameter = str(_field(point, "parameter", "") or "")
    family_t = f"{family} · t={parameter}" if family else f"t={parameter}"
    if not bool(_field(point, "exact_verified", 0)):
        state = "Needs exact verification"
    else:
        state = _status_label(point)
    return {
        "🔖": "🔖" if bool(_field(point, "hard_flag", 0)) else "",
        "point": int(_field(point, "id")),
        "curve": int(_field(point, "curve_id")),
        "rank ≥": _field(point, "rigorous_lower"),
        "family / t": family_t,
        "state": state,
        "potential": _potential_label(point),
        "source": _field(point, "source"),
        "search": _field(point, "search_ref") or "—",
    }


def _metadata(point):
    try:
        data = json.loads(_field(point, "metadata_json", "{}") or "{}")
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _denominator_provenance(metadata):
    """Return explicitly labeled chart-X vs stored-x denominator provenance."""
    data = dict(metadata or {})
    if (
        data.get("chart_x_denominator") is None
        and data.get("stored_x_denominator") is None
        and data.get("effective_denominator_high") is None
    ):
        return None
    return {
        "chart_X_denominator": data.get("chart_x_denominator"),
        "stored_x_denominator": data.get("stored_x_denominator"),
        "requested_chart_denominator_low": data.get(
            "requested_denominator_low", data.get("denominator_low")
        ),
        "requested_chart_denominator_high": data.get(
            "requested_denominator_high", data.get("denominator_high")
        ),
        "effective_chart_denominator_low": data.get(
            "effective_denominator_low"
        ),
        "effective_chart_denominator_high": data.get(
            "effective_denominator_high"
        ),
    }


def _job_id(point, metadata):
    for key in ("job_id", "search_job_id", "ui_job_id", "source_job_id"):
        value = metadata.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass

    ref = str(_field(point, "search_ref", "") or "").strip()
    if ref.isdigit():
        return int(ref)
    match = re.search(r"\bjob(?:\s*[:#-]?\s*)(\d+)\b", ref, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _open_curve(point):
    curve_id = set_active_curve_id(
        st.session_state,
        int(_field(point, "curve_id")),
    )
    st.session_state["curves_selected_id"] = curve_id
    st.session_state["rh_page"] = "Curves"
    st.rerun()


def _open_independence(points):
    points = [point for point in points if _exact_actionable(point)]
    curve_ids = {int(_field(point, "curve_id")) for point in points}
    if not points or len(curve_ids) != 1:
        return False
    curve_id = next(iter(curve_ids))
    ids = [int(_field(point, "id")) for point in points]
    set_active_curve_id(
        st.session_state,
        curve_id,
        "independence_curve_id",
    )
    st.session_state["independence_point_id"] = ids[0]
    st.session_state["independence_selected_point_ids"] = ids
    st.session_state["rh_page"] = "Independence"
    st.rerun()
    return True


def _render_point_detail(db, point):
    metadata = _metadata(point)
    point_id = int(_field(point, "id"))
    curve_id = int(_field(point, "curve_id"))
    lower = _field(point, "rigorous_lower")
    exact_rank = _field(point, "curve_exact_rank")
    status = str(_field(point, "independence_status", "unknown") or "unknown")

    st.markdown(f"### Point #{point_id}")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Curve", f"#{curve_id}")
    q2.metric("Current rigorous rank", f"≥ {int(lower)}" if lower is not None else "unresolved")
    q3.metric("Exact on curve", "yes" if _field(point, "exact_verified", 0) else "no")
    q4.metric("State", _status_label(point) if _field(point, "exact_verified", 0) else "Needs verification")

    if bool(_field(point, "rigorous_independent", 0)):
        st.success(
            "This point is already a rigorous Mordell–Weil witness and contributes to stored lower-bound evidence."
        )
    elif not bool(_field(point, "exact_verified", 0)):
        st.warning(
            "This coordinate pair has not yet crossed the exact-on-curve boundary. Verify it exactly before treating it as an independence candidate."
        )
    elif exact_rank is not None and status in ACTIONABLE_STATUSES:
        st.warning(
            f"This curve currently has exact rank {int(exact_rank)}. An apparently new exact point therefore needs a consistency review rather than an assumed rank increase."
        )
    elif status in ACTIONABLE_STATUSES:
        target = f"≥ {int(lower) + 1}" if lower is not None else "a higher rigorous lower bound"
        st.info(
            f"If an exact independence certificate proves this point is outside the current Mordell–Weil subgroup, it could support {target}. "
            "That is a workflow target, not a rank claim."
        )
    elif status == "dependent":
        st.caption(
            "This point was closed as dependent: different rational coordinates can still lie in the subgroup generated by the existing basis."
        )

    st.code(
        f"x = {_field(point, 'x')}\ny = {_field(point, 'y')}",
        language="text",
    )
    st.caption(
        f"Source: {_field(point, 'source')} · role: {_field(point, 'role')} · "
        f"plugin: {_field(point, 'plugin_id') or '—'} · search ref: {_field(point, 'search_ref') or '—'}"
    )
    denominator_provenance = _denominator_provenance(metadata)
    if denominator_provenance is not None:
        st.caption(
            "Search denominator provenance — chart X denominator: "
            f"{denominator_provenance['chart_X_denominator'] or '—'} · "
            "stored x denominator: "
            f"{denominator_provenance['stored_x_denominator'] or '—'} · "
            "effective chart band: "
            f"{denominator_provenance['effective_chart_denominator_low'] or '—'}.."
            f"{denominator_provenance['effective_chart_denominator_high'] or '—'}"
        )

    job_id = _job_id(point, metadata)
    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.button("Open Curve", width="stretch", key=f"pt-curve-{point_id}"):
        _open_curve(point)
    if c2.button(
        "Test Independence",
        width="stretch",
        key=f"pt-ind-{point_id}",
        disabled=not _exact_actionable(point),
        type="primary" if _exact_actionable(point) else "secondary",
    ):
        _open_independence([point])
    if c3.button("Open Lattices", width="stretch", key=f"pt-lattice-{point_id}"):
        set_active_curve_id(st.session_state, curve_id, "analysis_curve_id")
        st.session_state["rh_page"] = "Lattices & Heights"
        st.rerun()
    if c4.button("Target Curve", width="stretch", key=f"pt-target-{point_id}"):
        set_active_curve_id(st.session_state, curve_id, "target_curve_id")
        st.session_state["rh_page"] = "Target"
        st.rerun()
    if c5.button(
        "Open Results",
        width="stretch",
        key=f"pt-results-{point_id}",
        disabled=job_id is None,
        help=None if job_id is not None else "No source job id is stored for this point.",
    ):
        st.session_state["results_selected_job_id"] = int(job_id)
        st.session_state["rh_page"] = "Results"
        st.rerun()

    with st.expander("Source / search metadata", expanded=False):
        st.json(
            {
                "source": _field(point, "source"),
                "role": _field(point, "role"),
                "search_ref": _field(point, "search_ref"),
                "plugin_id": _field(point, "plugin_id"),
                "metadata": metadata,
            }
        )


def _point_curve_options(db):
    ids = [
        int(row["curve_id"])
        for row in db.execute(
            "SELECT DISTINCT curve_id FROM points ORDER BY curve_id"
        ).fetchall()
    ]
    if not ids:
        return []

    states = curve_research_state_map(db, curve_ids=ids)
    rows = []
    for state in states.values():
        row = dict(state["curve"])
        row["rigorous_lower"] = (
            int(state["rigorous_lower"])
            if int(state["rigorous_lower"]) > 0 or state["exact_rank"] is not None
            else None
        )
        row["rigorous_upper"] = state["rigorous_upper"]
        row["exact_rank"] = state["exact_rank"]
        row["rank_inconsistent"] = bool(state["rank_inconsistent"])
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -int(row["rigorous_lower"] or 0),
            -int(row["id"]),
        )
    )
    return rows


def _curve_selector(db, *, key):
    curves = _point_curve_options(db)
    if not curves:
        return None

    selected_id = get_active_curve_id(st.session_state, "points_curve_id")
    curve_by_id = {int(record["id"]): record for record in curves}
    curve_ids = [0, *curve_by_id]
    default_index = 0
    if selected_id is not None and int(selected_id) in curve_by_id:
        default_index = curve_ids.index(int(selected_id))

    chosen = st.selectbox(
        "Curve filter",
        curve_ids,
        index=default_index,
        key=key,
        format_func=lambda curve_id: (
            "All curves"
            if int(curve_id) == 0
            else curve_label(curve_by_id[int(curve_id)])
        ),
    )
    if int(chosen) == 0:
        st.session_state.pop("points_curve_id", None)
        return None

    chosen = set_active_curve_id(
        st.session_state,
        int(chosen),
        "points_curve_id",
    )
    return chosen


def _render_actionable(db):
    curve_id = _curve_selector(db, key="points-actionable-curve")

    kind = st.selectbox(
        "Attention filter",
        [
            "All attention",
            "Promising — needs proof",
            "Untested / unresolved",
            "Needs exact verification",
        ],
        key="points-attention-filter",
    )
    attention_key = {
        "All attention": "all",
        "Promising — needs proof": "promising",
        "Untested / unresolved": "unresolved",
        "Needs exact verification": "needs_exact",
    }[kind]
    attention = list_actionable_points(
        db,
        curve_id=curve_id,
        attention=attention_key,
        limit=2000,
    )

    if not attention:
        st.info("No points match this actionable filter.")
        return

    if len(attention) >= 2000:
        st.caption(
            "Showing the first 2,000 highest-priority actionable points after filtering. "
            "Use Curve filter or Attention filter to narrow the work queue."
        )

    default_ids = st.session_state.get("points_selected_ids")
    if default_ids is None and attention:
        default_ids = [int(attention[0]["id"])]

    selected = selectable_dataframe_rows(
        [_table_row(row) for row in attention],
        attention,
        semantic="points-actionable-table",
        selected_ids=default_ids,
        selection_state_key="points_selected_ids",
        height=430,
    )

    exact_selected = [row for row in selected if _exact_actionable(row)]
    selected_curve_ids = {int(_field(row, "curve_id")) for row in exact_selected}
    can_batch = bool(exact_selected) and len(selected_curve_ids) == 1

    left, right = st.columns([1.4, 2.6])
    with left:
        if st.button(
            f"Send selected to Independence ({len(exact_selected)})",
            type="primary",
            width="stretch",
            key="points-batch-independence",
            disabled=not can_batch,
        ):
            _open_independence(exact_selected)
    with right:
        if selected and any(not _exact_actionable(row) for row in selected):
            st.caption("Only exact unresolved points are handed to Independence; unverified or closed rows remain in the ledger.")
        elif exact_selected and len(selected_curve_ids) > 1:
            st.caption("Batch certification is one curve at a time. Select points from one curve or use the curve filter.")
        else:
            st.caption(
                "Candidate-for-rank-growth labels are scheduling cues only. Exact independence certificates decide whether the rigorous lower bound increases."
            )

    if selected:
        _render_point_detail(db, selected[0])

def _render_all_points(db):
    curve_id = _curve_selector(db, key="points-all-curve")
    status = st.selectbox(
        "Point state",
        [
            "Any",
            "Needs exact verification",
            "Untested",
            "Promising — needs proof",
            "Inconclusive — retry",
            "Dependent",
            "Rigorous witness",
        ],
        key="points-all-status",
    )

    query = {
        "curve_id": curve_id,
        "limit": 10000,
    }
    if status == "Needs exact verification":
        query["exact_verified"] = False
    elif status == "Untested":
        query["exact_only"] = True
        query["independence_status"] = "unknown"
    elif status == "Promising — needs proof":
        query["exact_only"] = True
        query["independence_status"] = "numerical_novel"
    elif status == "Inconclusive — retry":
        query["exact_only"] = True
        query["independence_status"] = "inconclusive"
    elif status == "Dependent":
        query["exact_only"] = True
        query["independence_status"] = "dependent"
    elif status == "Rigorous witness":
        query["exact_only"] = True
        query["independence_status"] = "rigorous_independent"

    rows = list_points(db, **query)

    if not rows:
        st.info("No points match the filters.")
        return

    if len(rows) >= 10000:
        st.caption(
            "Showing up to 10,000 rows after the selected filters are applied."
        )

    point = selectable_dataframe(
        [_table_row(row) for row in rows],
        rows,
        semantic="points-ledger-table",
        selected_id=st.session_state.get("points_selected_id"),
        selection_state_key="points_selected_id",
        height=430,
    )
    if point is not None:
        _render_point_detail(db, point)

def _render_by_curve(db):
    rows = _curve_rollup(db)
    if not rows:
        st.info("No stored points yet.")
        return

    presentation = [
        {
            "curve": int(row["curve_id"]),
            "rank ≥": row["rigorous_lower"],
            "exact rank": row["exact_rank"],
            "family / t": f"{row['family']} · t={row['parameter']}",
            "needs attention": int(row["needs_exact"] or 0) + int(row["actionable"] or 0),
            "needs exact": int(row["needs_exact"] or 0),
            "promising": int(row["promising"] or 0),
            "rigorous": int(row["rigorous"] or 0),
            "dependent": int(row["dependent"] or 0),
            "total": int(row["total"] or 0),
        }
        for row in rows
    ]
    chosen = selectable_dataframe(
        presentation,
        rows,
        semantic="points-by-curve-table",
        selected_id=st.session_state.get("points_rollup_curve_id"),
        id_key="curve_id",
        selection_state_key="points_rollup_curve_id",
        height=430,
    )
    if chosen is None:
        return

    curve_id = int(chosen["curve_id"])
    all_points = list_points(db, curve_id=curve_id, limit=10000)
    attention = [row for row in all_points if _needs_attention(row)]

    st.markdown(f"### Curve #{curve_id} · point workload")
    a, b, c, d, e = st.columns(5)
    a.metric("Needs attention", int(chosen["needs_exact"] or 0) + int(chosen["actionable"] or 0))
    b.metric("Needs exact", int(chosen["needs_exact"] or 0))
    c.metric("Promising", int(chosen["promising"] or 0))
    d.metric("Rigorous witnesses", int(chosen["rigorous"] or 0))
    e.metric("Dependent / closed", int(chosen["dependent"] or 0))

    c1, c2 = st.columns(2)
    if c1.button("Open Curve", width="stretch", key=f"points-rollup-open-{curve_id}"):
        if all_points:
            _open_curve(all_points[0])
        st.session_state["curves_selected_id"] = curve_id
        st.session_state["rh_page"] = "Curves"
        st.rerun()
    if c2.button(
        f"Open actionable points ({len(attention)})",
        width="stretch",
        key=f"points-rollup-attention-{curve_id}",
        disabled=not attention,
    ):
        set_active_curve_id(st.session_state, curve_id, "points_curve_id")
        st.session_state["points_selected_ids"] = [int(attention[0]["id"])] if attention else []
        st.session_state["points_force_view"] = "Actionable"
        st.rerun()

    if attention:
        st.caption("Actionable preview for this curve")
        st.dataframe(
            [_table_row(row) for row in attention[:50]],
            width="stretch",
            hide_index=True,
        )


def page(db, ctx):
    title(
        "Points",
        "Rational-point inbox and permanent ledger: track exact verification, independence, provenance, and discoveries that may justify a higher rigorous rank bound.",
        "Data",
    )
    render_feature_hook(db, ctx, "points.after_header")

    counts = _workflow_counts(db)
    a, b, c, d, e = st.columns(5)
    with a:
        metric_card("Needs exact verification", counts["needs_exact"], key="pts-needs-exact")
    with b:
        metric_card("Needs independence", counts["needs_independence"], key="pts-needs-ind")
    with c:
        metric_card("Promising", counts["numerical_novel"], key="pts-promising")
    with d:
        metric_card("Rigorous witnesses", counts["rigorous_independent"], key="pts-rigorous")
    with e:
        metric_card("Dependent / closed", counts["dependent"], key="pts-dependent")

    st.caption(
        f"{counts['total']:,} total stored rational-point row(s). "
        "Numerical novelty is a prioritization signal only; exact verification and exact independence certificates carry rigorous weight."
    )

    forced = st.session_state.pop("points_force_view", None)
    if forced:
        st.session_state.pop(rh_key("points-workflow-view"), None)
    choice = tabs(
        ["Actionable", "By curve", "All points"],
        value=forced or "Actionable",
        key="points-workflow-view",
    )
    st.divider()

    if choice == "By curve":
        _render_by_curve(db)
    elif choice == "All points":
        _render_all_points(db)
    else:
        _render_actionable(db)
