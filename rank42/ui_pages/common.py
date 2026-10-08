from __future__ import annotations
import base64, html, json, logging, re, shlex, sqlite3, sys, shutil
from dataclasses import dataclass
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

from rank42.theme_loader import Theme
from rank42.icon_sprite import rh_css_mask_uri
from rank42.curve_research_state import list_curve_research_states
from rank42.launch_context import (
    launch_context_snapshot,
    lookup_active_campaign,
    normalize_launch_metadata,
    resolve_launch_context,
)
from rank42.manage_store import (
    campaign_curve_ids_readonly,
    current_campaign_readonly,
    list_campaigns,
)
from rank42.ratpoints import vendored_executable
from rank42.settings_registry import (
    save_setting_value,
    science_python_bootstrap_value,
    setting_value,
)
from rank42.ui_store import latest_candidate_done, enqueue_job, list_jobs, reconcile_jobs, read_log, stop_job
from rank42.ui_active_curve import set_active_curve_id
from rank42.ui_components import rh_key, selectbox as rh_selectbox, multiselect as rh_multiselect, button as rh_button


logger = logging.getLogger(__name__)


ACTIVE_WORK_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "active_work"
)
_ACTIVE_WORK = components.declare_component(
    "rank42_active_work",
    path=str(ACTIVE_WORK_COMPONENT_DIR),
)


@dataclass(frozen=True)
class UIContext:
    project_root: Path
    db_path: Path
    theme: Theme | None = None
    appearance: str = "light"

    def detected_science_python(self):
        """Return the one-way installer/bootstrap candidate for science_python."""
        return science_python_bootstrap_value(
            self.project_root,
            fallback=sys.executable,
        )

def _runtime_theme_palette(theme: Theme | None, appearance: str):
    """Return resolved presentation tokens for iframe/component consumers."""
    if theme is None:
        return {}
    tokens = theme.resolved_tokens(appearance)
    keys = (
        "rh-bg",
        "rh-card",
        "rh-card-2",
        "rh-surface-hover",
        "rh-surface-active",
        "rh-border",
        "rh-border-strong",
        "rh-control-border",
        "rh-text",
        "rh-text-secondary",
        "rh-muted",
        "rh-muted-2",
        "rh-primary",
        "rh-primary-deep",
        "rh-success",
        "rh-warning",
        "rh-danger",
        "rh-text-on-accent",
    )
    return {
        key: value
        for key in keys
        if (value := str(tokens.get(key) or "").strip())
    }


_DARK_NATIVE_SURFACE_CSS = """
<style>
:root {
    color-scheme: dark;
    --rh-native-control-bg: var(--rh-card-2,#151c28);
    --rh-native-control-border: var(--rh-control-border,var(--rh-border-strong,#475569));
    --rh-native-table-bg: var(--rh-card,#111827);
    --rh-active-campaign-pin-bg: color-mix(
        in srgb,
        var(--rh-success,#1fa65d) 34%,
        var(--rh-card,#111827)
    );
}
section[data-testid="stSidebar"],
[data-testid="stMainBlockContainer"] {
    color-scheme: dark;
}
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"] button,
[data-testid="stExpandSidebarButton"] {
    color: var(--rh-text-secondary,#cbd5e1) !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    outline: none !important;
}
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapsedControl"] [data-testid="stIconMaterial"],
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stExpandSidebarButton"] svg {
    color: var(--rh-text-secondary,#cbd5e1) !important;
    fill: currentColor !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] details {
    border-color: var(--rh-border-strong,#475569) !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary [data-testid="stIconMaterial"],
section[data-testid="stSidebar"] [data-testid="stExpander"] summary svg,
section[data-testid="stSidebar"] .material-symbols-rounded {
    color: var(--rh-text-secondary,#cbd5e1) !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary [data-testid="stIconMaterial"],
section[data-testid="stSidebar"] [data-testid="stExpander"] summary svg {
    fill: currentColor !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:focus,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:focus-visible {
    outline: none !important;
    box-shadow: none !important;
}
[data-testid="stTextInputRootElement"],
[data-testid="stDateInputField"],
[data-testid="stTimeInputTimeDisplay"],
[data-testid="stTextInput"] [data-baseweb="input"],
[data-testid="stNumberInput"] [data-baseweb="input"],
[data-testid="stDateInput"] [data-baseweb="input"],
[data-testid="stTimeInput"] [data-baseweb="input"],
[data-testid="stTextArea"] [data-baseweb="textarea"],
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div {
    background: var(--rh-native-control-bg) !important;
    background-color: var(--rh-native-control-bg) !important;
    border-color: var(--rh-native-control-border) !important;
    color: var(--rh-text,#f3f6fb) !important;
    box-shadow: none !important;
}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stDateInputField"]:focus-within,
[data-testid="stTimeInputTimeDisplay"]:focus-within,
[data-testid="stTextInput"] [data-baseweb="input"]:focus-within,
[data-testid="stNumberInput"] [data-baseweb="input"]:focus-within,
[data-testid="stDateInput"] [data-baseweb="input"]:focus-within,
[data-testid="stTimeInput"] [data-baseweb="input"]:focus-within,
[data-testid="stTextArea"] [data-baseweb="textarea"]:focus-within,
[data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div:focus-within {
    border-color: var(--rh-primary,#6ea2ff) !important;
    box-shadow: 0 0 0 1px color-mix(
        in srgb,
        var(--rh-primary,#6ea2ff) 45%,
        transparent
    ) !important;
}
[data-testid="stTextInput"] [data-baseweb="base-input"],
[data-testid="stNumberInput"] [data-baseweb="base-input"],
[data-testid="stDateInput"] [data-baseweb="base-input"],
[data-testid="stTimeInput"] [data-baseweb="base-input"],
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input {
    color: var(--rh-text,#f3f6fb) !important;
    background: transparent !important;
    background-color: transparent !important;
    caret-color: var(--rh-text,#f3f6fb) !important;
}
[data-testid="stDateInputField"] [role="spinbutton"],
[data-testid="stTimeInputTimeDisplay"] [role="spinbutton"] {
    color: var(--rh-text,#f3f6fb) !important;
    background: transparent !important;
}
[data-testid="stDateInputField"] [data-placeholder],
[data-testid="stTimeInputTimeDisplay"] [data-placeholder] {
    color: var(--rh-muted,#94a3b8) !important;
}
[data-testid="stDateInputField"] button,
[data-testid="stTimeInputTimeDisplay"] button,
[data-testid="stDateInputField"] svg,
[data-testid="stTimeInputTimeDisplay"] svg {
    color: var(--rh-text-secondary,#cbd5e1) !important;
    fill: currentColor !important;
}
[data-testid="stDateInputCalendar"] {
    background: var(--rh-card,#111827) !important;
    color: var(--rh-text,#f3f6fb) !important;
    border-color: var(--rh-native-control-border) !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stNumberInput"] input::placeholder,
[data-testid="stDateInput"] input::placeholder,
[data-testid="stTimeInput"] input::placeholder {
    color: var(--rh-muted,#94a3b8) !important;
    opacity: 1 !important;
}
[data-testid="stTextArea"] textarea {
    color: var(--rh-text,#f3f6fb) !important;
    background: transparent !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] svg,
[data-testid="stMultiSelect"] [data-baseweb="select"] svg {
    color: var(--rh-text-secondary,#cbd5e1) !important;
    fill: currentColor !important;
}
[data-baseweb="popover"],
[data-baseweb="menu"],
[role="listbox"] {
    background: var(--rh-card,#111827) !important;
    color: var(--rh-text,#f3f6fb) !important;
    border-color: var(--rh-native-control-border) !important;
}
label[data-baseweb="radio"]:has(input:not(:checked)) > div:first-child,
[data-testid="stRadio"] [role="radio"][aria-checked="false"] {
    background: var(--rh-native-control-bg) !important;
    border-color: var(--rh-native-control-border) !important;
    box-shadow: none !important;
}
div[class*="-page-nav"] [data-testid="stBaseButton-primary"],
div[class*="-page-nav"] button[kind="primary"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-primary"],
section[data-testid="stSidebar"] button[kind="primary"] {
    background: var(--rh-surface-active,#243044) !important;
    color: var(--rh-text,#f3f6fb) !important;
    border-color: var(--rh-border-strong,#475569) !important;
    box-shadow: none !important;
}
/* st.table is ordinary HTML and can safely inherit Rank Hunter surfaces.
   st.dataframe is canvas-backed; leave its grid/canvas painting to Streamlit's
   native dark theme or the Glide renderer becomes an opaque blank rectangle. */
[data-testid="stTable"],
[data-testid="stTable"] table,
[data-testid="stTable"] thead,
[data-testid="stTable"] tbody,
[data-testid="stTable"] tr,
[data-testid="stTable"] th,
[data-testid="stTable"] td {
    background-color: var(--rh-native-table-bg) !important;
    color: var(--rh-text,#f3f6fb) !important;
    border-color: var(--rh-border,#374151) !important;
}
[data-testid="stTable"] th {
    background-color: var(--rh-card-2,#151c28) !important;
}
[data-testid="stDataFrame"] .gdg-search-bar {
    background: var(--rh-card-2,#151c28) !important;
    color: var(--rh-text,#f3f6fb) !important;
    border-color: var(--rh-native-control-border) !important;
}
[data-testid="stDataFrame"] .gdg-search-bar input {
    background: var(--rh-native-control-bg) !important;
    color: var(--rh-text,#f3f6fb) !important;
    border-color: var(--rh-native-control-border) !important;
}
</style>
"""


def _native_appearance_css(appearance: str) -> str:
    return _DARK_NATIVE_SURFACE_CSS if str(appearance).lower() == "dark" else ""


_RESPONSIVE_LAYOUT_CSS = """
<style>
/* Shared narrow-screen safeguards. Desktop column ratios remain untouched. */
[data-testid="stMainBlockContainer"],
[data-testid="stMainBlockContainer"] [data-testid="stElementContainer"],
[data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"],
[data-testid="stMainBlockContainer"] [data-testid="stColumn"] {
    min-width: 0 !important;
    max-width: 100%;
    box-sizing: border-box;
}
[data-testid="stMainBlockContainer"] [data-testid="stDataFrame"],
[data-testid="stMainBlockContainer"] [data-testid="stTable"],
[data-testid="stMainBlockContainer"] [data-testid="stIFrame"] {
    max-width: 100% !important;
}
[data-testid="stMainBlockContainer"] [data-testid="stDataFrame"],
[data-testid="stMainBlockContainer"] [data-testid="stTable"] {
    overflow-x: auto;
    overscroll-behavior-x: contain;
}
@media (max-width: 900px) {
    [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        row-gap: .65rem !important;
    }
    [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 16rem !important;
        width: auto !important;
        min-width: min(16rem, 100%) !important;
        max-width: 100% !important;
    }
}
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 100% !important;
    }
    [data-testid="stMainBlockContainer"] button,
    [data-testid="stMainBlockContainer"] [role="button"],
    [data-testid="stMainBlockContainer"] input,
    [data-testid="stMainBlockContainer"] select {
        max-width: 100%;
    }
}
</style>
"""


def configure(theme: Theme | None = None, *, appearance: str = "light"):
    """Configure Streamlit and apply one filesystem theme appearance."""
    app_icon = Path(__file__).resolve().parents[2] / "assets" / "rank-hunter-app-icon.svg"
    st.set_page_config(
        page_title='Rank Hunter',
        page_icon=str(app_icon),
        layout='wide',
        initial_sidebar_state='expanded',
    )
    st.session_state["_rh_appearance"] = str(appearance or "light")
    st.session_state["_rh_theme_palette"] = _runtime_theme_palette(
        theme,
        appearance,
    )
    if theme is not None:
        st.html(f"<style>{theme.css_text(appearance)}</style>")
    native_appearance_css = _native_appearance_css(appearance)
    if native_appearance_css:
        st.html(native_appearance_css)
    st.html(_RESPONSIVE_LAYOUT_CSS)

QUARTICS_ICON = ":material/control_camera:"

_FA_INLINE_ICONS = {
    "clover": {
        "view_box": "0 0 512 512",
        "path": (
            "M310.4 16C346.6 16 376 45.4 376 81.7l0 5.2c0 11.2-2.7 22.3-7.8 32.2"
            "l-2.3 4.2-20.1 33.5c-1.1 1.9-1.2 3.4-1.1 4.5 .2 1.3 .9 2.7 2.1 3.9"
            "s2.6 1.9 3.9 2.1c1.1 .2 2.6 .1 4.5-1.1l33.5-20.1 4.2-2.3c10-5.1 21-7.8 32.2-7.8"
            "l5.2 0c36.2 0 65.6 29.4 65.6 65.7 0 17.4-6.9 34.1-19.2 46.4l-1.3 1.3c-3.7 3.7-3.7 9.6 0 13.3"
            "l1.3 1.3c12.3 12.3 19.2 29 19.2 46.4 0 36.2-29.4 65.6-65.6 65.6l-5.2 0c-12.8 0-25.5-3.5-36.5-10.1"
            "l-33.5-20.1c-1.9-1.1-3.4-1.2-4.5-1.1-1.3 .2-2.7 .9-3.9 2.1s-1.9 2.6-2.1 3.9c-.2 1.1-.1 2.6 1.1 4.5"
            "l20.1 33.5c6.6 11 10.1 23.6 10.1 36.5l0 5.2c0 36.2-29.4 65.6-65.6 65.6-17.4 0-34.1-6.9-46.4-19.2"
            "l-1.3-1.3c-3.7-3.7-9.6-3.7-13.3 0l-1.3 1.3c-12.3 12.3-29 19.2-46.4 19.2-36.2 0-65.6-29.4-65.7-65.6"
            "l0-5.2c0-12.8 3.5-25.5 10.1-36.5l20.1-33.5c1.1-1.9 1.2-3.4 1.1-4.5-.2-1.3-.9-2.7-2.1-3.9s-2.6-1.9-3.9-2.1"
            "c-.5-.1-1.2-.1-1.9 0l-2.5 1-33.5 20.1c-11 6.6-23.6 10.1-36.5 10.1l-5.2 0C45.4 376 16 346.6 16 310.4"
            " 16 293 22.9 276.3 35.2 264l1.3-1.3 1.2-1.5c2.1-3.1 2.1-7.2 0-10.3l-1.2-1.5-1.3-1.3C22.9 235.8 16 219.1"
            " 16 201.7 16 165.4 45.4 136 81.7 136l5.2 0c12.8 0 25.5 3.5 36.5 10.1l33.5 20.1 2.5 1c.7 .1 1.4 .1 1.9 .1"
            " 1.3-.2 2.7-.9 3.9-2.1s1.9-2.6 2.1-3.9c.1-.5 .1-1.2-.1-1.9l-1-2.5-20.1-33.5c-6.6-11-10.1-23.6-10.1-36.5"
            "l0-5.2c0-36.2 29.4-65.7 65.7-65.7 17.4 0 34.1 6.9 46.4 19.2l1.3 1.3c3.7 3.6 9.6 3.7 13.3 0l1.3-1.3"
            " 4.8-4.4C280.5 21.3 295.1 16 310.4 16z"
        ),
    },
    "sun": {
        "view_box": "0 0 512 512",
        "path": (
            "M361.5 1.2c5 2.1 8.6 6.6 9.6 11.9L391 121l107.9 19.8c5.3 1 9.8 4.6 11.9 9.6"
            "s1.5 10.7-1.6 15.2L446.9 256l62.3 90.3c3.1 4.5 3.7 10.2 1.6 15.2s-6.6 8.6-11.9 9.6"
            "L391 391 371.1 498.9c-1 5.3-4.6 9.8-9.6 11.9s-10.7 1.5-15.2-1.6L256 446.9"
            "l-90.3 62.3c-4.5 3.1-10.2 3.7-15.2 1.6s-8.6-6.6-9.6-11.9L121 391 13.1 371.1"
            "c-5.3-1-9.8-4.6-11.9-9.6s-1.5-10.7 1.6-15.2L65.1 256 2.8 165.7"
            "c-3.1-4.5-3.7-10.2-1.6-15.2s6.6-8.6 11.9-9.6L121 121 140.9 13.1"
            "c1-5.3 4.6-9.8 9.6-11.9s10.7-1.5 15.2 1.6L256 65.1 346.3 2.8"
            "c4.5-3.1 10.2-3.7 15.2-1.6zM160 256a96 96 0 1 1 192 0 96 96 0 1 1 -192 0z"
            "m224 0a128 128 0 1 0 -256 0 128 128 0 1 0 256 0z"
        ),
    },
    "moon": {
        "view_box": "0 0 384 512",
        "path": (
            "M223.5 32C100 32 0 132.3 0 256S100 480 223.5 480c60.6 0 115.5-24.2 155.8-63.4"
            "c5-4.9 6.3-12.5 3.1-18.7s-10.1-9.7-17-8.5c-9.8 1.7-19.8 2.6-30.1 2.6"
            "c-96.9 0-175.5-78.8-175.5-176c0-65.8 36-123.1 89.3-153.3"
            "c6.1-3.5 9.2-10.5 7.7-17.3s-7.3-11.9-14.3-12.5c-6.3-.5-12.6-.8-19-.8z"
        ),
    },
    "power-off": {
        "view_box": "0 0 512 512",
        "path": (
            "M288 32c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 224c0 17.7 14.3 32 32 32"
            "s32-14.3 32-32l0-224zM143.5 120.6c13.6-11.3 15.4-31.5 4.1-45.1s-31.5-15.4-45.1-4.1"
            "C49.7 115.4 16 181.8 16 256c0 132.5 107.5 240 240 240s240-107.5 240-240"
            "c0-74.2-33.8-140.6-86.6-184.6c-13.6-11.3-33.8-9.4-45.1 4.1s-9.4 33.8 4.1 45.1"
            "c38.9 32.3 63.5 81 63.5 135.4c0 97.2-78.8 176-176 176s-176-78.8-176-176"
            "c0-54.4 24.7-103.1 63.5-135.4z"
        ),
    },
    "rotate": {
        "view_box": "0 0 512 512",
        "path": (
            "M142.9 142.9c-17.5 17.5-30.1 38-37.8 59.8c-5.9 16.7-24.2 25.4-40.8 19.5"
            "s-25.4-24.2-19.5-40.8C55.6 150.7 73.2 122 97.6 97.6c87.2-87.2 228.3-87.5 315.8-1"
            "L455 55c6.9-6.9 17.2-8.9 26.2-5.2s14.8 12.5 14.8 22.2l0 128c0 13.3-10.7 24-24 24"
            "l-8.4 0L344 224c-9.7 0-18.5-5.8-22.2-14.8s-1.7-19.3 5.2-26.2l41.1-41.1"
            "c-62.6-61.5-163.1-61.2-225.3 1zM16 312c0-13.3 10.7-24 24-24l7.6 0 .7 0L168 288"
            "c9.7 0 18.5 5.8 22.2 14.8s1.7 19.3-5.2 26.2l-41.1 41.1c62.6 61.5 163.1 61.2 225.3-1"
            "c17.5-17.5 30.1-38 37.8-59.8c5.9-16.7 24.2-25.4 40.8-19.5s25.4 24.2 19.5 40.8"
            "c-10.8 30.6-28.4 59.3-52.9 83.8c-87.2 87.2-228.3 87.5-315.8 1L57 457"
            "c-6.9 6.9-17.2 8.9-26.2 5.2S16 449.7 16 440l0-119.6 0-.7 0-7.6z"
        ),
    },
    "gauge": {
        "view_box": "0 0 512 512",
        "path": (
            "M0 256a256 256 0 1 1 512 0A256 256 0 1 1 0 256zm320 96c0-26.9-16.5-49.9-40-59.3"
            "L280 88c0-13.3-10.7-24-24-24s-24 10.7-24 24l0 204.7c-23.5 9.5-40 32.5-40 59.3"
            "c0 35.3 28.7 64 64 64s64-28.7 64-64zM144 176a32 32 0 1 0 0-64 32 32 0 1 0 0 64z"
            "m-16 80a32 32 0 1 0 -64 0 32 32 0 1 0 64 0zm288 32a32 32 0 1 0 0-64 32 32 0 1 0 0 64z"
            "M400 144a32 32 0 1 0 -64 0 32 32 0 1 0 64 0z"
        ),
    },
    "bolt": {
        "view_box": "0 0 448 512",
        "path": (
            "M349.4 44.6c5.9-13.7 1.5-29.7-10.6-38.5s-28.6-8-39.9 1.8l-256 224"
            "c-10 8.8-13.6 22.9-8.9 35.3S50.7 288 64 288l111.5 0L98.6 467.4"
            "c-5.9 13.7-1.5 29.7 10.6 38.5s28.6 8 39.9-1.8l256-224c10-8.8 13.6-22.9 8.9-35.3"
            "s-16.6-20.7-30-20.7l-111.5 0L349.4 44.6z"
        ),
    },
    "magnifying-glass": {
        "view_box": "0 0 512 512",
        "path": (
            "M416 208c0 45.9-14.9 88.3-40 122.7L502.6 457.4c12.5 12.5 12.5 32.8 0 45.3"
            "s-32.8 12.5-45.3 0L330.7 376c-34.4 25.2-76.8 40-122.7 40C93.1 416 0 322.9 0 208"
            "S93.1 0 208 0S416 93.1 416 208zM208 352a144 144 0 1 0 0-288 144 144 0 1 0 0 288z"
        ),
    },
    "crosshairs": {
        "view_box": "0 0 512 512",
        "path": (
            "M256 0c17.7 0 32 14.3 32 32l0 10.4c93.7 13.9 167.7 88 181.6 181.6l10.4 0"
            "c17.7 0 32 14.3 32 32s-14.3 32-32 32l-10.4 0c-13.9 93.7-88 167.7-181.6 181.6l0 10.4"
            "c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-10.4C130.3 455.7 56.3 381.7 42.4 288L32 288"
            "c-17.7 0-32-14.3-32-32s14.3-32 32-32l10.4 0C56.3 130.3 130.3 56.3 224 42.4L224 32"
            "c0-17.7 14.3-32 32-32zM107.4 288c12.5 58.3 58.4 104.1 116.6 116.6l0-20.6"
            "c0-17.7 14.3-32 32-32s32 14.3 32 32l0 20.6c58.3-12.5 104.1-58.4 116.6-116.6L384 288"
            "c-17.7 0-32-14.3-32-32s14.3-32 32-32l20.6 0C392.1 165.7 346.3 119.9 288 107.4l0 20.6"
            "c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-20.6C165.7 119.9 119.9 165.7 107.4 224l20.6 0"
            "c17.7 0 32 14.3 32 32s-14.3 32-32 32l-20.6 0zM256 224a32 32 0 1 1 0 64 32 32 0 1 1 0-64z"
        ),
    },
    "fingerprint": {
        "view_box": "0 0 512 512",
        "path": (
            "M48 256C48 141.1 141.1 48 256 48c63.1 0 119.6 28.1 157.8 72.5c8.6 10.1 23.8 11.2 33.8 2.6"
            "s11.2-23.8 2.6-33.8C403.3 34.6 333.7 0 256 0C114.6 0 0 114.6 0 256l0 40c0 13.3 10.7 24 24 24"
            "s24-10.7 24-24l0-40zm458.5-52.9c-2.7-13-15.5-21.3-28.4-18.5s-21.3 15.5-18.5 28.4"
            "c2.9 13.9 4.5 28.3 4.5 43.1l0 40c0 13.3 10.7 24 24 24s24-10.7 24-24l0-40"
            "c0-18.1-1.9-35.8-5.5-52.9zM256 80c-19 0-37.4 3-54.5 8.6c-15.2 5-18.7 23.7-8.3 35.9"
            "c7.1 8.3 18.8 10.8 29.4 7.9c10.6-2.9 21.8-4.4 33.4-4.4c70.7 0 128 57.3 128 128l0 24.9"
            "c0 25.2-1.5 50.3-4.4 75.3c-1.7 14.6 9.4 27.8 24.2 27.8c11.8 0 21.9-8.6 23.3-20.3"
            "c3.3-27.4 5-55 5-82.7l0-24.9c0-97.2-78.8-176-176-176zM150.7 148.7"
            "c-9.1-10.6-25.3-11.4-33.9-.4C93.7 178 80 215.4 80 256l0 24.9c0 24.2-2.6 48.4-7.8 71.9"
            "C68.8 368.4 80.1 384 96.1 384c10.5 0 19.9-7 22.2-17.3c6.4-28.1 9.7-56.8 9.7-85.8l0-24.9"
            "c0-27.2 8.5-52.4 22.9-73.1c7.2-10.4 8-24.6-.2-34.2zM256 160c-53 0-96 43-96 96l0 24.9"
            "c0 35.9-4.6 71.5-13.8 106.1c-3.8 14.3 6.7 29 21.5 29c9.5 0 17.9-6.2 20.4-15.4"
            "c10.5-39 15.9-79.2 15.9-119.7l0-24.9c0-28.7 23.3-52 52-52s52 23.3 52 52l0 24.9"
            "c0 36.3-3.5 72.4-10.4 107.9c-2.7 13.9 7.7 27.2 21.8 27.2c10.2 0 19-7 21-17"
            "c7.7-38.8 11.6-78.3 11.6-118.1l0-24.9c0-53-43-96-96-96zm24 96c0-13.3-10.7-24-24-24"
            "s-24 10.7-24 24l0 24.9c0 59.9-11 119.3-32.5 175.2l-5.9 15.3c-4.8 12.4 1.4 26.3 13.8 31"
            "s26.3-1.4 31-13.8l5.9-15.3C267.9 411.9 280 346.7 280 280.9l0-24.9z"
        ),
    },
    "arrow-trend-down": {
        "view_box": "0 0 576 512",
        "path": (
            "M384 352c-17.7 0-32 14.3-32 32s14.3 32 32 32l160 0c17.7 0 32-14.3 32-32l0-160"
            "c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 82.7L342.6 137.4c-12.5-12.5-32.8-12.5-45.3 0"
            "L192 242.7 54.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l160 160"
            "c12.5 12.5 32.8 12.5 45.3 0L320 205.3 466.7 352 384 352z"
        ),
    },
    "circle-nodes": {
        "view_box": "0 0 512 512",
        "path": (
            "M418.4 157.9c35.3-8.3 61.6-40 61.6-77.9c0-44.2-35.8-80-80-80c-43.4 0-78.7 34.5-80 77.5"
            "L136.2 151.1C121.7 136.8 101.9 128 80 128c-44.2 0-80 35.8-80 80s35.8 80 80 80"
            "c12.2 0 23.8-2.7 34.1-7.6L259.7 407.8c-2.4 7.6-3.7 15.8-3.7 24.2c0 44.2 35.8 80 80 80"
            "s80-35.8 80-80c0-27.7-14-52.1-35.4-66.4l37.8-207.7zM156.3 232.2c2.2-6.9 3.5-14.2 3.7-21.7"
            "l183.8-73.5c3.6 3.5 7.4 6.7 11.6 9.5L317.6 354.1c-5.5 1.3-10.8 3.1-15.8 5.5"
            "L156.3 232.2z"
        ),
    },
    "arrow-up-right-dots": {
        "view_box": "0 0 576 512",
        "path": (
            "M160 0c-17.7 0-32 14.3-32 32s14.3 32 32 32l50.7 0L9.4 265.4c-12.5 12.5-12.5 32.8 0 45.3"
            "s32.8 12.5 45.3 0L256 109.3l0 50.7c0 17.7 14.3 32 32 32s32-14.3 32-32l0-128"
            "c0-17.7-14.3-32-32-32L160 0zM576 80a48 48 0 1 0 -96 0 48 48 0 1 0 96 0z"
            "M448 208a48 48 0 1 0 -96 0 48 48 0 1 0 96 0zM400 384a48 48 0 1 0 0-96"
            " 48 48 0 1 0 0 96zm48 80a48 48 0 1 0 -96 0 48 48 0 1 0 96 0zm128 0"
            "a48 48 0 1 0 -96 0 48 48 0 1 0 96 0zM272 384a48 48 0 1 0 0-96 48 48 0 1 0 0 96z"
            "m48 80a48 48 0 1 0 -96 0 48 48 0 1 0 96 0zM144 512a48 48 0 1 0 0-96"
            " 48 48 0 1 0 0 96zM576 336a48 48 0 1 0 -96 0 48 48 0 1 0 96 0z"
            "m-48-80a48 48 0 1 0 0-96 48 48 0 1 0 0 96z"
        ),
    },
    "droplet": {
        "view_box": "0 0 384 512",
        "path": (
            "M192 512C86 512 0 426 0 320C0 228.8 130.2 57.7 166.6 11.7C172.6 4.2 181.5 0 191.1 0"
            "l1.8 0c9.6 0 18.5 4.2 24.5 11.7C253.8 57.7 384 228.8 384 320c0 106-86 192-192 192z"
            "M96 336c0-8.8-7.2-16-16-16s-16 7.2-16 16c0 61.9 50.1 112 112 112c8.8 0 16-7.2 16-16"
            "s-7.2-16-16-16c-44.2 0-80-35.8-80-80z"
        ),
    },
    "table-cells-large": {
        "view_box": "0 0 512 512",
        "path": (
            "M448 96l0 128-160 0 0-128 160 0zm0 192l0 128-160 0 0-128 160 0zM224 224L64 224 64 96"
            "l160 0 0 128zM64 288l160 0 0 128L64 416l0-128zM64 32C28.7 32 0 60.7 0 96L0 416"
            "c0 35.3 28.7 64 64 64l384 0c35.3 0 64-28.7 64-64l0-320c0-35.3-28.7-64-64-64L64 32z"
        ),
    },
    "bezier-curve": {
        "view_box": "0 0 640 512",
        "path": (
            "M296 136l0-48 48 0 0 48-48 0zM288 32c-26.5 0-48 21.5-48 48l0 4L121.6 84"
            "C111.2 62.7 89.3 48 64 48C28.7 48 0 76.7 0 112s28.7 64 64 64c25.3 0 47.2-14.7 57.6-36"
            "l66.9 0c-58.9 39.6-98.9 105-104 180L80 320c-26.5 0-48 21.5-48 48l0 64"
            "c0 26.5 21.5 48 48 48l64 0c26.5 0 48-21.5 48-48l0-64c0-26.5-21.5-48-48-48l-3.3 0"
            "c5.9-67 48.5-123.4 107.5-149.1c8.6 12.7 23.2 21.1 39.8 21.1l64 0"
            "c16.6 0 31.1-8.4 39.8-21.1c59 25.7 101.6 82.1 107.5 149.1l-3.3 0"
            "c-26.5 0-48 21.5-48 48l0 64c0 26.5 21.5 48 48 48l64 0c26.5 0 48-21.5 48-48l0-64"
            "c0-26.5-21.5-48-48-48l-4.5 0c-5-75-45.1-140.4-104-180l66.9 0"
            "c10.4 21.3 32.3 36 57.6 36c35.3 0 64-28.7 64-64s-28.7-64-64-64"
            "c-25.3 0-47.2 14.7-57.6 36L400 84l0-4c0-26.5-21.5-48-48-48l-64 0z"
            "M88 376l48 0 0 48-48 0 0-48zm416 48l0-48 48 0 0 48-48 0z"
        ),
    },
    "folder-open": {
        "view_box": "0 0 576 512",
        "path": (
            "M88.7 223.8L0 375.8 0 96C0 60.7 28.7 32 64 32l117.5 0c17 0 33.3 6.7 45.3 18.7"
            "l26.5 26.5c12 12 28.3 18.7 45.3 18.7L416 96c35.3 0 64 28.7 64 64l0 32-336 0"
            "c-22.8 0-43.8 12.1-55.3 31.8zm27.6 16.1C122.1 230 132.6 224 144 224l400 0"
            "c11.5 0 22 6.1 27.7 16.1s5.7 22.2-.1 32.1l-112 192C453.9 474 443.4 480 432 480L32 480"
            "c-11.5 0-22-6.1-27.7-16.1s-5.7-22.2 .1-32.1l112-192z"
        ),
    },
    "earth-americas": {
        "view_box": "0 0 512 512",
        "path": (
            "M57.7 193l9.4 16.4c8.3 14.5 21.9 25.2 38 29.8L163 255.7c17.2 4.9 29 20.6 29 38.5"
            "l0 39.9c0 11 6.2 21 16 25.9s16 14.9 16 25.9l0 39c0 15.6 14.9 26.9 29.9 22.6"
            "c16.1-4.6 28.6-17.5 32.7-33.8l2.8-11.2c4.2-16.9 15.2-31.4 30.3-40l8.1-4.6"
            "c15-8.5 24.2-24.5 24.2-41.7l0-8.3c0-12.7-5.1-24.9-14.1-33.9l-3.9-3.9"
            "c-9-9-21.2-14.1-33.9-14.1L257 256c-11.1 0-22.1-2.9-31.8-8.4l-34.5-19.7"
            "c-4.3-2.5-7.6-6.5-9.2-11.2c-3.2-9.6 1.1-20 10.2-24.5l5.9-3c6.6-3.3 14.3-3.9 21.3-1.5"
            "l23.2 7.7c8.2 2.7 17.2-.4 21.9-7.5c4.7-7 4.2-16.3-1.2-22.8l-13.6-16.3"
            "c-10-12-9.9-29.5 .3-41.3l15.7-18.3c8.8-10.3 10.2-25 3.5-36.7l-2.4-4.2"
            "c-3.5-.2-6.9-.3-10.4-.3C163.1 48 84.4 108.9 57.7 193zM464 256"
            "c0-36.8-9.6-71.4-26.4-101.5L412 164.8c-15.7 6.3-23.8 23.8-18.5 39.8l16.9 50.7"
            "c3.5 10.4 12 18.3 22.6 20.9l29.1 7.3c1.2-9 1.8-18.2 1.8-27.5z"
            "M0 256a256 256 0 1 1 512 0A256 256 0 1 1 0 256z"
        ),
    },
    "list-check": {
        "view_box": "0 0 512 512",
        "path": (
            "M152.1 38.2c9.9 8.9 10.7 24 1.8 33.9l-72 80c-4.4 4.9-10.6 7.8-17.2 7.9"
            "s-12.9-2.4-17.6-7L7 113C-2.3 103.6-2.3 88.4 7 79s24.6-9.4 33.9 0l22.1 22.1"
            " 55.1-61.2c8.9-9.9 24-10.7 33.9-1.8zm0 160c9.9 8.9 10.7 24 1.8 33.9l-72 80"
            "c-4.4 4.9-10.6 7.8-17.2 7.9s-12.9-2.4-17.6-7L7 273c-9.4-9.4-9.4-24.6 0-33.9"
            "s24.6-9.4 33.9 0l22.1 22.1 55.1-61.2c8.9-9.9 24-10.7 33.9-1.8zM224 96"
            "c0-17.7 14.3-32 32-32l224 0c17.7 0 32 14.3 32 32s-14.3 32-32 32l-224 0"
            "c-17.7 0-32-14.3-32-32zm0 160c0-17.7 14.3-32 32-32l224 0c17.7 0 32 14.3 32 32"
            "s-14.3 32-32 32l-224 0c-17.7 0-32-14.3-32-32zM160 416c0-17.7 14.3-32 32-32l288 0"
            "c17.7 0 32 14.3 32 32s-14.3 32-32 32l-288 0c-17.7 0-32-14.3-32-32z"
            "M48 368a48 48 0 1 1 0 96 48 48 0 1 1 0-96z"
        ),
    },
    "flag": {
        "view_box": "0 0 448 512",
        "path": (
            "M64 32C64 14.3 49.7 0 32 0S0 14.3 0 32L0 64 0 368 0 480c0 17.7 14.3 32 32 32"
            "s32-14.3 32-32l0-128 64.3-16.1c41.1-10.3 84.6-5.5 122.5 13.4"
            "c44.2 22.1 95.5 24.8 141.7 7.4l34.7-13c12.5-4.7 20.8-16.6 20.8-30l0-247.7"
            "c0-23-24.2-38-44.8-27.7l-9.6 4.8c-46.3 23.2-100.8 23.2-147.1 0"
            "c-35.1-17.6-75.4-22-113.5-12.5L64 48l0-16z"
        ),
    },
    "sliders": {
        "view_box": "0 0 512 512",
        "path": (
            "M0 416c0 17.7 14.3 32 32 32l54.7 0c12.3 28.3 40.5 48 73.3 48s61-19.7 73.3-48"
            "L480 448c17.7 0 32-14.3 32-32s-14.3-32-32-32l-246.7 0c-12.3-28.3-40.5-48-73.3-48"
            "s-61 19.7-73.3 48L32 384c-17.7 0-32 14.3-32 32zm128 0a32 32 0 1 1 64 0"
            " 32 32 0 1 1 -64 0zM320 256a32 32 0 1 1 64 0 32 32 0 1 1 -64 0zm32-80"
            "c-32.8 0-61 19.7-73.3 48L32 224c-17.7 0-32 14.3-32 32s14.3 32 32 32l246.7 0"
            "c12.3 28.3 40.5 48 73.3 48s61-19.7 73.3-48l54.7 0c17.7 0 32-14.3 32-32"
            "s-14.3-32-32-32l-54.7 0c-12.3-28.3-40.5-48-73.3-48zM192 128a32 32 0 1 1 0-64"
            " 32 32 0 1 1 0 64zm73.3-64C253 35.7 224.8 16 192 16s-61 19.7-73.3 48L32 64"
            "C14.3 64 0 78.3 0 96s14.3 32 32 32l86.7 0c12.3 28.3 40.5 48 73.3 48"
            "s61-19.7 73.3-48L480 128c17.7 0 32-14.3 32-32s-14.3-32-32-32L265.3 64z"
        ),
    },
    "puzzle-piece": {
        "view_box": "0 0 512 512",
        "path": (
            "M192 104.8c0-9.2-5.8-17.3-13.2-22.8C167.2 73.3 160 61.3 160 48c0-26.5 28.7-48 64-48"
            "s64 21.5 64 48c0 13.3-7.2 25.3-18.8 34c-7.4 5.5-13.2 13.6-13.2 22.8"
            "c0 12.8 10.4 23.2 23.2 23.2l56.8 0c26.5 0 48 21.5 48 48l0 56.8"
            "c0 12.8 10.4 23.2 23.2 23.2c9.2 0 17.3-5.8 22.8-13.2c8.7-11.6 20.7-18.8 34-18.8"
            "c26.5 0 48 28.7 48 64s-21.5 64-48 64c-13.3 0-25.3-7.2-34-18.8"
            "c-5.5-7.4-13.6-13.2-22.8-13.2c-12.8 0-23.2 10.4-23.2 23.2L384 464"
            "c0 26.5-21.5 48-48 48l-56.8 0c-12.8 0-23.2-10.4-23.2-23.2c0-9.2 5.8-17.3 13.2-22.8"
            "c11.6-8.7 18.8-20.7 18.8-34c0-26.5-28.7-48-64-48s-64 21.5-64 48"
            "c0 13.3 7.2 25.3 18.8 34c7.4 5.5 13.2 13.6 13.2 22.8c0 12.8-10.4 23.2-23.2 23.2"
            "L48 512c-26.5 0-48-21.5-48-48L0 343.2C0 330.4 10.4 320 23.2 320"
            "c9.2 0 17.3 5.8 22.8 13.2C54.7 344.8 66.7 352 80 352c26.5 0 48-28.7 48-64"
            "s-21.5-64-48-64c-13.3 0-25.3 7.2-34 18.8C40.5 250.2 32.4 256 23.2 256"
            "C10.4 256 0 245.6 0 232.8L0 176c0-26.5 21.5-48 48-48l120.8 0"
            "c12.8 0 23.2-10.4 23.2-23.2z"
        ),
    },
    "stethoscope": {
        "view_box": "0 0 576 512",
        "path": (
            "M142.4 21.9c5.6 16.8-3.5 34.9-20.2 40.5L96 71.1 96 192c0 53 43 96 96 96s96-43 96-96"
            "l0-120.9-26.1-8.7c-16.8-5.6-25.8-23.7-20.2-40.5s23.7-25.8 40.5-20.2l26.1 8.7"
            "C334.4 19.1 352 43.5 352 71.1L352 192c0 77.2-54.6 141.6-127.3 156.7"
            "C231 404.6 278.4 448 336 448c61.9 0 112-50.1 112-112l0-70.7c-28.3-12.3-48-40.5-48-73.3"
            "c0-44.2 35.8-80 80-80s80 35.8 80 80c0 32.8-19.7 61-48 73.3l0 70.7"
            "c0 97.2-78.8 176-176 176c-92.9 0-168.9-71.9-175.5-163.1C87.2 334.2 32 269.6 32 192"
            "L32 71.1c0-27.5 17.6-52 43.8-60.7l26.1-8.7c16.8-5.6 34.9 3.5 40.5 20.2z"
            "M480 224a32 32 0 1 0 0-64 32 32 0 1 0 0 64z"
        ),
    },
    "database": {
        "view_box": "0 0 448 512",
        "path": (
            "M448 80l0 48c0 44.2-100.3 80-224 80S0 172.2 0 128L0 80C0 35.8 100.3 0 224 0"
            "S448 35.8 448 80zM393.2 214.7c20.8-7.4 39.9-16.9 54.8-28.6L448 288"
            "c0 44.2-100.3 80-224 80S0 332.2 0 288L0 186.1c14.9 11.8 34 21.2 54.8 28.6"
            "C99.7 230.7 159.5 240 224 240s124.3-9.3 169.2-25.3zM0 346.1"
            "c14.9 11.8 34 21.2 54.8 28.6C99.7 390.7 159.5 400 224 400s124.3-9.3 169.2-25.3"
            "c20.8-7.4 39.9-16.9 54.8-28.6l0 85.9c0 44.2-100.3 80-224 80S0 476.2 0 432l0-85.9z"
        ),
    },
    "gear": {
        "view_box": "0 0 512 512",
        "path": (
            "M495.9 166.6c3.2 8.7 .5 18.4-6.4 24.6l-43.3 39.4c1.1 8.3 1.7 16.8 1.7 25.4"
            "s-.6 17.1-1.7 25.4l43.3 39.4c6.9 6.2 9.6 15.9 6.4 24.6c-4.4 11.9-9.7 23.3-15.8 34.3"
            "l-4.7 8.1c-6.6 11-14 21.4-22.1 31.2c-5.9 7.2-15.7 9.6-24.5 6.8l-55.7-17.7"
            "c-13.4 10.3-28.2 18.9-44 25.4l-12.5 57.1c-2 9.1-9 16.3-18.2 17.8"
            "c-13.8 2.3-28 3.5-42.5 3.5s-28.7-1.2-42.5-3.5c-9.2-1.5-16.2-8.7-18.2-17.8"
            "l-12.5-57.1c-15.8-6.5-30.6-15.1-44-25.4L83.1 425.9c-8.8 2.8-18.6 .3-24.5-6.8"
            "c-8.1-9.8-15.5-20.2-22.1-31.2l-4.7-8.1c-6.1-11-11.4-22.4-15.8-34.3"
            "c-3.2-8.7-.5-18.4 6.4-24.6l43.3-39.4C64.6 273.1 64 264.6 64 256"
            "s.6-17.1 1.7-25.4L22.4 191.2c-6.9-6.2-9.6-15.9-6.4-24.6c4.4-11.9 9.7-23.3 15.8-34.3"
            "l4.7-8.1c6.6-11 14-21.4 22.1-31.2c5.9-7.2 15.7-9.6 24.5-6.8l55.7 17.7"
            "c13.4-10.3 28.2-18.9 44-25.4l12.5-57.1c2-9.1 9-16.3 18.2-17.8"
            "C227.3 1.2 241.5 0 256 0s28.7 1.2 42.5 3.5c9.2 1.5 16.2 8.7 18.2 17.8"
            "l12.5 57.1c15.8 6.5 30.6 15.1 44 25.4l55.7-17.7c8.8-2.8 18.6-.3 24.5 6.8"
            "c8.1 9.8 15.5 20.2 22.1 31.2l4.7 8.1c6.1 11 11.4 22.4 15.8 34.3z"
            "M256 336a80 80 0 1 0 0-160 80 80 0 1 0 0 160z"
        ),
    },
    "arrow-rotate-left": {
        "view_box": "0 0 512 512",
        "path": (
            "M125.7 160l50.3 0c17.7 0 32 14.3 32 32s-14.3 32-32 32L48 224"
            "c-17.7 0-32-14.3-32-32L16 64c0-17.7 14.3-32 32-32s32 14.3 32 32"
            "l0 51.2L97.6 97.6c87.5-87.5 229.3-87.5 316.8 0s87.5 229.3 0 316.8"
            "s-229.3 87.5-316.8 0c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0"
            "c62.5 62.5 163.8 62.5 226.3 0s62.5-163.8 0-226.3s-163.8-62.5-226.3 0"
            "L125.7 160z"
        ),
    },
    "arrows-to-dot": {
        "view_box": "0 0 512 512",
        "path": (
            "M256 0c17.7 0 32 14.3 32 32l0 32 32 0c12.9 0 24.6 7.8 29.6 19.8"
            "s2.2 25.7-6.9 34.9l-64 64c-12.5 12.5-32.8 12.5-45.3 0l-64-64"
            "c-9.2-9.2-11.9-22.9-6.9-34.9s16.6-19.8 29.6-19.8l32 0 0-32"
            "c0-17.7 14.3-32 32-32zM169.4 393.4l64-64c12.5-12.5 32.8-12.5 45.3 0"
            "l64 64c9.2 9.2 11.9 22.9 6.9 34.9s-16.6 19.8-29.6 19.8l-32 0 0 32"
            "c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-32-32 0c-12.9 0-24.6-7.8-29.6-19.8"
            "s-2.2-25.7 6.9-34.9zM32 224l32 0 0-32c0-12.9 7.8-24.6 19.8-29.6"
            "s25.7-2.2 34.9 6.9l64 64c12.5 12.5 12.5 32.8 0 45.3l-64 64"
            "c-9.2 9.2-22.9 11.9-34.9 6.9s-19.8-16.6-19.8-29.6l0-32-32 0"
            "c-17.7 0-32-14.3-32-32s14.3-32 32-32zm297.4 54.6c-12.5-12.5-12.5-32.8 0-45.3"
            "l64-64c9.2-9.2 22.9-11.9 34.9-6.9s19.8 16.6 19.8 29.6l0 32 32 0"
            "c17.7 0 32 14.3 32 32s-14.3 32-32 32l-32 0 0 32c0 12.9-7.8 24.6-19.8 29.6"
            "s-25.7 2.2-34.9-6.9l-64-64zM256 224a32 32 0 1 1 0 64 32 32 0 1 1 0-64z"
        ),
    },
    "filter": {
        "view_box": "0 0 512 512",
        "path": (
            "M32 64C19.1 64 7.4 71.8 2.4 83.8S.2 109.5 9.4 118.6L192 301.3"
            " 192 416c0 8.5 3.4 16.6 9.4 22.6l64 64c9.2 9.2 22.9 11.9 34.9 6.9"
            "S320 492.9 320 480l0-178.7 182.6-182.6c9.2-9.2 11.9-22.9 6.9-34.9"
            "S492.9 64 480 64L32 64z"
        ),
    },
}


_FA_REGULAR_ICONS = {
    "sun": {
        "view_box": "0 0 512 512",
        "path": (
            "M375.7 19.7c-1.5-8-6.9-14.7-14.4-17.8s-16.1-2.2-22.8 2.4L256 61.1"
            " 173.5 4.2c-6.7-4.6-15.3-5.5-22.8-2.4s-12.9 9.8-14.4 17.8l-18.1 98.5"
            "L19.7 136.3c-8 1.5-14.7 6.9-17.8 14.4s-2.2 16.1 2.4 22.8L61.1 256"
            " 4.2 338.5c-4.6 6.7-5.5 15.3-2.4 22.8s9.8 13 17.8 14.4l98.5 18.1"
            " 18.1 98.5c1.5 8 6.9 14.7 14.4 17.8s16.1 2.2 22.8-2.4L256 450.9"
            "l82.5 56.9c6.7 4.6 15.3 5.5 22.8 2.4s12.9-9.8 14.4-17.8l18.1-98.5"
            " 98.5-18.1c8-1.5 14.7-6.9 17.8-14.4s2.2-16.1-2.4-22.8L450.9 256"
            "l56.9-82.5c4.6-6.7 5.5-15.3 2.4-22.8s-9.8-12.9-17.8-14.4l-98.5-18.1"
            "L375.7 19.7zM269.6 110l65.6-45.2 14.4 78.3c1.8 9.8 9.5 17.5 19.3 19.3"
            "l78.3 14.4L402 242.4c-5.7 8.2-5.7 19 0 27.2l45.2 65.6-78.3 14.4"
            "c-9.8 1.8-17.5 9.5-19.3 19.3l-14.4 78.3L269.6 402c-8.2-5.7-19-5.7-27.2 0"
            "l-65.6 45.2-14.4-78.3c-1.8-9.8-9.5-17.5-19.3-19.3L64.8 335.2 110 269.6"
            "c5.7-8.2 5.7-19 0-27.2L64.8 176.8l78.3-14.4c9.8-1.8 17.5-9.5 19.3-19.3"
            "l14.4-78.3L242.4 110c8.2 5.7 19 5.7 27.2 0zM256 368a112 112 0 1 0 0-224"
            " 112 112 0 1 0 0 224zM192 256a64 64 0 1 1 128 0 64 64 0 1 1 -128 0z"
        ),
    },
    "moon": {
        "view_box": "0 0 384 512",
        "path": (
            "M144.7 98.7c-21 34.1-33.1 74.3-33.1 117.3c0 98 62.8 181.4 150.4 211.7"
            "c-12.4 2.8-25.3 4.3-38.6 4.3C126.6 432 48 353.3 48 256c0-68.9 39.4-128.4 96.8-157.3"
            "zm62.1-66C91.1 41.2 0 137.9 0 256C0 379.7 100 480 223.5 480c47.8 0 92-15 128.4-40.6"
            "c1.9-1.3 3.7-2.7 5.5-4c4.8-3.6 9.4-7.4 13.9-11.4c2.7-2.4 5.3-4.8 7.9-7.3"
            "c5-4.9 6.3-12.5 3.1-18.7s-10.1-9.7-17-8.5c-3.7 .6-7.4 1.2-11.1 1.6"
            "c-5 .5-10.1 .9-15.3 1c-1.2 0-2.5 0-3.7 0l-.3 0c-96.8-.2-175.2-78.9-175.2-176"
            "c0-54.8 24.9-103.7 64.1-136c1-.9 2.1-1.7 3.2-2.6c4-3.2 8.2-6.2 12.5-9"
            "c3.1-2 6.3-4 9.6-5.8c6.1-3.5 9.2-10.5 7.7-17.3s-7.3-11.9-14.3-12.5"
            "c-3.6-.3-7.1-.5-10.7-.6c-2.7-.1-5.5-.1-8.2-.1c-3.3 0-6.5 .1-9.8 .2"
            "c-2.3 .1-4.6 .2-6.9 .4z"
        ),
    },
    "folder-open": {
        "view_box": "0 0 576 512",
        "path": (
            "M384 480l48 0c11.4 0 21.9-6 27.6-15.9l112-192c5.8-9.9 5.8-22.1 .1-32.1"
            "S555.5 224 544 224l-400 0c-11.4 0-21.9 6-27.6 15.9L48 357.1 48 96"
            "c0-8.8 7.2-16 16-16l117.5 0c4.2 0 8.3 1.7 11.3 4.7l26.5 26.5"
            "c21 21 49.5 32.8 79.2 32.8L416 144c8.8 0 16 7.2 16 16l0 32 48 0 0-32"
            "c0-35.3-28.7-64-64-64L298.5 96c-17 0-33.3-6.7-45.3-18.7L226.7 50.7"
            "c-12-12-28.3-18.7-45.3-18.7L64 32C28.7 32 0 60.7 0 96L0 416"
            "c0 35.3 28.7 64 64 64l23.7 0L384 480z"
        ),
    },
    "flag": {
        "view_box": "0 0 448 512",
        "path": (
            "M48 24C48 10.7 37.3 0 24 0S0 10.7 0 24L0 64 0 350.5 0 400l0 88"
            "c0 13.3 10.7 24 24 24s24-10.7 24-24l0-100 80.3-20.1"
            "c41.1-10.3 84.6-5.5 122.5 13.4c44.2 22.1 95.5 24.8 141.7 7.4l34.7-13"
            "c12.5-4.7 20.8-16.6 20.8-30l0-279.7c0-23-24.2-38-44.8-27.7l-9.6 4.8"
            "c-46.3 23.2-100.8 23.2-147.1 0c-35.1-17.6-75.4-22-113.5-12.5L48 52l0-28z"
            "m0 77.5l96.6-24.2c27-6.7 55.5-3.6 80.4 8.8c54.9 27.4 118.7 29.7 175 6.8"
            "l0 241.8-24.4 9.1c-33.7 12.6-71.2 10.7-103.4-5.4c-48.2-24.1-103.3-30.1-155.6-17.1"
            "L48 338.5l0-237z"
        ),
    },
}


def fa_css_mask_uri(name, *, style="solid"):
    """Return one embedded Font Awesome SVG suitable for a CSS mask."""
    registry = _FA_REGULAR_ICONS if str(style) == "regular" else _FA_INLINE_ICONS
    spec = registry.get(str(name))
    if spec is None:
        raise KeyError(f"unknown Font Awesome {style} icon: {name}")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{spec['view_box']}'>"
        f"<path d=\"{spec['path']}\"></path></svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def fa_markdown_icon(name, *, alt="", style="solid"):
    """Return a tiny embedded Font Awesome SVG as a Markdown image label."""
    registry = _FA_REGULAR_ICONS if str(style) == "regular" else _FA_INLINE_ICONS
    spec = registry.get(str(name))
    if spec is None:
        raise KeyError(f"unknown Font Awesome {style} icon: {name}")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{spec['view_box']}'>"
        f"<path fill='#737780' d=\"{spec['path']}\"/></svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    safe_alt = str(alt).replace("]", "")
    return f"![{safe_alt}](data:image/svg+xml;base64,{encoded})"


PAGE_ICONS = {
    "Dashboard": ":material/space_dashboard:",
    "Search": ":material/search:",
    "Auto Search": ":material/bolt_boost:",
    "Auto": ":material/bolt_boost:",
    "Pipelines": ":material/flowsheet:",
    "Builder": ":material/flowsheet:",
    "Target": ":material/target:",
    "Curves": ":material/line_curve:",
    "Candidates": ":material/group:",
    "Candidate Generate": ":material/group_add:",
    "Candidate Pools": ":material/group:",
    "Generate Candidates": ":material/group_add:",
    "Corpora": ":material/library_books:",
    "Libraries": ":material/library_books:",
    "Points": ":material/grain:",
    "Results": ":material/work_history:",
    "External Catalog": ":material/public:",
    "Catalogs": ":material/public:",
    "Campaigns": ":material/bookmark:",
    "Jobs": ":material/work_history:",
    "Analyze": ":material/grain:",
    "Rank Proof": ":material/trending_down:",
    "Descent": ":material/trending_down:",
    "Lattices": ":material/grid_4x4:",
    "Quartics": QUARTICS_ICON,
    "Lattices & Heights": ":material/grid_4x4:",
    "Independence": ":material/schema:",
    "Saturation": ":material/salinity:",
    "Plugins": ":material/extension:",
    "Families": ":material/extension:",
    "Extensions": ":material/extension:",
    "Database": ":material/database:",
    "Diagnostics": ":material/health_and_safety:",
    "Settings": ":material/settings:",
    "ICARM Submit": ":material/cloud_upload:",
}

_BREADCRUMB_ROUTE_LABELS = {
    "Corpora": "Libraries",
    "External Catalog": "Catalogs",
}


def page_breadcrumb_path(name, state=None):
    """Return the canonical root-to-page breadcrumb for one rendered page."""
    state = st.session_state if state is None else state
    route = str(state.get("rh_page") or name or "Dashboard")
    if route.startswith("extension:"):
        leaf = str(name or route)
    else:
        leaf = _BREADCRUMB_ROUTE_LABELS.get(route, route)
    if route == "Dashboard":
        return (("Dashboard", None),)
    return (("Dashboard", "Dashboard"), (leaf, None))


def _set_breadcrumb_route(route):
    st.session_state["rh_page"] = str(route)


def _render_page_breadcrumb(name):
    path = page_breadcrumb_path(name)
    if len(path) == 1:
        return

    leaf = str(path[-1][0])
    breadcrumb_key = rh_key(f"page-breadcrumb-{leaf}")
    trailing = json.dumps(f"› {leaf}", ensure_ascii=False)
    with st.container(key=breadcrumb_key):
        st.html(
            "<style>"
            f"div[class*='st-key-{breadcrumb_key}']{{"
            "margin-top:-.5rem!important;margin-bottom:0!important;"
            "padding:0!important;min-height:1.05rem!important;"
            "overflow:visible!important}"
            f"div[class*='st-key-{breadcrumb_key}'] [data-testid='stButton']{{"
            "position:relative!important;width:max-content!important;"
            "margin:0!important;padding:0!important;overflow:visible!important}"
            f"div[class*='st-key-{breadcrumb_key}'] [data-testid='stButton']::after{{"
            f"content:{trailing};"
            "position:absolute;left:calc(100% + .18rem);top:50%;transform:translateY(-50%);"
            "white-space:nowrap;pointer-events:none;"
            "font-size:.78rem;font-weight:600;line-height:1.05rem;"
            "color:var(--rh-text-secondary,#31333f)}"
            f"div[class*='st-key-{breadcrumb_key}'] button[kind='tertiary']{{"
            "padding:0!important;min-height:1.05rem!important;height:1.05rem!important;"
            "font-size:.78rem!important;font-weight:500!important;line-height:1.05rem!important;"
            "white-space:nowrap!important;color:var(--rh-muted,#737780)!important;"
            "box-shadow:none!important;background:transparent!important}"
            f"div[class*='st-key-{breadcrumb_key}'] button[kind='tertiary']:hover{{"
            "color:var(--rh-text,#31333f)!important;background:transparent!important}"
            "</style>"
        )
        st.button(
            str(path[0][0]),
            key=rh_key(f"breadcrumb-{leaf}-dashboard"),
            type="tertiary",
            width="content",
            on_click=_set_breadcrumb_route,
            args=(str(path[0][1]),),
        )
        st.html(
            "<span aria-current='page' class='rh-page-breadcrumb-current-sr'>"
            + html.escape(leaf)
            + "</span>"
            "<style>.rh-page-breadcrumb-current-sr{position:absolute!important;"
            "width:1px!important;height:1px!important;padding:0!important;"
            "margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;"
            "white-space:nowrap!important;border:0!important}</style>"
        )


###
# A HUMAN WAS HERE :-)
###
def title(name, subtitle=None, kicker=None, icon=None):
    """Render one consistent application-level page header.

    ``subtitle`` and ``kicker`` remain compatibility arguments for existing
    callers, but page-level explanatory subtitles are intentionally suppressed.
    The canonical shell route is shown instead through the shared breadcrumb.
    """
    _ = subtitle, kicker
    icon = PAGE_ICONS.get(str(name), icon or "analytics")
    icon_text = str(icon)
    with st.container(key=rh_key(f"page-header-{name}")):
        if icon_text.startswith("text:"):
            st.title(f"{icon_text[5:]} {name}", anchor=False)
        elif icon_text.startswith(("rh:", "fa:", "fa-regular:")):
            try:
                if icon_text.startswith("rh:"):
                    mask_uri = rh_css_mask_uri(icon_text.split(":", 1)[1])
                else:
                    regular = icon_text.startswith("fa-regular:")
                    fa_name = icon_text.split(":", 1)[1]
                    mask_uri = fa_css_mask_uri(
                        fa_name,
                        style="regular" if regular else "solid",
                    )
            except KeyError:
                st.title(str(name), anchor=False)
            else:
                header_key = rh_key(f"page-header-{name}")
                st.html(
                    "<style>"
                    f"div[class*='st-key-{header_key}'] h1::before{{"
                    "content:'';display:inline-block;width:.82em;height:.82em;"
                    "margin-right:.4rem;vertical-align:-.08em;background:currentColor;"
                    f"-webkit-mask:url('{mask_uri}') center/contain no-repeat;"
                    f"mask:url('{mask_uri}') center/contain no-repeat;"
                    "}</style>"
                )
                st.title(str(name), anchor=False)
        else:
            material_icon = icon_text.removeprefix(":material/").removesuffix(":")
            st.title(f":material/{material_icon}: {name}", anchor=False)
        _render_page_breadcrumb(name)

def section_title(name, subtitle=None):
    st.subheader(str(name))
    if subtitle:
        st.caption(str(subtitle))

def setting(db,key,default=None):
    """Compatibility facade backed by the typed settings resolver."""
    return setting_value(db, key, default)

def save_setting(db,key,value):
    """Compatibility facade backed by the typed settings writer."""
    return save_setting_value(db, key, value)


def _active_campaign_lookup(db):
    """Return (campaign, failure) for the green active-Campaign pin.

    The selected Campaign context may remain paused/completed/archived, but the
    green sticky pin is reserved for lifecycle-active Campaigns only.
    """
    try:
        from rank42.manage_store import active_campaign
        return active_campaign(db), None
    except sqlite3.OperationalError as exc:
        text = str(exc)
        lowered = text.lower()
        transient = any(
            token in lowered
            for token in (
                "database is locked",
                "database is busy",
                "database table is locked",
                "database schema is locked",
            )
        )
        if transient:
            return None, {
                "level": "warning",
                "message": f"Current campaign temporarily unavailable: {text}",
            }
        logger.exception("Current campaign lookup failed")
        return None, {
            "level": "error",
            "message": f"Current campaign lookup failed: OperationalError: {text}",
        }
    except Exception as exc:
        logger.exception("Active campaign lookup failed")
        return None, {
            "level": "error",
            "message": (
                "Current campaign lookup failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }


def _report_active_campaign_lookup_failure(failure, *, launch=False):
    if not failure:
        return
    message = str(failure.get("message") or "Current campaign lookup failed.")
    if launch:
        message += " Launching without automatic campaign attachment."
    if str(failure.get("level") or "error") == "warning":
        st.warning(message)
    else:
        st.error(message)


def _enqueue_resolved_launch(ctx, db, *, kind, label, command, metadata):
    metadata = dict(metadata or {})
    command = [str(x) for x in command]

    # Functional Feature bridge (v0.8.6.4 compatibility). Search-command
    # transforms run once, immediately before enqueue.
    try:
        from rank42.plugins import apply_search_command_features, get_plugin
        family_plugin = None
        plugin_id = metadata.get("plugin_id")
        if plugin_id:
            try:
                candidate = get_plugin(ctx.project_root, plugin_id)
                if candidate.plugin_type == "family":
                    family_plugin = candidate
            except Exception:
                family_plugin = None
        command, feature_audit = apply_search_command_features(
            ctx.project_root,
            db,
            command,
            family_plugin=family_plugin,
            kind=kind,
            metadata=metadata,
            plugin_ids=(
                metadata.get("feature_plugin_ids")
                if "feature_plugin_ids" in metadata
                else None
            ),
        )
        if feature_audit:
            metadata["feature_command_hooks"] = feature_audit
            for rec in feature_audit:
                if rec.get("error"):
                    st.warning(
                        f"Feature `{rec['plugin_id']}` command hook failed; "
                        f"original/current command preserved: {rec['error']}"
                    )
                elif rec.get("note"):
                    st.caption(f"Feature `{rec['plugin_id']}`: {rec['note']}")
    except Exception as exc:
        st.warning(f"Feature command host failed; launching unmodified command: {exc}")

    job_id = enqueue_job(
        ctx.db_path,
        kind=kind,
        label=label,
        command=command,
        cwd=ctx.project_root,
        metadata=metadata,
    )
    st.session_state["_rh_job_just_started"] = int(job_id)
    watched = set(st.session_state.get("_rh_watched_jobs") or [])
    watched.add(int(job_id))
    st.session_state["_rh_watched_jobs"] = sorted(watched)
    return job_id


def launch_resolved(
    ctx,
    db,
    *,
    kind,
    label,
    command,
    metadata,
    campaign_failure=None,
):
    """Launch using an already-resolved/frozen Launch Context snapshot."""
    if campaign_failure is not None:
        _report_active_campaign_lookup_failure(campaign_failure, launch=True)
    return _enqueue_resolved_launch(
        ctx,
        db,
        kind=kind,
        label=label,
        command=command,
        metadata=metadata,
    )


def launch(ctx, db, *, kind, label, command, metadata=None):
    resolved = resolve_launch_context(
        db,
        metadata=metadata,
        launch_surface=str(kind),
        logger=logger,
    )
    return launch_resolved(
        ctx,
        db,
        kind=kind,
        label=label,
        command=command,
        metadata=resolved.metadata,
        campaign_failure=resolved.failure,
    )

def search_pipeline_selector(
    db,
    *,
    target_mode,
    key,
    suggested_goal=8,
    fixed_goal=None,
    label="Execution pipeline",
    builtin_label=None,
):
    """Choose the built-in/native path or a saved compatible Pipeline.

    When builtin_label is provided, option 0 represents a built-in Pipeline
    recipe rather than native execution and returns {"builtin": True, ...}.
    """
    from rank42.pipeline_state import ensure_pipeline_schema, list_pipelines, pipeline_payload

    ensure_pipeline_schema(db)
    mode = str(target_mode)
    wanted = "general" if mode == "curve" else mode
    rows = [row for row in list_pipelines(db, limit=250) if str(row["target_mode"]) == wanted]

    from rank42.pipeline_catalog import normalize_pipeline, stage_spec, validate_pipeline

    compatible = []
    for row in rows:
        payload = pipeline_payload(row)
        stages = normalize_pipeline(payload.get("stages") or [])
        if mode == "curve":
            stages = [
                rec for rec in stages
                if stage_spec(rec["id"]).category != "Candidates"
            ]
        if stages and not validate_pipeline(mode, stages):
            compatible.append(row)
    rows = compatible
    by_id = {int(row["id"]): row for row in rows}
    options = [0] + list(by_id)
    selected = 0
    if rows:
        selected = st.selectbox(
            label,
            options,
            format_func=lambda pid: (
                str(builtin_label)
                if int(pid) == 0 and builtin_label
                else "Native / default search"
                if int(pid) == 0
                else f"Pipeline · #{pid} · {by_id[int(pid)]['name']}"
            ),
            key=f"{key}-pipeline",
            help=(
                "Choose the built-in Pipeline/current controls or one of your saved "
                "compatible pipelines."
                if builtin_label
                else "Choose Native/default to use this page's built-in search flow, "
                "or route this launch through one of your saved pipelines."
            ),
        )

    goal = int(fixed_goal) if fixed_goal is not None else int(
        st.number_input(
            "Pipeline rigorous rank goal",
            min_value=1,
            value=max(1, int(suggested_goal)),
            step=1,
            key=f"{key}-pipeline-goal",
            help="Used by pipeline stages such as rigorous upper gates and Stop at Rank Goal.",
        )
    )

    if int(selected) == 0:
        if builtin_label:
            return {"builtin": True, "target_rank": goal}
        return None

    payload = pipeline_payload(by_id[int(selected)])
    if mode == "curve":
        st.caption(
            "Target uses the selected pipeline recipe's per-curve stages; candidate-generation stages are skipped because the curve already exists."
        )
    return {"pipeline": payload, "target_rank": goal}


def prepare_pipeline_launch(
    ctx,
    db,
    *,
    pipeline_choice,
    target_mode,
    target,
    run_config,
    native_kind,
    native_label,
    native_metadata=None,
    commit=True,
):
    """Freeze one Pipeline Run plus the exact command/metadata used to execute it."""
    if not pipeline_choice:
        raise ValueError("Pipeline execution requires a selected Pipeline")

    native_metadata = dict(native_metadata or {})
    base_run_config = dict(run_config or {})
    if base_run_config.get("ratpoints_backend"):
        native_metadata.setdefault(
            "ratpoints_backend",
            str(base_run_config["ratpoints_backend"]),
        )

    from rank42.pipeline_catalog import normalize_pipeline, stage_spec, validate_pipeline
    from rank42.pipeline_state import create_pipeline_run

    payload = dict(pipeline_choice["pipeline"])
    mode = str(target_mode)
    target_payload = dict(target or {})
    stages = normalize_pipeline(payload.get("stages") or [])
    if mode == "curve":
        stages = [
            rec for rec in stages
            if stage_spec(rec["id"]).category != "Candidates"
        ]
    errors = validate_pipeline(mode, stages)
    if errors:
        raise ValueError("selected pipeline is incompatible: " + "; ".join(errors))

    saved_config = dict(payload.get("config") or {})
    merged_run_config = dict(base_run_config)
    merged_run_config.setdefault("target_rank", int(pipeline_choice["target_rank"]))
    merged_run_config.setdefault(
        "retention_floor",
        max(0, int(saved_config.get("retention_floor") or 0)),
    )
    if "feature_plugin_ids" not in merged_run_config:
        merged_run_config["feature_plugin_ids"] = list(
            saved_config.get("feature_plugin_ids") or []
        )
    if payload.get("id") is not None:
        merged_run_config.setdefault("source_pipeline_id", int(payload["id"]))
    if payload.get("revision") is not None:
        merged_run_config.setdefault(
            "source_pipeline_revision",
            int(payload["revision"]),
        )
    if payload.get("content_hash"):
        merged_run_config.setdefault(
            "source_pipeline_hash",
            str(payload["content_hash"]),
        )
    if (
        payload.get("id") is not None
        and payload.get("revision") is not None
        and payload.get("content_hash")
    ):
        merged_run_config.setdefault("pipeline_source_relation", "exact")

    launch_surface = str(native_metadata.get("launch_surface") or native_kind)
    plugin_id = native_metadata.get("plugin_id") or target_payload.get("plugin_id")
    plugin_variant = (
        native_metadata.get("plugin_variant")
        or target_payload.get("variant_id")
        or target_payload.get("plugin_variant")
    )
    source_pool_id = (
        native_metadata.get("source_pool_id")
        or native_metadata.get("candidate_pool_id")
        or native_metadata.get("pool_id")
        or target_payload.get("candidate_pool_id")
        or target_payload.get("pool_id")
    )
    context_seed = dict(merged_run_config)
    for key, value in launch_context_snapshot(native_metadata).items():
        context_seed.setdefault(key, value)

    run_context = resolve_launch_context(
        db,
        metadata=context_seed,
        launch_surface=launch_surface,
        plugin_id=plugin_id,
        plugin_variant=plugin_variant,
        target_mode=mode,
        pipeline_id=payload.get("id"),
        pipeline_name=payload.get("name"),
        feature_plugin_ids=merged_run_config.get("feature_plugin_ids") or [],
        source_pool_id=source_pool_id,
        logger=logger,
    )
    merged_run_config = dict(run_context.metadata)
    run_id = create_pipeline_run(
        db,
        pipeline_id=payload.get("id"),
        pipeline_name=payload.get("name") or "Pipeline",
        target_mode=mode,
        target=target_payload,
        stages=stages,
        run_config=merged_run_config,
        project_root=ctx.project_root,
        commit=commit,
    )
    py = setting(db, "science_python", ctx.detected_science_python())

    metadata = dict(native_metadata)
    if merged_run_config.get("ratpoints_backend"):
        metadata.setdefault(
            "ratpoints_backend",
            str(merged_run_config["ratpoints_backend"]),
        )
    metadata.update(launch_context_snapshot(merged_run_config))

    metadata = normalize_launch_metadata(
        metadata,
        launch_surface=launch_surface,
        plugin_id=plugin_id,
        plugin_variant=plugin_variant,
        target_mode=mode,
        pipeline_run_id=run_id,
        pipeline_id=payload.get("id"),
        pipeline_name=payload.get("name"),
        feature_plugin_ids=merged_run_config.get("feature_plugin_ids") or [],
        source_pool_id=source_pool_id,
    )
    metadata["builder_override"] = True

    return {
        "kind": "pipeline_search",
        "label": f"Pipeline · {payload.get('name') or native_label}",
        "command": [
            str(py), "-m", "rank42.pipeline_runner",
            "--project-root", str(ctx.project_root),
            "--db", str(ctx.db_path),
            "--run-id", str(run_id),
        ],
        "cwd": str(ctx.project_root),
        "metadata": metadata,
        "pipeline_run_id": int(run_id),
        "campaign_failure": run_context.failure,
    }


def launch_with_pipeline(
    ctx,
    db,
    *,
    pipeline_choice,
    target_mode,
    target,
    run_config,
    native_kind,
    native_label,
    native_command,
    native_metadata=None,
    require_pipeline=False,
):
    """Launch a native search or substitute the chosen saved/built-in Pipeline."""
    native_metadata = dict(native_metadata or {})
    if not pipeline_choice:
        if require_pipeline:
            raise ValueError("this Search surface requires Pipeline execution")
        return launch(
            ctx,
            db,
            kind=native_kind,
            label=native_label,
            command=native_command,
            metadata=native_metadata,
        )

    prepared = prepare_pipeline_launch(
        ctx,
        db,
        pipeline_choice=pipeline_choice,
        target_mode=target_mode,
        target=target,
        run_config=run_config,
        native_kind=native_kind,
        native_label=native_label,
        native_metadata=native_metadata,
        commit=True,
    )
    return launch_resolved(
        ctx,
        db,
        kind=prepared["kind"],
        label=prepared["label"],
        command=prepared["command"],
        metadata=prepared["metadata"],
        campaign_failure=prepared["campaign_failure"],
    )


def _usable_executable(value):
    value = str(value or '').strip()
    if not value:
        return ''
    expanded = str(Path(value).expanduser())
    found = shutil.which(expanded)
    if found:
        return str(Path(found).resolve())
    path = Path(expanded)
    return str(path.resolve()) if path.is_file() else ''


def resolve_ratpoints(db, backend=None):
    backend = str(backend or setting(db, 'ratpoints_backend', 'CPU') or 'CPU').upper()
    if backend not in {'CPU', 'GPU'}:
        backend = 'CPU'
    key = 'ratpoints_gpu' if backend == 'GPU' else 'ratpoints'
    configured = _usable_executable(setting(db, key, ''))
    if configured:
        return configured
    vendored = _usable_executable(vendored_executable(backend))
    if vendored:
        return vendored
    names = ('ratpoints_gpu', 'ratpoints-gpu') if backend == 'GPU' else ('ratpoints',)
    for name in names:
        found = _usable_executable(name)
        if found:
            return found
    return ''


def ratpoints_selector(db, *, key, label='Point engine', default_backend=None):
    default = str(
        default_backend
        or setting(db, 'ratpoints_backend', 'CPU')
        or 'CPU'
    ).upper()
    if default not in {'CPU','GPU'}:
        default='CPU'
    widget_key = rh_key(key)
    selector_kwargs = {
        "help": "GPU expects a ratpoints-gpu executable with ratpoints-compatible CLI arguments.",
    }
    if widget_key not in st.session_state:
        selector_kwargs["index"] = 0 if default == "CPU" else 1
    backend = rh_selectbox(
        label,
        ["CPU", "GPU"],
        semantic=key,
        **selector_kwargs,
    )
    executable = resolve_ratpoints(db, backend)
    if not executable:
        expected = vendored_executable(backend)
        st.warning(f'{backend} point engine is not built/configured. Expected vendored executable: {expected}')
    return backend, executable


def _notification_text(row, summary):
    status = str(summary.get('result_status') or row['status']).replace('_',' ').title()
    if summary.get('candidates_written') is not None:
        return f"Job #{row['id']} finished — {int(summary['candidates_written']):,} candidates generated."
    if summary.get('new_native_fibers') or summary.get('discovered_anchor_fibres'):
        native=int(summary.get('new_native_fibers') or summary.get('discovered_anchor_fibres') or 0)
        extras=int(summary.get('mapped_extra_points') or 0)
        return f"Job #{row['id']} finished — {native:,} native fibre(s), {extras:,} mapped extra point(s)."
    if str(summary.get('result_status') or '').upper()=='NO HITS':
        return f"Job #{row['id']} finished — no retained rank-growth hit."
    if summary.get('best_rigorous_lower'):
        return f"Job #{row['id']} finished — best rigorous lower bound ≥{int(summary['best_rigorous_lower'])}."
    return f"Job #{row['id']} finished — {status}."


def render_job_notifications(db):
    watched={int(x) for x in (st.session_state.get('_rh_watched_jobs') or [])}
    notified={int(x) for x in (st.session_state.get('_rh_notified_jobs') or [])}
    if not watched:
        return
    from rank42.ui_results import summarize_job
    for row in list_jobs(db, limit=100):
        jid=int(row['id'])
        if jid not in watched or jid in notified or row['status'] in {'queued','running','stopping'}:
            continue
        summary=summarize_job(row,read_log(row['log_path']))
        icon='✅' if row['status']=='succeeded' else '⚠️'
        st.toast(_notification_text(row,summary),icon=icon)
        notified.add(jid)
    st.session_state['_rh_notified_jobs']=sorted(notified)


def _job_meta(row):
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _paused_job_rows(rows):
    paused = []
    for row in rows:
        if str(row["status"]) not in {"stopped", "interrupted", "killed", "failed"}:
            continue
        meta = _job_meta(row)
        if bool(meta.get("pause_requested")) and meta.get("resumed_as_job_id") is None:
            paused.append(row)
    return paused


def _active_work_component_event(payload, *, job_id):
    if not isinstance(payload, dict):
        return None
    nonce = payload.get("nonce")
    key = f"_rh_active_work_nonce_{int(job_id)}"
    if nonce is None or str(st.session_state.get(key)) == str(nonce):
        return None
    st.session_state[key] = str(nonce)
    return str(payload.get("action") or "")


def _render_active_jobs(db, db_path, *, theme: Theme | None = None, appearance: str = "light"):
    """Render sticky global work controls for running or explicitly paused jobs."""
    reconcile_jobs(db)
    rows = list_jobs(db, limit=50)
    active = [
        row for row in rows
        if str(row["status"]) in {"queued", "running", "stopping"}
    ]
    paused = _paused_job_rows(rows)
    if not active and not paused:
        return active

    running = [
        row for row in active
        if str(row["status"]) in {"running", "stopping"}
    ]
    queued = [row for row in active if str(row["status"]) == "queued"]
    focus = running[0] if running else (queued[0] if queued else paused[0])
    focus_meta = _job_meta(focus)
    is_paused = any(
        int(row["id"]) == int(focus["id"])
        for row in paused
    )

    planned = int(
        focus_meta.get("count")
        or len(focus_meta.get("candidate_ids") or [])
        or 0
    )
    completed = latest_candidate_done(focus["log_path"])
    if planned and completed is not None:
        progress = f"{completed}/{planned}"
    elif is_paused:
        progress = "paused"
    else:
        progress = str(focus["status"]).lower()

    status_bits = []
    if running:
        status_bits.append(f"{len(running)} running")
    if queued:
        status_bits.append(f"{len(queued)} queued")
    if paused:
        status_bits.append(f"{len(paused)} paused")
    more_count = len(active) + len(paused) - 1
    more = f" · +{more_count} more" if more_count > 0 else ""
    detail = (
        f"{' · '.join(status_bits)} · #{int(focus['id'])} "
        f"{focus['label']} · {progress}{more}"
    )

    st.html(
        """
        <style>
        div[class*="st-key-rh-active-work-strip"] {
            position: sticky;
            top: 1.75rem;
            z-index: 980;
            margin: -.7rem 0 .7rem 0 !important;
            width: 100% !important;
            max-width: none !important;
            background: color-mix(
                in srgb,
                var(--rh-info, #2563eb) 10%,
                var(--rh-raised, transparent)
            );
            border-top: 1px solid color-mix(
                in srgb,
                var(--rh-info, #2563eb) 16%,
                var(--rh-border, transparent)
            );
            border-bottom: 1px solid color-mix(
                in srgb,
                var(--rh-info, #2563eb) 16%,
                var(--rh-border, transparent)
            );
            border-radius: 0 !important;
            box-shadow: 0 1px 0 color-mix(
                in srgb,
                var(--rh-text, #31333f) 4%,
                transparent
            ) !important;
        }
        div[class*="st-key-rh-active-work-strip"] iframe {
            display: block !important;
            width: 100% !important;
            min-height: 32px !important;
        }
        </style>
        """
    )

    if is_paused:
        mode = "resume"
    elif str(focus["status"]) in {"queued", "running"}:
        mode = "pause"
    else:
        mode = "none"

    active_work_palette = None
    if theme is not None:
        tokens = theme.resolved_tokens(appearance)
        active_work_palette = {
            "text": tokens.get("rh-text"),
            "muted": tokens.get("rh-muted"),
            "primary": tokens.get("rh-primary"),
            "danger": tokens.get("rh-danger"),
            "hover": tokens.get("rh-surface-hover"),
        }

    with st.container(key=rh_key("active-work-strip"), border=False):
        payload = _ACTIVE_WORK(
            title="Paused work" if is_paused else "Active work",
            detail=detail,
            mode=mode,
            can_stop=True,
            can_force=not is_paused,
            palette=active_work_palette,
            key=f"rank42-active-work-{int(focus['id'])}",
            default=None,
        )

    action = _active_work_component_event(payload, job_id=int(focus["id"]))
    if not action:
        return active

    job_id = int(focus["id"])
    if action == "open":
        st.session_state["manage_job_id"] = job_id
        st.session_state["jobs_section"] = "History" if is_paused else "Active"
        st.session_state["rh_page"] = "Jobs"
        st.rerun()

    if action == "pause":
        from rank42.manage_store import pause_job

        pause_job(db, job_id)
        st.rerun()

    if action == "resume":
        from rank42.manage_store import resume_job

        new_id = resume_job(db, db_path, job_id)
        watched = set(st.session_state.get("_rh_watched_jobs") or [])
        watched.add(int(new_id))
        st.session_state["_rh_watched_jobs"] = sorted(watched)
        st.session_state["manage_job_id"] = int(new_id)
        st.rerun()

    if action == "stop":
        stop_job(db, job_id, force=False, pause=False)
        st.rerun()

    if action == "force_stop":
        stop_job(db, job_id, force=True, pause=False)
        st.rerun()

    return active


def active_campaign_pin_payload(row):
    if row is None:
        return None
    bits = []
    if row["target_rank"] is not None:
        bits.append(f"target ≥{int(row['target_rank'])}")
    if row["family"]:
        bits.append(f"family {row['family']}")
    if row["plugin_id"]:
        bits.append(f"plugin {row['plugin_id']}")
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "detail": " · ".join(bits),
    }


def active_campaign_pin(db):
    """Render the selected Campaign only while its lifecycle is active."""
    row, campaign_failure = _active_campaign_lookup(db)
    if campaign_failure is not None:
        _report_active_campaign_lookup_failure(campaign_failure)
        return None

    payload = active_campaign_pin_payload(row)
    if payload is None:
        return None

    st.html(
        """
        <style>
        div[class*="st-key-rh-active-campaign-pin"] {
            position: sticky;
            top: 0;
            z-index: 990;
            margin: -1rem 0 .7rem 0 !important;
            width: 100% !important;
            max-width: none !important;
            background: var(
                --rh-active-campaign-pin-bg,
                color-mix(
                    in srgb,
                    var(--rh-success, #1fa65d) 72%,
                    var(--rh-raised, transparent)
                )
            );
            border-radius: 0 !important;
            box-shadow: 0 1px 0 color-mix(
                in srgb,
                var(--rh-text, #31333f) 6%,
                transparent
            ) !important;
        }
        div[class*="st-key-rh-active-campaign-pin"] button {
            width: 100% !important;
            min-height: 1.75rem !important;
            height: 1.75rem !important;
            padding: 0 .2rem !important;
            border: 0 !important;
            border-radius: 0 !important;
            background: transparent !important;
            color: var(--rh-text-on-accent, white) !important;
            box-shadow: none !important;
            justify-content: flex-start !important;
            font-size: .74rem !important;
            font-weight: 600 !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
        }
        div[class*="st-key-rh-active-campaign-pin"] button:hover {
            background: color-mix(
                in srgb,
                var(--rh-text-on-accent, white) 7%,
                transparent
            ) !important;
        }
        div[class*="st-key-rh-active-campaign-pin"] button p {
            margin: 0 !important;
            color: var(--rh-text-on-accent, white) !important;
            font-size: .74rem !important;
            line-height: 1rem !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
        }
        </style>
        """
    )

    detail = f" · {payload['detail']}" if payload["detail"] else ""
    label = f"Current · #{payload['id']} · {payload['name']}{detail}"
    with st.container(key=rh_key("active-campaign-pin"), border=False):
        if st.button(
            label,
            icon=":material/flag:",
            key=rh_key("active-campaign-open"),
            help="Open current campaign",
            width="stretch",
        ):
            st.session_state["manage_campaign_id"] = int(payload["id"])
            st.session_state["rh_page"] = "Campaigns"
            st.rerun()
    return payload
def job_strip(
    db,
    db_path,
    *,
    notifications=True,
    theme: Theme | None = None,
    appearance: str = "light",
):
    """Render compact active work and, on full renders, completion toasts.

    Polling fragments pass notifications=False. This keeps terminal transition
    detection separate from notification consumption so a toast is not marked
    delivered immediately before the shell requests a full rerun.
    """
    if notifications:
        render_job_notifications(db)
    if theme is None and appearance == "light":
        return _render_active_jobs(db, db_path)
    return _render_active_jobs(
        db,
        db_path,
        theme=theme,
        appearance=appearance,
    )

def recent_jobs(db, *, kinds=None, limit=15):
    rows=list_jobs(db,limit=max(limit,50))
    if kinds: rows=[r for r in rows if r['kind'] in set(kinds)]
    return rows[:limit]

def curve_options(db, *, minimum_lower=0, family=None):
    """Selectable curves projected from authoritative Curve Research State.

    Manual external imports remain selectable at rank 0 for inspection.  Rank
    filtering and ordering use the same reduced evidence semantics as Curves,
    Analyze, Points, Results, Dashboard, and Diagnostics.
    """
    states = list_curve_research_states(
        db,
        family=str(family) if family else None,
    )
    manual_ids = {
        int(row["curve_id"])
        for row in db.execute(
            """SELECT DISTINCT curve_id
               FROM events
               WHERE curve_id IS NOT NULL
                 AND message LIKE 'Manual external curve import%'"""
        ).fetchall()
    }
    threshold = int(minimum_lower or 0)
    rows = []
    for state in states:
        curve_id = int(state["curve_id"])
        lower = int(state["rigorous_lower"] or 0)
        if lower <= 0 and curve_id not in manual_ids:
            continue
        if threshold and lower < threshold:
            continue

        row = dict(state["curve"])
        row["rigorous_lower"] = (
            lower if lower > 0 or state["exact_rank"] is not None else None
        )
        row["rigorous_upper"] = state["rigorous_upper"]
        row["exact_rank"] = state["exact_rank"]
        row["rank_inconsistent"] = bool(state["rank_inconsistent"])
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -int(row["rigorous_lower"] or 0),
            -(float(row["score"]) if row["score"] is not None else float("-inf")),
            -int(row["id"]),
        )
    )
    return rows


def curve_label(row):
    lower = row["rigorous_lower"] if "rigorous_lower" in row.keys() else None
    exact = row["exact_rank"] if "exact_rank" in row.keys() else None
    conflict = bool(row["rank_inconsistent"]) if "rank_inconsistent" in row.keys() else False
    upper = row["rigorous_upper"] if "rigorous_upper" in row.keys() else None

    if conflict:
        rank_text = f"rank conflict ≥ {int(lower or 0)}"
        if upper is not None:
            rank_text += f" / ≤ {int(upper)}"
    elif exact is not None:
        rank_text = f"rank = {int(exact)}"
    elif lower is not None:
        rank_text = f"rank ≥ {int(lower)}"
    else:
        rank_text = "rank unknown"
    return f"#{row['id']} · {rank_text} · {row['family']} · t={row['parameter']}"


_CURVE_SELECTOR_SCOPE_VERSION = 1


def _curve_selector_updated_key(row):
    try:
        updated_at = row.get("updated_at")
    except AttributeError:
        updated_at = row["updated_at"] if "updated_at" in row.keys() else None
    return (str(updated_at or ""), int(row["id"]))


def curve_selector_scope_rows(
    rows,
    *,
    scope,
    wanted=None,
    campaign_curve_ids=None,
    latest_limit=50,
):
    """Project a shared All / Latest / Campaign active-curve list."""
    rows = list(rows or ())
    if scope == "Latest":
        selected = sorted(rows, key=_curve_selector_updated_key, reverse=True)[
            : max(1, int(latest_limit))
        ]
        if wanted is not None and all(int(row["id"]) != int(wanted) for row in selected):
            active = next(
                (row for row in rows if int(row["id"]) == int(wanted)),
                None,
            )
            if active is not None:
                selected.append(active)
        return selected

    if scope == "Campaign":
        ids = {int(value) for value in (campaign_curve_ids or ())}
        return sorted(
            [row for row in rows if int(row["id"]) in ids],
            key=_curve_selector_updated_key,
            reverse=True,
        )

    return rows


def normalize_curve_selector_scope_state(state, state_prefix):
    """Normalize one page's curve-browser scope, migrating old state to All."""
    prefix = str(state_prefix).strip().replace("-", "_")
    scope_key = f"{prefix}_curve_scope"
    pending_key = f"{prefix}_curve_scope_pending"
    version_key = f"_{prefix}_curve_scope_version"
    valid = {"All", "Latest", "Campaign"}

    pending = state.pop(pending_key, None)
    version = state.get(version_key)
    if pending in valid:
        scope = str(pending)
    elif version != _CURVE_SELECTOR_SCOPE_VERSION:
        scope = "All"
    else:
        scope = str(state.get(scope_key) or "All")
        if scope not in valid:
            scope = "All"

    state[scope_key] = scope
    state[version_key] = _CURVE_SELECTOR_SCOPE_VERSION
    return scope


def _curve_selector_campaign_label(row, *, current_id=None):
    prefix = (
        "Current · "
        if current_id is not None and int(row["id"]) == int(current_id)
        else ""
    )
    status = str(row["status"] or "unknown")
    return f"{prefix}#{int(row['id'])} · {row['name']} · {status}"


def _close_active_curve_filter(filter_key):
    st.session_state[str(filter_key)] = False


def _sync_active_curve_selector(selection_key, compatibility_keys):
    value = st.session_state.get(str(selection_key))
    if value is None:
        return
    try:
        curve_id = int(value)
    except (TypeError, ValueError):
        return
    set_active_curve_id(
        st.session_state,
        curve_id,
        *(str(key) for key in compatibility_keys),
    )


def active_curve_selector(
    db,
    rows,
    *,
    wanted=None,
    state_prefix,
    widget_prefix=None,
    compatibility_keys=(),
    latest_limit=50,
    format_func=curve_label,
):
    """Render the shared native active-curve selector and icon-only filter."""
    rows = list(rows or ())
    if not rows:
        return None

    state_prefix = str(state_prefix).strip().replace("-", "_")
    widget_prefix = str(widget_prefix or f"{state_prefix}-curve").strip()
    scope_key = f"{state_prefix}_curve_scope"
    campaign_key = f"{state_prefix}_campaign_filter_id"
    selection_key = f"{widget_prefix}-native"
    filter_key = f"{widget_prefix}-filter"
    scope = normalize_curve_selector_scope_state(st.session_state, state_prefix)

    campaigns = list(list_campaigns(db, include_archived=False))
    current = current_campaign_readonly(db)
    current_campaign_id = None if current is None else int(current["id"])
    campaign_by_id = {int(row["id"]): row for row in campaigns}

    selected_campaign_id = st.session_state.get(campaign_key)
    try:
        selected_campaign_id = (
            None
            if selected_campaign_id in (None, "")
            else int(selected_campaign_id)
        )
    except (TypeError, ValueError):
        selected_campaign_id = None
    if selected_campaign_id not in campaign_by_id:
        selected_campaign_id = (
            current_campaign_id
            if current_campaign_id in campaign_by_id
            else (next(iter(campaign_by_id), None))
        )
        if selected_campaign_id is not None:
            st.session_state[campaign_key] = selected_campaign_id

    curve_col, filter_col = st.columns([7.65, 0.55], vertical_alignment="center")
    css_key = re.sub(r"[^A-Za-z0-9_-]", "-", filter_key)
    st.html(
        "<style>"
        f".st-key-{css_key} button svg{{display:none!important}}"
        f".st-key-{css_key} button{{min-width:44px!important;width:44px!important;"
        "height:44px!important;padding:0!important;border:0!important;border-radius:0!important;"
        "background:transparent!important;box-shadow:none!important}"
        f".st-key-{css_key} button:hover{{background:transparent!important;opacity:.82}}"
        f".st-key-{css_key} button p{{margin:0!important;display:flex;"
        "align-items:center;justify-content:center;line-height:1}"
        f".st-key-{css_key} button img{{width:22px;height:22px;display:block;margin:0}}"
        "</style>"
    )

    with filter_col:
        with st.popover(
            fa_markdown_icon("filter", alt="Filter curves"),
            help="Filter curves",
            key=filter_key,
            type="tertiary",
            width=44,
            on_change="rerun",
        ):
            scope = st.radio(
                "Curve set",
                ["All", "Latest", "Campaign"],
                key=scope_key,
                on_change=_close_active_curve_filter,
                args=(filter_key,),
            )
            if scope == "Latest":
                st.caption(f"{int(latest_limit)} most recently updated retained curves.")
            elif scope == "Campaign":
                if not campaign_by_id:
                    st.info("No non-archived campaigns.")
                else:
                    campaign_ids = list(campaign_by_id)
                    if st.session_state.get(campaign_key) not in campaign_ids:
                        st.session_state[campaign_key] = (
                            current_campaign_id
                            if current_campaign_id in campaign_by_id
                            else campaign_ids[0]
                        )
                    selected_campaign_id = st.selectbox(
                        "Campaign",
                        campaign_ids,
                        key=campaign_key,
                        format_func=lambda value: _curve_selector_campaign_label(
                            campaign_by_id[int(value)],
                            current_id=current_campaign_id,
                        ),
                        on_change=_close_active_curve_filter,
                        args=(filter_key,),
                    )

    campaign_curve_ids = None
    if scope == "Campaign" and selected_campaign_id is not None:
        campaign_curve_ids = campaign_curve_ids_readonly(db, int(selected_campaign_id))

    filtered = curve_selector_scope_rows(
        rows,
        scope=scope,
        wanted=wanted,
        campaign_curve_ids=campaign_curve_ids,
        latest_limit=latest_limit,
    )
    if not filtered:
        st.info("No retained curves match this filter.")
        return None

    row_by_id = {int(row["id"]): row for row in filtered}
    default_curve_id = (
        int(wanted)
        if wanted is not None and int(wanted) in row_by_id
        else int(filtered[0]["id"])
    )
    if st.session_state.get(selection_key) not in row_by_id:
        st.session_state[selection_key] = default_curve_id
    elif (
        wanted is not None
        and int(wanted) in row_by_id
        and int(st.session_state.get(selection_key)) != int(wanted)
    ):
        st.session_state[selection_key] = int(wanted)

    with curve_col:
        selected_id = st.selectbox(
            "Curve",
            list(row_by_id),
            key=selection_key,
            format_func=lambda value: format_func(row_by_id[int(value)]),
            label_visibility="collapsed",
            width="stretch",
            on_change=_sync_active_curve_selector,
            args=(selection_key, tuple(compatibility_keys)),
        )

    set_active_curve_id(
        st.session_state,
        int(selected_id),
        *(str(key) for key in compatibility_keys),
    )
    return row_by_id[int(selected_id)]


def select_row(label, rows, *, index=0, key=None, format_func=None, id_key='id', **widget_kwargs):
    """Streamlit-safe row selector. Widget state stores only primitive IDs, never sqlite3.Row objects."""
    if not rows:
        return None
    row_by_id={row[id_key]:row for row in rows}
    ids=list(row_by_id)
    index=max(0,min(int(index),len(ids)-1))
    def _label(value):
        row=row_by_id[value]
        return format_func(row) if format_func else str(value)
    selected_id=rh_selectbox(
        label,
        ids,
        index=index,
        semantic=key or f'select-{label}',
        format_func=_label,
        **widget_kwargs,
    )
    return row_by_id[selected_id]

def multiselect_rows(label, rows, *, default=None, key=None, format_func=None, id_key='id'):
    """Streamlit-safe multi-row selector using primitive IDs as widget values."""
    if not rows:
        return []
    row_by_id={row[id_key]:row for row in rows}
    ids=list(row_by_id)
    default_ids=[]
    for item in default or []:
        default_ids.append(item[id_key] if hasattr(item,'keys') else item)
    def _label(value):
        row=row_by_id[value]
        return format_func(row) if format_func else str(value)
    selected_ids=rh_multiselect(label,ids,default=default_ids,semantic=key or f'multiselect-{label}',format_func=_label)
    return [row_by_id[value] for value in selected_ids]

def parse_command(value):
    if isinstance(value,(list,tuple)): return [str(x) for x in value]
    return shlex.split(str(value))
