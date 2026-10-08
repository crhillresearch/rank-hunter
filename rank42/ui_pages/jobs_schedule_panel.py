from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st

from rank42.manage_store import (
    RETIRED_SEARCH_JOB_KINDS,
    create_schedule,
    delete_schedule,
    job_campaign_id,
    list_campaigns,
    list_schedule_occurrences,
    list_schedules,
    run_schedule_now,
    update_schedule,
)
from rank42.ui_components import dataframe
from rank42.ui_store import list_jobs


TERMINAL = {"succeeded", "failed", "interrupted", "stopped", "killed"}

CATCH_UP_LABELS = {
    "latest": "Latest only",
    "all": "Run all missed",
    "skip": "Skip missed",
}


def _loads(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _local_now():
    return datetime.now().astimezone()


def _to_utc_iso(day, clock):
    local = _local_now()
    tz = local.tzinfo
    value = datetime.combine(day, clock, tzinfo=tz)
    return value.astimezone(timezone.utc).isoformat()


def _local_text(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return str(value)


def _campaign_options(db):
    rows = list_campaigns(db, include_archived=False)
    return {int(row["id"]): row for row in rows}


def _job_campaign(row):
    return job_campaign_id(row)


def schedule_source_jobs(db):
    return [
        row for row in list_jobs(db, limit=1000)
        if str(row["status"]) in TERMINAL
        and str(row["kind"]) not in RETIRED_SEARCH_JOB_KINDS
    ]


def schedule_time_inputs(prefix, *, next_run_at=None):
    if next_run_at:
        try:
            default = datetime.fromisoformat(
                str(next_run_at).replace("Z", "+00:00")
            ).astimezone()
        except Exception:
            default = _local_now()
    else:
        default = _local_now()
    a, b = st.columns(2)
    day = a.date_input(
        "Next run date",
        value=default.date(),
        key=f"{prefix}-date",
    )
    clock = b.time_input(
        "Next run time",
        value=default.time().replace(second=0, microsecond=0),
        key=f"{prefix}-time",
    )
    return _to_utc_iso(day, clock)


def render_new_schedule(db, ctx):
    source_rows = schedule_source_jobs(db)
    if not source_rows:
        st.info(
            "To create a schedule, first complete at least one UI job. "
            "Schedules clone a known-good command instead of asking you to "
            "hand-author shell commands."
        )
        return

    campaigns = _campaign_options(db)
    by_id = {int(row["id"]): row for row in source_rows}
    with st.expander("New schedule", expanded=False):
        st.info(
            "**Frozen snapshot schedule** — replays the exact recipe/configuration "
            "captured from the selected completed Job. Later edits to a Saved "
            "Pipeline do not change this schedule."
        )
        source_id = st.selectbox(
            "Template job",
            list(by_id),
            format_func=lambda jid: (
                f"#{jid} · {by_id[jid]['kind']} · {by_id[jid]['label']}"
            ),
            key="schedule-new-source",
        )
        source = by_id[int(source_id)]
        source_meta = _loads(source["metadata_json"], {})
        if source_meta.get("pipeline_run_id") is not None:
            st.caption(
                f"Captured Pipeline Run #{int(source_meta['pipeline_run_id'])}. "
                "Each occurrence clones that frozen Run snapshot, not the latest "
                "Saved Pipeline definition."
            )
        label = st.text_input(
            "Schedule label",
            value=str(source["label"]),
            key="schedule-new-label",
        )
        recurrence = st.selectbox(
            "Recurrence",
            ["once", "daily", "weekly", "interval"],
            key="schedule-new-recurrence",
        )
        catch_up_policy = st.selectbox(
            "After downtime",
            list(CATCH_UP_LABELS),
            format_func=lambda value: CATCH_UP_LABELS[value],
            disabled=recurrence == "once",
            key="schedule-new-catch-up",
            help=(
                "Latest only queues the newest overdue slot. Run all missed "
                "drains every overdue slot in bounded batches. Skip missed "
                "records the backlog as skipped and resumes at the next future slot."
            ),
        )
        interval_hours = None
        if recurrence == "interval":
            interval_hours = int(
                st.number_input(
                    "Every N hours",
                    min_value=1,
                    value=6,
                    step=1,
                    key="schedule-new-interval-hours",
                )
            )
        next_run = schedule_time_inputs("schedule-new")
        current_campaign = _job_campaign(source)
        campaign_opts = [0, *campaigns]
        campaign_index = (
            campaign_opts.index(current_campaign)
            if current_campaign in campaign_opts
            else 0
        )
        campaign_id = st.selectbox(
            "Campaign",
            campaign_opts,
            index=campaign_index,
            format_func=lambda cid: (
                "Unassigned"
                if int(cid) == 0
                else f"#{cid} · {campaigns[int(cid)]['name']}"
            ),
            key="schedule-new-campaign",
        )
        enabled = st.checkbox(
            "Enabled",
            value=True,
            key="schedule-new-enabled",
        )
        if st.button(
            "Create schedule",
            type="primary",
            width="stretch",
            key="schedule-new-create",
        ):
            sid = create_schedule(
                db,
                label=label or str(source["label"]),
                kind=str(source["kind"]),
                command=_loads(source["command_json"], []),
                cwd=str(source["cwd"]),
                next_run_at=next_run,
                recurrence=recurrence,
                interval_minutes=(
                    None
                    if recurrence != "interval"
                    else int(interval_hours) * 60
                ),
                catch_up_policy=(
                    "latest" if recurrence == "once" else catch_up_policy
                ),
                metadata=_loads(source["metadata_json"], {}),
                campaign_id=(
                    None if int(campaign_id) == 0 else int(campaign_id)
                ),
                enabled=enabled,
            )
            st.session_state["manage_schedule_id"] = int(sid)
            st.success(f"Created schedule #{sid}.")
            st.rerun()


def render_schedule_editor(db, ctx, row):
    campaigns = _campaign_options(db)
    st.markdown(f"### Schedule #{int(row['id'])} · {row['label']}")
    st.info(
        "**Frozen snapshot schedule** — replays the captured Job recipe/configuration. "
        "Editing the Saved Pipeline later does not update this schedule."
    )
    schedule_meta = _loads(row["metadata_json"], {})
    if schedule_meta.get("pipeline_run_id") is not None:
        st.caption(
            f"Captured Pipeline Run #{int(schedule_meta['pipeline_run_id'])}; "
            "scheduled occurrences clone this Run snapshot."
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Enabled", "Yes" if row["enabled"] else "No")
    c2.metric("Recurrence", row["recurrence"])
    c3.metric(
        "Catch-up",
        CATCH_UP_LABELS.get(
            str(row["catch_up_policy"] or "latest"),
            str(row["catch_up_policy"] or "latest"),
        ),
    )
    c4.metric("Next run", _local_text(row["next_run_at"]))
    st.caption(f"Last job: {row['last_job_id'] or '—'}")
    if row["last_error"]:
        st.error(f"Last scheduler error: {row['last_error']}")

    with st.form(f"schedule-edit-{int(row['id'])}"):
        label = st.text_input("Label", value=str(row["label"]))
        recurrence = st.selectbox(
            "Recurrence",
            ["once", "daily", "weekly", "interval"],
            index=["once", "daily", "weekly", "interval"].index(
                str(row["recurrence"])
            ),
        )
        current_catch_up = str(row["catch_up_policy"] or "latest")
        catch_up_policy = st.selectbox(
            "After downtime",
            list(CATCH_UP_LABELS),
            index=list(CATCH_UP_LABELS).index(
                current_catch_up
                if current_catch_up in CATCH_UP_LABELS
                else "latest"
            ),
            format_func=lambda value: CATCH_UP_LABELS[value],
            disabled=recurrence == "once",
            help=(
                "Controls recurring backlog only. A normal single due slot "
                "still runs on its scheduled dispatcher cycle."
            ),
        )
        interval_hours = int(
            st.number_input(
                "Interval hours",
                min_value=1,
                value=max(
                    1,
                    int(row["interval_minutes"] or 360) // 60,
                ),
                step=1,
                disabled=recurrence != "interval",
            )
        )
        next_run = schedule_time_inputs(
            f"schedule-edit-{int(row['id'])}",
            next_run_at=row["next_run_at"],
        )
        campaign_opts = [0, *campaigns]
        current_campaign = (
            int(row["campaign_id"])
            if row["campaign_id"] is not None
            else 0
        )
        campaign_idx = (
            campaign_opts.index(current_campaign)
            if current_campaign in campaign_opts
            else 0
        )
        campaign_id = st.selectbox(
            "Campaign",
            campaign_opts,
            index=campaign_idx,
            format_func=lambda cid: (
                "Unassigned"
                if int(cid) == 0
                else f"#{cid} · {campaigns[int(cid)]['name']}"
            ),
        )
        enabled = st.checkbox("Enabled", value=bool(row["enabled"]))
        save = st.form_submit_button("Save schedule", width="stretch")
    if save:
        update_schedule(
            db,
            int(row["id"]),
            label=label,
            recurrence=recurrence,
            interval_minutes=(
                int(interval_hours) * 60
                if recurrence == "interval"
                else None
            ),
            catch_up_policy=(
                "latest" if recurrence == "once" else catch_up_policy
            ),
            next_run_at=next_run,
            campaign_id=(
                None if int(campaign_id) == 0 else int(campaign_id)
            ),
            enabled=enabled,
        )
        st.success("Schedule updated.")
        st.rerun()

    a, b, c = st.columns(3)
    if a.button(
        "Run now",
        type="primary",
        width="stretch",
        key=f"schedule-run-{row['id']}",
    ):
        try:
            jid = run_schedule_now(
                db,
                ctx.db_path,
                ctx.project_root,
                int(row["id"]),
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["manage_job_id"] = int(jid)
            st.session_state["jobs_section_pending"] = "Queue"
            st.success(f"Queued job #{jid}.")
            st.rerun()
    if b.button(
        "Disable" if row["enabled"] else "Enable",
        width="stretch",
        key=f"schedule-toggle-{row['id']}",
    ):
        update_schedule(
            db,
            int(row["id"]),
            enabled=not bool(row["enabled"]),
        )
        st.rerun()
    confirm = c.checkbox(
        "Delete",
        key=f"schedule-delete-confirm-{row['id']}",
    )
    if st.button(
        "Delete schedule",
        disabled=not confirm,
        width="stretch",
        key=f"schedule-delete-{row['id']}",
    ):
        delete_schedule(db, int(row["id"]))
        st.session_state.pop("manage_schedule_id", None)
        st.rerun()

    with st.expander("Command template", expanded=False):
        st.code(
            " ".join(
                str(x)
                for x in _loads(row["command_json"], [])
            ),
            language="bash",
        )

    occurrences = list_schedule_occurrences(
        db,
        int(row["id"]),
        limit=25,
    )
    with st.expander("Occurrence history", expanded=False):
        if not occurrences:
            st.caption("No automatic schedule occurrences yet.")
        else:
            dataframe(
                [
                    {
                        "occurrence": int(rec["id"]),
                        "scheduled for": _local_text(rec["scheduled_for"]),
                        "state": rec["state"],
                        "catch-up": CATCH_UP_LABELS.get(
                            str(
                                _loads(rec["snapshot_json"], {}).get(
                                    "catch_up_policy",
                                    "latest",
                                )
                            ),
                            str(
                                _loads(rec["snapshot_json"], {}).get(
                                    "catch_up_policy",
                                    "latest",
                                )
                            ),
                        ),
                        "job": (
                            f"#{int(rec['job_id'])}"
                            if rec["job_id"] is not None
                            else "—"
                        ),
                        "error": rec["last_error"] or "—",
                    }
                    for rec in occurrences
                ],
                semantic=(
                    f"jobs-schedule-occurrences-{int(row['id'])}"
                ),
                width="stretch",
                hide_index=True,
            )


def render_scheduled(db, ctx):
    render_new_schedule(db, ctx)
    rows = list_schedules(db, include_disabled=True)
    if not rows:
        st.info("No schedules yet.")
        return

    dataframe(
        [
            {
                "schedule": int(row["id"]),
                "enabled": bool(row["enabled"]),
                "label": row["label"],
                "kind": row["kind"],
                "mode": "Frozen snapshot",
                "campaign": (
                    f"#{int(row['campaign_id'])}"
                    if row["campaign_id"] is not None
                    else "—"
                ),
                "recurrence": row["recurrence"],
                "catch-up": CATCH_UP_LABELS.get(
                    str(row["catch_up_policy"] or "latest"),
                    str(row["catch_up_policy"] or "latest"),
                ),
                "next run": _local_text(row["next_run_at"]),
                "last run": _local_text(row["last_run_at"]),
                "last job": (
                    str(row["last_job_id"])
                    if row["last_job_id"] is not None
                    else "—"
                ),
            }
            for row in rows
        ],
        semantic="jobs-schedules-table",
        width="stretch",
        hide_index=True,
    )

    by_id = {int(row["id"]): row for row in rows}
    ids = list(by_id)
    wanted = st.session_state.get("manage_schedule_id")
    idx = next(
        (
            i
            for i, sid in enumerate(ids)
            if wanted and int(sid) == int(wanted)
        ),
        0,
    )
    sid = st.selectbox(
        "Schedule",
        ids,
        index=idx,
        format_func=lambda value: (
            f"#{value} · "
            f"{'on' if by_id[value]['enabled'] else 'off'} · "
            f"{by_id[value]['label']}"
        ),
        key="jobs-schedule-select",
    )
    st.session_state["manage_schedule_id"] = int(sid)
    render_schedule_editor(db, ctx, by_id[int(sid)])

    st.caption(
        "Schedules are owned by the durable Rank Hunter dispatcher, not Streamlit. "
        "Due occurrences are enqueued even after the Control Center UI is closed. "
        "With the persistent dispatcher service installed, scheduling survives UI "
        "restarts and resumes with the user systemd session after reboot."
    )
