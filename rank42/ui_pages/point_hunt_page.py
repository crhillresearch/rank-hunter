"""Focused, per-curve exact rational-point search workbench."""
from __future__ import annotations

import streamlit as st

from rank42.curve_research_state import curve_research_state_map
from rank42.pipeline_catalog import stage_spec
from rank42.ui_active_curve import get_active_curve_id
from .common import (
    active_curve_selector,
    launch_resolved,
    prepare_pipeline_launch,
    resolve_ratpoints,
    setting,
)


def _heights(value):
    try:
        heights = [int(item.strip()) for item in str(value).split(",")]
    except ValueError:
        return None
    if not heights or any(height < 1 for height in heights):
        return None
    if heights != sorted(set(heights)):
        return None
    return heights


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _attempts(db, curve_id):
    if not _table_exists(db, "search_pipeline_point_attempts"):
        return []
    return db.execute(
        """SELECT run_id,stage_id,tier,height,denominator_low,denominator_high,
                  effective_denominator_low,effective_denominator_high,status,
                  exact_points,attempt_count,runtime,updated_at
           FROM search_pipeline_point_attempts
           WHERE curve_id=?
           ORDER BY updated_at DESC,id DESC LIMIT 100""",
        (int(curve_id),),
    ).fetchall()


def _points(db, curve_id):
    return db.execute(
        """SELECT id,x,y,source,exact_verified,independence_status,
                  rigorous_independent,created_at
           FROM points WHERE curve_id=? ORDER BY id DESC LIMIT 30""",
        (int(curve_id),),
    ).fetchall()


def render(db, ctx):
    curves = db.execute(
        "SELECT id,family,parameter,score,updated_at FROM curves ORDER BY id DESC"
    ).fetchall()
    if not curves:
        st.info("Store an elliptic curve first, then return here to search it for points.")
        return

    chosen = active_curve_selector(
        db,
        curves,
        wanted=get_active_curve_id(st.session_state),
        state_prefix="point_hunt",
        widget_prefix="point-hunt-curve",
        format_func=lambda row: (
            f"#{int(row['id'])} · {row['family']} · t={row['parameter']}"
        ),
    )
    if chosen is None:
        return
    curve_id = int(chosen["id"])
    state = curve_research_state_map(db, curve_ids=[curve_id]).get(curve_id) or {}
    lower = int(state.get("rigorous_lower") or 0)
    upper = state.get("rigorous_upper")
    exact = state.get("exact_rank")
    rank_label = (
        f"rank = {int(exact)}" if exact is not None else f"rank ≥ {lower}"
    )
    if upper is not None and exact is None:
        rank_label += f" · rank ≤ {int(upper)}"
    st.caption(f"Curve #{curve_id} · {rank_label} · searches below use exact ratpoints and the existing pipeline certificate step.")

    st.markdown("#### Search a new region")
    with st.form("point-hunt-search"):
        a, b, c, d = st.columns(4)
        low = int(a.number_input("Chart denominator min", min_value=1, value=2))
        high = int(b.number_input("Chart denominator max", min_value=1, value=100))
        charts = int(c.number_input("Adaptive charts", min_value=1, value=5))
        timeout = int(d.number_input("Seconds per search", min_value=1, value=8))
        e, f, g = st.columns([2, 1, 1])
        height_text = e.text_input("Height stages", value="10000, 100000")
        exact_candidates = int(f.number_input("Exact candidates", min_value=1, value=64))
        certificate_timeout = int(g.number_input("Certificate seconds", min_value=1, value=120))
        backend = st.selectbox(
            "Point engine", ["CPU", "GPU"],
            index=0 if str(setting(db, "ratpoints_backend", "CPU")).upper() != "GPU" else 1,
        )
        submitted = st.form_submit_button(
            "Start point hunt", icon=":material/travel_explore:", type="primary"
        )
    st.caption(
        "Denominator bounds apply to adaptive affine search charts. A completed search only covers "
        "its stated chart, height, and denominator region; new rank requires an exact independence certificate."
    )
    if submitted:
        heights = _heights(height_text)
        if high < low:
            st.error("Chart denominator max must be at least the minimum.")
        elif heights is None:
            st.error("Enter positive, increasing height stages separated by commas.")
        elif low > heights[-1]:
            st.error("At least one height stage must reach the minimum chart denominator.")
        elif not resolve_ratpoints(db, backend):
            st.error(f"The {backend} point engine is not configured or built.")
        else:
            config = dict(stage_spec("denominator_band").defaults)
            config.update({
                "denominator_low": low,
                "denominator_high": high,
                "charts": charts,
                "heights": heights,
                "timeout": timeout,
                "exact_candidates": exact_candidates,
                "certificate_timeout": certificate_timeout,
            })
            prepared = prepare_pipeline_launch(
                ctx, db,
                pipeline_choice={
                    "pipeline": {
                        "name": "Point Hunt",
                        "stages": [{"id": "denominator_band", "config": config}],
                        "config": {},
                    },
                    "target_rank": max(lower + 1, 1),
                },
                target_mode="curve",
                target={"curve_id": curve_id},
                run_config={"ratpoints_backend": backend},
                native_kind="point_hunt",
                native_label=f"Point Hunt · curve #{curve_id}",
                native_metadata={"curve_id": curve_id, "launch_surface": "point_hunt"},
            )
            job_id = launch_resolved(
                ctx, db,
                kind=prepared["kind"],
                label=f"Point Hunt · curve #{curve_id}",
                command=prepared["command"],
                metadata=prepared["metadata"],
                campaign_failure=prepared["campaign_failure"],
            )
            st.success(f"Point Hunt started: job #{job_id}, pipeline run #{prepared['pipeline_run_id']}.")

    st.markdown("#### Search coverage")
    attempts = _attempts(db, curve_id)
    if attempts:
        st.dataframe(
            [{
                "Run": int(row["run_id"]),
                "Stage": str(row["stage_id"]),
                "Chart": str(row["tier"]),
                "Height": int(row["height"]),
                "Denominator": (
                    f"{row['effective_denominator_low'] or row['denominator_low'] or '—'}"
                    f"–{row['effective_denominator_high'] or row['denominator_high'] or '—'}"
                ),
                "Status": str(row["status"]),
                "Exact hits": int(row["exact_points"] or 0),
                "Attempts": int(row["attempt_count"] or 0),
                "Updated": str(row["updated_at"] or ""),
            } for row in attempts],
            width="stretch", hide_index=True,
        )
        st.caption("Recent chart attempts for this curve. Timeout and partial attempts are shown separately from completed coverage.")
    else:
        st.info("No recorded pipeline point-search attempts for this curve yet.")

    st.markdown("#### Points found")
    points = _points(db, curve_id)
    if points:
        st.dataframe(
            [{
                "Point": int(row["id"]),
                "x": str(row["x"]),
                "y": str(row["y"]),
                "Source": str(row["source"]),
                "Exact": "Yes" if row["exact_verified"] else "Pending",
                "Independence": (
                    "Rigorous witness" if row["rigorous_independent"]
                    else str(row["independence_status"]).replace("_", " ").title()
                ),
            } for row in points],
            width="stretch", hide_index=True,
        )
    else:
        st.info("No stored rational points for this curve yet.")

    left, right = st.columns(2)
    if left.button("Open point ledger", icon=":material/grain:", width="stretch"):
        st.session_state["tools_selection"] = "Points"
        st.rerun()
    if right.button("Certify independence", icon=":material/schema:", width="stretch"):
        st.session_state["tools_selection"] = "Independence"
        st.rerun()
