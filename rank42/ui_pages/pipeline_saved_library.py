from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from rank42.pipeline_catalog import normalize_pipeline, stage_spec
from rank42.pipeline_state import delete_pipeline, list_pipelines, pipeline_payload


TARGET_LABELS = {
    "family": "Family",
    "torsion": "Torsion Group",
    "general": "General Curves",
    "curve": "Target Curve",
}

PIPELINE_LIBRARY_CARD_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "pipeline_library_card"
)
_PIPELINE_LIBRARY_CARD = components.declare_component(
    "rank42_pipeline_library_card",
    path=str(PIPELINE_LIBRARY_CARD_COMPONENT_DIR),
)


def _component_theme_palette():
    palette = st.session_state.get("_rh_theme_palette") or {}
    return dict(palette) if isinstance(palette, dict) else {}


# These narrow compatibility shims keep the editor's existing state/lifecycle
# authority in build_your_own.py while letting the Saved library live in its
# own panel module. Imports are intentionally lazy to avoid a module cycle.
def _discard_pipeline_draft(db):
    from . import build_your_own
    return build_your_own._discard_pipeline_draft(db)


def _load_pipeline_for_edit(row):
    from . import build_your_own
    return build_your_own._load_pipeline_for_edit(row)


def _duplicate_loaded_pipeline():
    from . import build_your_own
    return build_your_own._duplicate_loaded_pipeline()


def _clear_loaded_pipeline(mode):
    from . import build_your_own
    return build_your_own._clear_loaded_pipeline(mode)


def _switch_builder_view(view):
    from . import build_your_own
    return build_your_own._switch_builder_view(view)


def _pipeline_stage_summary(payload):
    labels = []
    for rec in normalize_pipeline(payload.get("stages") or []):
        try:
            labels.append(stage_spec(rec["id"]).label)
        except Exception:
            labels.append(str(rec.get("id") or "unknown"))
    if not labels:
        return "Blank pipeline"
    if len(labels) <= 5:
        return " → ".join(labels)
    return " → ".join(labels[:5]) + f" → +{len(labels) - 5} more"


def _pipeline_library_item(row):
    payload = pipeline_payload(row)
    config = dict(payload.get("config") or {})
    target_rank = config.get("target_rank")
    try:
        target_rank = None if target_rank is None else int(target_rank)
    except (TypeError, ValueError):
        target_rank = None
    return {
        "row": row,
        "payload": payload,
        "name": str(payload["name"]),
        "target_mode": str(payload["target_mode"]),
        "target_label": TARGET_LABELS.get(
            str(payload["target_mode"]), str(payload["target_mode"])
        ),
        "stage_count": len(payload.get("stages") or []),
        "stage_summary": _pipeline_stage_summary(payload),
        "target_rank": target_rank,
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _pipeline_library_search_blob(item):
    payload = item["payload"]
    config = dict(payload.get("config") or {})
    stage_ids = " ".join(
        str(rec.get("id") or "").replace("_", " ").replace("-", " ")
        for rec in normalize_pipeline(payload.get("stages") or [])
    )
    return " ".join(
        [
            str(item["name"]),
            str(item["target_label"]),
            str(item["stage_summary"]),
            stage_ids,
            str(item["target_rank"] or ""),
            " ".join(str(value) for value in config.get("feature_plugin_ids") or []),
        ]
    ).lower()


def _filtered_pipeline_library(
    items,
    *,
    query="",
    target_type="All",
    sort="Recently updated",
    loaded_id=None,
):
    query = str(query or "").strip().lower()
    wanted_mode = {
        "Family": "family",
        "Torsion Group": "torsion",
        "General Curves": "general",
        "Target Curve": "curve",
    }.get(str(target_type))

    visible = []
    for item in items:
        if query and query not in _pipeline_library_search_blob(item):
            continue
        if wanted_mode is not None and item["target_mode"] != wanted_mode:
            continue
        visible.append(item)

    if sort == "Name A–Z":
        visible.sort(key=lambda item: (item["name"].lower(), int(item["payload"]["id"])))
    elif sort == "Name Z–A":
        visible.sort(
            key=lambda item: (item["name"].lower(), int(item["payload"]["id"])),
            reverse=True,
        )
    elif sort == "Newest created":
        visible.sort(
            key=lambda item: (item["created_at"], int(item["payload"]["id"])),
            reverse=True,
        )
    elif sort == "Oldest created":
        visible.sort(
            key=lambda item: (item["created_at"], int(item["payload"]["id"]))
        )
    elif sort == "Stage count ↓":
        visible.sort(
            key=lambda item: (
                -int(item["stage_count"]),
                item["name"].lower(),
                -int(item["payload"]["id"]),
            )
        )
    elif sort == "Target rank ↓":
        visible.sort(
            key=lambda item: (
                item["target_rank"] is None,
                -(int(item["target_rank"]) if item["target_rank"] is not None else 0),
                -int(item["payload"]["id"]),
            )
        )
    else:
        visible.sort(
            key=lambda item: (item["updated_at"], int(item["payload"]["id"])),
            reverse=True,
        )

    if loaded_id is not None:
        editing = [
            item for item in visible
            if int(item["payload"]["id"]) == int(loaded_id)
        ]
        if editing:
            visible = editing + [
                item for item in visible
                if int(item["payload"]["id"]) != int(loaded_id)
            ]
    return visible


def _pipeline_library_page_slice(items, *, page, page_size):
    page_size = max(1, int(page_size))
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * page_size
    end = min(total, start + page_size)
    return items[start:end], page, total_pages, start, end


def _pipeline_library_event(payload, *, pipeline_id):
    if not isinstance(payload, dict):
        return None
    nonce = payload.get("nonce")
    key = f"byo_pipeline_library_nonce_{int(pipeline_id)}"
    if nonce is None or str(st.session_state.get(key)) == str(nonce):
        return None
    st.session_state[key] = str(nonce)
    return str(payload.get("action") or "")


def _render_pipeline_library_card(db, item, *, loaded_id):
    row = item["row"]
    payload = item["payload"]
    pipeline_id = int(payload["id"])
    editing = loaded_id is not None and pipeline_id == int(loaded_id)
    target = (
        f" · target ≥{int(item['target_rank'])}"
        if item["target_rank"] is not None
        else ""
    )
    meta = (
        f"#{pipeline_id} · {item['target_label']} · "
        f"{int(item['stage_count'])} stage"
        f"{'s' if int(item['stage_count']) != 1 else ''}{target} · "
        f"updated {item['updated_at']}"
    )
    event = _PIPELINE_LIBRARY_CARD(
        name=item["name"],
        meta=meta,
        summary=item["stage_summary"],
        editing=bool(editing),
        palette=_component_theme_palette(),
        key=f"byo-pipeline-library-card-{pipeline_id}",
        default=None,
    )
    action = _pipeline_library_event(event, pipeline_id=pipeline_id)
    if not action:
        return

    if action == "open":
        _discard_pipeline_draft(db)
        _load_pipeline_for_edit(row)
        st.rerun()
    elif action == "duplicate":
        _discard_pipeline_draft(db)
        _load_pipeline_for_edit(row)
        _duplicate_loaded_pipeline()
        st.toast(
            f"Duplicated pipeline #{pipeline_id}. "
            "Save Pipeline will create a new definition.",
            icon="📋",
        )
        st.rerun()
    elif action == "delete":
        delete_pipeline(db, pipeline_id)
        if st.session_state.get("byo_loaded_pipeline_id") == pipeline_id:
            st.session_state["byo_loaded_pipeline_id"] = None
        st.toast(f"Deleted pipeline · {item['name']}", icon="🗑️")
        st.rerun()


def _render_pipeline_library(db):
    rows = list_pipelines(db, limit=5000)

    heading_col, action_col = st.columns(
        [8.6, 1.4],
        vertical_alignment="center",
    )
    with heading_col:
        st.subheader("Saved pipelines")
    with action_col:
        if st.button(
            "New Pipeline",
            icon=":material/add:",
            width="stretch",
            key="byo-saved-new-pipeline",
        ):
            mode = str(st.session_state.get("byo_stage_mode") or "family")
            _discard_pipeline_draft(db)
            _clear_loaded_pipeline(mode)
            _switch_builder_view("Editor")
            st.rerun()

    if not rows:
        st.info(
            "No saved pipelines yet. Use New Pipeline or open the Editor tab "
            "to build and save one."
        )
        return

    items = [_pipeline_library_item(row) for row in rows]
    loaded_id = st.session_state.get("byo_loaded_pipeline_id")

    search_col, type_col, sort_col, size_col = st.columns(
        [3.4, 1.35, 1.65, 0.9],
        vertical_alignment="bottom",
    )
    query = search_col.text_input(
        "Search pipelines",
        placeholder="Search name, modules, target type, features…",
        key="byo-saved-search",
    )
    target_type = type_col.selectbox(
        "Target type",
        ["All", "Family", "Torsion Group", "General Curves", "Target Curve"],
        key="byo-saved-target-type",
    )
    sort = sort_col.selectbox(
        "Sort",
        [
            "Recently updated",
            "Newest created",
            "Oldest created",
            "Name A–Z",
            "Name Z–A",
            "Stage count ↓",
            "Target rank ↓",
        ],
        key="byo-saved-sort",
    )
    page_size = int(size_col.selectbox(
        "Per page",
        [10, 20, 50],
        index=1,
        key="byo-saved-page-size",
    ))

    signature = (
        str(query),
        str(target_type),
        str(sort),
        int(page_size),
    )
    if st.session_state.get("byo-saved-filter-signature") != signature:
        st.session_state["byo-saved-filter-signature"] = signature
        st.session_state["byo-saved-page"] = 1

    visible = _filtered_pipeline_library(
        items,
        query=query,
        target_type=target_type,
        sort=sort,
        loaded_id=loaded_id,
    )
    if not visible:
        st.info("No saved pipelines match the current search and filters.")
        return

    page_items, page, total_pages, start, end = _pipeline_library_page_slice(
        visible,
        page=int(st.session_state.get("byo-saved-page") or 1),
        page_size=page_size,
    )
    st.session_state["byo-saved-page"] = page

    st.caption(
        f"Showing {start + 1}–{end} of {len(visible)} saved pipeline"
        f"{'s' if len(visible) != 1 else ''}"
    )
    for item in page_items:
        _render_pipeline_library_card(db, item, loaded_id=loaded_id)

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
            key="byo-saved-prev",
        ):
            st.session_state["byo-saved-page"] = page - 1
            st.rerun()
        page_col.markdown(
            f"<div style='text-align:center;opacity:.7'>"
            f"Page {page} of {total_pages}</div>",
            unsafe_allow_html=True,
        )
        if next_col.button(
            "Next",
            icon=":material/chevron_right:",
            disabled=page >= total_pages,
            width="stretch",
            key="byo-saved-next",
        ):
            st.session_state["byo-saved-page"] = page + 1
            st.rerun()
