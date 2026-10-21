"""Focused rank-proof workbench for a selected elliptic curve."""
from __future__ import annotations

import html
import json

import streamlit as st

from rank42.analysis_planner import analysis_plan
from rank42.analyze_workspace import curve_analysis_snapshot
from rank42.feature_hooks import render_feature_hook
from rank42.rank_evidence import list_rank_evidence, reduce_rank_state
from rank42.ui_active_curve import get_active_curve_id
from rank42.ui_store import get_job

from .common import active_curve_selector, curve_options, launch, setting, title


def _status_card(state, proof_action):
    lower = int(state["rigorous_lower"] or 0)
    upper = state["rigorous_upper"]
    exact = state["exact_rank"]
    if state.get("inconsistent"):
        tone, heading, detail, rank = (
            "conflict", "Evidence needs review",
            "Conflicting rigorous bounds are stored for this curve.", "Review evidence",
        )
        next_step = "Inspect the evidence history before launching another calculation."
    elif exact is not None:
        tone, heading, detail, rank = (
            "complete", "Exact rank certified",
            "The rigorous lower and upper bounds meet.", str(exact),
        )
        next_step = "Review the certificate and evidence below if needed."
    elif upper is None:
        tone, heading, detail, rank = (
            "open", "Upper bound still open",
            "A rigorous lower bound is known. A matching upper bound would certify the rank.",
            f"≥{lower}",
        )
        next_step = str(proof_action["title"]) if proof_action else "Try a rigorous upper bound."
    else:
        tone, heading, detail, rank = (
            "open", "Rank interval open",
            "The rigorous lower and upper bounds have not met yet.", f"{lower}–{upper}",
        )
        next_step = str(proof_action["title"]) if proof_action else "Strengthen the rank evidence."
    esc = html.escape
    upper_text = "Open" if upper is None else f"≤{upper}"
    exact_text = "Pending" if exact is None else str(exact)
    st.html(
        f'<section class="rh-descent-status rh-descent-status--{tone}" aria-label="Rank proof status">'
        '<div class="rh-descent-status-main">'
        '<div class="rh-descent-status-copy"><span class="rh-descent-eyebrow">RANK PROOF STATUS</span>'
        f'<h2>{esc(heading)}</h2><p>{esc(detail)}</p></div>'
        f'<div class="rh-descent-rank" aria-label="Current rank statement">{esc(rank)}</div>'
        '</div><div class="rh-descent-facts">'
        f'<div><span>RIGOROUS LOWER</span><strong>≥{lower}</strong></div>'
        f'<div><span>RIGOROUS UPPER</span><strong>{esc(upper_text)}</strong></div>'
        f'<div><span>EXACT RANK</span><strong>{esc(exact_text)}</strong></div>'
        '</div>'
        f'<div class="rh-descent-next"><span>NEXT STEP</span><p>{esc(next_step)}</p></div>'
        '</section>'
    )
    if state["conditional_analytic_upper"] is not None:
        st.caption(
            f'Conditional analytic upper bound: {state["conditional_analytic_upper"]}. '
            'This does not close the rigorous rank interval.'
        )


def _evidence_table(db, curve_id):
    rows = list_rank_evidence(db, curve_id, limit=12)
    with st.expander(f"Evidence history · {len(rows)} recent runs", expanded=False):
        if not rows:
            st.caption("No structured rank-engine evidence is recorded for this curve yet.")
            return
        data = []
        for rec in rows:
            try:
                options = json.loads(rec["options_json"] or "{}")
            except (TypeError, ValueError):
                options = {}
            if not isinstance(options, dict):
                options = {}
            data.append({
                "Engine": rec["engine"], "Status": rec["status"],
                "Rigorous lower": rec["rigorous_lower"],
                "Rigorous upper": rec["rigorous_upper"],
                "Exact rank": rec["exact_rank"],
                "Failure": options.get("failure_class"),
                "Conditional upper": rec["conditional_analytic_upper"],
                "Numerical signal": rec["numerical_rank_signal"],
                "Elapsed (s)": round(float(rec["elapsed_seconds"]), 2)
                if rec["elapsed_seconds"] is not None else None,
            })
        st.dataframe(data, width="stretch", hide_index=True)


def _remember_job(curve_id, job_id):
    st.session_state["descent_recent_job"] = {
        "curve_id": int(curve_id), "job_id": int(job_id),
    }
    st.rerun()


def _recent_job(db, curve_id):
    recent = st.session_state.get("descent_recent_job") or {}
    if recent.get("curve_id") != curve_id:
        return
    job_id = int(recent["job_id"])
    job = get_job(db, job_id)
    if job is None:
        return
    status = str(job["status"])
    with st.container(key="rh-descent-recent-job", border=True):
        info, action = st.columns([4, 1], vertical_alignment="center")
        info.markdown(f"**Latest run** · Job #{job_id} · {status.title()}")
        if action.button(
            "Open job", icon=":material/terminal:",
            key=f"descent-open-job-{job_id}", width="stretch",
        ):
            section = "Queue" if status == "queued" else (
                "Active" if status in {"running", "stopping", "paused"} else "History"
            )
            st.session_state["manage_job_id"] = job_id
            st.session_state["jobs_section_pending"] = section
            st.session_state["rh_page"] = "Jobs"
            st.rerun()


def _rank_bounds(db, ctx, curve_id):
    st.markdown("### Run rank bounds")
    st.caption("Start with Auto. It tries PARI first; choose an engine to repeat or isolate that method.")
    with st.container(key="rh-descent-primary", border=True):
        with st.form(f"descent-rank-bounds-{curve_id}", border=False):
            engine_label = st.segmented_control(
                "Method", ["Auto", "PARI", "mwrank"], default="Auto",
                help="Auto runs PARI first. The other choices run one engine directly.",
            )
            with st.expander("Adjust time and memory limits", expanded=False):
                a, b, c = st.columns(3)
                pari_timeout = int(a.number_input(
                    "PARI timeout · seconds", min_value=10,
                    value=int(setting(db, "pari_rank_timeout", 300)), step=30,
                ))
                mwrank_timeout = int(b.number_input(
                    "mwrank timeout · seconds", min_value=10,
                    value=int(setting(db, "mwrank_rank_timeout", 300)), step=30,
                ))
                pari_stack_gib = int(c.number_input(
                    "PARI stack cap · GiB", min_value=2, max_value=16, value=4, step=2,
                    help="Used only after a genuine PARI stack overflow.",
                ))
                escalate = st.checkbox(
                    "Try mwrank if PARI finishes without closing the gap", value=False,
                    help="Optional. mwrank may take longer or hit a machine-integer limit.",
                )
            submitted = st.form_submit_button(
                "Run rank bounds", type="primary", icon=":material/play_arrow:", width="stretch",
            )
        if submitted:
            engine = {"Auto": "auto", "PARI": "pari", "mwrank": "mwrank"}[engine_label or "Auto"]
            py = setting(db, "science_python", ctx.detected_science_python())
            command = [
                py, "-m", "rank42.cli", "rank-bounds", "--db", ctx.db_path,
                "--curve-id", curve_id, "--engine", engine,
                "--timeout", pari_timeout, "--mwrank-timeout", mwrank_timeout,
                "--pari-stack-max-gib", pari_stack_gib,
            ]
            if escalate:
                command.append("--escalate")
            job_id = launch(
                ctx, db, kind="rank_bounds", label=f"Rank bounds curve #{curve_id}",
                command=command, metadata={"curve_id": curve_id, "engine": engine},
            )
            _remember_job(curve_id, job_id)


def _other_methods(db, ctx, curve_id):
    st.markdown("### Other proof methods")
    st.caption("Use these when the main rank-bound run leaves an open question.")

    with st.expander("Attempt exact-rank certification", expanded=False, icon=":material/verified:"):
        st.caption("Run the proof-mode certifier against the evidence stored for this curve.")
        with st.form(f"descent-exact-{curve_id}", border=False):
            cert_timeout = int(st.number_input("Time limit · seconds", min_value=1, value=30, step=10))
            submitted = st.form_submit_button("Attempt certification", width="stretch")
        if submitted:
            py = setting(db, "science_python", ctx.detected_science_python())
            job_id = launch(
                ctx, db, kind="rank_cert", label=f"Exact-rank curve #{curve_id}",
                command=[py, "-m", "rank42.certify_curve", "--db", ctx.db_path,
                         "--id", curve_id, "--timeout", cert_timeout],
                metadata={"curve_id": curve_id},
            )
            _remember_job(curve_id, job_id)

    with st.expander("Strong mwrank descent", expanded=False, icon=":material/tune:"):
        st.caption("An expensive direct mwrank attack. Raise limits deliberately after reviewing earlier attempts.")
        with st.form(f"descent-strong-{curve_id}", border=False):
            a, b = st.columns(2)
            timeout = int(a.number_input(
                "Time limit · seconds", min_value=10,
                value=int(setting(db, "deep_cert_timeout", 900)), step=60,
            ))
            precision = int(b.number_input("Precision", min_value=64, value=512, step=64))
            a, b = st.columns(2)
            first = int(a.number_input("First limit", min_value=1, value=40))
            second = int(b.number_input("Second limit", min_value=1, value=20))
            submitted = st.form_submit_button("Run strong descent", width="stretch")
        if submitted:
            py = setting(db, "science_python", ctx.detected_science_python())
            job_id = launch(
                ctx, db, kind="descent", label=f"Descent curve #{curve_id}",
                command=[py, "-m", "rank42.attack", "--db", ctx.db_path,
                         "--id", curve_id, "--timeout", timeout,
                         "--first-limit", first, "--second-limit", second,
                         "--precision", precision],
                metadata={"curve_id": curve_id},
            )
            _remember_job(curve_id, job_id)

    with st.expander("Check the known subgroup", expanded=False, icon=":material/hub:"):
        st.caption("Saturation checks subgroup primitivity and index. It does not supply a rank upper bound.")
        if st.button(
            "Open Saturation", icon=":material/arrow_forward:",
            key=f"descent-open-saturation-{curve_id}",
        ):
            st.session_state["saturation_curve_id"] = curve_id
            st.session_state["rh_page"] = "Saturation"
            st.rerun()


def page(db, ctx):
    title("Descent", "Close the rigorous rank interval.", "Analysis")
    render_feature_hook(db, ctx, "analysis.descent.after_header")
    rows = curve_options(db)
    if not rows:
        st.info("No curves with a proven lower bound yet.")
        return
    wanted = get_active_curve_id(st.session_state, "descent_curve_id", "analyze_curve_id")
    row = active_curve_selector(
        db, rows, wanted=wanted, state_prefix="descent",
        widget_prefix="descent-curve", compatibility_keys=("descent_curve_id",),
    )
    if row is None:
        return
    curve_id = int(row["id"])
    state = reduce_rank_state(db, curve_id)
    plan = analysis_plan(curve_analysis_snapshot(db, curve_id))
    proof_action = next(
        (action for action in plan["actions"] if action.get("kind") == "descent"), None,
    )
    _status_card(state, proof_action)
    _recent_job(db, curve_id)
    history = ((proof_action or {}).get("prior_attempt_state") or {}).get("method_history") or {}
    if history.get("all_exhausted_at_budget"):
        st.warning("Tracked upper-bound methods are exhausted at the current budgets. Review evidence or raise a limit before retrying.")
    elif history.get("stronger_budget_available"):
        st.info("A stronger budget is available for at least one method tried before.")

    size_limited = db.execute(
        """SELECT 1 FROM rank_evidence
           WHERE curve_id=? AND engine='mwrank'
             AND (stderr_summary LIKE '%to long fails%'
                  OR stderr_summary LIKE '%too large%')
           LIMIT 1""",
        (curve_id,),
    ).fetchone()
    if size_limited is not None:
        st.warning(
            "A previous mwrank run hit its machine-integer size limit on this curve. "
            "That attempt was inconclusive. PARI and the other methods remain available."
        )

    _rank_bounds(db, ctx, curve_id)
    _evidence_table(db, curve_id)
    _other_methods(db, ctx, curve_id)
