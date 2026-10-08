from __future__ import annotations

import html

import streamlit as st

from rank42 import __version__
from rank42.catalog import catalog_status, icarm_torsion_groups, list_external_curves
from rank42.dashboard_snapshot import (
    acknowledge_dashboard_attention,
    dashboard_attention_snapshot,
    dashboard_operations_snapshot,
    dashboard_research_snapshot,
)
from rank42.db import connect_existing
from rank42.evidence_depth import (
    EVIDENCE_DEPTH_STAGES,
    evidence_depth_counts,
    reached_stage_counts,
)
from rank42.feature_hooks import render_feature_hook
from rank42.ui_components import region
from .common import PAGE_ICONS, title
from .dashboard_charts import render_research_charts
from .dashboard_plugins import render_plugins_panel


def _go_to(page):
    st.session_state["rh_page"] = page


def _route_icon(page, fallback=":material/arrow_forward:"):
    return PAGE_ICONS.get(str(page), fallback)


def _metric_card(label, value, detail=None, *, accent="blue", status=None, detail_title=None):
    status_html = ""
    if status:
        status_html = f'<span class="rh-dashboard-metric-status">{html.escape(str(status))}</span>'
    detail_html = ""
    if detail is not None:
        title_attr = f' title="{html.escape(str(detail_title), quote=True)}"' if detail_title else ""
        detail_html = f'<small{title_attr}>{html.escape(str(detail))}</small>'
    st.html(
        f'<div class="rh-dashboard-metric rh-dashboard-metric-{html.escape(accent)}">'
        f'<div class="rh-dashboard-metric-head"><span>{html.escape(str(label))}</span>{status_html}</div>'
        f'<strong>{html.escape(str(value))}</strong>'
        f'{detail_html}'
        f'</div>'
    )


def _latest_activity(jobs):
    with region("dashboard-latest-activity", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading">'
            '<span class="rh-dashboard-section-title">MOST RECENT ACTIVITY</span></div>'
        )
        if not jobs:
            st.info("No jobs have been launched yet.")
        else:
            for job in jobs:
                status = str(job["status"] or "unknown").lower()
                when = job["finished_at"] or job["updated_at"] or job["created_at"] or ""
                st.html(
                    f'<div class="rh-dashboard-activity">'
                    f'<i class="level-{html.escape(status)}"></i>'
                    f'<div><small>JOB #{int(job["id"])} · {html.escape(status.upper())} · {html.escape(str(when)[:19])}</small>'
                    f'<p>{html.escape(str(job["label"]))}</p></div>'
                    f'</div>'
                )
        st.button(
            "View all results",
            key="rh-dashboard-view-results",
            width="stretch",
            icon=_route_icon("Results"),
            on_click=_go_to,
            args=("Results",),
        )


def _quick_actions_panel():
    with region("dashboard-quick-actions", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading">'
            '<span class="rh-dashboard-section-title">QUICK ACTIONS</span></div>'
        )
        left, right = st.columns(2, gap="small")
        with left:
            st.button(
                "Generate candidates",
                key="rh-dashboard-generate-candidates",
                width="stretch",
                icon=_route_icon("Candidate Generate"),
                on_click=_go_to,
                args=("Candidate Generate",),
            )
            st.button(
                "Target a curve",
                key="rh-dashboard-target",
                width="stretch",
                icon=_route_icon("Target"),
                on_click=_go_to,
                args=("Target",),
            )
        with right:
            st.button(
                "Search",
                key="rh-dashboard-search",
                width="stretch",
                icon=_route_icon("Search"),
                on_click=_go_to,
                args=("Search",),
            )
            st.button(
                "Auto",
                key="rh-dashboard-auto",
                width="stretch",
                icon=_route_icon("Auto"),
                on_click=_go_to,
                args=("Auto",),
            )


def _compact_count(value):
    value = int(value or 0)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= divisor:
            scaled = value / divisor
            digits = 0 if scaled >= 100 else 1
            return f"{scaled:.{digits}f}{suffix}"
    return f"{value:,}"


def _frontier_certification_html(summary):
    total = int(summary["total"])
    counts = summary["counts"]

    labels = [
        ("exact", "Exact"),
        ("lower", "Lower bound"),
        ("interval", "Open interval"),
        ("none", "Uncertified"),
    ]
    if counts["conflict"]:
        labels.append(("conflict", "Conflict"))
    segments = []
    legend = []
    for key, label in labels:
        count = counts[key]
        pct = 0.0 if total == 0 else 100.0 * count / total
        title = f"{label}: {count:,} · {pct:.1f}%"
        if pct > 0:
            segments.append(
                f'<i class="rh-frontier-cert-segment rh-frontier-cert-{key}" '
                f'style="width:{pct:.4f}%" title="{html.escape(title, quote=True)}"></i>'
            )
        legend.append(
            f'<span title="{html.escape(title, quote=True)}"><i class="rh-frontier-cert-{key}"></i>'
            f'{html.escape(label)} <strong>{count:,}</strong></span>'
        )
    return (
        '<div class="rh-frontier-certification rh-frontier-certification-standalone">'
        '<div class="rh-frontier-cert-head"><span>RANK CERTIFICATION</span>'
        f'<small>{total:,} retained curves</small></div>'
        f'<div class="rh-frontier-cert-track">{"".join(segments)}</div>'
        f'<div class="rh-frontier-cert-legend">{"".join(legend)}</div>'
        '</div>'
    )


def _evidence_depth_html(db):
    deepest_counts = evidence_depth_counts(db)
    reached_counts = reached_stage_counts(deepest_counts)
    total = sum(deepest_counts.values())
    segments = []
    legend = []
    for depth, key, label in EVIDENCE_DEPTH_STAGES:
        deepest_count = deepest_counts[depth]
        deepest_pct = 0.0 if total == 0 else 100.0 * deepest_count / total
        segment_title = (
            f"Deepest stage {depth} · {label}: {deepest_count:,} · {deepest_pct:.1f}%"
        )
        if deepest_pct > 0:
            segments.append(
                f'<i class="rh-evidence-depth-segment rh-evidence-depth-{key}" '
                f'style="width:{deepest_pct:.4f}%" '
                f'title="{html.escape(segment_title, quote=True)}"></i>'
            )

        count = reached_counts[depth]
        pct = 0.0 if total == 0 else 100.0 * count / total
        if depth == 0:
            title = f"No completed evidence stage: {count:,} · {pct:.1f}%"
        else:
            title = f"Reached {label}: {count:,} · {pct:.1f}%"
        legend.append(
            f'<span title="{html.escape(title, quote=True)}"><i class="rh-evidence-depth-{key}"></i>'
            f'{html.escape(label)} <strong>{count:,}</strong></span>'
        )

    return (
        '<div class="rh-evidence-depth">'
        '<div class="rh-frontier-cert-head rh-evidence-depth-head"><span>EVIDENCE DEPTH</span>'
        f'<small>{total:,} retained curves · cumulative totals · deepest-stage bar</small></div>'
        f'<div class="rh-evidence-depth-track">{"".join(segments)}</div>'
        f'<div class="rh-frontier-cert-legend rh-evidence-depth-legend">{"".join(legend)}</div>'
        '</div>'
    )

def _evidence_summary_row(db, certification):
    left, right = st.columns(2, gap="medium")
    with left:
        with region("dashboard-rank-certification-summary", border=False):
            st.html(_frontier_certification_html(certification))
    with right:
        with region("dashboard-evidence-depth-summary", border=False):
            st.html(_evidence_depth_html(db))


def _research_frontier_actions():
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.button(
            "Curves",
            key="rh-dashboard-explore-curves",
            width="stretch",
            icon=_route_icon("Curves"),
            on_click=_go_to,
            args=("Curves",),
        )
    with c2:
        st.button(
            "Points",
            key="rh-dashboard-open-points",
            width="stretch",
            icon=_route_icon("Points"),
            on_click=_go_to,
            args=("Points",),
        )
    with c3:
        st.button(
            "Descent",
            key="rh-dashboard-open-descent",
            width="stretch",
            icon=_route_icon("Descent"),
            on_click=_go_to,
            args=("Descent",),
        )
    with c4:
        st.button(
            "Lattices",
            key="rh-dashboard-open-lattices",
            width="stretch",
            icon=_route_icon("Lattices & Heights"),
            on_click=_go_to,
            args=("Lattices & Heights",),
        )


def _local_frontier_panel(rows, searched):

    with region("dashboard-local-frontier", border=True):
        if not rows:
            st.html(
                '<div class="rh-frontier-heading">'
                '<span>RESEARCH FRONTIER</span><span>LIVE DATABASE</span></div>'
            )
            st.info("No specialization has a rigorous rank certificate yet.")
        else:
            by_rank = {}
            exact_total = 0
            distinct_families = len({str(row["family"]) for row in rows})
            for row in rows:
                exact = row["exact_rank"]
                lower = int(row["rigorous_lower"] or 0)
                if exact is not None:
                    rank_value = int(exact)
                    kind = "exact"
                    exact_total += 1
                elif lower > 0:
                    rank_value = lower
                    kind = "lower"
                else:
                    continue

                bucket = by_rank.setdefault(
                    rank_value,
                    {"exact": 0, "lower": 0, "strongest_exact": None, "strongest_lower": None},
                )
                bucket[kind] += 1
                strongest_key = f"strongest_{kind}"
                incumbent = bucket[strongest_key]
                score = (
                    int(row["independent_points"] or 0),
                    str(row["updated_at"] or ""),
                    int(row["id"]),
                )
                if incumbent is None or score > incumbent[0]:
                    bucket[strongest_key] = (score, row)

            exact_ranks = [rank for rank, bucket in by_rank.items() if bucket["exact"]]
            lower_ranks = [rank for rank, bucket in by_rank.items() if bucket["lower"]]
            best_exact = max(exact_ranks) if exact_ranks else None
            best_lower = max(lower_ranks) if lower_ranks else None
            high_rank_floor = best_exact if best_exact is not None else best_lower
            high_rank = sum(
                bucket["exact"] + bucket["lower"]
                for rank, bucket in by_rank.items()
                if high_rank_floor is not None and rank >= high_rank_floor
            )

            rank_values = sorted(by_rank)
            if len(rank_values) > 12:
                rank_values = rank_values[-12:]

            rank_label = "≥ EXACT" if best_exact is not None else "FRONTIER"
            stat_items = [
                ("BEST EXACT", "—" if best_exact is None else str(best_exact), "Largest specialization rank with matching rigorous lower and upper bounds."),
                ("BEST LOWER", "—" if best_lower is None else f"≥{best_lower}", "Largest rigorous specialization lower bound that is not stored as an exact rank."),
                (rank_label, f"{high_rank:,}", "Curves whose exact rank or rigorous specialization lower bound reaches the best exact-rank frontier."),
                ("EXACTLY", f"{exact_total:,}", "Curves whose reduced rigorous lower and upper bounds certify an exact specialization rank."),
                ("FAMILIES", f"{distinct_families:,}", "Distinct curve families represented among rows with rigorous specialization evidence."),
                ("SEARCHES", _compact_count(searched), "Candidate rows that have moved beyond the unsearched state."),
            ]
            stats_html = "".join(
                f'<div title="{html.escape(help_text, quote=True)}"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'
                for label, value, help_text in stat_items
            )

            nodes = []
            for rank_value in rank_values:
                bucket = by_rank[rank_value]
                marker_kind = "exact" if bucket["exact"] else "lower"
                strongest = bucket[f"strongest_{marker_kind}"]
                strongest_row = strongest[1] if strongest is not None else None
                total_at_rank = bucket["exact"] + bucket["lower"]
                tooltip_parts = [f"Rank {rank_value}", f"exact: {bucket['exact']:,}", f"lower-bound only: {bucket['lower']:,}"]
                if strongest_row is not None:
                    tooltip_parts.append(
                        "strongest curve: "
                        f"#{int(strongest_row['id'])} · {strongest_row['family']} · parameter {strongest_row['parameter']} · "
                        f"{int(strongest_row['independent_points'] or 0)} rigorous independent points"
                    )
                tooltip = " · ".join(tooltip_parts)
                nodes.append(
                    f'<div class="rh-frontier-node rh-frontier-node-{marker_kind}" title="{html.escape(tooltip, quote=True)}">'
                    f'<span class="rh-frontier-rank">{rank_value}</span>'
                    f'<span class="rh-frontier-marker"><i></i></span>'
                    f'<span class="rh-frontier-kind">{"EXACT" if marker_kind == "exact" else "LB"}</span>'
                    f'<small>{total_at_rank:,}</small></div>'
                )

            st.html(
                '<div class="rh-frontier-shell"><div class="rh-frontier-heading">'
                '<span>RESEARCH FRONTIER</span><span>LIVE DATABASE</span></div>'
                f'<div class="rh-frontier-stats">{stats_html}</div>'
                f'<div class="rh-frontier-sequence">{"".join(nodes)}</div>'
                '<div class="rh-frontier-legend"><span><i class="rh-frontier-legend-exact"></i>Exact rank certified</span>'
                '<span><i class="rh-frontier-legend-lower"></i>Rigorous lower bound only</span></div>'
                '</div>'
            )

        _research_frontier_actions()


def _icarm_leaderboard(db):
    status = catalog_status(db, "icarm")
    fetched = str(status["fetched_at"] if status is not None else "—")[:19]
    groups = icarm_torsion_groups(db)
    labels = {"all": "All"}
    labels.update(
        {
            str(group["key"]): (
                "Trivial"
                if str(group["label"]).strip().lower() == "trivial"
                else str(group["label"])
            )
            for group in groups
        }
    )
    options = list(labels)

    filter_key = "rh-dashboard-icarm-torsion-menu"
    selected = str(st.session_state.get("rh-dashboard-icarm-torsion") or "all")
    triggered = st.session_state.get(filter_key)
    if triggered is not None and str(triggered) in options:
        selected = str(triggered)
        st.session_state["rh-dashboard-icarm-torsion"] = selected
    if selected not in options:
        selected = "all"
        st.session_state["rh-dashboard-icarm-torsion"] = selected

    with region("dashboard-icarm-leaderboard", border=True):
        heading_col, filter_col = st.columns(
            [5.8, 2.2],
            gap="small",
            vertical_alignment="top",
        )
        with heading_col:
            st.html(
                '<div class="rh-dashboard-section-heading">'
                '<span class="rh-dashboard-section-title">LEADERBOARD</span></div>'
            )
        if groups:
            with filter_col:
                chosen = st.menu_button(
                    labels[selected],
                    options,
                    help="Filter leaderboard by torsion group",
                    key=filter_key,
                    type="tertiary",
                    width="content",
                    format_func=lambda value: labels[str(value)],
                )
                if chosen is not None:
                    selected = str(chosen)
                    st.session_state["rh-dashboard-icarm-torsion"] = selected

        torsion = None if selected == "all" else selected
        leaders = list_external_curves(
            db,
            source="icarm",
            rank_at_least=0,
            torsion=torsion,
            limit=1,
        )
        leader = leaders[0] if leaders else None

        source_url = ""
        source_id = ""
        if leader is None:
            st.html(
                '<div class="rh-dashboard-icarm-rank">'
                '<span>Top rank</span><strong>—</strong>'
                f'<small>Last local sync · {html.escape(fetched)}</small></div>'
            )
        else:
            rank = int(leader["rank_lower_bound"] or 0)
            st.html(
                '<div class="rh-dashboard-icarm-rank">'
                '<span>Top rank</span>'
                f'<strong>≥ {rank}</strong>'
                f'<small>Last local sync · {html.escape(fetched)}</small></div>'
            )
            source_url = str(leader["source_url"] or "").strip()
            source_id = str(leader["source_id"])

        if leader is not None:
            if source_url:
                st.link_button(
                    f"ICARM entry #{source_id}",
                    source_url,
                    width="stretch",
                    icon=":material/open_in_new:",
                )
            else:
                st.caption(f"ICARM entry #{source_id}")




def _go_to_jobs_section(section):
    st.session_state["jobs_section_pending"] = str(section)
    st.session_state["rh_page"] = "Jobs"


def _operations_state_panel(snapshot):
    candidates = snapshot["candidate_counts"]
    runs = snapshot["pipeline_run_counts"]
    jobs = snapshot["job_counts"]
    queue = snapshot["queue_counts"]
    scheduler = snapshot["scheduler"]
    dispatcher = snapshot["dispatcher"]

    dispatcher_healthy = bool(dispatcher.get("healthy"))
    dispatcher_label = str(dispatcher.get("label") or "Offline")
    dispatcher_detail = str(dispatcher.get("detail") or "").strip()
    queue_waiting = int(snapshot["queue_waiting"])
    queue_claimed = int(snapshot["queue_claimed"])
    queue_running = int(snapshot["queue_running"])

    with region("dashboard-operations-state", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading">'
            '<span class="rh-dashboard-section-title">OPERATIONS</span>'
            f'<span class="rh-dashboard-plugin-health {"ok" if dispatcher_healthy else "warning"}">'
            f'Dispatcher {html.escape(dispatcher_label)}</span>'
            '</div>'
            '<div class="rh-dashboard-pipeline-stats">'
            f'<div><span>Candidate backlog</span><strong>{int(candidates["backlog"]):,}</strong></div>'
            f'<div><span>Pipeline Runs active</span><strong>{int(runs["active"]):,}</strong></div>'
            f'<div><span>Jobs active</span><strong>{int(jobs["active"]):,}</strong></div>'
            f'<div><span>Queue waiting</span><strong>{queue_waiting:,}</strong></div>'
            f'<div><span>Schedules enabled</span><strong>{int(scheduler["enabled"]):,}</strong></div>'
            f'<div><span>Operation errors</span><strong>{int(jobs["failed"]) + int(runs["failed"]) + int(scheduler["errors"]):,}</strong></div>'
            '</div>'
        )
        st.caption(
            "Candidate backlog is unsearched scientific work. Queue waiting is durable "
            "Job-dispatch state; they are different concepts."
        )
        st.caption(
            f'Queue detail: queued {int(queue.get("queued") or 0):,} · '
            f'held {int(queue.get("held") or 0):,} · claimed {queue_claimed:,} · '
            f'running {queue_running:,}. '
            f'Pipeline Runs: paused {int(runs["paused"]):,} · failed {int(runs["failed"]):,}. '
            f'Schedules: {int(scheduler["enabled"]):,}/{int(scheduler["total"]):,} enabled'
            + (f' · {int(scheduler["errors"]):,} with errors' if int(scheduler["errors"]) else "")
            + "."
        )

        if dispatcher_detail:
            st.caption(f"Dispatcher: {dispatcher_detail}")

        c1, c2 = st.columns(2, gap="small")
        with c1:
            st.button(
                "Open Pipelines",
                key="rh-dashboard-operations-pipelines",
                width="stretch",
                icon=_route_icon("Pipelines"),
                on_click=_go_to,
                args=("Pipelines",),
            )
            st.button(
                "Candidate Pools",
                key="rh-dashboard-operations-pools",
                width="stretch",
                icon=_route_icon("Candidate Pools"),
                on_click=_go_to,
                args=("Candidate Pools",),
            )
        with c2:
            st.button(
                "Jobs & Queue",
                key="rh-dashboard-operations-jobs",
                width="stretch",
                icon=":material/work_history:",
                on_click=_go_to_jobs_section,
                args=("Queue",),
            )
            st.button(
                "Schedules",
                key="rh-dashboard-operations-schedules",
                width="stretch",
                icon=":material/schedule:",
                on_click=_go_to_jobs_section,
                args=("Scheduled",),
            )


def _open_attention_item(item):
    page = str(item["page"])
    object_type = str(item["object_type"])
    object_id = item["object_id"]

    if object_type == "curve":
        st.session_state["analyze_curve_id"] = int(object_id)
        st.session_state["analyze_section_pending"] = "Overview"
    elif object_type == "campaign":
        st.session_state["manage_campaign_id"] = int(object_id)
    elif object_type == "job":
        st.session_state["manage_job_id"] = int(object_id)
        st.session_state["jobs_section_pending"] = "History"
    elif object_type == "pipeline_run":
        st.session_state["byo_focus_run_id"] = int(object_id)
    elif object_type == "schedule":
        st.session_state["manage_schedule_id"] = int(object_id)
        st.session_state["jobs_section_pending"] = "Scheduled"
    elif object_type == "dispatcher":
        st.session_state["jobs_section_pending"] = "Queue"

    st.session_state["rh_page"] = page


def _acknowledge_attention_item(db_path, item):
    """Acknowledge using a fresh callback-owned connection.

    Streamlit callbacks can execute after the render connection has already
    been closed by the outer page lifecycle, so never capture that connection.
    """
    live = connect_existing(db_path)
    try:
        acknowledge_dashboard_attention(live, item)
    finally:
        live.close()
    st.session_state["dashboard_attention_notice"] = "Attention item acknowledged."


def _render_attention_item(db_path, item, index):
    severity = str(item["severity"]).upper()
    title_text = html.escape(str(item["title"]))
    reason = html.escape(str(item["reason"]))
    owner = html.escape(str(item["page"]))
    object_type = html.escape(str(item["object_type"]))
    object_id = html.escape(str(item["object_id"]))
    st.html(
        '<div class="rh-dashboard-activity">'
        f'<i class="level-{html.escape(str(item["severity"]))}"></i>'
        f'<div><small>{severity} · {object_type} #{object_id} · {owner}</small>'
        f'<p>{title_text}</p><small>{reason}</small></div>'
        '</div>'
    )
    key_base = (
        "rh-dashboard-attention-"
        f'{item["kind"]}-{item["object_type"]}-{item["object_id"]}-{index}'
    )
    if bool(item.get("acknowledgeable")):
        open_col, acknowledge_col = st.columns(2, gap="small")
        open_col.button(
            f'Open {item["page"]}',
            key=f"{key_base}-open",
            width="stretch",
            icon=_route_icon(item["page"]),
            on_click=_open_attention_item,
            args=(item,),
        )
        acknowledge_col.button(
            "Acknowledge",
            key=f"{key_base}-acknowledge",
            width="stretch",
            icon=":material/check:",
            help="Remove this terminal operational alert from Attention while preserving its history and status.",
            on_click=_acknowledge_attention_item,
            args=(db_path, item),
        )
        return

    st.button(
        f'Open {item["page"]}',
        key=f"{key_base}-open",
        width="stretch",
        icon=_route_icon(item["page"]),
        on_click=_open_attention_item,
        args=(item,),
    )


def _attention_panel(db_path, items):
    notice = st.session_state.pop("dashboard_attention_notice", None)
    if notice:
        st.toast(str(notice))
    with region("dashboard-attention", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading">'
            '<span class="rh-dashboard-section-title">ATTENTION</span>'
            f'<span class="rh-dashboard-plugin-health {"warning" if items else "ok"}">'
            f'{"%d Open" % len(items) if items else "Clear"}</span>'
            '</div>'
        )
        if not items:
            st.caption("No current cross-system attention items.")
            return

        st.caption(
            "Open an item to fix it at its owner. Terminal Job/Pipeline failures may be acknowledged without changing history."
        )
        if len(items) <= 3:
            for index, item in enumerate(items):
                _render_attention_item(db_path, item, index)
        else:
            with st.container(height=520, border=False):
                for index, item in enumerate(items):
                    _render_attention_item(db_path, item, index)



def _dashboard_header(ctx):
    left, right = st.columns([1.35, 1], gap="small", vertical_alignment="bottom")
    with left:
        title("Dashboard")
    with right:
        st.html(f'<div class="rh-dashboard-app-meta"><span class="rh-dashboard-app-name">Rank Hunter</span><span class="rh-dashboard-app-version">v{html.escape(__version__)}</span></div>')
    st.html("""
        <style>
        .rh-dashboard-app-meta {display:flex;align-items:baseline;justify-content:flex-end;gap:.5rem;padding-bottom:.24rem;text-align:right;}
        .rh-dashboard-app-name {font-size:1rem;font-weight:400;}
        .rh-dashboard-app-version {font-size:.76rem;opacity:.6;}
        </style>
        """)


def _summary_metrics(research):
    summary = research["rank"]
    points = int(research["point_count"])

    with region("dashboard-top-stats"):
        c1, c2, c3, c4 = st.columns(4, gap="medium")
        with c1:
            best = summary["best_rigorous"]
            _metric_card("Best Rank", "—" if best is None else f"≥ {best}", f"Best exact rank: {summary['best_exact'] if summary['best_exact'] is not None else '—'}", accent="blue")
        with c2:
            _metric_card("Curves", f"{summary['curve_count']:,}", f"{points:,} points", accent="violet")
        with c3:
            _metric_card("Exact Ranks", f"{summary['exact_count']:,}", f"{summary['exact_pct']:.1f}% of retained curves", accent="green", detail_title="Persisted exact ranks only: rigorous lower and upper bounds meet.")
        with c4:
            floor = int(summary["high_rank_floor"])
            _metric_card("High-Rank Hits", f"{summary['high_rank_hits']:,}", f"{summary['high_rank_plus_2']:,} at ≥{floor + 2} · {summary['high_rank_plus_4']:,} at ≥{floor + 4}", accent="amber", detail_title=f"Specializations with exact rank or rigorous specialization lower bound ≥{floor}.")
    return summary



def _render_dashboard(db, ctx):
    """Render the complete Dashboard as one continuous research workspace."""
    diagnostics_result = st.session_state.get("diagnostics_result_cache")
    research = dashboard_research_snapshot(db)
    operations = dashboard_operations_snapshot(
        db,
        ctx.project_root,
        diagnostics_result=diagnostics_result,
        db_path=ctx.db_path,
    )
    attention = dashboard_attention_snapshot(
        db,
        ctx.project_root,
        campaign=None,
        rank_conflicts=research["rank_conflicts"],
        diagnostics_result=diagnostics_result,
        operations=operations,
    )

    # One Dashboard: every existing high-value widget is visible without
    # switching between Overview / Research / Operations views.
    _summary_metrics(research)
    _evidence_summary_row(db, research["certification"])

    left, right = st.columns([2.05, 0.95], gap="medium")
    with left:
        _local_frontier_panel(
            research["frontier"],
            research["searched_candidates"],
        )
        _operations_state_panel(operations)
        render_research_charts(db)
    with right:
        _quick_actions_panel()
        _icarm_leaderboard(db)
        _attention_panel(ctx.db_path, attention)
        render_plugins_panel(db, ctx, health=operations["plugins"])
        _latest_activity(operations["recent_jobs"])


def page(db, ctx):
    _dashboard_header(ctx)
    render_feature_hook(db, ctx, "dashboard.after_header")
    _render_dashboard(db, ctx)
