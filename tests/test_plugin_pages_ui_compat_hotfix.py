from pathlib import Path



def root():
    return Path(__file__).resolve().parents[1]


def test_plugin_navigation_page_owns_canonical_title_and_subpage_headings():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert 'title(\n        "Plugins",' in source
    assert 'icon="extension"' in source
    assert 'st.subheader("Families", anchor=False)' not in source
    assert 'st.subheader("Extensions", anchor=False)' not in source
    assert 'initial_tab in {"Families", "Extensions"}' in source


def test_families_use_manager_status_filter_for_enabled_disabled_states():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '"Active only"' not in source
    assert 'plugin-families-active-only' not in source
    assert '"plugin-family-manager-status"' in source
    assert '"Enabled"' in source
    assert '"Disabled"' in source


def test_family_filter_toolbar_layout():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '[2.3, 1.15, 1.35, 0.9]' in source
    assert "with search_col:" in source
    assert "with status_col:" in source
    assert "with capability_col:" in source
    assert "with sort_col:" in source
    assert '"plugin-family-manager-capability"' in source
    assert "rh-family-toolbar-summary" not in source


def test_extensions_landing_separates_features_and_workspaces():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '"plugin-extension-manager-search"' in source
    assert '"plugin-extension-manager-status"' in source
    assert '"plugin-extension-manager-type"' not in source
    assert 'st.markdown("### Features")' in source
    assert 'st.markdown("### Workspaces")' in source
    assert "def _extension_manager_row" in source
    assert "def _render_extension_detail_page" in source


def test_plugin_page_icons_are_specific():
    common = (root() / "rank42" / "ui_pages" / "common.py").read_text()
    ui = (root() / "rank42" / "ui.py").read_text()
    assert '"Plugins": ":material/extension:"' in common
    assert '"Families": ":material/extension:"' in common
    assert '"Extensions": ":material/extension:"' in common
    assert '"Plugin Families": PAGE_ICONS["Plugins"]' in ui


def test_latest_core_rank_claim_logic_is_preserved():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert "family_rank_claim" in source
    assert '"HISTORICAL CLAIM"' in source
    assert 'claim["historical_lower"]' in source


def test_latest_core_feature_command_bridge_is_preserved():
    source = (root() / "rank42" / "ui_pages" / "common.py").read_text()
    assert "apply_search_command_features" in source
    assert "feature_command_hooks" in source


def test_latest_core_vendored_ratpoints_defaults_are_preserved():
    source = (root() / "rank42" / "ui.py").read_text()
    assert "vendored_executable" in source
    assert "seed_setting_if_missing(" in source
    assert '"ratpoints",' in source
    assert 'vendored_executable("CPU", ctx.project_root)' in source
    assert '"ratpoints_gpu",' in source
    assert 'vendored_executable("GPU", ctx.project_root)' in source
    assert 'set_setting(db, "ratpoints"' not in source
    assert 'set_setting(db, "ratpoints_gpu"' not in source


def test_rank_hunter_app_version_is_0_9_2():
    init_source = (root() / "rank42" / "__init__.py").read_text()
    ui_source = (root() / "rank42" / "ui.py").read_text()
    assert '__version__ = "0.9.2"' in init_source
    assert "from rank42 import __version__" in ui_source
    assert "v{__version__}" in ui_source
