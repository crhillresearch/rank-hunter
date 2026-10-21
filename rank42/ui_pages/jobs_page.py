from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.launch_context import launch_context_snapshot, normalize_launch_metadata
from rank42.manage_store import (
    resume_pipeline_run,
)
from rank42.pipeline_state import (
    get_pipeline_run,
    pipeline_run_runtime_drift,
)
from rank42.settings_registry import setting_value
from rank42.ui_store import (
    get_job,
    list_jobs,
)
from rank42.ui_components import dataframe, rh_key, tabs
from . import results_page
from .common import launch_resolved, title
from .jobs_detail_panel import (
    campaign_assignment as _campaign_assignment,
    job_campaign as _job_campaign,
    job_display_status as _job_display_status,
    job_is_paused as _job_is_paused,
    job_provenance,
    job_selector as _job_selector,
    job_table_row as _job_table_row,
    pipeline_run_id as _pipeline_run_id,
    render_active as _active,
    render_history as _history,
    render_job_detail as _job_detail,
    render_job_provenance as _render_job_provenance,
)
from .jobs_queue_panel import (
    dispatcher_service_status as _dispatcher_service_status,
    queue_row as _queue_row,
    render_advanced_queue_dispatcher as _advanced_queue_dispatcher,
    render_queue as _queue,
)
from .jobs_schedule_panel import (
    CATCH_UP_LABELS,
    render_new_schedule as _new_schedule,
    render_schedule_editor as _schedule_editor,
    render_scheduled as _scheduled,
    schedule_source_jobs as _schedule_source_jobs,
    schedule_time_inputs as _schedule_time_inputs,
)


ACTIVE = {"running", "stopping"}
WAITING = {"queued"}
TERMINAL = {"succeeded", "failed", "interrupted", "stopped", "killed"}
RESUMABLE = {"failed", "interrupted", "stopped", "killed"}
PIPELINE_RESUMABLE = RESUMABLE | {"paused"}


def _loads(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _pipeline_jobs_for_run(db, run_id):
    run_id = int(run_id)
    rows = []
    for row in list_jobs(db, limit=1000):
        if str(row["kind"]) != "pipeline_search":
            continue
        meta = _loads(row["metadata_json"], {})
        try:
            linked = int(meta.get("pipeline_run_id"))
        except (TypeError, ValueError):
            continue
        if linked == run_id:
            rows.append(row)
    return sorted(rows, key=lambda row: int(row["id"]), reverse=True)


def _pipeline_job_section(row):
    status = str(row["status"])
    if status == "queued":
        return "Queue"
    if status in ACTIVE or _job_is_paused(row):
        return "Active"
    return "History"


def _launch_pipeline_run_resume_attempt(db, ctx, run):
    """Create a new process attempt for an existing frozen Pipeline Run."""
    run_id = int(run["id"])
    run_config = _loads(run["run_config_json"], {})
    metadata = {
        "ratpoints_backend": str(run_config.get("ratpoints_backend") or ""),
    }
    metadata.update(launch_context_snapshot(run_config))
    metadata = normalize_launch_metadata(
        metadata,
        launch_surface=run_config.get("launch_surface") or "pipeline_resume",
        plugin_id=run_config.get("plugin_id"),
        plugin_variant=run_config.get("plugin_variant"),
        target_mode=run["target_mode"],
        pipeline_run_id=run_id,
        pipeline_id=run["pipeline_id"],
        pipeline_name=run["pipeline_name"],
        feature_plugin_ids=run_config.get("feature_plugin_ids") or [],
        source_pool_id=(
            run_config.get("source_pool_id")
            or run_config.get("candidate_pool_id")
            or run_config.get("pool_id")
        ),
    )
    py = setting_value(db, "science_python", ctx.detected_science_python())
    failure = run_config.get("campaign_context_error")
    if not isinstance(failure, dict):
        failure = None
    return launch_resolved(
        ctx,
        db,
        kind="pipeline_search",
        label=f"Pipeline · {run['pipeline_name']}",
        command=[
            str(py), "-m", "rank42.pipeline_runner",
            "--project-root", str(ctx.project_root),
            "--db", str(ctx.db_path),
            "--run-id", str(run_id),
        ],
        metadata=metadata,
        campaign_failure=failure,
    )


def _pipeline_run_handoff(db, ctx):
    """Resolve a Pipelines->Jobs handoff to a process attempt or orphan control."""
    requested = st.session_state.get("manage_pipeline_run_id")
    if requested is None:
        return
    try:
        run_id = int(requested)
    except (TypeError, ValueError):
        st.session_state.pop("manage_pipeline_run_id", None)
        return

    run = get_pipeline_run(db, run_id)
    if run is None:
        st.session_state.pop("manage_pipeline_run_id", None)
        st.error(f"Pipeline Run #{run_id} no longer exists.")
        return

    attempts = _pipeline_jobs_for_run(db, run_id)
    if attempts:
        row = attempts[0]
        section = _pipeline_job_section(row)
        st.session_state["manage_job_id"] = int(row["id"])
        st.session_state["jobs_section_pending"] = section
        selector_key = {
            "Active": "jobs-active-select",
            "Queue": "jobs-queue-select",
            "History": "jobs-history-select",
        }.get(section)
        if selector_key:
            st.session_state.pop(selector_key, None)
        st.session_state.pop("manage_pipeline_run_id", None)
        return

    with st.container(border=True):
        st.markdown(f"### Pipeline Run #{run_id} · process handoff")
        st.caption(
            "This durable Pipeline Run has no recorded Job attempt. "
            "Jobs owns creation of a new process attempt."
        )
        drift = pipeline_run_runtime_drift(
            run,
            project_root=ctx.project_root,
        )
        if drift["blocking"]:
            st.error(
                "Pipeline runtime incompatibility: "
                + "; ".join(drift["blocking"])
            )
        elif drift["warnings"]:
            st.warning(
                "Pipeline replay/runtime drift: "
                + "; ".join(drift["warnings"])
            )

        status = str(run["status"])
        if status in PIPELINE_RESUMABLE and not drift["blocking"]:
            if st.button(
                "Resume as new Job attempt",
                type="primary",
                width="stretch",
                icon=":material/play_arrow:",
                key=f"jobs-orphan-pipeline-resume-{run_id}",
            ):
                new_id = resume_pipeline_run(
                    db,
                    ctx.db_path,
                    run_id,
                    launch_fallback=lambda frozen: _launch_pipeline_run_resume_attempt(
                        db,
                        ctx,
                        frozen,
                    ),
                )
                st.session_state["manage_job_id"] = int(new_id)
                st.session_state["jobs_section_pending"] = "Queue"
                st.session_state.pop("manage_pipeline_run_id", None)
                st.success(f"Queued resume attempt as job #{new_id}.")
                st.rerun()
        else:
            st.caption(
                f"Run state: {status}. No resumable process attempt is available."
            )


def page(db, ctx):
    title(
        "Jobs",
        "Manage running, queued, completed, and scheduled Rank Hunter computation.",
        "Manage",
    )
    render_feature_hook(db, ctx, "manage.jobs.after_header")
    _pipeline_run_handoff(db, ctx)

    sections = ["Active", "Queue", "History", "Results", "Scheduled"]
    pending = st.session_state.pop("jobs_section_pending", None)
    if pending in sections:
        st.session_state["jobs_section"] = pending
        st.session_state.pop(rh_key("jobs-main-tabs"), None)
    current = st.session_state.get("jobs_section") or "Active"
    active = tabs(
        sections,
        value=current if current in sections else "Active",
        key="jobs-main-tabs",
    )
    st.session_state["jobs_section"] = active
    st.html("<div style='height:.85rem'></div>")

    if active == "Active":
        _active(db, ctx)
    elif active == "Queue":
        _queue(db, ctx)
    elif active == "History":
        _history(db, ctx)
    elif active == "Results":
        results_page.render_jobs_results(db, ctx)
    else:
        _scheduled(db, ctx)
