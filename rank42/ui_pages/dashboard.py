from __future__ import annotations

import html
import base64
import json
import math
from pathlib import Path
from fractions import Fraction
from datetime import datetime, timezone

import streamlit as st

from rank42 import __version__
from rank42.catalog import (
    catalog_status,
    external_torsion_factors,
    external_torsion_label,
    icarm_torsion_groups,
    list_external_curves,
    torsion_key_from_factors,
    torsion_label_from_factors,
)
from rank42.dashboard_snapshot import (
    acknowledge_dashboard_attention,
    dashboard_attention_snapshot,
    dashboard_operations_snapshot,
    dashboard_research_snapshot,
)
from rank42.db import connect_existing
from rank42.feature_hooks import render_feature_hook
from rank42.torsion import MAZUR_TORSION_CHOICES, torsion_invariants_from_label
from rank42.ui_components import region
from .common import PAGE_ICONS, launch, setting, title
from .dashboard_charts import render_research_charts
from .dashboard_plugins import render_plugins_panel
from .dashboard_sections import _torsion_research_data


_DASHBOARD_NAV_GROUPS = (
    ("Explore", ("Dashboard", "Curves", "Points", "Models")),
    ("Search", ("Candidates", "Search", "Auto", "Target", "Pipelines")),
    ("Analysis", ("Casebook", "Rank Lab", "Tools")),
    ("Manage", ("Jobs", "Campaigns", "Plugins", "Settings")),
)
_COMMAND_BAR_JS = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "tools_command_bar.js"
).read_text(encoding="utf-8")
_DASHBOARD_COMMAND_BAR = st.components.v2.component(
    "rh_dashboard_command_bar", js=_COMMAND_BAR_JS, isolate_styles=False
)


def _go_to(page):
    st.session_state["rh_page"] = page


def _route_icon(page, fallback=":material/arrow_forward:"):
    return PAGE_ICONS.get(str(page), fallback)


def _frontier_curve_art_html(db, curve_id, *, class_name=""):
    """Draw the best curve's real locus and its stored independent points."""
    curve = db.execute(
        "SELECT a_invariants_json FROM curves WHERE id=?", (int(curve_id),)
    ).fetchone()
    if curve is None or not curve["a_invariants_json"]:
        return ""
    try:
        a1, a2, a3, a4, a6 = (
            Fraction(value) for value in json.loads(curve["a_invariants_json"])
        )
        b2 = a1 * a1 + 4 * a2
        b4 = a1 * a3 + 2 * a4
        b6 = a3 * a3 + 4 * a6
        # Complete the square and translate x to obtain Y^2 = X^3 + AX + B.
        A = b4 / 2 - b2 * b2 / 48
        B = b6 / 4 - b2 * b4 / 24 + b2 ** 3 / 864

        def log_abs(value):
            if not value:
                return float("-inf")
            return math.log(abs(value.numerator)) - math.log(abs(value.denominator))

        log_scale = max(0.0, log_abs(A) / 4, log_abs(B) / 6)

        def scaled(value, power):
            if not value:
                return 0.0
            exponent = log_abs(value) - power * log_scale
            if exponent > 700:
                return math.copysign(float("inf"), -1 if value < 0 else 1)
            if exponent < -745:
                return 0.0
            return (-1 if value < 0 else 1) * math.exp(exponent)

        aa, bb = scaled(A, 4), scaled(B, 6)
        width, height = 190.0, 70.0
        xmin, xmax, ymax = -2.1, 2.1, 3.5

        def project(x, y):
            return (
                (x - xmin) * width / (xmax - xmin),
                height / 2 - y * (height / 2 - 2) / ymax,
            )

        pieces = []
        samples = 88
        for sign in (-1, 1):
            previous = None
            for index in range(samples + 1):
                x = xmin + (xmax - xmin) * index / samples
                y2 = x * x * x + aa * x + bb
                if y2 < 0:
                    previous = None
                    continue
                current = project(x, sign * math.sqrt(y2))
                if previous is not None:
                    x0, y0 = previous
                    x1, y1 = current
                    dx, dy = x1 - x0, y1 - y0
                    length = math.hypot(dx, dy)
                    angle = math.degrees(math.atan2(dy, dx))
                    pieces.append(
                        '<span aria-hidden="true" style="position:absolute;'
                        f'left:{x0:.2f}px;top:{y0:.2f}px;width:{length:.2f}px;height:2px;'
                        'transform-origin:0 50%;'
                        f'transform:rotate({angle:.2f}deg);border-radius:2px;'
                        'background:linear-gradient(90deg,#397BFF,#7867E8 58%,#21A7C7);'
                        'box-shadow:0 0 6px #397BFF77"></span>'
                    )
                previous = current

        points = db.execute(
            """SELECT x,y FROM points
               WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1""",
            (int(curve_id),),
        ).fetchall()
        visible_points = 0
        for row in points:
            x = Fraction(row["x"]) + b2 / 12
            y = Fraction(row["y"]) + (a1 * Fraction(row["x"]) + a3) / 2
            px, py = scaled(x, 2), scaled(y, 3)
            if xmin <= px <= xmax and -ymax <= py <= ymax:
                dot_x, dot_y = project(px, py)
                pieces.append(
                    '<i aria-hidden="true" style="position:absolute;'
                    f'left:{dot_x - 2.7:.2f}px;top:{dot_y - 2.7:.2f}px;'
                    'width:5.4px;height:5.4px;border:1.4px solid #1557E8;'
                    'border-radius:50%;background:#fff;box-shadow:0 0 6px #397BFFAA"></i>'
                )
                visible_points += 1

        label = (
            f"Real curve for #{int(curve_id)} with {visible_points} stored rational "
            "points in view"
        )
        return (
            f'<div role="img" aria-label="{html.escape(label, quote=True)}" '
            f'title="{html.escape(label, quote=True)}" '
            f'class="rh-frontier-curve-art {html.escape(class_name, quote=True)}" '
            'style="position:relative;width:190px;height:70px;margin:1px auto 2px;overflow:visible">'
            + "".join(pieces)
            + "</div>"
        )
    except (ValueError, TypeError, ZeroDivisionError, OverflowError, json.JSONDecodeError):
        return ""


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
        ("interval", "Bounded interval"),
        ("none", "No rigorous bounds"),
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
        '<div class="rh-frontier-certification">'
        f'<div class="rh-frontier-cert-track">{"".join(segments)}</div>'
        f'<div class="rh-frontier-cert-legend">{"".join(legend)}</div>'
        '</div>'
    )


def _torsion_findings_html(db):
    rows = db.execute(
        """SELECT source_id, rank_lower_bound, raw_json
           FROM external_curves
           WHERE source='icarm' AND active=1
           ORDER BY rank_lower_bound DESC, source_id"""
    ).fetchall()
    groups = {}
    for row in rows:
        factors = external_torsion_factors(row)
        key = torsion_key_from_factors(factors)
        if key is None:
            continue
        group = groups.setdefault(
            key,
            {
                "label": torsion_label_from_factors(factors),
                "factors": factors,
                "count": 0,
                "best_rank": -1,
                "best_source_id": "",
            },
        )
        rank = int(row["rank_lower_bound"] or 0)
        group["count"] += 1
        if rank > group["best_rank"]:
            group["best_rank"] = rank
            group["best_source_id"] = str(row["source_id"])

    if not rows:
        return (
            '<section class="rh-torsion-findings">'
            '<div class="rh-torsion-heading"><div><span class="rh-dashboard-section-title">TORSION GROUPS</span>'
            '<p>Sync the ICARM catalog to see its torsion group findings.</p></div></div>'
            '</section>'
        )

    local_data = _torsion_research_data(db)
    local_groups = {}
    for record in local_data["record_rows"]:
        try:
            factors = torsion_invariants_from_label(record["torsion_label"])
        except (TypeError, ValueError):
            continue
        key = torsion_key_from_factors(factors)
        local = local_groups.setdefault(
            key,
            {
                "label": "Trivial" if not factors else " × ".join(f"C{x}" for x in factors),
                "count": 0,
                "best_exact": None,
                "best_lower": 0,
                "best_lower_only": 0,
                "best_lower_only_curve_id": None,
            },
        )
        local["count"] += int(record["retained_curves"] or 0)
        if record["best_exact"] is not None:
            exact = int(record["best_exact"])
            local["best_exact"] = max(local["best_exact"] or 0, exact)
        lower = int(record["best_lower"] or 0)
        local["best_lower"] = max(local["best_lower"], lower)
        lower_only = int(record["best_lower_only"] or 0)
        if lower_only > local["best_lower_only"]:
            local["best_lower_only"] = lower_only
            local["best_lower_only_curve_id"] = record.get("best_lower_only_curve_id")

    all_keys = set(groups) | set(local_groups)
    comparison = []
    for key in all_keys:
        external = groups.get(key)
        local = local_groups.get(key)
        label = (
            "Trivial" if key == "trivial" else
            external["label"] if external else local["label"]
        )
        local_exact = local["best_exact"] if local else None
        local_lower = int(local["best_lower"] or 0) if local else 0
        local_best = max(int(local_exact or 0), local_lower)
        local_best_curve = None
        if local and local_best == int(local["best_lower_only"] or 0):
            local_best_curve = local.get("best_lower_only_curve_id")
        comparison.append(
            {
                "key": key,
                "label": label,
                "local": local,
                "local_best": local_best,
                "local_exact": local_exact,
                "local_curve": local_best_curve,
                "external": external,
            }
        )
    torsion_order = {
        (): 0,
        **{(n,): n - 1 for n in range(2, 11)},
        (12,): 10,
        (2, 2): 11,
        (2, 4): 12,
        (2, 6): 13,
        (2, 8): 14,
    }
    comparison.sort(
        key=lambda item: torsion_order.get(
            tuple(torsion_invariants_from_label(item["label"])), 99
        )
    )

    local_leader = max(comparison, key=lambda item: item["local_best"], default=None)
    external_leader = max(
        comparison,
        key=lambda item: item["external"]["best_rank"] if item["external"] else -1,
        default=None,
    )
    status = catalog_status(db, "icarm")
    synced = _format_local_sync_time(status["fetched_at"] if status else None)

    torsion_cards = []
    for item in comparison:
        local = item["local"]
        external = item["external"]
        if local is None:
            local_rank = "—"
            local_detail = "no local torsion records"
            local_count = 0
        else:
            local_count = int(local["count"])
            if item["local_best"] > 0:
                exact_is_best = (
                    item["local_exact"] is not None
                    and int(item["local_exact"]) >= int(local["best_lower"] or 0)
                )
                local_rank = (
                    f'={item["local_best"]}' if exact_is_best
                    else f'≥{item["local_best"]}'
                )
                detail_bits = []
                if item["local_curve"] is not None:
                    detail_bits.append(f'#{int(item["local_curve"])}')
                if item["local_exact"] is not None:
                    detail_bits.append(f'exact {int(item["local_exact"])}')
                local_detail = " · ".join(detail_bits) or "best local evidence"
            else:
                local_rank = "—"
                local_detail = "no positive rank bound"
        if external is None:
            external_rank = "—"
            external_detail_row = "not in ICARM"
            external_count = 0
            delta = "Local only"
            delta_class = "local"
        else:
            external_count = int(external["count"])
            external_rank = f'≥{external["best_rank"]}'
            external_detail_row = f'#{external["best_source_id"]}'
            if item["local_best"] <= 0:
                delta = "ICARM only"
                delta_class = "external"
            else:
                difference = int(external["best_rank"]) - int(item["local_best"])
                if difference > 0:
                    delta = f'ICARM +{difference}'
                    delta_class = "external"
                elif difference < 0:
                    delta = f'Local +{-difference}'
                    delta_class = "local"
                else:
                    delta = "Tied"
                    delta_class = "tied"
        leader_class = (
            " is-leader"
            if item["key"] in {
                local_leader["key"] if local_leader else None,
                external_leader["key"] if external_leader else None,
            }
            else ""
        )
        torsion_cards.append(
            f'<article class="rh-torsion-card{leader_class}">'
            '<div class="rh-torsion-card-head">'
            f'<strong>{html.escape(item["label"])}</strong>'
            f'<span class="rh-torsion-delta rh-torsion-delta-{delta_class}">{html.escape(delta)}</span>'
            '</div>'
            '<div class="rh-torsion-card-sides">'
            '<div class="rh-torsion-card-side rh-torsion-card-local">'
            '<small>RANK HUNTER</small>'
            f'<strong>{html.escape(local_rank)}</strong>'
            f'<span>{local_count:,} local curves</span>'
            f'<em>{html.escape(local_detail)}</em>'
            '</div>'
            '<div class="rh-torsion-card-side rh-torsion-card-icarm">'
            '<small>ICARM BEST</small>'
            f'<strong>{html.escape(external_rank)}</strong>'
            f'<span>{external_count:,} curves</span>'
            f'<em>{html.escape(external_detail_row)}</em>'
            '</div></div></article>'
        )

    return (
        '<section class="rh-torsion-findings">'
        '<div class="rh-torsion-heading">'
        '<div><span class="rh-dashboard-section-title">TORSION GROUPS</span>'
        '</div>'
        f'<small>ICARM snapshot · synced {html.escape(synced)}</small>'
        '</div>'
        f'<div class="rh-torsion-cards">{"".join(torsion_cards)}</div>'
        '<p class="rh-torsion-note">Local ranks use persisted, conflict-free evidence; ICARM ranks are source-provided lower bounds. '
        'The comparison badge is the difference between each side’s best bound, often on different curves.</p>'
        '</section>'
    )


def _world_record_html(leader, fetched, *, show_title=True, empty_message=None):
    title_html = '<div class="rh-world-record-title">LEADERBOARD</div>' if show_title else ''
    if leader is None:
        return (
            '<div class="rh-world-record-card">' + title_html +
            '<div class="rh-world-record-empty">'
            f'<strong>—</strong><span>{html.escape(empty_message or "Sync ICARM to load its top-ranked curve")}</span></div>'
            '</div>'
        )
    rank = int(leader["rank_lower_bound"] or 0)
    source_id = html.escape(str(leader["source_id"]))
    torsion = (external_torsion_label(leader) or "unknown").capitalize()
    return (
        '<div class="rh-world-record-card">' + title_html +
        '<div class="rh-world-record-main">'
        f'<strong>≥{rank}</strong>'
        '<span><span class="rh-world-record-meta-top">'
        f'<b>{html.escape(torsion)}</b><small>ICARM · #{source_id}</small>'
        '</span>'
        f'<small class="rh-world-record-synced">synced {html.escape(fetched)}</small></span>'
        '</div>'
        '</div>'
    )


def _format_local_sync_time(value):
    try:
        timestamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        local = timestamp.astimezone()
        hour = local.strftime("%I").lstrip("0") or "12"
        return f"{local:%b} {local.day}, {local.year}, {hour}:{local:%M %p %Z}"
    except (TypeError, ValueError):
        return str(value or "—")


def _sync_icarm_catalog(db, ctx):
    py = setting(db, "science_python", ctx.detected_science_python())
    cmd = [py, "-m", "rank42.catalog", "--db", ctx.db_path, "sync", "--source", "icarm"]
    jid = launch(ctx, db, kind="catalog_sync", label="Sync ICARM catalog", command=cmd)
    st.toast(f"Started ICARM catalog sync job #{jid}.")


def _frontier_leaderboard(db, torsion_group=None):
    """Return the selected ICARM leader and destination for the hero."""
    status = catalog_status(db, "icarm")
    fetched = _format_local_sync_time(status["fetched_at"] if status is not None else None)
    torsion_key = (torsion_key_from_factors(torsion_invariants_from_label(torsion_group))
                   if torsion_group else None)
    leaders = list_external_curves(
        db, source="icarm", rank_at_least=0, torsion=torsion_key, limit=1,
    )
    leader = leaders[0] if leaders else None
    empty_message = ("No ICARM curve recorded for this torsion group"
                     if torsion_group else None)
    return _world_record_html(
        leader, fetched, show_title=False, empty_message=empty_message,
    ), (
        str(leader["source_url"] or "").strip() if leader is not None else ""
    )


def _dashboard_page_nav():
    groups = [
        {
            "label": label,
            "tools": [
                {
                    "name": page,
                    "icon": _route_icon(page).removeprefix(":material/").removesuffix(":"),
                    "description": f"Open {page}",
                }
                for page in pages
            ],
        }
        for label, pages in _DASHBOARD_NAV_GROUPS
    ]
    config = json.dumps({
        "selected": "Dashboard",
        "groups": groups,
        "rootId": "rh-dashboard-commandbar",
        "bridgePrefix": "rh-dashboard-route-",
        "controllerSlot": "__rhToolsCommandBar",
        "queryParam": None,
        "toolbarLabel": "Rank Hunter pages",
        "itemNoun": "pages",
        "currentNoun": "page",
        "mobileLabel": "Pages",
    }).replace("<", "\\u003c")
    st.html(
        '<div id="rh-dashboard-commandbar" class="rh-tools-commandbar-host" '
        'aria-label="Rank Hunter pages; current page Dashboard"></div>'
    )
    _DASHBOARD_COMMAND_BAR(data=json.loads(config), key="rh-dashboard-command-runtime")
    with st.container(key="rh-dashboard-command-bridge"):
        for _, pages in _DASHBOARD_NAV_GROUPS:
            for page in pages:
                slug = page.lower().replace(" ", "-")
                st.button(
                    page,
                    key=f"rh-dashboard-route-{slug}",
                    on_click=_go_to,
                    args=(page,),
                )


def _hero_torsion_label(group):
    if group in ("Any", "Trivial"):
        return group
    return " × ".join(f"Z/{factor}Z" for factor in torsion_invariants_from_label(group))


def _local_frontier_panel(rows, research, db, ctx, genus2_model_count=0,
                          torsion_group=None):

    with region("dashboard-local-frontier", border=False):
        options = ("Any", *MAZUR_TORSION_CHOICES)
        by_rank = {}
        for row in rows:
            exact = row["exact_rank"]
            lower = int(row["rigorous_lower"] or 0)
            if exact is not None:
                rank_value = int(exact)
                kind = "exact"
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

        best_rank = max(by_rank, default=None)
        zero_rank_curve_id = research.get("zero_rank_curve_id")
        if best_rank is None and zero_rank_curve_id is not None:
            best_rank = 0
        best_exact = research["rank"]["best_exact"]
        best_exact_label = str(best_exact) if best_exact is not None else "—"
        best_kind = "exact" if best_rank == 0 or (
            best_rank is not None and by_rank[best_rank]["exact"]
        ) else "lower"
        best_row = ({"id": zero_rank_curve_id} if best_rank == 0
                    else by_rank[best_rank][f"strongest_{best_kind}"][1]
                    if best_rank is not None else None)
        prefix = "=" if best_kind == "exact" else "&ge;"
        empty_curve_label = (
            "No certified curve yet" if research["rank"]["curve_count"]
            else "No stored curves in this group"
        )
        leaderboard_html, leaderboard_url = _frontier_leaderboard(db, torsion_group)
        left, right = st.columns([0.82, 1.18], gap="medium", vertical_alignment="top")
        with left:
            with st.container(key="rh-dashboard-hero-torsion-row"):
                chosen = st.menu_button(
                    _hero_torsion_label(torsion_group) if torsion_group else "Any",
                    options,
                    key="rh-dashboard-hero-torsion-menu",
                    help="Show hero results for one exact rational torsion group",
                    type="tertiary",
                    width="content",
                    format_func=_hero_torsion_label,
                )
                if chosen is not None:
                    st.session_state["rh-dashboard-hero-torsion"] = (
                        None if chosen == "Any" else chosen
                    )
                    st.rerun()
            st.html(
                '<section class="rh-frontier-shell rh-frontier-presentation rh-frontier-best" aria-label="Research Frontier">'
                '<div class="rh-frontier-best-top">'
                '<div class="rh-frontier-best-score"><span class="rh-frontier-eyebrow">BEST CERTIFIED RANK</span>'
                f'<div class="rh-frontier-best-value"><span>{prefix if best_row else ""}</span><strong>{best_rank if best_rank is not None else "—"}</strong></div>'
                + '</div>'
                + '<div class="rh-frontier-curve-slot">'
                + (_frontier_curve_art_html(db, int(best_row["id"])) if best_row
                   else f'<span class="rh-frontier-empty-curve">{empty_curve_label}</span>')
                + '</div>'
                + '</div>'
                + f'<div class="rh-frontier-best-exact"><span>BEST EXACT RANK</span><strong>{best_exact_label}</strong></div>'
                + '</section>'
            )
            with st.container(key="rh-dashboard-hero-leaderboard"):
                record_col, sync_col, open_col = st.columns(
                    [5, 0.55, 0.55], gap="small", vertical_alignment="center",
                )
                with record_col:
                    st.html('<div class="rh-frontier-leaderboard">' + leaderboard_html + '</div>')
                with sync_col:
                    if st.button(
                        "", key="rh-dashboard-hero-leader-sync", icon=":material/sync:",
                        help="Sync ICARM catalog", type="tertiary", width="content",
                    ):
                        _sync_icarm_catalog(db, ctx)
                with open_col:
                    st.link_button(
                        "", leaderboard_url or "#", key="rh-dashboard-hero-leader-open",
                        icon=":material/open_in_new:",
                        help="Open this ICARM curve" if leaderboard_url else "No ICARM curve is available yet",
                        type="tertiary", width="content", disabled=not bool(leaderboard_url),
                    )
        with right:
            st.html(
                '<section class="rh-frontier-shell rh-frontier-presentation rh-frontier-stat-column" aria-label="Research statistics">'
                f'<div class="rh-frontier-stats">{_frontier_stats_html(research, genus2_model_count)}</div>'
                + _frontier_certification_html(research["certification"])
                + '</section>'
            )


def _torsion_findings_panel(db):
    with region("dashboard-torsion-findings", border=True):
        st.html(_torsion_findings_html(db))


def _genus2_census(db):
    """Read saved genus-two models and their strongest completed run evidence."""
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genus2_models'"
    ).fetchone()
    if exists is None:
        return {
            "count": 0, "best_lower": None, "latest": None,
            "exact_count": 0, "lower_only_count": 0, "unknown_count": 0,
            "rank_counts": [],
        }
    models = db.execute(
        "SELECT id,label,source_curve_id FROM genus2_models ORDER BY id DESC"
    ).fetchall()
    model_ids = {int(row["id"]) for row in models}
    evidence = {model_id: {"lower": 0, "exact": set()} for model_id in model_ids}
    if model_ids:
        runs = db.execute(
            """SELECT r.target_json,c.rigorous_lower,c.exact_rank
               FROM search_pipeline_runs r
               JOIN search_pipeline_candidates c ON c.run_id=r.id
               WHERE r.target_mode='genus2_jacobian'
                 AND r.status IN ('completed','target_hit')
                 AND c.status='completed'"""
        ).fetchall()
        for row in runs:
            try:
                model_id = int(json.loads(row["target_json"] or "{}").get("genus2_model_id"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if model_id not in evidence:
                continue
            evidence[model_id]["lower"] = max(
                evidence[model_id]["lower"], int(row["rigorous_lower"] or 0)
            )
            if row["exact_rank"] is not None:
                evidence[model_id]["exact"].add(int(row["exact_rank"]))
    exact_count = lower_only_count = unknown_count = 0
    certified_bounds = []
    rank_counts = {}
    for item in evidence.values():
        lower = item["lower"]
        exacts = item["exact"]
        if len(exacts) == 1 and lower <= next(iter(exacts)):
            rank_value = next(iter(exacts))
            exact_count += 1
            certified_bounds.append(rank_value)
            rank_counts.setdefault(rank_value, {"exact": 0, "lower": 0})["exact"] += 1
        elif lower > 0 and not exacts:
            lower_only_count += 1
            certified_bounds.append(lower)
            rank_counts.setdefault(lower, {"exact": 0, "lower": 0})["lower"] += 1
        else:
            unknown_count += 1
    best_lower = max(certified_bounds, default=0) or None
    return {
        "count": len(model_ids), "best_lower": best_lower,
        "exact_count": exact_count, "lower_only_count": lower_only_count,
        "unknown_count": unknown_count,
        "rank_counts": [
            {"rank": rank_value, **counts}
            for rank_value, counts in sorted(rank_counts.items(), reverse=True)
        ],
    }


def _genus_census_html(research, genus2):
    """Render genus coverage in the same visual system as the dashboard cards."""
    rank = research["rank"]
    total = int(rank["curve_count"])

    genus1_detail = (
        f"Curves · best rank ≥{int(rank['best_rigorous'])}"
        if rank.get("best_rigorous") is not None
        else "Curves · no certified rank" if total else "No curves yet"
    )
    genus2_count = int(genus2["count"])
    genus2_detail = (
        f"Models · best rank ≥{int(genus2['best_lower'])}"
        if genus2.get("best_lower") is not None
        else "Models · no certified rank" if genus2_count else "No models yet"
    )
    genus_cards = []
    for genus, value, detail, accent in (
        (1, f"{total:,}", genus1_detail, "blue"),
        (2, f"{genus2_count:,}", genus2_detail, "blue" if genus2_count else "violet"),
    ):
        genus_cards.append(
            f'<div class="rh-dashboard-metric rh-dashboard-metric-{accent}">'
            f'<div class="rh-dashboard-metric-head"><span>GENUS {genus}</span></div>'
            f'<strong>{html.escape(value)}</strong>'
            f'<small>{html.escape(detail)}</small></div>'
        )

    def rank_chart(rows, unit):
        if not rows:
            return '<p class="rh-frontier-chart-note">No certified ranks yet.</p>'
        maximum = max(int(row["exact"]) + int(row["lower"]) for row in rows)
        bars = []
        for row in sorted(rows, key=lambda item: int(item["rank"])):
            rank_value = int(row["rank"])
            exact = int(row["exact"])
            lower = int(row["lower"])
            count = exact + lower
            height = 10 + round(24 * math.log1p(count) / math.log1p(maximum))
            tooltip = html.escape(
                f"Rank {rank_value} · exact: {exact:,} · certified lower bound: {lower:,}",
                quote=True,
            )
            bars.append(
                f'<div class="rh-genus-rank-column" title="{tooltip}" '
                f'aria-label="{tooltip}">'
                f'<small class="rh-genus-rank-count">{count:,}</small>'
                f'<span class="rh-genus-rank-bar" style="--rh-genus-bar-height:{height}px">'
                + (f'<i class="rh-genus-rank-exact" style="flex:{exact}"></i>' if exact else "")
                + (f'<i class="rh-genus-rank-lower" style="flex:{lower}"></i>' if lower else "")
                + '</span>'
                f'<strong>{rank_value}</strong>'
                '</div>'
            )
        return (
            f'<div class="rh-genus-rank-chart" role="group" aria-label="Rank counts by {unit}">'
            f'<div class="rh-genus-rank-columns">{"".join(bars)}</div></div>'
        )

    genus1_rank_counts = research.get("rank_counts") or []
    genus1_certified = sum(int(row["exact"]) + int(row["lower"]) for row in genus1_rank_counts)
    genus1_unknown = max(0, total - genus1_certified)
    genus1_rank_html = rank_chart(genus1_rank_counts, "curve")
    genus2_rank_html = rank_chart(genus2["rank_counts"], "model")

    return (
        '<section class="rh-frontier-shell rh-frontier-presentation rh-genus-census" '
        'aria-label="Rank landscape by genus">'
        '<div class="rh-dashboard-section-heading">'
        '<span class="rh-dashboard-section-title">RANK LANDSCAPE</span>'
        '<small>Local elliptic curves and genus 2 models</small></div>'
        '<div class="rh-genus-census-rows">'
        '<section class="rh-genus-census-row">'
        f'<div class="rh-genus-census-card">{genus_cards[0]}</div>'
        f'<div class="rh-genus-census-chart">{genus1_rank_html}'
        f'<small class="rh-genus-census-unknown">{genus1_unknown:,} without certified rank</small></div>'
        '</section>'
        '<section class="rh-genus-census-row rh-genus-census-row-two">'
        f'<div class="rh-genus-census-card">{genus_cards[1]}</div>'
        f'<div class="rh-genus-census-chart">{genus2_rank_html}'
        f'<small class="rh-genus-census-unknown">{int(genus2["unknown_count"]):,} without certified rank</small></div>'
        '</section>'
        '</div>'
        '<div class="rh-genus-rank-legend">'
        '<span><i class="rh-genus-rank-exact"></i>Exact rank</span>'
        '<span><i class="rh-genus-rank-lower"></i>Certified lower bound</span>'
        '<small>Bar height uses a log scale · count of curves above each bar</small>'
        '</div>'
        '</section>'
    )


def _genus_census_panel(db, research, genus2=None):
    with region("dashboard-genus-census", border=False):
        st.html(_genus_census_html(research, genus2 or _genus2_census(db)))


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
    dispatcher = snapshot["dispatcher"]

    dispatcher_healthy = bool(dispatcher.get("healthy"))
    dispatcher_label = str(dispatcher.get("label") or "Offline")
    dispatcher_detail = str(dispatcher.get("detail") or "").strip()
    queue_waiting = int(snapshot["queue_waiting"])

    with region("dashboard-operations-state", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading">'
            '<span class="rh-dashboard-section-title">OPERATIONS</span>'
            f'<span class="rh-dashboard-plugin-health {"ok" if dispatcher_healthy else "warning"}" '
            f'title="{html.escape(dispatcher_detail, quote=True)}">'
            f'Dispatcher {html.escape(dispatcher_label)}</span>'
            '</div>'
            '<div class="rh-dashboard-pipeline-stats">'
            f'<div><span>Candidate backlog</span><strong>{int(candidates["backlog"]):,}</strong></div>'
            f'<div><span>Pipeline Runs active</span><strong>{int(runs["active"]):,}</strong></div>'
            f'<div><span>Jobs active</span><strong>{int(jobs["active"]):,}</strong></div>'
            f'<div><span>Queue waiting</span><strong>{queue_waiting:,}</strong></div>'
            '</div>'
        )
        c1, c2, c3, c4 = st.columns(4, gap="small")
        with c1:
            st.button(
                " ",
                key="rh-dashboard-operations-pipelines",
                width="stretch",
                icon=_route_icon("Pipelines"),
                help="Open Pipelines",
                on_click=_go_to,
                args=("Pipelines",),
            )
        with c2:
            st.button(
                " ",
                key="rh-dashboard-operations-pools",
                width="stretch",
                icon=_route_icon("Candidate Pools"),
                help="Open Candidate Pools",
                on_click=_go_to,
                args=("Candidate Pools",),
            )
        with c3:
            st.button(
                " ",
                key="rh-dashboard-operations-jobs",
                width="stretch",
                icon=":material/work_history:",
                help="Open Jobs and Queue",
                on_click=_go_to_jobs_section,
                args=("Queue",),
            )
        with c4:
            st.button(
                " ",
                key="rh-dashboard-operations-schedules",
                width="stretch",
                icon=":material/schedule:",
                help="Open Schedules",
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
    logo_path = Path(ctx.project_root) / "assets" / "rank-hunter-logo.svg"
    logo = logo_path.read_text(encoding="utf-8").replace('fill="#006DFC"', 'fill="#000000"')
    logo_uri = "data:image/svg+xml;base64," + base64.b64encode(logo.encode("utf-8")).decode("ascii")
    with region("dashboard-brand-header", border=False):
        st.html(
            '<div class="rh-dashboard-brand-header">'
            f'<span class="rh-dashboard-brand-logo"><img src="{logo_uri}" alt="Rank Hunter logo" width="24" height="24"></span>'
            '<h1>Rank Hunter</h1>'
            f'<span class="rh-dashboard-version-badge">v{html.escape(__version__)}</span>'
            '</div>'
        )


def _frontier_stats_html(research, genus2_model_count=0):
    summary = research["rank"]
    pencils = research["pencils"]
    conductor = research["best_rank_conductor"]
    resolution = research["rank_resolution"]
    jump = research["rank_jump"]
    jump_value = f"+{jump['gain']}" if jump else "—"
    jump_detail = (
        f"Generic ≥{jump['generic_lower']} → certified ≥{jump['specialization_lower']} · #{jump['curve_id']}"
        if jump else "No linked certified family baseline"
    )
    conductor_value = f"{conductor['log_n']:.2f}" if conductor['log_n'] is not None else "—"
    conductor_detail = (
        f"Curve #{conductor['curve_id']} · rank ≥{conductor['rank']}"
        if conductor['curve_id'] is not None else
        (f"Rank ≥{conductor['rank']} · conductor not stored" if conductor['rank'] is not None else "No certified rank yet")
    )
    conductor_tooltip = (
        "Natural logarithm of the smallest stored conductor at the highest certified specialization rank. "
        f"Conductors available for {conductor['known_count']} of {conductor['curve_count']} curves at that rank. "
        "Estimates, upper bounds and conflicting conductor values are excluded."
    )
    metrics = [
        ("Curves", f"{summary['curve_count']:,}", f"{int(research['point_count']):,} points | {int(research['independent_point_count']):,} certified independent", "violet", "Stored points; independent count uses one strongest unconditional certified subgroup per curve, excluding conflicting rank records and duplicate growth certificates."),
        ("Smallest log N at Best Rank", conductor_value, conductor_detail, "amber", conductor_tooltip),
        ("Research Portfolio", f"{pencils['explored']:,}" if pencils['available'] else "—", f"{int(genus2_model_count):,}", "blue", "Distinct registered fibrations with at least one stored specialization, alongside the number of saved genus 2 models."),
        ("Exact Ranks", f"{summary['exact_count']:,}", f"{summary['exact_pct']:.1f}% of retained curves", "green", "Persisted exact ranks only: rigorous lower and upper bounds meet."),
        ("Unresolved Ranks", f"{resolution['unresolved_count']:,}", f"{resolution['without_upper_count']:,} without a proven upper bound", "violet", "Curves with a rigorous positive specialization lower bound whose exact rank remains unknown; conflicting rank records are excluded."),
        ("Biggest Rank Jump", jump_value, jump_detail, "blue", "Largest gain in certified lower bound over a linked pencil's certified generic baseline. The difference compares proven lower bounds; it does not establish an exact generic rank or an exact rank increase."),
    ]
    cards = []
    for label, value, detail, accent, tooltip in metrics:
        value_html = f'<strong>{html.escape(value)}</strong>'
        if label == "Curves":
            detail_html = (
                '<div class="rh-frontier-point-summary">'
                f'<span><strong>{int(research["point_count"]):,}</strong> Points</span>'
                '<i>·</i>'
                f'<span><strong>{int(research["independent_point_count"]):,}</strong> Independent</span>'
                '</div>'
            )
        elif label == "Research Portfolio":
            value_html = (
                '<div class="rh-frontier-metric-pair">'
                f'<div><strong>{html.escape(value)}</strong><small>Pencils Explored</small></div>'
                f'<div><strong>{html.escape(detail)}</strong><small>Models (Genus 2)</small></div>'
                '</div>'
            )
            detail_html = ""
        else:
            detail_html = f'<small>{html.escape(detail)}</small>'
        cards.append(
            f'<div class="rh-dashboard-metric rh-dashboard-metric-{accent}" title="{html.escape(tooltip, quote=True)}">'
            f'<div class="rh-dashboard-metric-head"><span>{html.escape(label)}</span></div>'
            f'{value_html}{detail_html}</div>'
        )
    return "".join(cards)



def _render_dashboard(db, ctx):
    """Render the complete Dashboard as one continuous research workspace."""
    diagnostics_result = st.session_state.get("diagnostics_result_cache")
    torsion_group = st.session_state.get("rh-dashboard-hero-torsion")
    if torsion_group not in MAZUR_TORSION_CHOICES:
        torsion_group = None
    research = dashboard_research_snapshot(db, hero_torsion=torsion_group)
    genus2 = _genus2_census(db)
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

    _local_frontier_panel(
        (research["hero"] if torsion_group else research)["frontier"],
        research["hero"] if torsion_group else research,
        db,
        ctx,
        research["hero"]["genus2_model_count"] if torsion_group else genus2["count"],
        torsion_group=torsion_group,
    )
    _genus_census_panel(db, research, genus2)
    _torsion_findings_panel(db)
    left, right = st.columns([2.05, 0.95], gap="small")
    with left:
        render_research_charts(db)
    with right:
        _attention_panel(ctx.db_path, attention)
        _operations_state_panel(operations)
        render_plugins_panel(db, ctx, health=operations["plugins"])
        _latest_activity(operations["recent_jobs"])


def page(db, ctx):
    _dashboard_header(ctx)
    _dashboard_page_nav()
    render_feature_hook(db, ctx, "dashboard.after_header")
    _render_dashboard(db, ctx)
