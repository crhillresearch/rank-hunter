from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path

import streamlit as st

from rank42.manifest_assets import ManifestAssetError, preview_image_path
from rank42.theme_loader import Theme, theme_records
from rank42.ui_components import region, rh_key, button
from .common import save_setting, setting, title


def _selected_theme_id(db, ctx, themes):
    ids = {theme.id for theme in themes}
    desired = str(setting(db, "ui_theme", "") or "").strip()
    if desired in ids:
        return desired
    loaded = str(ctx.theme.id if ctx.theme is not None else "").strip()
    if loaded in ids:
        return loaded
    return themes[0].id if themes else ""


def _theme_preview(theme):
    try:
        return preview_image_path(theme.root, theme.manifest), None
    except ManifestAssetError as exc:
        return None, str(exc)


@st.cache_data(show_spinner=False)
def _preview_image_data(path_text, mtime_ns):
    path = Path(path_text)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return mime, encoded


def _preview_markup(image, theme_name):
    if image is None:
        return (
            '<div class="rh-theme-preview-placeholder" aria-label="No theme preview">'
            '<span class="material-symbols-rounded">palette</span>'
            '<small>No preview</small></div>'
        )
    try:
        mtime_ns = int(image.stat().st_mtime_ns)
    except OSError:
        mtime_ns = 0
    mime, encoded = _preview_image_data(str(image), mtime_ns)
    return (
        '<div class="rh-theme-preview-media">'
        f'<img src="data:{mime};base64,{encoded}" alt="{html.escape(theme_name, quote=True)} preview">'
        '</div>'
    )


def _theme_card(db, theme, *, selected_id, loaded_id):
    selected = theme.id == selected_id
    active = theme.id == loaded_id
    preview, preview_error = _theme_preview(theme)
    fallback = theme.logo_path if theme.logo_path is not None and theme.logo_path.is_file() else None
    image = preview or fallback
    description = str(theme.manifest.get("description") or "").strip()

    with region(f"theme-card-{theme.id}", border=True):
        with st.container(key=rh_key(f"theme-action-slot-{theme.id}")):
            if active:
                st.html('<div class="rh-theme-active-badge">ACTIVE</div>')
            elif selected:
                st.html('<div class="rh-theme-selected-badge">SELECTED</div>')
            if not selected and button(
                "Select",
                semantic=f"theme-select-{theme.id}",
                type="primary",
                width="content",
            ):
                save_setting(db, "ui_theme", theme.id)
                st.rerun()

        media, body = st.columns([0.58, 1.7], gap="medium", vertical_alignment="top")
        with media:
            st.html(_preview_markup(image, theme.name))
            if preview_error:
                st.caption(f"Preview unavailable: {preview_error}")

        with body:
            st.markdown(
                f'<div class="rh-theme-card-copy">'
                f'<div class="rh-theme-card-title">{html.escape(theme.name)}</div>'
                f'<div class="rh-theme-card-version">v{html.escape(theme.version)}</div>'
                f'<div class="rh-theme-card-description">{html.escape(description) if description else "No description provided."}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def page(db, ctx):
    with st.container(key=rh_key("themes-header-title")):
        title("Themes", icon="palette")

    st.html(
        """
        <style>
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-themes-header-title) {
            width:100% !important;
            gap:.42rem !important;
            align-items:center !important;
            margin-bottom:.8rem;
        }
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-themes-header-title) > [data-testid="stColumn"]:nth-child(1) {
            flex:0 0 auto !important;
            width:auto !important;
            min-width:0 !important;
        }
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-themes-header-title) > [data-testid="stColumn"]:nth-child(2) {
            flex:1 1 auto !important;
            width:auto !important;
            min-width:0 !important;
        }
        [data-testid="stHorizontalBlock"]:has(.st-key-rh-themes-header-title) > [data-testid="stColumn"]:nth-child(3) {
            flex:0 0 auto !important;
            width:auto !important;
            min-width:max-content !important;
        }
        [class*="st-key-rh-themes-header-action"] {
            width:100% !important;
            min-height:3.15rem;
            display:flex;
            justify-content:flex-end;
            align-items:center;
        }
        [class*="st-key-rh-themes-header-action"] > div,
        [class*="st-key-rh-themes-header-action"] [data-testid="stVerticalBlock"] {
            width:auto !important;
            align-items:flex-end;
        }
        [class*="st-key-rh-theme-card-"] {
            position:relative;
        }
        .rh-theme-preview-media,
        .rh-theme-preview-placeholder {
            width:100%; min-height:8.5rem; height:8.5rem;
            display:flex; align-items:flex-start; justify-content:center;
        }
        .rh-theme-preview-media img {
            display:block; width:6rem; max-width:6rem; height:8.5rem;
            object-fit:contain;
            object-position:center top;
            border-radius: calc(var(--rh-radius, 12px) - 2px);
        }
        .rh-theme-preview-placeholder {
            border: 1px dashed var(--rh-border, #d7dce5);
            border-radius: var(--rh-radius, 12px); flex-direction:column;
            align-items:center; justify-content:center; gap:.4rem; opacity:.68;
            background: var(--rh-card-2, rgba(127,127,127,.06));
        }
        .rh-theme-preview-placeholder .material-symbols-rounded { font-size:2rem; }
        .rh-theme-card-copy { padding-top:0; padding-right:4.8rem; }
        .rh-theme-card-title { font-size:1.35rem; font-weight:780; line-height:1.15; }
        .rh-theme-card-version { margin-top:.18rem; font-size:.76rem; opacity:.62; }
        .rh-theme-card-description {
            display:-webkit-box;
            min-height:calc(1.45em * 3);
            max-height:calc(1.45em * 3);
            margin:.72rem 0 0;
            overflow:hidden;
            line-height:1.45;
            opacity:.78;
            -webkit-box-orient:vertical;
            -webkit-line-clamp:3;
        }
        [class*="st-key-rh-theme-action-slot-"] {
            position:absolute !important;
            top:.68rem;
            right:.72rem;
            z-index:6;
            width:auto !important;
            min-width:0 !important;
            height:auto !important;
            min-height:0 !important;
            margin:0 !important;
            overflow:visible;
        }
        [class*="st-key-rh-theme-action-slot-"] [data-testid="stVerticalBlock"] {
            gap:.28rem !important;
            align-items:flex-end !important;
        }
        .rh-theme-active-badge,
        .rh-theme-selected-badge {
            display:inline-flex; align-items:center; min-height:1.55rem; padding:.12rem .56rem;
            border-radius:999px; font-size:.66rem; font-weight:800; letter-spacing:.08em;
        }
        .rh-theme-active-badge {
            color:var(--rh-success, #24b47e);
            background:color-mix(in srgb, var(--rh-success, #24b47e) 12%, var(--rh-card, white));
            border:1px solid color-mix(in srgb, var(--rh-success, #24b47e) 24%, transparent);
        }
        .rh-theme-selected-badge {
            color:var(--rh-info, #3b82f6);
            background:color-mix(in srgb, var(--rh-info, #3b82f6) 12%, var(--rh-card, white));
            border:1px solid color-mix(in srgb, var(--rh-info, #3b82f6) 24%, transparent);
        }
        [class*="st-key-rh-theme-select-"] {
            width:auto !important;
            min-width:0 !important;
        }
        [class*="st-key-rh-theme-select-"] button {
            min-width:4.5rem !important;
            min-height:1.8rem !important;
            padding:.18rem .7rem !important;
            border-radius:999px !important;
            font-size:.68rem !important;
            font-weight:760 !important;
        }
        [class*="st-key-rh-theme-card-"] > [data-testid="stVerticalBlockBorderWrapper"] {
            min-height:14rem;
            height:14rem;
        }
        </style>
        """
    )

    records = theme_records(ctx.project_root)
    themes = [row for row in records if isinstance(row, Theme)]
    invalid = [row for row in records if not isinstance(row, Theme)]
    for row in invalid:
        st.warning(f"Invalid theme at `{row['path']}`: {row['error']}")

    if not themes:
        st.info("No themes are installed.")
        return

    selected_id = _selected_theme_id(db, ctx, themes)
    loaded_id = str(ctx.theme.id if ctx.theme is not None else "").strip()
    if selected_id != loaded_id:
        chosen = next((theme for theme in themes if theme.id == selected_id), None)
        selected_label = chosen.name if chosen is not None else selected_id
        loaded = next((theme for theme in themes if theme.id == loaded_id), None)
        loaded_label = loaded.name if loaded is not None else "Core baseline"
        st.info(
            f"{selected_label} is selected for the next Rank Hunter UI start. "
            f"{loaded_label} remains active until restart."
        )

    columns = st.columns(2, gap="medium")
    for index, theme in enumerate(themes):
        with columns[index % 2]:
            _theme_card(
                db,
                theme,
                selected_id=selected_id,
                loaded_id=loaded_id,
            )
