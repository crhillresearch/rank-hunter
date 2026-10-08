"""Rank Hunter v0.9.1 Streamlit shell.

Scientific logic remains in backend modules/plugins and SQLite remains the
source of truth.  The shell owns navigation, job orchestration, plugin page
hosting, feature hook hosting, and presentation-neutral semantic UI regions.
"""
from __future__ import annotations

import argparse
import html
import sys
import time
import tomllib
from pathlib import Path

PROJECT_GUESS = Path(__file__).resolve().parents[1]
while str(PROJECT_GUESS) in sys.path:
    sys.path.remove(str(PROJECT_GUESS))
sys.path.insert(0, str(PROJECT_GUESS))

import streamlit as st

from rank42 import __version__
from rank42.icon_sprite import rh_css_mask_uri
from rank42.db import connect_existing
from rank42.migration_manager import open_database_with_migrations
from rank42.system_metrics import collect_system_metrics
from rank42.ratpoints import vendored_executable
from rank42.server_control import restart_server, shutdown_server
from rank42.theme_loader import RELEASE_THEME_ID, resolve_release_theme
from rank42.settings_registry import (
    save_setting_value,
    seed_setting_if_missing,
    setting_value,
)
from rank42.ui_components import rh_key
from rank42.ui_extensions import enabled_extension_pages, render_extension
from rank42.ui_pages.common import PAGE_ICONS, UIContext, active_campaign_pin, configure, fa_css_mask_uri, job_strip
from rank42.ui_store import ensure_dispatcher_running
from rank42.ui_pages import (
    dashboard,
    search_page,
    target_curve,
    auto_search,
    build_your_own,
    curves,
    candidates_page,
    corpus_page,
    points_page,
    descent_page,
    lattices_page,
    quartics_page,
    independence_page,
    saturation_page,
    landscape_page,
    plugins_page,
    themes_page,
    external_catalog,
    icarm_submit,
    campaigns_page,
    jobs_page,
    database_page,
    diagnostics_page,
    settings_page,
)


NAV_ICONS = {
    "Dashboard": PAGE_ICONS["Dashboard"],
    "Search": PAGE_ICONS["Search"],
    "Target": PAGE_ICONS["Target"],
    "Auto": PAGE_ICONS["Auto"],
    "Pipelines": PAGE_ICONS["Pipelines"],
    "Curves": PAGE_ICONS["Curves"],
    "Points": PAGE_ICONS["Points"],
    "Candidate Generate": PAGE_ICONS["Candidate Generate"],
    "Candidate Pools": PAGE_ICONS["Candidate Pools"],
    "Candidates": PAGE_ICONS["Candidates"],
    "Corpora": PAGE_ICONS["Corpora"],
    "Results": PAGE_ICONS["Results"],
    "External Catalog": PAGE_ICONS["External Catalog"],
    "Campaigns": PAGE_ICONS["Campaigns"],
    "Jobs": PAGE_ICONS["Jobs"],
    "Descent": PAGE_ICONS["Descent"],
    "Rank Proof": PAGE_ICONS["Rank Proof"],
    "Lattices": PAGE_ICONS["Lattices"],
    "Quartics": PAGE_ICONS["Quartics"],
    "Independence": PAGE_ICONS["Independence"],
    "Saturation": PAGE_ICONS["Saturation"],
    "Landscape": ":material/landscape:",
    "Plugins": PAGE_ICONS["Plugins"],
    "Plugin Families": PAGE_ICONS["Plugins"],
    "Plugin Extensions": PAGE_ICONS["Plugins"],
    "Themes": ":material/palette:",
    "Database": PAGE_ICONS["Database"],
    "Diagnostics": PAGE_ICONS["Diagnostics"],
    "Settings": PAGE_ICONS["Settings"],
}


_LEGACY_ROUTE_ALIASES = {
    "Family Search": "Search",
    "General Hunt": "Search",
    "Auto Search": "Auto",
    "Auto Hunter": "Auto",
    "Geometry Search": "Auto",
    "Geometry": "Auto",
    "Build Your Own": "Pipelines",
    "Builder": "Pipelines",
    "Target Curve": "Target",
    "Work Center": "Curves",
    "Rank Proof": "Descent",
    "MW Geometry": "Lattices",
    "Lattices & Heights": "Lattices",
    "Quartic Searches": "Quartics",
    "Quartic Search": "Quartics",
}


def _normalize_route(page, state):
    """Normalize legacy shell routes into one canonical session-state route.

    Compatibility aliases remain accepted for old bookmarks/session state, but
    navigation highlighting and page rendering always operate on the canonical
    route after this function returns.
    """
    requested = str(page or "Dashboard")
    if requested in {"Candidates", "Candidate Generate", "Candidate Pools"}:
        legacy_tab = str(state.get("candidates_tab", "") or "")
        page_nav_tab = str(
            state.get("_rh-candidates-main-tabs_value", "") or ""
        )
        if requested == "Candidate Pools":
            candidate_tab = "Pools"
        elif requested == "Candidate Generate":
            candidate_tab = "Generate"
        elif page_nav_tab in {"Generate", "Pools"}:
            candidate_tab = page_nav_tab
        else:
            candidate_tab = (
                "Pools"
                if legacy_tab in {"Pools", "Import / Export"}
                else "Generate"
            )
        canonical = "Candidates"
        state["candidates_tab"] = candidate_tab
        state["_rh-candidates-main-tabs_value"] = candidate_tab
    elif requested == "Results":
        canonical = "Jobs"
        state["jobs_section_pending"] = "Results"
    elif requested in {"Analyze", "Case Board", "Work Center"}:
        canonical = "Curves"
        for key in (
            "case_board_curve_id",
            "analyze_curve_id",
            "analysis_curve_id",
            "analysis_active_curve_id",
        ):
            try:
                curve_id = int(state.get(key) or 0)
            except (TypeError, ValueError):
                curve_id = 0
            if curve_id > 0:
                state["curves_detail_id"] = curve_id
                break
    elif requested in {"Plugin Families", "Plugin Extensions"}:
        canonical = "Plugins"
        plugin_tab = "Extensions" if requested == "Plugin Extensions" else "Families"
        state["plugins_tab"] = plugin_tab
        state["_rh-plugins-main-tabs_value"] = plugin_tab
    else:
        canonical = _LEGACY_ROUTE_ALIASES.get(requested, requested)

    if requested in {"Family Search", "General Hunt"}:
        state["search_tab"] = "General" if requested == "General Hunt" else "Family"
    elif requested in {"Geometry Search", "Geometry"}:
        # Historical Geometry deep-links now land in Auto. Do not carry an old
        # tab selection that may point at a removed geometry-only surface.
        state.pop("auto-main-tab", None)

    if canonical != requested:
        state["rh_page"] = canonical
    return canonical


def cli_args():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--project-root", default=None)
    args, _ = ap.parse_known_args()
    return args


def _set_page(page, state_updates=None):
    st.session_state["rh_page"] = page
    for key, value in dict(state_updates or {}).items():
        st.session_state[key] = value


def _nav_button(label, page, *, icon=True, icon_value=None, state_updates=None):
    updates = dict(state_updates or {})
    active = (
        st.session_state.get("rh_page", "Dashboard") == page
        and all(st.session_state.get(key) == value for key, value in updates.items())
    )
    resolved_icon = None
    button_label = str(label)
    key_suffix = f"-{label}" if updates else ""
    button_key = rh_key(f"nav-{page}{key_suffix}")
    if icon:
        resolved_icon = icon_value or NAV_ICONS.get(page, ":material/chevron_right:")
        if isinstance(resolved_icon, str) and resolved_icon.startswith("text:"):
            button_label = f"{resolved_icon[5:]}  {label}"
            resolved_icon = None
        elif isinstance(resolved_icon, str) and resolved_icon.startswith(("rh:", "fa:", "fa-regular:")):
            try:
                if resolved_icon.startswith("rh:"):
                    mask_uri = rh_css_mask_uri(resolved_icon.split(":", 1)[1])
                else:
                    regular = resolved_icon.startswith("fa-regular:")
                    fa_name = resolved_icon.split(":", 1)[1]
                    mask_uri = fa_css_mask_uri(
                        fa_name,
                        style="regular" if regular else "solid",
                    )
            except KeyError:
                resolved_icon = None
            else:
                st.html(
                    "<style>"
                    f"div[class*='st-key-{button_key}'] button::before{{"
                    "content:'';display:inline-block;width:1.375rem;height:1.375rem;"
                    "margin-right:.36rem;flex:0 0 auto;background:currentColor;"
                    f"-webkit-mask:url('{mask_uri}') center/contain no-repeat;"
                    f"mask:url('{mask_uri}') center/contain no-repeat;"
                    "}</style>"
                )
                resolved_icon = None
    st.button(
        button_label,
        key=button_key,
        width="stretch",
        type="primary" if active else "secondary",
        icon=resolved_icon,
        on_click=_set_page,
        args=(page, updates),
    )


def _nav_group(group, items, active_page, *, footer=None):
    expanded = any(item[1] == active_page for item in items)
    with st.container(key=rh_key(f"nav-group-{group}")):
        with st.expander(group, expanded=expanded):
            with st.container(key=rh_key(f"nav-items-{group}")):
                for item in items:
                    label, page = item[:2]
                    item_icon = item[2] if len(item) > 2 else None
                    state_updates = item[3] if len(item) > 3 else None
                    _nav_button(
                        label,
                        page,
                        icon_value=item_icon,
                        state_updates=state_updates,
                    )
                if footer is not None:
                    footer()


_SYSTEM_METRICS_REFRESH_SECONDS = 3.0


def _cached_system_metrics():
    now = time.monotonic()
    rows = st.session_state.get("_rh_system_metrics_rows")
    sampled_at = float(st.session_state.get("_rh_system_metrics_sampled_at") or 0.0)
    if rows is None or now - sampled_at >= _SYSTEM_METRICS_REFRESH_SECONDS:
        rows = collect_system_metrics()
        st.session_state["_rh_system_metrics_rows"] = rows
        st.session_state["_rh_system_metrics_sampled_at"] = now
    return rows


@st.fragment(run_every="3s")
def _system_metrics_panel():
    rows = _cached_system_metrics()
    parts = ['<div class="rh-system-metrics">']
    for row in rows:
        level = html.escape(str(row.get("level") or "muted"))
        label = html.escape(str(row.get("label") or ""))
        value = html.escape(str(row.get("value") or "—"))
        detail = html.escape(str(row.get("detail") or ""))
        pct = row.get("percent")
        width = 0 if pct is None else max(0.0, min(100.0, float(pct)))
        parts.append(f'<div class="rh-system-row level-{level}">')
        parts.append(f'<div class="rh-system-head"><span>{label}</span><strong>{value}</strong></div>')
        if row.get("show_bar"):
            parts.append(f'<div class="rh-system-track"><i style="width:{width:.1f}%"></i></div>')
        if detail:
            parts.append(f'<div class="rh-system-detail">{detail}</div>')
        parts.append("</div>")
    parts.append("</div>")
    st.html("".join(parts))


def _sidebar_branding(ctx):
    project_root = ctx.project_root.resolve()
    core_logo = project_root / "assets" / "rank-hunter-logo.svg"
    if not core_logo.is_file():
        core_logo = None

    theme = ctx.theme
    if theme is None:
        return core_logo, 96, True

    branding = theme.manifest.get("branding") or {}
    logo = core_logo or theme.logo_path
    app_logo = str(branding.get("app_logo") or "").strip()
    if app_logo:
        candidate = (project_root / app_logo).resolve()
        if candidate != project_root and project_root in candidate.parents and candidate.is_file():
            logo = candidate

    show_footer = branding.get("show_sidebar_footer", True) is not False
    return logo, int(theme.logo_width), show_footer


def _sidebar_utility_style(active_appearance):
    """Style appearance and lifecycle utility controls with compact icon treatment."""
    toggle_key = rh_key("appearance-toggle")
    light_key = rh_key("appearance-light")
    dark_key = rh_key("appearance-dark")
    restart_key = rh_key("server-restart")
    power_key = rh_key("server-power")
    lifecycle_key = rh_key("server-lifecycle")
    search_group_key = rh_key("nav-group-Search")
    system_group_key = rh_key("nav-group-System")
    active_key = light_key if active_appearance == "light" else dark_key
    inactive_key = dark_key if active_appearance == "light" else light_key
    st.html(
        "<style>"
        + f"div[class*='st-key-{toggle_key}']{{"
          "width:5.35rem!important;margin:.15rem auto .45rem!important;"
          "padding:.2rem!important;border:1px solid var(--rh-border,#d1d5db)!important;"
          "border-radius:999px!important;background:var(--rh-card,#fff)!important;"
          "box-shadow:0 1px 3px rgba(15,23,42,.12)!important}"
        + f"div[class*='st-key-{toggle_key}'] [data-testid='stHorizontalBlock']{{"
          "gap:0!important;align-items:center!important}"
        + f"div[class*='st-key-{toggle_key}'] [data-testid='column']{{"
          "padding:0!important}"
        + ",".join(
            f"div[class*='st-key-{key}']"
            for key in (search_group_key, system_group_key)
        )
        + "{margin-top:.45rem!important}"
        + ".rh-system-head{opacity:.78!important}"
        + ",".join(
            f"div[class*='st-key-{key}'] button"
            for key in (light_key, dark_key)
        )
        + "{height:2.25rem!important;min-height:2.25rem!important;width:2.25rem!important;"
          "padding:0!important;margin:0 auto!important;border:0!important;border-radius:999px!important;"
          "box-shadow:none!important;display:flex!important;align-items:center!important;"
          "justify-content:center!important}"
        + f"div[class*='st-key-{toggle_key}'] button[kind='secondary']{{"
          "background:transparent!important;color:var(--rh-muted,#8a9099)!important}"
        + f"div[class*='st-key-{toggle_key}'] button[kind='secondary']:hover{{"
          "background:var(--rh-surface-hover,rgba(127,127,127,.10))!important;"
          "color:var(--rh-text,#31333f)!important}"
        + f"div[class*='st-key-{active_key}'] [data-testid='stIconMaterial']{{"
          "color:var(--rh-primary,#2563eb)!important}"
        + f"div[class*='st-key-{inactive_key}'] [data-testid='stIconMaterial']{{"
          "color:var(--rh-muted,#8a9099)!important}"
        + ",".join(
            f"div[class*='st-key-{key}'] button [data-testid='stMarkdownContainer']"
            for key in (light_key, dark_key, restart_key, power_key)
        )
        + "{display:none!important}"
        + ",".join(
            f"div[class*='st-key-{key}'] button [data-testid='stIconMaterial']"
            for key in (light_key, dark_key)
        )
        + "{font-size:1.2rem!important;margin:0!important}"
        + f"div[class*='st-key-{lifecycle_key}']{{"
          "width:5.35rem!important;margin:.35rem auto .1rem!important;"
          "padding:.2rem!important;border:0!important;border-radius:999px!important;"
          "background:rgba(220,38,38,.05)!important;box-shadow:none!important}"
        + f"div[class*='st-key-{lifecycle_key}'] [data-testid='stHorizontalBlock']{{"
          "gap:0!important;align-items:center!important}"
        + f"div[class*='st-key-{lifecycle_key}'] [data-testid='column']{{"
          "padding:0!important}"
        + ",".join(
            f"div[class*='st-key-{key}'] button"
            for key in (restart_key, power_key)
        )
        + "{height:2.25rem!important;min-height:2.25rem!important;width:2.25rem!important;"
          "padding:0!important;margin:0 auto!important;background:transparent!important;"
          "border:0!important;border-radius:999px!important;box-shadow:none!important;"
          "color:#dc2626!important;display:flex!important;align-items:center!important;"
          "justify-content:center!important}"
        + ",".join(
            f"div[class*='st-key-{key}'] button:hover"
            for key in (restart_key, power_key)
        )
        + "{background:rgba(220,38,38,.08)!important;color:#b91c1c!important}"
        + ",".join(
            f"div[class*='st-key-{key}'] button [data-testid='stIconMaterial']"
            for key in (restart_key, power_key)
        )
        + "{font-size:1.2rem!important;margin:0!important}"
        + f"div[class*='st-key-{power_key}'] button [data-testid='stIconMaterial']{{"
          "transform:scale(.92)!important;transform-origin:center!important}"
        + "</style>"
    )


def _flatten_streamlit_theme_options(values, *, prefix="theme"):
    options = {}
    for key, value in dict(values or {}).items():
        option_key = f"{prefix}.{key}"
        if isinstance(value, dict):
            options.update(
                _flatten_streamlit_theme_options(value, prefix=option_key)
            )
        else:
            options[option_key] = value
    return options


def _apply_native_streamlit_appearance(theme, appearance):
    """Update Streamlit's native theme in-process before the next rerun."""
    from streamlit import config as streamlit_config

    appearance = str(appearance or "light").strip().lower()
    if appearance not in {"light", "dark"}:
        appearance = "light"

    options = {"theme.base": appearance}
    theme_path = theme.streamlit_theme_for(appearance) if theme is not None else None
    if theme_path is not None and Path(theme_path).is_file():
        payload = tomllib.loads(Path(theme_path).read_text(encoding="utf-8"))
        raw_theme = payload.get("theme") or {}
        if isinstance(raw_theme, dict):
            options.update(_flatten_streamlit_theme_options(raw_theme))
        if str(options.get("theme.base") or "").lower() not in {"light", "dark"}:
            options["theme.base"] = appearance

    for key, value in options.items():
        streamlit_config.set_option(key, value, "rank-hunter appearance")


def _appearance_control(db, ctx):
    appearance = str(setting_value(db, "ui_appearance", "light") or "light").lower()
    if appearance not in {"light", "dark"}:
        appearance = "light"

    _sidebar_utility_style(appearance)
    light_key = rh_key("appearance-light")
    dark_key = rh_key("appearance-dark")

    with st.container(key=rh_key("appearance-toggle"), border=False):
        light_col, dark_col = st.columns(2, gap=None)
        with light_col:
            if st.button(
                "Light",
                icon=":material/light_mode:",
                type="secondary",
                key=light_key,
                width="stretch",
            ) and appearance != "light":
                _apply_native_streamlit_appearance(ctx.theme, "light")
                save_setting_value(db, "ui_appearance", "light")
                st.rerun()
        with dark_col:
            if st.button(
                "Dark",
                icon=":material/dark_mode:",
                type="secondary",
                key=dark_key,
                width="stretch",
            ) and appearance != "dark":
                _apply_native_streamlit_appearance(ctx.theme, "dark")
                save_setting_value(db, "ui_appearance", "dark")
                st.rerun()


@st.dialog("Restart Rank Hunter?")
def _restart_server_confirmation(ctx):
    st.warning(
        "Rank Hunter will stop this server process and launch a fresh replacement "
        "using the same project, database, and runtime Python."
    )
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button(
        "Cancel",
        width="stretch",
        key=rh_key("restart-cancel"),
    ):
        st.rerun()
    if confirm_col.button(
        "Restart",
        type="primary",
        width="stretch",
        key=rh_key("restart-confirm"),
    ):
        restart_server(
            ctx.project_root,
            ctx.db_path,
            runtime_python=sys.executable,
        )


@st.dialog("Power off Rank Hunter?")
def _shutdown_server_confirmation():
    st.warning(
        "This stops the Rank Hunter server. It will stay offline until you start "
        "it again from the terminal."
    )
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button(
        "Cancel",
        width="stretch",
        key=rh_key("power-cancel"),
    ):
        st.rerun()
    if confirm_col.button(
        "Power off",
        type="primary",
        width="stretch",
        key=rh_key("power-confirm"),
    ):
        shutdown_server()


def _server_lifecycle_controls(ctx):
    restart_key = rh_key("server-restart")
    power_key = rh_key("server-power")
    with st.container(key=rh_key("server-lifecycle"), border=False):
        restart_col, power_col = st.columns(2, gap=None)
        with restart_col:
            if st.button(
                "Restart Rank Hunter",
                icon=":material/restart_alt:",
                type="secondary",
                key=restart_key,
                width="stretch",
                help="Restart Rank Hunter",
            ):
                _restart_server_confirmation(ctx)
        with power_col:
            if st.button(
                "Power off Rank Hunter",
                icon=":material/power_settings_new:",
                type="secondary",
                key=power_key,
                width="stretch",
                help="Power off Rank Hunter",
            ):
                _shutdown_server_confirmation()


def navigation(db, ctx, extension_rows):
    with st.sidebar:
        logo, logo_width, show_footer = _sidebar_branding(ctx)
        if logo is not None and logo.exists():
            with st.container(key=rh_key("sidebar-logo")):
                st.image(str(logo), width=logo_width)

        _nav_button("Dashboard", "Dashboard")
        _appearance_control(db, ctx)
        st.divider()

        workspace_items = [
            (page.label, route, plugin.icon)
            for route, plugin, page in extension_rows
        ]

        groups = {
            "Search": [
                ("Auto", "Auto"),
                ("Search", "Search"),
                ("Target", "Target"),
                ("Candidates", "Candidates"),
            ],
            "Analysis": [
                ("Descent", "Descent"),
                ("Quartics", "Quartics"),
                ("Points", "Points"),
                ("Independence", "Independence"),
                ("Saturation", "Saturation"),
                ("Lattices", "Lattices"),
            ],
            **({"Workspaces": workspace_items} if workspace_items else {}),
            "Data": [
                ("Curves", "Curves"),
                ("Libraries", "Corpora"),
                ("Catalogs", "External Catalog"),
            ],
            "Manage": [
                ("Jobs", "Jobs"),
                ("Campaigns", "Campaigns"),
                ("Pipelines", "Pipelines"),
                ("Plugins", "Plugins"),
            ],
            "System": [("Diagnostics", "Diagnostics"), ("Database", "Database"), ("Settings", "Settings")],
        }

        active_page = st.session_state.get("rh_page", "Dashboard")
        for group, items in groups.items():
            _nav_group(
                group,
                items,
                active_page,
                footer=(
                    (lambda: _server_lifecycle_controls(ctx))
                    if group == "System"
                    else None
                ),
            )

        st.divider()
        _system_metrics_panel()
        if show_footer:
            st.divider()
            st.html(
                f'<div class="rh-sidebar-footer"><strong>Rank Hunter</strong>'
                f'<span>v{__version__}</span></div>'
            )


_PAGE_END_RUNWAY_HTML = (
    "<div class='rh-page-end-runway' "
    "style='height:3.5rem;min-height:3.5rem;pointer-events:none' "
    "aria-hidden='true'></div>"
)


def _page_end_runway():
    """Add real document-flow space after the routed page.

    This deliberately does not touch Streamlit's scroll container, overflow,
    viewport height, or sticky-header behavior. The extra element simply gives
    wheel scrolling enough real content below the final widget so tables/cards
    can clear the viewport edge cleanly.
    """
    st.html(_PAGE_END_RUNWAY_HTML)


def _ui_jobs_active(db):
    """Return whether any UI-launched work still needs live page polling."""
    row = db.execute(
        """SELECT 1 FROM ui_jobs
           WHERE status IN ('queued','running','stopping')
           LIMIT 1"""
    ).fetchone()
    return row is not None


def _resolve_theme(root: Path, db):
    """Resolve the 0.9.1 release-pinned Axiom theme.

    The generic filesystem theme engine and persisted selection code remain in
    the repository for later reactivation, but the running release shell does
    not expose or honor alternate theme selection.
    """

    _ = db
    return resolve_release_theme(root, RELEASE_THEME_ID)


def main():
    args = cli_args()
    root = Path(args.project_root or Path.cwd()).resolve()
    dbp = Path(args.db)
    dbp = (root / dbp).resolve() if not dbp.is_absolute() else dbp.resolve()

    db = None
    db_error = None
    try:
        db = open_database_with_migrations(dbp)
    except Exception as exc:
        db_error = exc

    active_theme = None
    theme_error = None
    if db is not None:
        active_theme, theme_error = _resolve_theme(root, db)

    appearance = (
        str(setting_value(db, "ui_appearance", "light") or "light").lower()
        if db is not None
        else "light"
    )
    if appearance not in {"light", "dark"}:
        appearance = "light"

    configure(active_theme, appearance=appearance)
    ctx = UIContext(root, dbp, active_theme, appearance)

    if db is None:
        st.error(f"Cannot open Rank Hunter database `{dbp}`: {db_error}")
        st.stop()
    if theme_error is not None:
        st.warning(f"UI theme fallback: {theme_error}")

    try:
        # Bootstrap markers/defaults seed persisted runtime settings once.
        # Existing rows are never overwritten here, even when invalid; typed
        # resolution may fall back safely while SYSTEM-14 owns health/probing.
        seed_setting_if_missing(
            db,
            "science_python",
            ctx.detected_science_python(),
        )
        seed_setting_if_missing(
            db,
            "ratpoints",
            vendored_executable("CPU", ctx.project_root),
        )
        seed_setting_if_missing(
            db,
            "ratpoints_gpu",
            vendored_executable("GPU", ctx.project_root),
        )

        ensure_dispatcher_running(ctx.db_path, ctx.project_root)

        extensions = enabled_extension_pages(db, ctx.project_root)
        page = _normalize_route(
            st.session_state.get("rh_page", "Dashboard"),
            st.session_state,
        )
        navigation(db, ctx, extensions)

        routes = {
            "Dashboard": dashboard.page,
            "Search": search_page.page,
            "Target": target_curve.page,
            "Auto": auto_search.page,
            "Pipelines": build_your_own.page,
            "Curves": curves.page,
            "Points": points_page.page,
            "Candidates": candidates_page.page,
            "Corpora": corpus_page.page,
            "External Catalog": external_catalog.page,
            "Campaigns": campaigns_page.page,
            "Jobs": jobs_page.page,
            # Preserved hidden research destinations for old deep links/state.
            "Landscape": landscape_page.page,
            "Descent": descent_page.page,
            "Lattices": lattices_page.page,
            "Quartics": quartics_page.page,
            "Independence": independence_page.page,
            "Saturation": saturation_page.page,
            "Plugins": plugins_page.page,
            "Themes": themes_page.page,
            # Intentional hidden destination: Curves routes explicit ICARM
            # submission here without adding another permanent sidebar item.
            "ICARM Submit": icarm_submit.page,
            "Database": database_page.page,
            "Diagnostics": diagnostics_page.page,
            "Settings": settings_page.page,
        }

        ext_by_route = {route: (plugin, epage) for route, plugin, epage in extensions}

        def _render_route(render_db):
            if page in ext_by_route:
                plugin, epage = ext_by_route[page]
                render_extension(render_db, ctx, plugin, epage)
            else:
                routes.get(page, dashboard.page)(render_db, ctx)

        active_jobs = _ui_jobs_active(db)

        # Global live-work rule: never rebuild the routed page on a polling
        # timer. Streamlit replaces fragment output while it reruns, so polling
        # the whole page produced the repeated white flash on nearly every
        # screen. Keep the campaign strip and routed page mounted; only the tiny
        # active-work strip polls. When the job leaves the active states, do one
        # normal app rerun to refresh scientific/page state and completion
        # notifications.
        active_campaign_pin(db)
        if active_jobs:
            @st.fragment(run_every="2s")
            def _live_job_strip():
                live = connect_existing(ctx.db_path)
                try:
                    active = job_strip(
                        live,
                        ctx.db_path,
                        notifications=False,
                        theme=ctx.theme,
                        appearance=ctx.appearance,
                    )
                    if not active:
                        st.rerun()
                finally:
                    live.close()

            _live_job_strip()
        else:
            job_strip(
                db,
                ctx.db_path,
                theme=ctx.theme,
                appearance=ctx.appearance,
            )

        _render_route(db)
        _page_end_runway()

        if st.session_state.pop("_rh_job_just_started", None) is not None:
            st.rerun()
    finally:
        db.close()


if __name__ == "__main__":
    main()
