from pathlib import Path
import re

from streamlit.material_icon_names import ALL_MATERIAL_ICONS


ROOT = Path(__file__).resolve().parents[1]


def _ui_source():
    return (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")


def _common_source():
    return (ROOT / "rank42" / "ui_pages" / "common.py").read_text(encoding="utf-8")



def test_sidebar_uses_core_vector_logo_with_theme_override_support():
    source = _ui_source()
    svg_path = ROOT / "assets" / "rank-hunter-logo.svg"

    assert svg_path.is_file()
    svg = svg_path.read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg")
    assert 'viewBox="0 0 1280 1552"' in svg
    assert 'fill="#006DFC"' in svg
    assert "stroke=" not in svg
    assert svg.count("<path ") == 2

    branding = source[
        source.index("def _sidebar_branding(ctx):"):
        source.index("def _sidebar_utility_style(active_appearance):")
    ]
    assert 'core_logo = project_root / "assets" / "rank-hunter-logo.svg"' in branding
    assert "logo = core_logo or theme.logo_path" in branding
    assert 'app_logo = str(branding.get("app_logo") or "").strip()' in branding
    assert "logo = candidate" in branding

def test_browser_uses_transparent_bright_blue_rank_hunter_svg_app_icon():
    common = _common_source()
    icon_path = ROOT / "assets" / "rank-hunter-app-icon.svg"

    assert icon_path.is_file()
    svg = icon_path.read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg")
    assert 'fill="#1E8BFF"' in svg
    assert 'fill="#FFFFFF"' not in svg
    assert "<rect " not in svg
    assert 'viewBox="0 0 1552 1552"' in svg
    assert 'translate(290 186) scale(.76)' in svg
    assert 'stroke="#1E8BFF"' in svg
    assert 'stroke-width="48"' in svg
    assert 'paint-order="stroke fill"' in svg

    assert 'app_icon = Path(__file__).resolve().parents[2] / "assets" / "rank-hunter-app-icon.svg"' in common
    assert "page_icon=str(app_icon)" in common


def test_sidebar_appearance_buttons_use_material_icons_and_persist_setting():
    source = _ui_source()
    compile(source, "rank42/ui.py", "exec")

    assert "def _sidebar_utility_style(active_appearance):" in source
    assert "def _flatten_streamlit_theme_options(values, *, prefix=\"theme\"):" in source
    assert "def _apply_native_streamlit_appearance(theme, appearance):" in source
    assert "def _appearance_control(db, ctx):" in source
    assert 'light_col, dark_col = st.columns(2, gap=None)' in source
    assert '"Light",' in source
    assert 'icon=":material/light_mode:"' in source
    assert '"Dark",' in source
    assert 'icon=":material/dark_mode:"' in source
    assert 'help="Use Light mode"' not in source
    assert 'help="Use Dark mode"' not in source
    assert source.count('type="secondary"') >= 2
    assert 'type="primary" if appearance == "light" else "secondary"' not in source
    assert 'type="primary" if appearance == "dark" else "secondary"' not in source
    assert 'save_setting_value(db, "ui_appearance", "light")' in source
    assert 'save_setting_value(db, "ui_appearance", "dark")' in source
    native_block = source[
        source.index("def _apply_native_streamlit_appearance(theme, appearance):"):
        source.index("def _appearance_control(db, ctx):")
    ]
    assert 'streamlit_config.set_option(key, value, "rank-hunter appearance")' in native_block
    assert 'theme.streamlit_theme_for(appearance)' in native_block
    assert 'tomllib.loads(' in native_block

    appearance_block = source[
        source.index("def _appearance_control(db, ctx):"):
        source.index('@st.dialog("Restart Rank Hunter?")')
    ]
    assert appearance_block.count('_apply_native_streamlit_appearance(ctx.theme,') == 2
    assert appearance_block.count("st.rerun()") == 2
    assert "restart_server(" not in appearance_block
    assert "st.stop()" not in appearance_block
    assert "_appearance_control(db, ctx)" in source
    assert "def _appearance_button_icon" not in source
    assert "st.toggle(" not in source

    style = source[
        source.index("def _sidebar_utility_style(active_appearance):"):
        source.index("def _appearance_control(db, ctx):")
    ]
    assert 'width:5.35rem!important' in style
    assert 'border-radius:999px!important' in style
    assert 'search_group_key = rh_key("nav-group-Search")' in style
    assert 'system_group_key = rh_key("nav-group-System")' in style
    assert "for key in (search_group_key, system_group_key)" in style
    assert "margin-top:.45rem!important" in style
    assert ".rh-system-head{opacity:.78!important}" in style
    assert 'gap:0!important' in style
    assert "button[kind='primary']" not in style
    assert "button[kind='secondary']" in style
    assert "[data-testid='stIconMaterial']" in style
    assert 'color:var(--rh-primary,#2563eb)!important' in style
    assert 'color:var(--rh-muted,#8a9099)!important' in style
    assert 'background:transparent!important;color:var(--rh-muted,#8a9099)!important' in style
    assert "for key in (light_key, dark_key, restart_key, power_key)" in style
    assert "{display:none!important}" in style


def test_workspace_extensions_are_isolated_in_conditional_workspace_nav():
    source = _ui_source()
    navigation = source[
        source.index("def navigation(db, ctx, extension_rows):"):
        source.index("_PAGE_END_RUNWAY_HTML")
    ]

    assert 'workspace_items = [' in navigation
    assert 'for route, plugin, page in extension_rows' in navigation
    assert '**({"Workspaces": workspace_items} if workspace_items else {})' in navigation
    assert navigation.index('"Analysis": [') < navigation.index('"Workspaces": workspace_items')
    assert navigation.index('"Workspaces": workspace_items') < navigation.index('"Data": [')
    assert 'groups["Workspace"] = workspace_items' not in navigation
    assert 'groups.setdefault(visual_group' not in navigation
    assert 'visual_group = "Manage" if section == "Plugins" else section' not in navigation


def test_system_lifecycle_controls_use_material_icons_and_confirm_actions():
    source = _ui_source()

    assert '@st.dialog("Restart Rank Hunter?")' in source
    assert '@st.dialog("Power off Rank Hunter?")' in source
    assert 'icon=":material/restart_alt:"' in source
    assert 'icon=":material/power_settings_new:"' in source
    assert 'icon=":material/power:"' not in source
    assert 'rh_css_mask_uri("restart")' not in source
    assert 'rh_css_mask_uri("power-off")' not in source

    nav_group = source[
        source.index("def _nav_group(group, items, active_page, *, footer=None):"):
        source.index("_SYSTEM_METRICS_REFRESH_SECONDS")
    ]
    assert "with st.expander(group, expanded=expanded):" in nav_group
    assert "if footer is not None:" in nav_group
    assert "footer()" in nav_group

    navigation = source[
        source.index("def navigation(db, ctx, extension_rows):"):
        source.index("_PAGE_END_RUNWAY_HTML")
    ]
    assert 'if group == "System"' in navigation
    assert "footer=(" in navigation
    assert "_server_lifecycle_controls(ctx)" in navigation

    controls = source[
        source.index("def _server_lifecycle_controls(ctx):"):
        source.index("def navigation(db, ctx, extension_rows):")
    ]
    assert 'restart_col, power_col = st.columns(2, gap=None)' in controls

    style = source[
        source.index("def _sidebar_utility_style(active_appearance):"):
        source.index("def _appearance_control(db, ctx):")
    ]
    assert 'lifecycle_key = rh_key("server-lifecycle")' in style
    assert 'width:5.35rem!important' in style
    assert 'background:rgba(220,38,38,.05)!important' in style
    assert 'border:0!important;border-radius:999px!important' in style
    assert 'box-shadow:none!important' in style
    assert 'color:#dc2626!important' in style
    assert 'width:2.25rem!important' in style
    assert 'height:2.25rem!important' in style
    assert 'transform:scale(.92)!important' in style
    assert style.count('font-size:1.2rem!important') >= 2
    assert "_restart_server_confirmation(ctx)" in controls
    assert "_shutdown_server_confirmation()" in controls
    assert "restart_server(" not in controls
    assert "shutdown_server(" not in controls

    restart_dialog = source[
        source.index('def _restart_server_confirmation(ctx):'):
        source.index('@st.dialog("Power off Rank Hunter?")')
    ]
    shutdown_dialog = source[
        source.index("def _shutdown_server_confirmation():"):
        source.index("def _server_lifecycle_controls(ctx):")
    ]
    assert "restart_server(" in restart_dialog
    assert "shutdown_server()" in shutdown_dialog


def test_menu_and_page_headers_share_material_route_icons():
    source = _ui_source()
    common = _common_source()

    expected = {
        "Dashboard": "space_dashboard",
        "Auto": "bolt_boost",
        "Search": "search",
        "Target": "target",
        "Candidates": "group",
        "Descent": "trending_down",
        "Points": "grain",
        "Independence": "schema",
        "Saturation": "salinity",
        "Lattices": "grid_4x4",
        "Curves": "line_curve",
        "Libraries": "library_books",
        "Catalogs": "public",
        "Jobs": "work_history",
        "Campaigns": "bookmark",
        "Pipelines": "flowsheet",
        "Plugins": "extension",
        "Diagnostics": "health_and_safety",
        "Database": "database",
        "Settings": "settings",
    }

    for label, icon in expected.items():
        assert f'"{label}": ":material/{icon}:"' in common

    assert 'QUARTICS_ICON = ":material/control_camera:"' in common
    assert '"Quartics": QUARTICS_ICON' in common
    assert '"Candidate Generate": ":material/group_add:"' in common
    assert '"Candidate Pools": ":material/group:"' in common
    assert '"Generate Candidates": ":material/group_add:"' in common
    assert '"Builder": ":material/flowsheet:"' in common

    page_icons = common[
        common.index("PAGE_ICONS = {"):
        common.index("_BREADCRUMB_ROUTE_LABELS")
    ]
    assert '"rh:' not in page_icons
    assert '"fa:' not in page_icons
    assert '"fa-regular:' not in page_icons

    assert 'icon = PAGE_ICONS.get(str(name), icon or "analytics")' in common
    assert '"Dashboard": PAGE_ICONS["Dashboard"]' in source
    assert '"Candidate Generate": PAGE_ICONS["Candidate Generate"]' in source
    assert '"Candidate Pools": PAGE_ICONS["Candidate Pools"]' in source
    assert '"Saturation": PAGE_ICONS["Saturation"]' in source
    assert '"Jobs": PAGE_ICONS["Jobs"]' in source
    assert '"Plugins": PAGE_ICONS["Plugins"]' in source
    assert '_nav_button("Dashboard", "Dashboard")' in source


def test_all_active_material_icons_are_supported_by_streamlit():
    ui_sources = [
        (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8"),
        (ROOT / "rank42" / "ui_components.py").read_text(encoding="utf-8"),
        (ROOT / "rank42" / "ui_extensions.py").read_text(encoding="utf-8"),
    ]
    ui_sources.extend(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "rank42" / "ui_pages").glob("*.py"))
    )

    material_names = set(
        re.findall(r':material/([a-z0-9_]+):', "\n".join(ui_sources))
    )
    assert material_names
    assert material_names <= ALL_MATERIAL_ICONS
