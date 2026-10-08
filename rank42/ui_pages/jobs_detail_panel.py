from __future__ import annotations

import json

import streamlit as st

from rank42.manage_store import (
    RETIRED_SEARCH_JOB_KINDS,
    attach_job_to_campaign,
    delete_terminal_job,
    job_campaign_id,
    list_campaigns,
    pause_job,
    pause_pipeline_run,
    retry_job,
    retry_pipeline_run,
    resume_job,
    resume_pipeline_run,
    stop_pipeline_run,
)
from rank42.pipeline_state import (
    get_pipeline_run,
    pipeline_run_payload,
    pipeline_run_runtime_drift,
)
from rank42.ui_components import dataframe
from rank42.ui_results import summarize_job
from rank42.ui_store import (
    active_work,
    list_jobs,
    read_log,
    reconcile_jobs,
    stop_job,
)
from . import results_page


ACTIVE = {"running", "stopping"}
WAITING = {"queued"}
RESUMABLE = {"failed", "interrupted", "stopped", "killed"}


def _loads(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _campaign_options(db):
    rows = list_campaigns(db, include_archived=False)
    return {int(row["id"]): row for row in rows}


def job_campaign(row):
    return job_campaign_id(row)


def job_is_paused(row):
    if str(row["status"]) not in {"stopped", "interrupted", "failed", "killed"}:
        return False
    meta = _loads(row["metadata_json"], {})
    return (
        bool(meta.get("pause_requested"))
        and meta.get("resumed_as_job_id") is None
    )


def job_display_status(row):
    return "paused" if job_is_paused(row) else str(row["status"])


def pipeline_run_id(row):
    if str(row["kind"]) != "pipeline_search":
        return None
    meta = _loads(row["metadata_json"], {})
    try:
        return int(meta.get("pipeline_run_id"))
    except (TypeError, ValueError):
        return None


def _provenance_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _provenance_table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def job_provenance(db, row):
    """Build one read-only, durable provenance projection for a Job."""
    meta = _loads(row["metadata_json"], {})
    out = []

    launch_surface = str(meta.get("launch_surface") or "").strip()
    if launch_surface:
        out.append(
            ("Launched from", launch_surface.replace("_", " ").title())
        )

    campaign_id = job_campaign_id(row)
    if campaign_id is not None:
        label = f"#{campaign_id}"
        if _provenance_table_exists(db, "research_campaigns"):
            campaign = db.execute(
                "SELECT name FROM research_campaigns WHERE id=?",
                (int(campaign_id),),
            ).fetchone()
            if campaign is not None:
                label += f" · {campaign['name']}"
        out.append(("Campaign", label))

    run_id = _provenance_int(meta.get("pipeline_run_id"))
    pipeline_payload = None
    if run_id is not None:
        run = get_pipeline_run(db, run_id)
        if run is not None:
            pipeline_payload = pipeline_run_payload(run)
            out.append(
                (
                    "Pipeline Run",
                    f"#{run_id} · {pipeline_payload['pipeline_name']}",
                )
            )
        else:
            out.append(("Pipeline Run", f"#{run_id}"))

    manifest = dict((pipeline_payload or {}).get("manifest") or {})
    run_config = dict((pipeline_payload or {}).get("run_config") or {})
    source_id = (
        manifest.get("source_pipeline_id")
        or run_config.get("source_pipeline_id")
    )
    source_revision = (
        manifest.get("source_pipeline_revision")
        or run_config.get("source_pipeline_revision")
    )
    source_hash = (
        manifest.get("source_pipeline_hash")
        or run_config.get("source_pipeline_hash")
    )
    derived_id = (
        manifest.get("derived_from_pipeline_id")
        or run_config.get("derived_from_pipeline_id")
    )
    derived_revision = (
        manifest.get("derived_from_pipeline_revision")
        or run_config.get("derived_from_pipeline_revision")
    )
    derived_hash = (
        manifest.get("derived_from_pipeline_hash")
        or run_config.get("derived_from_pipeline_hash")
    )
    relation = str(
        manifest.get("pipeline_source_relation")
        or run_config.get("pipeline_source_relation")
        or ""
    ).strip()

    if source_id is not None:
        pipeline_bits = [f"#{int(source_id)}"]
        if source_revision is not None:
            pipeline_bits.append(f"revision {int(source_revision)}")
        if relation:
            pipeline_bits.append(relation)
        if source_hash:
            pipeline_bits.append(f"hash {str(source_hash)[:12]}")
        out.append(("Pipeline", " · ".join(pipeline_bits)))
    elif derived_id is not None:
        pipeline_bits = [f"modified from #{int(derived_id)}"]
        if derived_revision is not None:
            pipeline_bits.append(f"revision {int(derived_revision)}")
        if derived_hash:
            pipeline_bits.append(f"hash {str(derived_hash)[:12]}")
        out.append(("Pipeline", " · ".join(pipeline_bits)))

    schedule_id = _provenance_int(meta.get("schedule_id"))
    occurrence_id = _provenance_int(meta.get("schedule_occurrence_id"))
    if schedule_id is not None:
        schedule_label = f"#{schedule_id}"
        if _provenance_table_exists(db, "ui_job_schedules"):
            schedule = db.execute(
                "SELECT label FROM ui_job_schedules WHERE id=?",
                (schedule_id,),
            ).fetchone()
            if schedule is not None:
                schedule_label += f" · {schedule['label']}"
        out.append(("Schedule", schedule_label))
    if occurrence_id is not None:
        scheduled_for = str(meta.get("scheduled_for") or "").strip()
        occurrence_label = f"#{occurrence_id}"
        if scheduled_for:
            occurrence_label += f" · {scheduled_for}"
        out.append(("Occurrence", occurrence_label))

    plugin_id = str(
        meta.get("plugin_id")
        or manifest.get("plugin_id")
        or run_config.get("plugin_id")
        or ""
    ).strip()
    plugin_variant = str(
        meta.get("plugin_variant")
        or manifest.get("plugin_variant")
        or run_config.get("plugin_variant")
        or ""
    ).strip()
    plugin_version = str(meta.get("plugin_version") or "").strip()
    if plugin_id:
        plugin_bits = [plugin_id]
        if plugin_variant:
            plugin_bits.append(plugin_variant)
        if plugin_version:
            plugin_bits.append(f"version {plugin_version}")
        out.append(("Plugin", " · ".join(plugin_bits)))

    feature_ids = (
        meta.get("feature_plugin_ids")
        or manifest.get("feature_plugin_ids")
        or run_config.get("feature_plugin_ids")
        or []
    )
    if feature_ids:
        out.append(
            ("Features", ", ".join(str(value) for value in feature_ids))
        )

    pool_id = (
        meta.get("source_pool_id")
        or meta.get("candidate_pool_id")
        or meta.get("pool_id")
    )
    if pool_id is not None:
        out.append(("Candidate Pool", f"#{int(pool_id)}"))

    ratpoints_backend = str(
        meta.get("ratpoints_backend")
        or manifest.get("ratpoints_backend")
        or run_config.get("ratpoints_backend")
        or ""
    ).strip()
    if ratpoints_backend:
        out.append(("Point engine", ratpoints_backend))

    resume_of = _provenance_int(meta.get("resume_of_job_id"))
    retry_of = _provenance_int(meta.get("retry_of_job_id"))
    resumed_as = _provenance_int(meta.get("resumed_as_job_id"))
    if resume_of is not None:
        out.append(("Resume lineage", f"resumed from Job #{resume_of}"))
    if retry_of is not None:
        out.append(("Retry lineage", f"fresh retry of Job #{retry_of}"))
    if resumed_as is not None:
        out.append(("Resume lineage", f"resumed as Job #{resumed_as}"))

    if not out:
        out.append(
            ("Provenance", "No structured launch provenance was recorded.")
        )
    return out


def render_job_provenance(db, row):
    with st.container(border=True):
        st.markdown("#### Owner & provenance")
        for label, value in job_provenance(db, row):
            st.markdown(f"**{label}**: {value}")


def job_table_row(row):
    meta = _loads(row["metadata_json"], {})
    return {
        "job": int(row["id"]),
        "status": job_display_status(row),
        "kind": row["kind"],
        "label": row["label"],
        "campaign": (
            str(meta.get("campaign_name"))
            if meta.get("campaign_name")
            else (
                f"#{int(meta['campaign_id'])}"
                if meta.get("campaign_id") is not None
                else "—"
            )
        ),
        "pid": str(row["pid"]) if row["pid"] is not None else "—",
        "created": row["created_at"],
        "started": row["started_at"] or "—",
        "finished": row["finished_at"] or "—",
        "exit": (
            str(row["exit_code"])
            if row["exit_code"] is not None
            else "—"
        ),
    }


def job_selector(rows, *, key):
    if not rows:
        return None
    by_id = {int(row["id"]): row for row in rows}
    ids = list(by_id)
    wanted = st.session_state.get("manage_job_id")
    idx = next(
        (
            i
            for i, jid in enumerate(ids)
            if wanted and int(jid) == int(wanted)
        ),
        0,
    )
    jid = st.selectbox(
        "Job",
        ids,
        index=idx,
        format_func=lambda value: (
            f"#{value} · {job_display_status(by_id[value])} · "
            f"{by_id[value]['label']}"
        ),
        key=key,
    )
    st.session_state["manage_job_id"] = int(jid)
    return by_id[int(jid)]


def campaign_assignment(db, row):
    campaigns = _campaign_options(db)
    current = job_campaign(row)
    options = [0, *campaigns]
    idx = options.index(current) if current in options else 0
    selected = st.selectbox(
        "Campaign",
        options,
        index=idx,
        format_func=lambda cid: (
            "Unassigned"
            if int(cid) == 0
            else f"#{cid} · {campaigns[int(cid)]['name']}"
        ),
        key=f"jobs-campaign-{int(row['id'])}",
    )
    if st.button(
        "Save campaign assignment",
        width="stretch",
        key=f"jobs-save-campaign-{int(row['id'])}",
    ):
        attach_job_to_campaign(
            db,
            int(row["id"]),
            None if int(selected) == 0 else int(selected),
        )
        st.success("Job campaign updated.")
        st.rerun()


_render_job_provenance = render_job_provenance
_campaign_assignment = campaign_assignment
_pipeline_run_id = pipeline_run_id


def _render_job_controls(db, ctx, row, *, run_id, retired_search):
    """Render Jobs-owned lifecycle controls for one durable Job."""
    if str(row["status"]) in ACTIVE:
        a, b, c = st.columns(3)
        if a.button(
            "Pause",
            width="stretch",
            icon=":material/pause:",
            key=f"jobs-pause-{row['id']}",
        ):
            if run_id is None:
                pause_job(db, int(row["id"]))
            else:
                pause_pipeline_run(
                    db,
                    run_id,
                    job_id=int(row["id"]),
                )
            st.rerun()
        if b.button(
            "Stop",
            width="stretch",
            icon=":material/stop:",
            key=f"jobs-stop-{row['id']}",
        ):
            if run_id is None:
                stop_job(db, int(row["id"]), force=False, pause=False)
            else:
                stop_pipeline_run(
                    db,
                    run_id,
                    force=False,
                    job_id=int(row["id"]),
                )
            st.rerun()
        if c.button(
            "Force stop",
            type="primary",
            width="stretch",
            icon=":material/dangerous:",
            key=f"jobs-kill-{row['id']}",
        ):
            if run_id is None:
                stop_job(db, int(row["id"]), force=True, pause=False)
            else:
                stop_pipeline_run(
                    db,
                    run_id,
                    force=True,
                    job_id=int(row["id"]),
                )
            st.rerun()
        return

    status = str(row["status"])
    if status in RESUMABLE:
        a, b, c = st.columns(3)
        if a.button(
            "Resume",
            type="primary",
            width="stretch",
            icon=":material/play_arrow:",
            key=f"jobs-resume-{row['id']}",
        ):
            new_id = (
                resume_job(db, ctx.db_path, int(row["id"]))
                if run_id is None
                else resume_pipeline_run(
                    db,
                    ctx.db_path,
                    run_id,
                )
            )
            st.session_state["manage_job_id"] = int(new_id)
            st.success(f"Resumed as job #{new_id}.")
            st.rerun()
        if b.button(
            "Retry fresh",
            width="stretch",
            key=f"jobs-retry-{row['id']}",
            disabled=retired_search,
            help=(
                "Retired native Search work may only resume its stored "
                "state. Start fresh work through a Pipeline."
                if retired_search else None
            ),
        ):
            new_id = (
                retry_job(db, ctx.db_path, int(row["id"]))
                if run_id is None
                else retry_pipeline_run(
                    db,
                    ctx.db_path,
                    run_id,
                )
            )
            st.session_state["manage_job_id"] = int(new_id)
            st.success(f"Queued fresh retry as job #{new_id}.")
            st.rerun()
        if c.button(
            "Open Results",
            width="stretch",
            key=f"jobs-results-{row['id']}",
        ):
            st.session_state["results_selected_job_id"] = int(row["id"])
            st.session_state["jobs_section_pending"] = "Results"
            st.session_state["rh_page"] = "Jobs"
            st.rerun()
    else:
        a, b = st.columns(2)
        if a.button(
            "Retry",
            width="stretch",
            key=f"jobs-retry-{row['id']}",
            disabled=retired_search,
            help=(
                "Retired native Search work is compatibility-only; start "
                "fresh work through a Pipeline."
                if retired_search else None
            ),
        ):
            new_id = (
                retry_job(db, ctx.db_path, int(row["id"]))
                if run_id is None
                else retry_pipeline_run(
                    db,
                    ctx.db_path,
                    run_id,
                )
            )
            st.session_state["manage_job_id"] = int(new_id)
            st.success(f"Queued retry as job #{new_id}.")
            st.rerun()
        if b.button(
            "Open Results",
            width="stretch",
            key=f"jobs-results-{row['id']}",
        ):
            st.session_state["results_selected_job_id"] = int(row["id"])
            st.session_state["jobs_section_pending"] = "Results"
            st.session_state["rh_page"] = "Jobs"
            st.rerun()

    confirm = st.checkbox(
        "Delete",
        key=f"jobs-delete-confirm-{row['id']}",
        help=(
            "Check, then use the delete button below. Deletes the UI job "
            "row and its log, not scientific database results."
        ),
    )
    if st.button(
        "Delete terminal job + log",
        disabled=not confirm,
        width="stretch",
        key=f"jobs-delete-{row['id']}",
    ):
        delete_terminal_job(db, int(row["id"]), delete_log=True)
        st.session_state.pop("manage_job_id", None)
        st.rerun()


def render_job_detail(db, ctx, row):
    retired_search = str(row["kind"]) in RETIRED_SEARCH_JOB_KINDS
    log = read_log(row["log_path"])
    summary = summarize_job(row, log)
    st.markdown(f"### Job #{int(row['id'])} · {row['label']}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", job_display_status(row).title())
    c2.metric("Kind", str(row["kind"]))
    c3.metric("PID", row["pid"] or "—")
    c4.metric(
        "Exit",
        row["exit_code"] if row["exit_code"] is not None else "—",
    )

    completed = (
        summary.get("candidates_completed")
        or summary.get("trials_completed")
        or summary.get("shortlist_searched")
    )
    planned = (
        summary.get("candidates_planned")
        or summary.get("trials_planned")
        or summary.get("shortlist_planned")
    )
    if planned:
        st.progress(
            min(
                1.0,
                float(completed or 0) / max(1.0, float(planned)),
            ),
            text=f"Progress: {int(completed or 0)}/{int(planned)}",
        )

    run_id = _pipeline_run_id(row)
    if run_id is not None:
        pipeline_run = get_pipeline_run(db, run_id)
        if pipeline_run is not None:
            runtime_drift = pipeline_run_runtime_drift(
                pipeline_run,
                project_root=ctx.project_root,
            )
            if runtime_drift["blocking"]:
                st.error(
                    "Pipeline runtime incompatibility: "
                    + "; ".join(runtime_drift["blocking"])
                )
            elif runtime_drift["warnings"]:
                st.warning(
                    "Pipeline replay/runtime drift: "
                    + "; ".join(runtime_drift["warnings"])
                )

    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            st.markdown("#### Campaign")
            _campaign_assignment(db, row)
        with st.container(border=True):
            st.markdown("#### Controls")
            _render_job_controls(
                db,
                ctx,
                row,
                run_id=run_id,
                retired_search=retired_search,
            )
    with right:
        _render_job_provenance(db, row)

    results_page.render_job_scientific_result(
        db,
        row,
        summary,
        science_python=ctx.detected_science_python(),
        show_dynamic_stats=True,
    )


def render_active(db, ctx):
    reconcile_jobs(db)
    work = active_work(db, ctx.project_root)
    running_rows = [
        row
        for row in work["ui"]
        if str(row["status"]) in ACTIVE
    ]
    paused_rows = [
        row
        for row in list_jobs(db, limit=1000)
        if job_is_paused(row)
    ]
    ui_rows = [*running_rows, *paused_rows]
    external = list(work["external"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("UI jobs active", len(ui_rows))
    c2.metric("Paused", len(paused_rows))
    c3.metric("Terminal jobs observed", len(external))
    c4.metric(
        "Running work",
        len(running_rows) + len(external),
    )

    if ui_rows:
        dataframe(
            [job_table_row(row) for row in ui_rows],
            semantic="jobs-active-table",
            width="stretch",
            hide_index=True,
        )
        row = job_selector(ui_rows, key="jobs-active-select")
        if row is not None:
            render_job_detail(db, ctx, row)
    else:
        st.info("No UI-launched jobs are currently running or paused.")

    if external:
        st.markdown("#### Terminal-launched Rank Hunter work")
        st.caption(
            "Observed read-only. Rank Hunter does not claim ownership of "
            "terminal processes it did not launch."
        )
        dataframe(
            [
                {
                    "pid": rec["pid"],
                    "module": rec["module"],
                    "label": rec["label"],
                    "elapsed s": rec["elapsed_seconds"],
                    "command": rec["command"],
                }
                for rec in external
            ],
            semantic="jobs-external-table",
            width="stretch",
            hide_index=True,
        )


def render_history(db, ctx):
    reconcile_jobs(db)
    rows = [
        row
        for row in list_jobs(db, limit=1000)
        if str(row["status"]) not in (ACTIVE | WAITING)
        and not job_is_paused(row)
    ]
    if not rows:
        st.info("No completed or stopped UI jobs yet.")
        return

    statuses = sorted({str(row["status"]) for row in rows})
    kinds = sorted({str(row["kind"]) for row in rows})
    a, b = st.columns(2)
    status = a.selectbox(
        "Status",
        ["All", *statuses],
        key="jobs-history-status",
    )
    kind = b.selectbox(
        "Kind",
        ["All", *kinds],
        key="jobs-history-kind",
    )
    shown = rows
    if status != "All":
        shown = [
            row
            for row in shown
            if str(row["status"]) == status
        ]
    if kind != "All":
        shown = [
            row
            for row in shown
            if str(row["kind"]) == kind
        ]
    if not shown:
        st.info("No jobs match those filters.")
        return

    dataframe(
        [job_table_row(row) for row in shown],
        semantic="jobs-history-table",
        width="stretch",
        hide_index=True,
    )
    row = job_selector(shown, key="jobs-history-select")
    if row is not None:
        render_job_detail(db, ctx, row)
