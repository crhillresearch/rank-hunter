from __future__ import annotations

import streamlit as st

from rank42.pipeline_catalog import general_search_stages
from .common import launch_with_pipeline, ratpoints_selector, search_pipeline_selector, title


def render(db, ctx, *, embedded=False, compact=False):
    if not embedded:
        title(
            "General Hunt",
            "Automated family-free discovery: generate curves, Nagao-prefilter, search rational points, then certify exact lower bounds.",
            "Search",
        )

    surface = st.container()
    if embedded:
        _, surface, _ = st.columns([0.6, 2.8, 0.6])

    with surface:
        rp_backend, rp_exec = ratpoints_selector(
            db,
            key="general-hunt-ratpoints",
            label="Point engine" if compact else "Point engine",
        )

        with st.form("general-hunt"):
            if compact:
                c1, c2 = st.columns(2, gap="small")
                mode = c1.selectbox("Pool mode", ["seeded", "open"])
                target = int(c2.number_input("Target rank ≥", min_value=2, value=8, step=1))
                c3, c4 = st.columns(2, gap="small")
                pool = int(c3.number_input("Pool size", min_value=10, value=5000, step=500))
                shortlist = int(c4.number_input("Shortlist", min_value=1, value=100, step=10))
                with st.expander("Search options", expanded=False):
                    nagao = int(st.number_input("Nagao bound", min_value=20, value=100, step=20))
                    stages = st.text_input("ratpoints stages", "100,1000,10000")
                    timeout = int(st.number_input("Seconds per curve/stage", min_value=1, value=5, step=1))
                    if mode == "seeded":
                        e1, e2 = st.columns(2)
                        umin = int(e1.number_input("u min", value=1))
                        umax = int(e2.number_input("u max", value=250))
                        e3, e4 = st.columns(2)
                        vmin = int(e3.number_input("v min", value=1))
                        vmax = int(e4.number_input("v max", value=250))
                    else:
                        e1, e2 = st.columns(2)
                        amin = int(e1.number_input("A min", value=-5000))
                        amax = int(e2.number_input("A max", value=5000))
                        e3, e4 = st.columns(2)
                        bmin = int(e3.number_input("B min", value=-5000))
                        bmax = int(e4.number_input("B max", value=5000))
            else:
                c1, c2, c3, c4 = st.columns(4)
                mode = c1.selectbox("Pool mode", ["seeded", "open"])
                target = int(c2.number_input("Target rank ≥", min_value=2, value=8, step=1))
                pool = int(c3.number_input("Cheap pool size", min_value=10, value=5000, step=500))
                shortlist = int(c4.number_input("Point-search shortlist", min_value=1, value=100, step=10))
                d1, d2, d3 = st.columns(3)
                nagao = int(d1.number_input("Nagao bound", min_value=20, value=100, step=20))
                stages = d2.text_input("ratpoints stages", "100,1000,10000")
                timeout = int(d3.number_input("Seconds per curve/stage", min_value=1, value=5, step=1))
                if mode == "seeded":
                    e1, e2, e3, e4 = st.columns(4)
                    umin = int(e1.number_input("u min", value=1))
                    umax = int(e2.number_input("u max", value=250))
                    vmin = int(e3.number_input("v min", value=1))
                    vmax = int(e4.number_input("v max", value=250))
                else:
                    e1, e2, e3, e4 = st.columns(4)
                    amin = int(e1.number_input("A min", value=-5000))
                    amax = int(e2.number_input("A max", value=5000))
                    bmin = int(e3.number_input("B min", value=-5000))
                    bmax = int(e4.number_input("B max", value=5000))

            pipeline_choice = search_pipeline_selector(
                db,
                target_mode="general",
                key="general-hunt",
                fixed_goal=target,
                builtin_label="Built-in Pipeline · Current General controls",
            )

            submit = st.form_submit_button(
                "Start General Hunt",
                type="primary",
                width="stretch",
                disabled=(rp_backend == "GPU" and not rp_exec),
            )

        if submit:
            if pipeline_choice.get("builtin"):
                try:
                    stages_config = general_search_stages(
                        nagao_bound=nagao,
                        shortlist=shortlist,
                        ratpoints_stages=stages,
                        ratpoints_timeout=timeout,
                        certificate_timeout=120,
                        exact_candidates=64,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                    return
                pipeline_choice = {
                    "pipeline": {
                        "id": None,
                        "name": "General Search · Current controls",
                        "target_mode": "general",
                        "stages": stages_config,
                        "config": {},
                    },
                    "target_rank": target,
                }

            pipeline_target = {
                "pool_mode": mode,
                "pool_size": pool,
                "random_seed": 42,
            }
            if mode == "seeded":
                pipeline_target.update(
                    u_min=umin, u_max=umax, v_min=vmin, v_max=vmax,
                )
            else:
                pipeline_target.update(
                    a_min=amin, a_max=amax, b_min=bmin, b_max=bmax,
                )
            jid = launch_with_pipeline(
                ctx,
                db,
                pipeline_choice=pipeline_choice,
                target_mode="general",
                target=pipeline_target,
                run_config={
                    "target_rank": target,
                    "ratpoints_backend": rp_backend,
                    "ratpoints": rp_exec,
                    "certificate_timeout": 120,
                    "exact_candidates": 64,
                },
                native_kind="general_hunt",
                native_label=f"General Hunt · seek ≥{target}",
                native_command=[],
                native_metadata={
                    "search_mode": "general",
                    "pool_mode": mode,
                    "target_rank": target,
                    "ratpoints_backend": rp_backend,
                },
                require_pipeline=True,
            )
            st.success(f"Started search job #{jid}.")


def page(db, ctx):
    return render(db, ctx, embedded=False)
