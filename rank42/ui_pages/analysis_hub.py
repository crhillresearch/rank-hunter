"""Portfolio-level rank advancement across retained elliptic curves."""
from __future__ import annotations

import html
import json

import streamlit as st

from rank42.analyze_workspace import research_inbox
from rank42.ui_pages.common import (
    launch, launch_with_pipeline, ratpoints_selector, setting, title,
)
from rank42.ui_store import list_jobs


LANES = ("Certify points", "Prove rank", "Search rank")
MAX_BATCH = 5


def _eligible(row, lane):
    if row["rank_inconsistent"] or row["exact_rank"] is not None:
        return False
    if lane == "Certify points":
        return int(row["unresolved_exact_points"] or 0) > 0
    if lane == "Prove rank":
        upper = row["rigorous_upper"]
        return int(row["rigorous_lower"] or 0) > 0 and (
            upper is None or int(upper) > int(row["rigorous_lower"])
        )
    return (
        int(row["rigorous_lower"] or 0) > 0
        and int(row["unresolved_exact_points"] or 0) == 0
    )


def _lane_rows(queue, lane):
    rows = [row for row in queue if _eligible(row, lane)]
    if lane == "Certify points":
        rows.sort(key=lambda row: (
            bool(row["active_campaign"]),
            int(row["numerical_novel_points"] or 0),
            int(row["unresolved_exact_points"] or 0),
            int(row["rigorous_lower"] or 0),
        ), reverse=True)
    elif lane == "Prove rank":
        rows.sort(key=lambda row: (
            bool(row["active_campaign"]),
            row["rigorous_upper"] is not None,
            int(row["rigorous_lower"] or 0),
        ), reverse=True)
    else:
        rows.sort(key=lambda row: (
            bool(row["active_campaign"]),
            int(row["rigorous_lower"] or 0),
            str(row["last_activity_at"] or ""),
        ), reverse=True)
    return rows


def _metric(label, value, detail):
    st.html(
        '<div class="rh-analysis-hub-metric">'
        f'<span>{html.escape(label)}</span>'
        f'<strong>{int(value):,}</strong>'
        f'<small>{html.escape(detail)}</small></div>'
    )


def _overview(queue, lanes):
    with st.container(key="rh-analysis-hub-hero"):
        totals = (
            ("POINT CERTIFICATES", len(lanes["Certify points"]), "curves with exact points to test"),
            ("OPEN RANK PROOFS", len(lanes["Prove rank"]), "curves with unresolved bounds"),
            ("POINT SEARCHES", len(lanes["Search rank"]), "curves ready for another search"),
            ("EVIDENCE CONFLICTS", sum(bool(row["rank_inconsistent"]) for row in queue), "require evidence review"),
        )
        for column, (label, value, detail) in zip(st.columns(4, gap="small"), totals):
            with column:
                _metric(label, value, detail)


def _table_rows(rows, lane):
    output = []
    for row in rows:
        data = {
            "Curve": int(row["curve_id"]),
            "Family": str(row["family"]),
            "Parameter": str(row["parameter"]),
            "Rigorous rank ≥": int(row["rigorous_lower"] or 0),
            "Campaign": str(row["active_campaign_name"] or ""),
        }
        if lane == "Certify points":
            data["Exact points to test"] = int(row["unresolved_exact_points"] or 0)
            data["Numerically novel"] = int(row["numerical_novel_points"] or 0)
            data["Witness gap"] = int(row["witness_gap"] or 0)
        elif lane == "Prove rank":
            data["Rigorous upper"] = row["rigorous_upper"]
            data["Evidence time"] = row["last_attempt_at"] or "—"
        else:
            data["Last activity"] = row["last_activity_at"] or "—"
            data["Known witnesses"] = int(row["rigorous_witnesses"] or 0)
        output.append(data)
    return output


def _select_batch(rows, lane):
    if not rows:
        st.info({
            "Certify points": "No unresolved exact points are available for certification.",
            "Prove rank": "No curves have an open rigorous rank interval in this portfolio.",
            "Search rank": "No curves are ready for another point search in this portfolio.",
        }[lane])
        return []
    visible = rows[:50]
    st.dataframe(_table_rows(visible, lane), hide_index=True, width="stretch")
    by_id = {int(row["curve_id"]): row for row in visible}
    selected = st.multiselect(
        "Choose curves for this batch", list(by_id),
        default=[],
        format_func=lambda curve_id: (
            f"#{curve_id} · {by_id[curve_id]['family']} · "
            f"rank ≥{int(by_id[curve_id]['rigorous_lower'])}"
        ),
        key=f"analysis-hub-batch-{lane.lower().replace(' ', '-')}",
        help=f"Launch up to {MAX_BATCH} jobs at once. The table shows the first {len(visible)} of {len(rows)} eligible curves.",
    )
    if len(selected) > MAX_BATCH:
        st.warning(f"Select at most {MAX_BATCH} curves per batch.")
    return [by_id[int(curve_id)] for curve_id in selected]


def _candidate_point_ids(db, curve_id, limit):
    rows = db.execute(
        """SELECT id FROM points
           WHERE curve_id=? AND exact_verified=1
             AND COALESCE(rigorous_independent,0)=0
             AND COALESCE(independence_status,'unknown')
                 IN ('unknown','numerical_novel','inconclusive')
           ORDER BY CASE WHEN independence_status='numerical_novel' THEN 0 ELSE 1 END,
                    id DESC LIMIT ?""",
        (int(curve_id), int(limit)),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _launch_certificates(db, ctx, selected):
    st.caption("Each selected curve gets one exact independence job. A certificate raises rank only if a new independent direction is proved.")
    a, b, c = st.columns(3)
    per_curve = int(a.number_input("Points per curve", min_value=1, max_value=20, value=3, key="analysis-hub-cert-points"))
    timeout = int(b.number_input("Seconds per point", min_value=1, value=120, step=30, key="analysis-hub-cert-timeout"))
    halvings = int(c.number_input("Exact halvings", min_value=0, value=40, step=5, key="analysis-hub-cert-halvings"))
    prepared = [(row, _candidate_point_ids(db, int(row["curve_id"]), per_curve)) for row in selected]
    point_count = sum(len(point_ids) for _, point_ids in prepared)
    st.caption(f"Prepared: {len(prepared)} curve(s) · {point_count} exact point(s).")
    if st.button("Certify this batch", type="primary", icon=":material/verified:", disabled=not point_count or len(selected) > MAX_BATCH, key="analysis-hub-run-cert", width="stretch"):
        py = setting(db, "science_python", ctx.detected_science_python())
        jobs = []
        for row, point_ids in prepared:
            if not point_ids:
                continue
            curve_id = int(row["curve_id"])
            command = [
                py, "-m", "rank42.independence_check", "--db", ctx.db_path,
                "--curve-id", curve_id, "--timeout", timeout,
                "--max-halvings", halvings,
            ]
            for point_id in point_ids:
                command += ["--point-id", point_id]
            jobs.append(launch(
                ctx, db, kind="independence",
                label=f"Portfolio independence curve #{curve_id}", command=command,
                metadata={
                    "curve_id": curve_id, "point_ids": point_ids,
                    "mode": "maximal_subset", "launch_surface": "analysis_hub",
                },
            ))
        st.success(f"Queued {len(jobs)} certification job(s): {', '.join(f'#{job}' for job in jobs)}.")


def _launch_proofs(db, ctx, selected):
    st.caption("Try to establish rigorous upper bounds for several promising curves. A matching upper and lower bound proves exact rank.")
    a, b = st.columns(2)
    engine = a.selectbox(
        "Rank-bounds engine", ["auto", "pari", "mwrank"],
        format_func=lambda value: {"auto": "Auto · PARI first", "pari": "PARI", "mwrank": "mwrank"}[value],
        key="analysis-hub-proof-engine",
    )
    timeout = int(b.number_input("Seconds per engine", min_value=10, value=int(setting(db, "pari_rank_timeout", 300)), step=30, key="analysis-hub-proof-timeout"))
    if st.button("Run rank bounds for this batch", type="primary", icon=":material/verified:", disabled=not selected or len(selected) > MAX_BATCH, key="analysis-hub-run-proofs", width="stretch"):
        py = setting(db, "science_python", ctx.detected_science_python())
        jobs = []
        for row in selected:
            curve_id = int(row["curve_id"])
            jobs.append(launch(
                ctx, db, kind="rank_bounds", label=f"Portfolio rank bounds curve #{curve_id}",
                command=[
                    py, "-m", "rank42.cli", "rank-bounds", "--db", ctx.db_path,
                    "--curve-id", curve_id, "--engine", engine, "--timeout", timeout,
                    "--mwrank-timeout", timeout, "--pari-stack-max-gib", 4,
                ],
                metadata={"curve_id": curve_id, "engine": engine, "launch_surface": "analysis_hub"},
            ))
        st.success(f"Queued {len(jobs)} rank-bound job(s): {', '.join(f'#{job}' for job in jobs)}.")


def _launch_searches(db, ctx, selected):
    from rank42.ui_pages.target_curve import _free_target_pipeline_choice

    st.caption("Run the exact-curve point-search Pipeline on selected curves, then attempt independence on its exact hits.")
    a, b, c = st.columns(3)
    heights = a.text_input("Height stages", value="1000,10000,100000", key="analysis-hub-search-heights")
    timeout = int(b.number_input("Seconds per stage", min_value=1, value=20, step=5, key="analysis-hub-search-timeout"))
    exact = int(c.number_input("Exact candidates", min_value=1, value=8, key="analysis-hub-search-candidates"))
    backend, executable = ratpoints_selector(db, key="analysis-hub-ratpoints")
    if st.button("Search this batch", type="primary", icon=":material/search:", disabled=not selected or len(selected) > MAX_BATCH or not executable, key="analysis-hub-run-searches", width="stretch"):
        try:
            # Validate the shared budget before launching any member of the batch.
            _free_target_pipeline_choice(
                stages=heights, timeout=timeout, exact_candidates=exact,
                certificate_timeout=120, model_prep_timeout=10, goal_rank=1,
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        jobs = []
        for row in selected:
            curve_id = int(row["curve_id"])
            choice = _free_target_pipeline_choice(
                stages=heights, timeout=timeout, exact_candidates=exact,
                certificate_timeout=120, model_prep_timeout=10,
                goal_rank=int(row["rigorous_lower"] or 0) + 1,
            )
            jobs.append(launch_with_pipeline(
                ctx, db, pipeline_choice=choice, target_mode="curve",
                target={"curve_id": curve_id},
                run_config={
                    "ratpoints_backend": backend, "ratpoints": executable,
                    "certificate_timeout": 120, "exact_candidates": exact,
                },
                native_kind="target_free",
                native_label=f"Portfolio point search curve #{curve_id}",
                native_command=[],
                native_metadata={"curve_id": curve_id, "launch_surface": "analysis_hub"},
                require_pipeline=True,
            ))
        st.success(f"Queued {len(jobs)} point-search job(s): {', '.join(f'#{job}' for job in jobs)}.")


def _recent_jobs(db, queue):
    by_id = {int(row["curve_id"]): row for row in queue}
    rows = []
    for job in list_jobs(db, limit=300):
        try:
            metadata = json.loads(job["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(metadata, dict) or metadata.get("launch_surface") != "analysis_hub":
            continue
        try:
            curve_id = int(metadata.get("curve_id") or 0)
        except (TypeError, ValueError):
            curve_id = 0
        current = by_id.get(curve_id)
        rows.append({
            "Job": int(job["id"]), "Curve": curve_id or "—",
            "Experiment": job["label"], "Status": job["status"],
            "Current rank ≥": None if current is None else int(current["rigorous_lower"]),
            "Exact rank": None if current is None else current["exact_rank"],
            "Started": job["created_at"],
        })
        if len(rows) >= 12:
            break
    with st.container(key="rh-analysis-hub-history", border=True):
        st.markdown("#### Portfolio experiments")
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.caption("No portfolio experiments have been launched from this hub yet.")
        if st.button("Refresh results", icon=":material/refresh:", key="analysis-hub-refresh"):
            st.rerun()


def page(db, ctx):
    title("Rank Lab", icon=":material/science:")
    queue = research_inbox(db, limit=10000)
    lanes = {lane: _lane_rows(queue, lane) for lane in LANES}
    _overview(queue, lanes)
    if not queue:
        st.info("No retained curves are available for portfolio analysis yet.")
        return

    lane = st.segmented_control(
        "Research operation", list(LANES),
        default=next((name for name in LANES if lanes[name]), LANES[0]),
        selection_mode="single", required=True,
        key="analysis-hub-lane", width="stretch",
    )
    with st.container(key="rh-analysis-hub-workbench", border=True):
        st.markdown({
            "Certify points": "### Convert stored points into rank evidence",
            "Prove rank": "### Close the rigorous rank gap",
            "Search rank": "### Look for another independent point",
        }[lane])
        selected = _select_batch(lanes[lane], lane)
        if lane == "Certify points":
            _launch_certificates(db, ctx, selected)
        elif lane == "Prove rank":
            _launch_proofs(db, ctx, selected)
        else:
            _launch_searches(db, ctx, selected)
    _recent_jobs(db, queue)
