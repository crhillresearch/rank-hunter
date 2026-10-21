from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from rank42.db import connect_existing
from rank42.manage_store import (
    active_campaign,
    campaign_progress_summary,
    create_campaign,
    list_campaigns,
    set_current_campaign,
    update_campaign,
)
from rank42.plugins import discover_plugins
from rank42.ui_components import rh_key
from .common import title


CAMPAIGN_MANAGER_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "campaign_manager"
)
_CAMPAIGN_MANAGER = components.declare_component(
    "rank42_campaign_manager",
    path=str(CAMPAIGN_MANAGER_COMPONENT_DIR),
)


def campaign_status_label(status):
    status = str(status or "")
    return {
        "active": "Open",
        "paused": "Paused",
        "completed": "Completed",
        "archived": "Archived",
    }.get(status, status.replace("_", " ").title())


def campaign_status_value(label):
    value = str(label or "").strip().lower()
    if value in {"open", "active"}:
        return "active"
    return value


def _discover_campaign_plugins(project_root):
    """Honor the pre-extraction Campaigns-page plugin discovery patch point."""
    import sys

    legacy_page = sys.modules.get("rank42.ui_pages.campaigns_page")
    provider = (
        getattr(legacy_page, "discover_plugins", discover_plugins)
        if legacy_page is not None
        else discover_plugins
    )
    return provider(project_root)


def campaign_family_choices(
    project_root,
    *,
    current_plugin_id=None,
    current_variant_id=None,
    current_family=None,
):
    """Return stable Family choices without asking users for internal plugin IDs."""
    choices = [
        {
            "key": "",
            "plugin_id": None,
            "variant_id": None,
            "family": None,
            "label": "Unspecified / choose later",
        }
    ]
    for plugin in _discover_campaign_plugins(project_root):
        if getattr(plugin, "plugin_type", None) != "family":
            continue
        variants = tuple(getattr(plugin, "variants", ()) or ())
        for variant in variants:
            family = str(
                getattr(variant, "curve_family_name", None)
                or (
                    getattr(variant, "id", "")
                    if len(variants) > 1
                    else getattr(plugin, "name", "")
                )
            ).strip()
            if not family:
                continue
            variant_id = str(getattr(variant, "id", "") or "default")
            label = str(
                getattr(plugin, "name", getattr(plugin, "id", "Family"))
            )
            if len(variants) > 1:
                label += f" · {getattr(variant, 'name', variant_id)}"
            choices.append(
                {
                    "key": f"{plugin.id}:{variant_id}",
                    "plugin_id": str(plugin.id),
                    "variant_id": variant_id,
                    "family": family,
                    "label": label,
                }
            )

    wanted_plugin = str(current_plugin_id or "").strip() or None
    wanted_variant = str(current_variant_id or "").strip() or None
    wanted_family = str(current_family or "").strip() or None
    if wanted_plugin or wanted_family:
        match = next(
            (
                rec
                for rec in choices
                if rec["plugin_id"] == wanted_plugin
                and (
                    (
                        wanted_variant
                        and rec["variant_id"] == wanted_variant
                    )
                    or (
                        not wanted_variant
                        and rec["family"] == wanted_family
                    )
                )
            ),
            None,
        )
        if match is None:
            choices.append(
                {
                    "key": "__existing__",
                    "plugin_id": wanted_plugin,
                    "variant_id": wanted_variant,
                    "family": wanted_family,
                    "label": (
                        "Current unavailable selection · "
                        + " / ".join(
                            value
                            for value in (
                                wanted_plugin,
                                wanted_variant,
                                wanted_family,
                            )
                            if value
                        )
                    ),
                }
            )
    return choices


def campaign_family_selector(
    ctx,
    *,
    key,
    current_plugin_id=None,
    current_variant_id=None,
    current_family=None,
):
    choices = _campaign_family_choices(
        ctx.project_root,
        current_plugin_id=current_plugin_id,
        current_variant_id=current_variant_id,
        current_family=current_family,
    )
    by_key = {rec["key"]: rec for rec in choices}
    wanted_plugin = str(current_plugin_id or "").strip() or None
    wanted_variant = str(current_variant_id or "").strip() or None
    wanted_family = str(current_family or "").strip() or None
    current_key = next(
        (
            rec["key"]
            for rec in choices
            if rec["plugin_id"] == wanted_plugin
            and (
                (
                    wanted_variant
                    and rec["variant_id"] == wanted_variant
                )
                or (
                    not wanted_variant
                    and rec["family"] == wanted_family
                )
            )
        ),
        "",
    )
    keys = list(by_key)
    selected = st.selectbox(
        "Family",
        keys,
        index=keys.index(current_key) if current_key in keys else 0,
        format_func=lambda value: by_key[value]["label"],
        key=key,
        help=(
            "Choose an installed Family plugin/variant. Rank Hunter stores the "
            "exact plugin ID, variant ID, and canonical family name for campaign "
            "provenance. You can leave this unspecified."
        ),
    )
    return by_key[selected]


def campaign_label(row):
    target = (
        f" · goal ≥{int(row['target_rank'])}"
        if row["target_rank"] is not None
        else ""
    )
    return (
        f"#{int(row['id'])} · {row['name']} · "
        f"{campaign_status_label(row['status'])}{target}"
    )


@st.dialog("Create campaign", width="large")
def create_campaign_dialog(ctx):
    with st.form("campaign-create-form", clear_on_submit=False):
        name = st.text_input(
            "Name",
            placeholder="ICARM 302 — Rank 32 Hunt",
        )
        objective = st.text_area(
            "Research objective",
            placeholder=(
                "Find a specialization with rigorous rank ≥32 and a replayable "
                "witness basis."
            ),
        )
        c1, c2 = st.columns([0.7, 2.3], vertical_alignment="bottom")
        with c1:
            target_rank = st.number_input(
                "Target rank",
                min_value=0,
                value=0,
                step=1,
                help="Use 0 when the campaign has no numeric rank target.",
            )
        with c2:
            family_choice = _campaign_family_selector(
                ctx,
                key="campaign-create-family",
            )
        notes = st.text_area(
            "Notes",
            placeholder="Strategy changes, hypotheses, handoff notes…",
        )
        submitted = st.form_submit_button(
            "Create campaign",
            type="primary",
            width="stretch",
        )

    if not submitted:
        return
    if not name.strip():
        st.error("Campaign name is required.")
        return

    live = connect_existing(ctx.db_path)
    try:
        cid = create_campaign(
            live,
            name=name.strip(),
            objective=objective,
            target_rank=(
                None if int(target_rank) <= 0 else int(target_rank)
            ),
            plugin_id=family_choice["plugin_id"],
            variant_id=family_choice["variant_id"],
            family=family_choice["family"],
            notes=notes,
        )
        set_current_campaign(live, cid)
    finally:
        live.close()
    st.session_state["manage_campaign_id"] = int(cid)
    st.toast(f"Created campaign #{cid} and set it as current.")
    st.rerun()


def campaign_row_meta(row, *, is_current):
    bits = [f"#{int(row['id'])}"]
    if is_current:
        bits.append("Current")
    bits.append(_campaign_status_label(row["status"]))
    if row["target_rank"] is not None:
        bits.append(f"target ≥{int(row['target_rank'])}")
    if row["family"]:
        bits.append(str(row["family"]))
    return " · ".join(bits)


def campaign_component_event(payload, *, key):
    if not isinstance(payload, dict):
        return None
    nonce = payload.get("nonce")
    nonce_key = f"campaign_component_nonce_{key}"
    if (
        nonce is None
        or str(st.session_state.get(nonce_key)) == str(nonce)
    ):
        return None
    st.session_state[nonce_key] = str(nonce)
    return str(payload.get("action") or "")


def campaign_add_button(db, ctx):
    if st.button(
        "New Campaign",
        icon=":material/add:",
        width="content",
        key="campaign-manager-add",
    ):
        _create_campaign_dialog(ctx)


def _component_theme_palette():
    palette = st.session_state.get("_rh_theme_palette") or {}
    return dict(palette) if isinstance(palette, dict) else {}


def campaign_row(db, row, *, current_id):
    campaign_id = int(row["id"])
    is_current = (
        current_id is not None
        and campaign_id == int(current_id)
    )
    status = str(row["status"] or "")
    progress = campaign_progress_summary(db, campaign_id)
    payload = _CAMPAIGN_MANAGER(
        mode="row",
        name=str(row["name"]),
        meta=_campaign_row_meta(row, is_current=is_current),
        objective=str(row["objective"] or ""),
        progress_label=str(progress["label"]),
        progress_fraction=progress["fraction"],
        progress_blocked=bool(progress["blocked"]),
        status=status,
        active=bool(is_current),
        palette=_component_theme_palette(),
        key=f"campaign-manager-row-{campaign_id}",
        default=None,
    )
    action = _campaign_component_event(
        payload,
        key=f"row-{campaign_id}",
    )
    if not action:
        return

    if action == "open":
        st.session_state["manage_campaign_id"] = campaign_id
        st.rerun()

    try:
        if action == "activate":
            set_current_campaign(db, campaign_id)
        elif action == "pause":
            update_campaign(db, campaign_id, status="paused")
        elif action in {"resume", "reopen"}:
            update_campaign(db, campaign_id, status="active")
            set_current_campaign(db, campaign_id)
        elif action == "complete":
            update_campaign(db, campaign_id, status="completed")
        elif action == "archive":
            update_campaign(db, campaign_id, status="archived")
        else:
            return
    except ValueError as exc:
        st.error(str(exc))
        return
    st.rerun()


_campaign_status_label = campaign_status_label
_campaign_status_value = campaign_status_value
_campaign_family_choices = campaign_family_choices
_campaign_family_selector = campaign_family_selector
_create_campaign_dialog = create_campaign_dialog
_campaign_row_meta = campaign_row_meta
_campaign_component_event = campaign_component_event
_campaign_add_button = campaign_add_button
_campaign_row = campaign_row


def campaign_page_style():
    st.html(
        """
        <style>
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) {
            width:100% !important;
            gap:.42rem !important;
            align-items:center !important;
            margin-bottom:.8rem;
        }
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(1) {
            flex:0 0 auto !important;
            width:auto !important;
            min-width:0 !important;
            overflow:visible !important;
        }
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(2) {
            flex:1 1 auto !important;
            width:auto !important;
            min-width:0 !important;
        }
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(3) {
            flex:0 0 auto !important;
            width:auto !important;
            min-width:max-content !important;
        }
        [class*="st-key-rh-campaigns-header-action"] {
            width:100% !important;
            min-height:3.15rem;
            display:flex;
            justify-content:flex-end;
            align-items:center;
        }
        [class*="st-key-rh-campaigns-header-action"] > div,
        [class*="st-key-rh-campaigns-header-action"] [data-testid="stVerticalBlock"] {
            width:auto !important;
            align-items:flex-end;
        }
        div[class*="st-key-rh-campaign-detail-tabs"] {
            margin-top:.75rem;
        }
        div[class*="st-key-rh-campaign-detail-tabs"] [data-testid="stSegmentedControl"] {
            margin-bottom:.7rem;
        }
        div[class*="st-key-rh-campaign-detail-tabs"] [data-testid="stSegmentedControl"] button,
        div[class*="st-key-rh-campaign-detail-tabs"] [role="radiogroup"] label {
            min-height:3rem !important;
            padding:.7rem 1.15rem !important;
            font-size:1rem !important;
            font-weight:700 !important;
        }
        div[class*="st-key-rh-campaign-progress-summary"] {
            margin-top:.45rem !important;
            margin-bottom:.8rem !important;
            background:rgba(127,127,127,.035);
        }
        div[class*="st-key-rh-campaign-progress-strategies"],
        div[class*="st-key-rh-campaign-progress-frontier"],
        div[class*="st-key-rh-campaign-progress-activity"],
        div[class*="st-key-rh-campaign-progress-pipelines"],
        div[class*="st-key-rh-campaign-progress-jobs"] {
            margin-top:.7rem !important;
            background:rgba(127,127,127,.018);
        }
        div[class*="st-key-rh-campaign-detail-tabs"] [data-testid="stExpander"] {
            margin-top:.45rem !important;
        }
        @media (max-width: 900px) {
            [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) {
                flex-wrap: wrap !important;
                align-items: flex-start !important;
                row-gap: .35rem !important;
            }
            [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(1) {
                flex: 1 1 18rem !important;
                width: auto !important;
                min-width: min(18rem,100%) !important;
            }
            [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(2) {
                display: none !important;
            }
            [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(3) {
                flex: 0 1 auto !important;
                width: auto !important;
                min-width: 0 !important;
            }
        }
        @media (max-width: 640px) {
            [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(1),
            [data-testid="stHorizontalBlock"]:has(.st-key-rh-campaigns-header-title) > [data-testid="stColumn"]:nth-child(3) {
                flex: 1 1 100% !important;
                width: 100% !important;
            }
            [class*="st-key-rh-campaigns-header-action"] {
                justify-content: flex-start !important;
                min-height: auto !important;
            }
        }
        </style>
        """
    )


def render_campaign_header(db, ctx):
    header_title, header_spacer, header_action = st.columns(
        [2.4, 5.5, 2.1],
        gap="small",
        vertical_alignment="center",
    )
    with header_title:
        with st.container(key=rh_key("campaigns-header-title")):
            title(
                "Campaigns",
                "Manage durable research objectives across Auto, Search, Target, "
                "Pipelines, and scheduled work.",
                "Manage",
            )
    with header_action:
        with st.container(key=rh_key("campaigns-header-action")):
            _campaign_add_button(db, ctx)


def campaign_search_blob(row):
    return " ".join(
        str(value or "")
        for value in (
            row["name"],
            row["objective"],
            row["family"],
            row["plugin_id"],
            row["variant_id"],
            row["notes"],
            row["status"],
            _campaign_status_label(row["status"]),
        )
    ).lower()


_campaign_search_blob = campaign_search_blob


def filtered_campaigns(
    rows,
    *,
    query="",
    status="All",
    sort="Recently updated",
    current_id=None,
):
    query = str(query or "").strip().lower()
    wanted_status = _campaign_status_value(status or "All")

    visible = []
    for row in rows:
        if query and query not in _campaign_search_blob(row):
            continue
        if (
            wanted_status != "all"
            and str(row["status"]).lower() != wanted_status
        ):
            continue
        visible.append(row)

    if sort == "Name A–Z":
        visible.sort(
            key=lambda row: (
                str(row["name"]).lower(),
                int(row["id"]),
            )
        )
    elif sort == "Name Z–A":
        visible.sort(
            key=lambda row: (
                str(row["name"]).lower(),
                int(row["id"]),
            ),
            reverse=True,
        )
    elif sort == "Newest created":
        visible.sort(
            key=lambda row: (
                str(row["created_at"]),
                int(row["id"]),
            ),
            reverse=True,
        )
    elif sort == "Oldest created":
        visible.sort(
            key=lambda row: (
                str(row["created_at"]),
                int(row["id"]),
            )
        )
    elif sort == "Target rank ↓":
        visible.sort(
            key=lambda row: (
                row["target_rank"] is None,
                -(
                    int(row["target_rank"])
                    if row["target_rank"] is not None
                    else 0
                ),
                -int(row["id"]),
            )
        )
    elif sort == "Target rank ↑":
        visible.sort(
            key=lambda row: (
                row["target_rank"] is None,
                (
                    int(row["target_rank"])
                    if row["target_rank"] is not None
                    else 0
                ),
                -int(row["id"]),
            )
        )
    else:
        visible.sort(
            key=lambda row: (
                str(row["updated_at"]),
                int(row["id"]),
            ),
            reverse=True,
        )

    if current_id is not None:
        current_rows = [
            row
            for row in visible
            if int(row["id"]) == int(current_id)
        ]
        if current_rows:
            visible = current_rows + [
                row
                for row in visible
                if int(row["id"]) != int(current_id)
            ]
    return visible


_filtered_campaigns = filtered_campaigns


def campaign_page_slice(rows, *, page, page_size):
    page_size = max(1, int(page_size))
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * page_size
    end = min(total, start + page_size)
    return rows[start:end], page, total_pages, start, end


_campaign_page_slice = campaign_page_slice


def render_campaign_landing(db, ctx):
    rows = list_campaigns(db, include_archived=True)
    current = active_campaign(db)
    current_id = None if current is None else int(current["id"])

    if not rows:
        st.info(
            "No campaigns yet. Use New Campaign above to start a durable "
            "research objective."
        )
        return

    search_col, status_col, sort_col, size_col = st.columns(
        [3.4, 1.25, 1.65, 0.9],
        vertical_alignment="bottom",
    )
    query = search_col.text_input(
        "Search campaigns",
        placeholder="Search name, objective, family, plugin, notes…",
        key="campaign-list-search",
    )
    status = status_col.selectbox(
        "Status",
        ["All", "Open", "Paused", "Completed", "Archived"],
        key="campaign-list-status",
    )
    sort = sort_col.selectbox(
        "Sort",
        [
            "Recently updated",
            "Newest created",
            "Oldest created",
            "Name A–Z",
            "Name Z–A",
            "Target rank ↓",
            "Target rank ↑",
        ],
        key="campaign-list-sort",
    )
    page_size = int(
        size_col.selectbox(
            "Per page",
            [10, 20, 50],
            index=1,
            key="campaign-list-page-size",
        )
    )

    filter_signature = (
        str(query),
        str(status),
        str(sort),
        int(page_size),
    )
    if (
        st.session_state.get("campaign-list-filter-signature")
        != filter_signature
    ):
        st.session_state["campaign-list-filter-signature"] = filter_signature
        st.session_state["campaign-list-page"] = 1

    visible = _filtered_campaigns(
        rows,
        query=query,
        status=status,
        sort=sort,
        current_id=current_id,
    )

    if not visible:
        st.info("No campaigns match the current search and filters.")
        return

    page_rows, page, total_pages, start, end = _campaign_page_slice(
        visible,
        page=int(
            st.session_state.get("campaign-list-page")
            or 1
        ),
        page_size=page_size,
    )
    st.session_state["campaign-list-page"] = page

    st.caption(
        f"Showing {start + 1}–{end} of {len(visible)} campaign"
        f"{'s' if len(visible) != 1 else ''}"
    )

    for row in page_rows:
        _campaign_row(
            db,
            row,
            current_id=current_id,
        )

    if total_pages > 1:
        prev_col, page_col, next_col = st.columns(
            [1.2, 5.6, 1.2],
            vertical_alignment="center",
        )
        if prev_col.button(
            "Previous",
            icon=":material/chevron_left:",
            disabled=page <= 1,
            width="stretch",
            key="campaign-list-prev",
        ):
            st.session_state["campaign-list-page"] = page - 1
            st.rerun()
        page_col.markdown(
            (
                "<div style='text-align:center;opacity:.7'>"
                f"Page {page} of {total_pages}</div>"
            ),
            unsafe_allow_html=True,
        )
        if next_col.button(
            "Next",
            icon=":material/chevron_right:",
            disabled=page >= total_pages,
            width="stretch",
            key="campaign-list-next",
        ):
            st.session_state["campaign-list-page"] = page + 1
            st.rerun()
