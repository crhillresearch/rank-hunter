from __future__ import annotations

import json
import sys

import streamlit as st

from rank42.settings_registry import setting_value, save_setting_value
from rank42.ui_components import dataframe
from rank42.ui_store import (
    cancel_queue_job,
    dispatcher_health_status,
    dispatcher_status,
    ensure_dispatcher_running,
    hold_queue_job,
    list_queue,
    queue_counts,
    queue_priority_label,
    reconcile_jobs,
    resource_limits,
    release_queue_job,
    set_queue_priority,
    stop_dispatcher_process,
)


@st.cache_data(ttl=5, show_spinner=False)
def dispatcher_service_status(project_root, db_path):
    from rank42.dispatcher_service import service_status

    return service_status(project_root, db_path)


@st.cache_data(ttl=2, show_spinner=False)
def dispatcher_runtime_status(project_root, db_path):
    from rank42.dispatcher_service import runtime_heartbeat_status

    return runtime_heartbeat_status(project_root, db_path)


def _loads(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def queue_row(row):
    meta = _loads(row["metadata_json"], {})
    return {
        "job": int(row["job_id"]),
        "state": str(row["state"]),
        "priority": (
            f"{queue_priority_label(row['priority'])} "
            f"({int(row['priority'])})"
        ),
        "resource": str(row["resource_class"]),
        "label": str(row["label"]),
        "campaign": (
            str(meta.get("campaign_name"))
            if meta.get("campaign_name")
            else (
                f"#{int(meta['campaign_id'])}"
                if meta.get("campaign_id") is not None
                else "—"
            )
        ),
        "attempts": int(row["attempts"] or 0),
        "available": row["available_at"],
        "lease": row["lease_owner"] or "—",
    }


def render_advanced_queue_dispatcher(db, ctx):
    with st.expander("Advanced Queue / Dispatcher", expanded=False):
        st.caption(
            "System service plumbing and concurrency limits. Routine queue work "
            "can stay in the main Queue view."
        )
        service = dispatcher_service_status(
            str(ctx.project_root),
            str(ctx.db_path),
        )
        with st.container(border=True):
            st.markdown("#### Persistent dispatcher")
            s1, s2, s3 = st.columns(3)
            s1.metric(
                "Service",
                "Installed" if service.get("installed") else "Not installed",
            )
            s2.metric(
                "Startup",
                "Enabled" if service.get("enabled") else "Disabled",
            )
            s3.metric(
                "Service process",
                "Active" if service.get("active") else "Inactive",
            )

            if not service.get("systemctl_available"):
                st.caption(
                    "systemd user services are unavailable here. Rank Hunter will "
                    "continue using its detached dispatcher fallback."
                )
            elif not service.get("installed"):
                st.caption(
                    "Install the dispatcher as a per-user systemd service so Queue "
                    "and Scheduler survive Streamlit restarts and start again with "
                    "your user session after reboot."
                )
                if st.button(
                    "Install persistent dispatcher",
                    type="primary",
                    width="stretch",
                    key="jobs-install-dispatcher-service",
                ):
                    from rank42.dispatcher_service import install_service, start_service

                    try:
                        install_service(
                            ctx.project_root,
                            ctx.db_path,
                            python_executable=sys.executable,
                            start=False,
                        )
                        stop_dispatcher_process(db, timeout=5.0)
                        start_service(ctx.project_root, ctx.db_path)
                    except Exception as exc:
                        st.error(f"Could not install dispatcher service: {exc}")
                    else:
                        dispatcher_service_status.clear()
                        dispatcher_runtime_status.clear()
                        st.success("Persistent dispatcher installed and started.")
                        st.rerun()
            else:
                st.caption(
                    f"{service.get('name')} · "
                    f"PID {service.get('main_pid') or '—'}"
                )
                r1, r2 = st.columns(2)
                if r1.button(
                    "Restart dispatcher service",
                    width="stretch",
                    key="jobs-restart-dispatcher-service",
                ):
                    from rank42.dispatcher_service import restart_service

                    try:
                        current = dispatcher_status(db)
                        current_pid = current.get("pid")
                        service_pid = service.get("main_pid")
                        if (
                            current_pid
                            and int(current_pid) != int(service_pid or 0)
                        ):
                            stop_dispatcher_process(db, timeout=5.0)
                        restart_service(ctx.project_root, ctx.db_path)
                    except Exception as exc:
                        st.error(f"Could not restart dispatcher service: {exc}")
                    else:
                        dispatcher_service_status.clear()
                        dispatcher_runtime_status.clear()
                        st.success("Dispatcher service restarted.")
                        st.rerun()

                if r2.button(
                    "Remove persistent service",
                    width="stretch",
                    key="jobs-remove-dispatcher-service",
                ):
                    from rank42.dispatcher_service import uninstall_service

                    try:
                        uninstall_service(ctx.project_root, ctx.db_path)
                        ensure_dispatcher_running(ctx.db_path, ctx.project_root)
                    except Exception as exc:
                        st.error(f"Could not remove dispatcher service: {exc}")
                    else:
                        dispatcher_service_status.clear()
                        dispatcher_runtime_status.clear()
                        st.success(
                            "Persistent service removed; detached dispatcher "
                            "fallback is running."
                        )
                        st.rerun()

        st.markdown("#### Concurrency & resource limits")
        current_workers = max(
            1,
            int(setting_value(db, "queue_max_workers", 2) or 2),
        )
        current_limits = resource_limits(db)
        workers = int(
            st.number_input(
                "Maximum concurrent Rank Hunter jobs",
                min_value=1,
                max_value=32,
                value=current_workers,
                step=1,
                key="jobs-queue-max-workers",
                help="Global ceiling across all queue-owned job runners.",
            )
        )
        r1, r2, r3 = st.columns(3)
        gpu_limit = int(
            r1.number_input(
                "GPU ratpoints",
                min_value=1,
                max_value=16,
                value=max(1, int(current_limits.get("gpu_ratpoints", 1))),
                step=1,
                key="jobs-limit-gpu-ratpoints",
                help="Maximum simultaneous jobs classified as GPU ratpoints.",
            )
        )
        sage_limit = int(
            r2.number_input(
                "Sage-heavy",
                min_value=1,
                max_value=32,
                value=max(1, int(current_limits.get("sage_heavy", 2))),
                step=1,
                key="jobs-limit-sage-heavy",
                help="Maximum simultaneous heavy Sage/search jobs.",
            )
        )
        db_limit = int(
            r3.number_input(
                "DB maintenance",
                min_value=1,
                max_value=8,
                value=max(
                    1,
                    int(current_limits.get("database_maintenance", 1)),
                ),
                step=1,
                key="jobs-limit-db-maintenance",
                help="Maximum simultaneous database maintenance jobs.",
            )
        )
        st.caption(
            "Resource limits are additional ceilings; the global worker limit "
            "still caps total concurrency."
        )
        if st.button(
            "Save queue limits",
            width="stretch",
            key="jobs-save-worker-limit",
        ):
            save_setting_value(db, "queue_max_workers", workers)
            save_setting_value(
                db,
                "queue_resource_limits",
                {
                    "gpu_ratpoints": gpu_limit,
                    "sage_heavy": sage_limit,
                    "database_maintenance": db_limit,
                },
            )
            st.success("Queue concurrency limits updated.")
            st.rerun()


def render_queue(db, ctx):
    reconcile_jobs(db)
    service = dispatcher_service_status(
        str(ctx.project_root),
        str(ctx.db_path),
    )
    runtime = dispatcher_runtime_status(
        str(ctx.project_root),
        str(ctx.db_path),
    )
    status = dispatcher_health_status(
        db,
        service=service,
        runtime=runtime,
    )
    counts = queue_counts(db)
    rows = list_queue(db, include_terminal=False, limit=1000)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Dispatcher", str(status.get("label") or "Offline"))
    c2.metric("Queued", int(counts.get("queued", 0)))
    c3.metric("Held", int(counts.get("held", 0)))
    c4.metric(
        "Running",
        int(counts.get("running", 0)) + int(counts.get("claimed", 0)),
    )

    state = str(status.get("state") or "offline")
    detail = str(status.get("detail") or "").strip()
    if status.get("healthy"):
        bits = [
            f"Dispatcher PID {status.get('pid') or '—'}",
            str(status.get("worker_id") or "worker —"),
            f"max workers {int(status.get('max_workers') or 0)}",
        ]
        st.caption(" · ".join(bits))
        if detail:
            st.caption(detail)
    elif state == "starting":
        st.warning(detail or "Dispatcher startup is in progress.")
    elif state == "degraded":
        st.warning(
            detail
            or (
                "Dispatcher infrastructure exists, but its heartbeat is not "
                "healthy. Open Advanced Queue / Dispatcher to inspect or restart it."
            )
        )
    else:
        st.error(
            detail
            or (
                "Queue dispatcher is offline. Queued work will not start until "
                "the dispatcher is running."
            )
        )
        if st.button(
            "Start dispatcher",
            type="primary",
            width="stretch",
            key="jobs-start-dispatcher",
        ):
            ensure_dispatcher_running(ctx.db_path, ctx.project_root)
            dispatcher_service_status.clear()
            dispatcher_runtime_status.clear()
            st.rerun()

    render_advanced_queue_dispatcher(db, ctx)

    if not rows:
        st.info("Queue is empty.")
        return

    dataframe(
        [queue_row(row) for row in rows],
        semantic="jobs-queue-table",
        width="stretch",
        hide_index=True,
    )

    by_job = {int(row["job_id"]): row for row in rows}
    job_ids = list(by_job)
    wanted_job = st.session_state.get("manage_job_id")
    selected_index = next(
        (
            index
            for index, jid in enumerate(job_ids)
            if wanted_job is not None and int(jid) == int(wanted_job)
        ),
        0,
    )
    selected_job = st.selectbox(
        "Queued job",
        job_ids,
        index=selected_index,
        format_func=lambda jid: (
            f"#{jid} · {by_job[jid]['state']} · {by_job[jid]['label']}"
        ),
        key="jobs-queue-select",
    )
    st.session_state["manage_job_id"] = int(selected_job)
    row = by_job[int(selected_job)]
    state = str(row["state"])

    st.markdown("#### Queue priority")
    p1, p2 = st.columns([3, 1], vertical_alignment="bottom")
    priority_options = ["High", "Normal", "Low"]
    current_priority = queue_priority_label(row["priority"])
    selected_priority = p1.selectbox(
        "Priority",
        priority_options,
        index=priority_options.index(current_priority),
        disabled=state not in {"queued", "held"},
        key=f"jobs-queue-priority-{selected_job}",
        help=(
            "High runs before Normal, which runs before Low. Jobs with the "
            "same priority keep queue order."
        ),
    )
    if p2.button(
        "Set priority",
        width="stretch",
        disabled=state not in {"queued", "held"},
        key=f"jobs-queue-priority-save-{selected_job}",
    ):
        priority_value = {
            "High": 200,
            "Normal": 100,
            "Low": 50,
        }[selected_priority]
        try:
            set_queue_priority(db, int(selected_job), priority_value)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(
                f"Job #{int(selected_job)} priority set to "
                f"{selected_priority} ({priority_value})."
            )
            st.rerun()

    a, b, c = st.columns(3)
    if state == "queued":
        if a.button(
            "Hold",
            width="stretch",
            key=f"jobs-queue-hold-{selected_job}",
        ):
            hold_queue_job(db, int(selected_job))
            st.rerun()
    elif state == "held":
        if a.button(
            "Release",
            type="primary",
            width="stretch",
            key=f"jobs-queue-release-{selected_job}",
        ):
            release_queue_job(db, int(selected_job))
            st.rerun()
    else:
        a.caption("Already claimed by dispatcher.")

    if b.button(
        "Cancel waiting job",
        width="stretch",
        disabled=state not in {"queued", "held"},
        key=f"jobs-queue-cancel-{selected_job}",
    ):
        cancel_queue_job(db, int(selected_job))
        st.rerun()

    if c.button(
        "Open job",
        width="stretch",
        key=f"jobs-queue-open-{selected_job}",
    ):
        st.session_state["manage_job_id"] = int(selected_job)
        st.session_state["jobs_section"] = (
            "Active" if state in {"claimed", "running"} else "Queue"
        )
        st.rerun()
