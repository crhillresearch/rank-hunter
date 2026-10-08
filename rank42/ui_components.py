"""Stable semantic Streamlit component layer for Rank Hunter.

Themes target ``rh-*`` keys/classes emitted here and by page-region containers,
never Streamlit's generated emotion/baseweb class names.  Keep mathematical
logic out of this module.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

import pandas as pd
import streamlit as st

try:
    import streamlit_shadcn_ui as shadcn
except Exception:  # pragma: no cover
    shadcn = None


try:
    from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder
except Exception:  # pragma: no cover
    AgGrid = None
    DataReturnMode = None
    GridOptionsBuilder = None


def rh_key(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return f"rh-{value or 'region'}"


def _region_key(name, *, card=False):
    """Return a stable semantic key while preserving existing key prefixes."""
    key = rh_key(name)
    return f"{key}-region-card" if card else key


@contextmanager
def region(name, *, border=False, height="content"):
    """Semantic themeable page/widget region.

    Bordered regions carry a stable ``-region-card`` marker on the keyed
    container itself. Themes can therefore own the card surface without
    depending on Streamlit's internal DOM wrappers. ``height`` is passed through
    to Streamlit so sibling cards can use native stretch sizing when needed.
    """
    with st.container(key=_region_key(name, card=border), border=border, height=height):
        yield


def button(label, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.button(label, **kwargs)


def selectbox(label, options, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.selectbox(label, options, **kwargs)


def text_input(label, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.text_input(label, **kwargs)


def number_input(label, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.number_input(label, **kwargs)


def checkbox(label, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.checkbox(label, **kwargs)


def toggle(label, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.toggle(label, **kwargs)


def multiselect(label, options, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.multiselect(label, options, **kwargs)


def dataframe(data, *, semantic, **kwargs):
    kwargs.setdefault("key", rh_key(semantic))
    return st.dataframe(data, **kwargs)


def _promote_cell_click_to_row(widget_key, *, multi=False):
    """Treat an ordinary cell click as selecting its entire record row."""
    state = st.session_state.get(widget_key)
    if not state:
        return
    try:
        selection = state.get("selection", {})
        cells = list(selection.get("cells") or [])
        rows = list(selection.get("rows") or [])
    except Exception:
        return
    if not cells:
        return

    try:
        clicked_row = int(cells[-1][0])
    except (TypeError, ValueError, IndexError):
        return

    if multi:
        selected = {int(value) for value in rows}
        if clicked_row in selected:
            selected.remove(clicked_row)
        else:
            selected.add(clicked_row)
        rows = sorted(selected)
    else:
        rows = [clicked_row]

    # Clear the transient cell selection so the visual state is the row itself.
    st.session_state[widget_key] = {"selection": {"rows": rows}}


def _aggrid_frame(data, records, id_key):
    frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    if len(frame) != len(records):
        raise ValueError("selectable dataframe presentation rows must match record rows")
    frame = frame.reset_index(drop=True)
    frame["__rh_record_id"] = [str(record[id_key]) for record in records]
    return frame


def _aggrid_selected_rows(response):
    selected = getattr(response, "selected_rows", None)
    if selected is None and isinstance(response, dict):
        selected = response.get("selected_rows")
    if selected is None:
        return []
    if hasattr(selected, "to_dict"):
        return selected.to_dict(orient="records")
    if isinstance(selected, list):
        return selected
    try:
        return list(selected)
    except Exception:
        return []


def _aggrid_height(row_count, requested=None):
    if requested is not None and requested != "content":
        try:
            return int(requested)
        except (TypeError, ValueError):
            pass
    visible = min(max(int(row_count), 1), 10)
    return min(430, max(150, 42 + 35 * visible))


def _runtime_theme_palette():
    palette = st.session_state.get("_rh_theme_palette") or {}
    return dict(palette) if isinstance(palette, dict) else {}


def _aggrid_custom_css():
    """Apply Rank Hunter's resolved appearance inside the AgGrid iframe."""
    palette = _runtime_theme_palette()
    if not palette:
        return {}

    card = palette.get("rh-card", "#ffffff")
    card_2 = palette.get("rh-card-2", card)
    hover = palette.get("rh-surface-hover", card_2)
    active = palette.get("rh-surface-active", hover)
    border = palette.get("rh-border", "#d9dee7")
    border_strong = palette.get("rh-border-strong", border)
    text = palette.get("rh-text", "#31333f")
    text_secondary = palette.get("rh-text-secondary", text)
    muted = palette.get("rh-muted", text_secondary)
    primary = palette.get("rh-primary", "#246bfd")

    return {
        "html, body, #root, .stAgGrid, .ag-theme-streamlit, .ag-root, .ag-layout-normal, .ag-body, .ag-body-viewport, .ag-center-cols-clipper, .ag-center-cols-viewport, .ag-center-cols-container": {
            "background-color": f"{card} !important",
            "color": f"{text_secondary} !important",
        },
        ".ag-theme-streamlit": {
            "--ag-background-color": f"{card} !important",
            "--ag-foreground-color": f"{text_secondary} !important",
            "--ag-secondary-foreground-color": f"{muted} !important",
            "--ag-header-background-color": f"{card_2} !important",
            "--ag-header-foreground-color": f"{text} !important",
            "--ag-border-color": f"{border} !important",
            "--ag-row-border-color": f"{border} !important",
            "--ag-row-hover-color": f"{hover} !important",
            "--ag-selected-row-background-color": f"{active} !important",
            "--ag-input-focus-border-color": f"{primary} !important",
            "--ag-input-border-color": f"{border_strong} !important",
            "--ag-input-focus-box-shadow": "none !important",
            "--ag-control-panel-background-color": f"{card} !important",
            "--ag-subheader-background-color": f"{card_2} !important",
            "--ag-odd-row-background-color": f"{card} !important",
        },
        ".ag-root-wrapper, .ag-root-wrapper-body, .ag-root": {
            "background-color": f"{card} !important",
            "border-color": f"{border} !important",
            "color": f"{text_secondary} !important",
        },
        ".ag-header, .ag-header-viewport, .ag-header-container, .ag-header-row, .ag-header-cell": {
            "background-color": f"{card_2} !important",
            "color": f"{text} !important",
            "border-color": f"{border} !important",
        },
        ".ag-row, .ag-row-even, .ag-row-odd, .ag-cell, .ag-cell-value": {
            "background-color": f"{card} !important",
            "color": f"{text_secondary} !important",
            "border-color": f"{border} !important",
        },
        ".ag-row-hover": {
            "background-color": f"{hover} !important",
        },
        ".ag-row-selected": {
            "background-color": f"{active} !important",
        },
        ".ag-menu, .ag-popup, .ag-filter": {
            "background-color": f"{card} !important",
            "color": f"{text_secondary} !important",
            "border-color": f"{border} !important",
        },
        ".ag-input-field-input, .ag-picker-field-wrapper": {
            "background-color": f"{card_2} !important",
            "color": f"{text} !important",
            "border-color": f"{border_strong} !important",
        },
    }


def _aggrid_instance_key(widget_key, requested_ids, sync_key):
    version_key = f"_{widget_key}_grid_version"
    version = int(st.session_state.get(version_key) or 0)
    synced = st.session_state.get(sync_key)
    if synced is not None and requested_ids is not None:
        old = [str(x) for x in (synced if isinstance(synced, (list, tuple)) else [synced])]
        new = [str(x) for x in (requested_ids if isinstance(requested_ids, (list, tuple)) else [requested_ids])]
        if old != new:
            version += 1
            st.session_state[version_key] = version
    return f"{widget_key}-grid-{version}"


def _aggrid_options(frame, *, multiple, preselected, localized_integer_columns=()):
    builder = GridOptionsBuilder.from_dataframe(frame)
    builder.configure_default_column(sortable=True, filter=True, resizable=True)
    builder.configure_column("__rh_record_id", hide=True)
    if multiple:
        builder.configure_selection(
            "multiple",
            use_checkbox=False,
            pre_selected_rows=preselected,
            rowMultiSelectWithClick=True,
            suppressRowClickSelection=False,
            suppressRowDeselection=False,
        )
    else:
        builder.configure_selection(
            "single",
            use_checkbox=False,
            pre_selected_rows=preselected,
            suppressRowClickSelection=False,
            suppressRowDeselection=True,
        )
    for column in localized_integer_columns or ():
        if column in frame.columns:
            builder.configure_column(
                column,
                valueFormatter="value == null ? '' : Number(value).toLocaleString()",
            )
    builder.configure_grid_options(
        rowHeight=34,
        headerHeight=36,
        animateRows=False,
        suppressCellFocus=True,
        suppressRowHoverHighlight=False,
    )
    return builder.build()


def selectable_dataframe(
    data,
    records,
    *,
    semantic,
    selected_id=None,
    id_key="id",
    selection_state_key=None,
    localized_integer_columns=None,
    allow_empty=False,
    **kwargs,
):
    """Render an actionable single-select grid.

    AgGrid owns the interaction when installed: clicking anywhere on a row
    highlights it immediately in the browser, while the resulting record id is
    synchronized back into Streamlit for the detail/action panel. Native
    st.dataframe remains a compatibility fallback for environments that have not
    installed the UI extras yet.
    """
    records = list(records)
    if not records:
        kwargs.setdefault("key", rh_key(semantic))
        st.dataframe(data, **kwargs)
        return None

    default_index = None if allow_empty and selected_id is None else 0
    if selected_id is not None:
        default_index = next(
            (i for i, record in enumerate(records) if str(record[id_key]) == str(selected_id)),
            None if allow_empty else 0,
        )

    if AgGrid is None or GridOptionsBuilder is None:
        kwargs.setdefault("width", "stretch")
        kwargs.setdefault("hide_index", True)
        widget_key = rh_key(semantic)
        frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        frame = frame.reset_index(drop=True)
        selected_index = default_index
        state = st.session_state.get(widget_key) or {}
        try:
            cells = list((state.get("selection") or {}).get("cells") or [])
            if cells:
                selected_index = int(cells[-1][0])
        except (AttributeError, TypeError, ValueError, IndexError):
            selected_index = default_index
        if selected_index is not None:
            selected_index = (
                selected_index
                if 0 <= selected_index < len(records)
                else default_index
            )

        if len(frame.columns) and selected_index is not None:
            first_column = str(frame.columns[0])
            kwargs["selection_default"] = {
                "selection": {"cells": [[selected_index, first_column]]}
            }
        localized = tuple(localized_integer_columns or ())
        if localized:
            column_config = dict(kwargs.get("column_config") or {})
            for column in localized:
                if column in frame.columns and column not in column_config:
                    column_config[column] = st.column_config.NumberColumn(format="localized")
            kwargs["column_config"] = column_config

        def _selected_style(row):
            if selected_index is not None and int(row.name) == int(selected_index):
                return [
                    "background-color: rgba(128, 128, 128, 0.16); font-weight: 600"
                    for _ in row
                ]
            return ["" for _ in row]

        kwargs["key"] = widget_key
        kwargs["on_select"] = "rerun"
        kwargs["selection_mode"] = "single-cell"
        event = st.dataframe(frame.style.apply(_selected_style, axis=1), **kwargs)
        try:
            cells = list(event.selection.cells)
            index = int(cells[-1][0]) if cells else selected_index
        except (AttributeError, TypeError, ValueError, IndexError):
            index = selected_index
        if index is None:
            return None
        fallback_index = 0 if default_index is None else default_index
        record = records[index if 0 <= index < len(records) else fallback_index]
    else:
        frame = _aggrid_frame(data, records, id_key)
        widget_key = rh_key(semantic)
        sync_key = f"_{widget_key}_resolved_id"
        grid_key = _aggrid_instance_key(widget_key, selected_id, sync_key)
        height = _aggrid_height(len(records), kwargs.pop("height", None))
        kwargs.pop("width", None)
        kwargs.pop("hide_index", None)
        kwargs.pop("column_config", None)
        response = AgGrid(
            frame,
            gridOptions=_aggrid_options(
                frame,
                multiple=False,
                preselected=[] if default_index is None else [default_index],
                localized_integer_columns=tuple(localized_integer_columns or ()),
            ),
            key=grid_key,
            height=height,
            theme="streamlit",
            custom_css=_aggrid_custom_css(),
            update_on=["selectionChanged"],
            data_return_mode=DataReturnMode.AS_INPUT,
            enable_enterprise_modules=False,
            allow_unsafe_jscode=False,
            fit_columns_on_grid_load=True,
        )
        selected = _aggrid_selected_rows(response)
        if not selected:
            if allow_empty:
                return None
            fallback_index = 0 if default_index is None else default_index
            selected_record_id = str(records[fallback_index][id_key])
        else:
            selected_record_id = selected[-1].get("__rh_record_id")
        fallback_index = 0 if default_index is None else default_index
        record = next(
            (rec for rec in records if str(rec[id_key]) == str(selected_record_id)),
            records[fallback_index],
        )
        st.session_state[sync_key] = record[id_key]

    resolved_id = record[id_key]
    if selection_state_key:
        st.session_state[str(selection_state_key)] = resolved_id
    return record


@contextmanager
def expander(label, *, semantic, **kwargs):
    with st.container(key=rh_key(semantic)):
        with st.expander(label, **kwargs):
            yield


def metric_card(label, value, *, description=None, key=None):
    semantic = key or f"metric-{label}"
    with st.container(key=rh_key(semantic)):
        if shadcn is not None:
            kwargs = dict(label=str(label), value=str(value), width="stretch")
            if description:
                kwargs["description"] = str(description)
            kwargs["key"] = rh_key(f"{semantic}-card")
            return shadcn.metric_card(**kwargs)
        return st.metric(str(label), str(value), help=str(description) if description else None)


def _set_page_nav_value(state_key, value):
    st.session_state[str(state_key)] = str(value)


def _page_nav(options, *, value, key, label="", width="stretch"):
    """Native page-level navigation with explicit white inactive surfaces."""
    semantic = rh_key(key or "page-nav")
    state_key = f"_{semantic}_value"
    current = str(st.session_state.get(state_key) or value or options[0])
    if current not in options:
        current = str(value or options[0])
    st.session_state[state_key] = current

    container_key = f"{semantic}-page-nav"
    st.html(
        "<style>"
        f"div[class*='st-key-{container_key}'] "
        "[data-testid='stBaseButton-secondary'],"
        f"div[class*='st-key-{container_key}'] button[kind='secondary']{{"
        "background:var(--rh-card,#fff)!important;"
        "color:var(--rh-text-secondary,#31333f)!important;"
        "border-color:var(--rh-border,#d9dee7)!important;"
        "box-shadow:0 1px 2px color-mix(in srgb,var(--rh-text,#31333f) 4%,transparent)!important}"
        f"div[class*='st-key-{container_key}'] "
        "[data-testid='stBaseButton-secondary']:hover,"
        f"div[class*='st-key-{container_key}'] button[kind='secondary']:hover{{"
        "background:var(--rh-surface-hover,#f6f8fb)!important;"
        "border-color:var(--rh-border-strong,#cbd2dc)!important;"
        "color:var(--rh-text,#31333f)!important}"
        f"div[class*='st-key-{container_key}'] button{{min-height:2.25rem!important}}"
        "</style>"
    )
    with st.container(key=container_key, border=False):
        columns = st.columns(len(options), gap="small")
        for index, option in enumerate(options):
            with columns[index]:
                st.button(
                    str(option),
                    key=f"{semantic}-page-{index}",
                    type="primary" if option == current else "secondary",
                    width="stretch" if width == "stretch" else "content",
                    on_click=_set_page_nav_value,
                    args=(state_key, option),
                )

    selected = str(st.session_state.get(state_key) or current)
    return selected if selected in options else current


def tabs(
    options,
    *,
    value=None,
    key=None,
    label="",
    variant="default",
    width="content",
):
    """Compact single-selection navigation whose presentation stays theme-owned."""
    options = [str(x) for x in options]
    current = str(value or options[0])
    if current not in options:
        current = options[0]
    semantic = rh_key(key or "tabs")

    if str(variant or "").lower() == "page":
        return _page_nav(
            options,
            value=current,
            key=key or "tabs",
            label=label,
            width=width,
        )

    # Prefer Streamlit's native segmented control so the active Rank Hunter
    # theme owns tab presentation through the normal CSS cascade.  Shadcn
    # components render outside that styling boundary and are therefore only a
    # compatibility fallback for tabs.
    if hasattr(st, "segmented_control"):
        selected = st.segmented_control(
            label or "View",
            options,
            default=current,
            selection_mode="single",
            required=True,
            key=semantic,
            width=width,
            label_visibility="collapsed" if not label else "visible",
        )
        return str(selected or current)

    if shadcn is not None and hasattr(shadcn, "tabs"):
        selected = shadcn.tabs(
            options,
            value=current,
            key=semantic,
            label=label or "View",
            variant=str(variant or "default"),
            width=width,
        )
        return str(selected or current)
    return st.radio(
        label or "View",
        options,
        index=options.index(current),
        horizontal=True,
        key=semantic,
        label_visibility="collapsed" if not label else "visible",
    )


def combobox(
    label,
    options,
    *,
    value=None,
    key=None,
    format_func=str,
    placeholder="Select an option",
    empty_message="No options found.",
    width="stretch",
):
    """Searchable single-value choice using shadcn when available."""
    options = list(options)
    if not options:
        return None
    if value not in options:
        value = options[0]
    semantic = rh_key(key or f"combobox-{label}")

    if shadcn is not None and hasattr(shadcn, "combobox"):
        selected = shadcn.combobox(
            str(label),
            options,
            value=value,
            key=semantic,
            format_func=format_func,
            placeholder=str(placeholder),
            empty_message=str(empty_message),
            selection_mode="single",
            clearable=False,
            width=width,
        )
        return value if selected is None else selected

    return st.selectbox(
        str(label),
        options,
        index=options.index(value),
        key=semantic,
        format_func=format_func,
        width=width,
    )




def selectable_dataframe_rows(
    data,
    records,
    *,
    semantic,
    selected_ids=None,
    id_key="id",
    selection_state_key=None,
    **kwargs,
):
    """Render an actionable multi-select grid and return underlying records."""
    records = list(records)
    if not records:
        return []

    selected_values = {str(value) for value in (selected_ids or [])}
    default_rows = [
        i for i, record in enumerate(records) if str(record[id_key]) in selected_values
    ]

    if AgGrid is None or GridOptionsBuilder is None:
        kwargs.setdefault("width", "stretch")
        kwargs.setdefault("hide_index", True)
        widget_key = rh_key(semantic)
        kwargs["key"] = widget_key
        kwargs["on_select"] = lambda: _promote_cell_click_to_row(widget_key, multi=True)
        kwargs["selection_mode"] = ["multi-row", "single-cell"]
        kwargs["selection_default"] = {"selection": {"rows": default_rows}}
        event = st.dataframe(data, **kwargs)
        try:
            selected_rows = list(event.selection.rows)
        except Exception:
            selected_rows = default_rows
        out = [records[i] for i in selected_rows if 0 <= i < len(records)]
    else:
        frame = _aggrid_frame(data, records, id_key)
        widget_key = rh_key(semantic)
        sync_key = f"_{widget_key}_resolved_ids"
        requested_ids = list(selected_ids) if selected_ids is not None else None
        grid_key = _aggrid_instance_key(widget_key, requested_ids, sync_key)
        height = _aggrid_height(len(records), kwargs.pop("height", None))
        kwargs.pop("width", None)
        kwargs.pop("hide_index", None)
        kwargs.pop("column_config", None)
        response = AgGrid(
            frame,
            gridOptions=_aggrid_options(frame, multiple=True, preselected=default_rows),
            key=grid_key,
            height=height,
            theme="streamlit",
            update_on=["selectionChanged"],
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            enable_enterprise_modules=False,
            allow_unsafe_jscode=False,
            fit_columns_on_grid_load=True,
        )
        selected = _aggrid_selected_rows(response)
        ids = {str(row.get("__rh_record_id")) for row in selected if row.get("__rh_record_id") is not None}
        out = [record for record in records if str(record[id_key]) in ids]
        st.session_state[sync_key] = [record[id_key] for record in out]

    if selection_state_key:
        st.session_state[str(selection_state_key)] = [record[id_key] for record in out]
    return out


def card(title, *, description=None, footer=None, key=None):
    semantic = key or f"card-{title}"
    with st.container(key=_region_key(semantic, card=True), border=True):
        if shadcn is not None:
            kwargs = dict(title=str(title), content=str(description or ""), width="stretch")
            if footer:
                kwargs["footer"] = str(footer)
            kwargs["key"] = rh_key(f"{semantic}-inner")
            try:
                return shadcn.card(**kwargs)
            except TypeError:
                kwargs.pop("title", None)
                kwargs["content"] = f"{title}\n\n{description or ''}".strip()
                return shadcn.card(**kwargs)
        st.markdown(f"**{title}**")
        if description:
            st.caption(str(description))
        if footer:
            st.caption(str(footer))


def badge(text, *, key=None, variant="secondary"):
    semantic = rh_key(key or f"badge-{text}")
    if shadcn is not None and hasattr(shadcn, "badge"):
        try:
            return shadcn.badge(label=str(text), variant=variant, key=semantic)
        except TypeError:
            try:
                return shadcn.badge(str(text), variant=variant, key=semantic)
            except TypeError:
                return shadcn.badge(str(text), key=semantic)
    st.caption(str(text))


def badge_row(items, *, key="badge-row"):
    values = [str(x) for x in items if str(x).strip()]
    if not values:
        return
    semantic = rh_key(key)
    with st.container(key=semantic):
        if shadcn is not None and hasattr(shadcn, "badges"):
            try:
                return shadcn.badges([(x, "secondary") for x in values], width="stretch", key=rh_key(f"{key}-items"))
            except TypeError:
                try:
                    return shadcn.badges([(x, "secondary") for x in values], key=rh_key(f"{key}-items"))
                except TypeError:
                    pass
        cols = st.columns(min(4, len(values)), gap="small")
        for i, value in enumerate(values):
            with cols[i % len(cols)]:
                badge(value, key=f"{key}-{i}")
