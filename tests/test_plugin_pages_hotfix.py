from pathlib import Path


def root():
    return Path(__file__).resolve().parents[1]


def test_plugin_navigation_page_owns_canonical_title_and_subpage_headings():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert 'title(\n        "Plugins",' in source
    assert 'icon="extension"' in source
    assert 'st.subheader("Families", anchor=False)' not in source
    assert 'st.subheader("Extensions", anchor=False)' not in source
    assert '["Families", "Extensions"]' in source
    assert 'variant="line"' in source
    assert 'width="content"' in source
    assert 'initial_tab in {"Families", "Extensions"}' in source


def test_families_use_status_filter_instead_of_separate_active_only_toggle():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '"Active only"' not in source
    assert 'plugin-families-active-only' not in source
    assert '"Enabled"' in source
    assert '"Disabled"' in source
    assert '"plugin-family-manager-status"' in source
    assert '"No Families match the current manager filters."' in source


def test_plugin_page_headers_use_matching_icons():
    common = (root() / "rank42" / "ui_pages" / "common.py").read_text()
    ui = (root() / "rank42" / "ui.py").read_text()
    assert '"Families": ":material/extension:"' in common
    assert '"Extensions": ":material/extension:"' in common
    assert '"Plugin Families": PAGE_ICONS["Plugins"]' in ui


def test_family_filter_toolbar_layout():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '[2.3, 1.15, 1.35, 0.9]' in source
    assert 'with search_col:' in source
    assert 'with status_col:' in source
    assert 'with capability_col:' in source
    assert 'with sort_col:' in source
    assert '"plugin-family-manager-capability"' in source
    assert '"All capabilities"' in source
    assert 'rh-family-toolbar-summary' not in source
    assert '"Install Plugins"' not in source


def test_sidebar_family_icon_matches_current_family_navigation():
    source = (root() / "rank42" / "ui.py").read_text()
    assert '"Plugin Families": PAGE_ICONS["Plugins"]' in source


def test_plugin_manager_exposes_archive_restore_and_validation_freshness():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert "archive_plugin" in source
    assert "restore_plugin" in source
    assert "validation_freshness" in source
    assert '"Archive"' in source
    assert '"Restore"' in source
    assert "Needs revalidation" in source


def test_families_are_managed_with_row_to_subpage_semantics():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '"plugin-family-manager-search"' in source
    assert '"plugin-family-manager-status"' in source
    assert '"plugin-family-manager-capability"' in source
    assert '"plugin-family-manager-sort"' in source
    assert "def _family_manager_row" in source
    assert "_family_manager_row(" in source
    assert '"plugin-family-manager-detail-open"' not in source
    assert '"Installed Families"' not in source
    assert 'def _render_family_detail_page' in source
    assert '"All families"' in source
    assert 'st.session_state.pop("plugin-family-manager-selected", None)' in source
    assert 'st.subheader(f"About · {plugin.name}")' in source
    assert "_family_detail(" in source
    assert '"Needs rank verification"' in source


def test_family_manager_row_action_keys_do_not_collide_with_detail_actions():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    row_start = source.index("def _family_row_actions")
    row_end = source.index("def _family_manager_row", row_start)
    row = source[row_start:row_end]

    for suffix in ("restore", "disable", "activate", "revalidate", "archive"):
        assert f'plugin-family-row-{suffix}-{{plugin.id}}' in row

    detail_start = source.index("def _family_toggle")
    detail_end = source.index("def _family_rank_display", detail_start)
    detail = source[detail_start:detail_end]
    assert 'semantic=f"plugin-disable-{plugin.id}"' in detail
    assert 'semantic=f"plugin-archive-{plugin.id}"' in detail


def test_family_detail_exposes_audited_research_metadata():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    start = source.index("def _family_detail")
    end = source.index("def _extension_type_label", start)
    detail = source[start:end]

    assert 'st.markdown("#### Variants")' in detail
    assert 'st.markdown("#### Research assets")' in detail
    assert 'st.markdown("**Capabilities**")' in detail
    assert 'st.markdown("**Research presets**")' in detail
    assert 'st.markdown("**Libraries**")' in detail
    assert 'st.markdown("#### Provenance & trust")' in detail
    assert "Family Plugins may execute local Python code" in detail
    assert "not a security sandbox" in detail
    assert "Source folder:" in detail
    assert "Search adapter:" in detail
    assert 'st.markdown("#### Validation record")' in detail
    assert 'st.expander("Provenance & trust"' not in detail
    assert 'st.expander("Validation record"' not in detail
    assert "search_presets_for_variant" in source
    assert "corpora_for_variant" in source


def test_family_manager_distinguishes_lifecycle_and_health_statuses():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    for label in (
        "Enabled",
        "Disabled",
        "Archived",
        "Invalid",
        "Needs revalidation",
        "Unavailable",
        "Experimental",
        "Superseded",
    ):
        assert f'"{label}"' in source


def test_extensions_are_separated_and_use_row_to_subpage_semantics():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert '"plugin-extension-manager-search"' in source
    assert '"plugin-extension-manager-status"' in source
    assert '"plugin-extension-manager-type"' not in source
    assert '"plugin-extension-manager-sort"' in source
    assert '"Installed Extensions"' not in source
    assert 'st.markdown("### Features")' in source
    assert 'st.markdown("### Workspaces")' in source
    assert "def _extension_manager_row" in source
    assert "_extension_manager_row(" in source
    row_start = source.index("def _extension_manager_row")
    row_end = source.index("def _extension_detail", row_start)
    row = source[row_start:row_end]
    assert "_extension_description(plugin)" not in row
    assert "def _render_extension_detail_page" in source
    assert '"All extensions"' in source
    assert 'st.session_state["plugin-extension-manager-selected"] = plugin.id' in source
    assert "_extension_detail(" in source


def test_extension_detail_exposes_pages_hooks_provenance_validation_and_trust():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    assert "_extension_interface_rows" in source
    assert '"Hooks" if plugin.plugin_type == "feature" else "Pages"' in source
    assert 'st.markdown(f"#### {interface_label}")' in source
    assert 'st.markdown("#### Provenance & trust")' in source
    assert "not a security sandbox" in source
    assert "Source folder:" in source
    assert 'st.markdown("#### Validation record")' in source
    assert "system_privileges" in source


def test_extensions_manager_preserves_existing_execution_boundaries():
    extensions = (root() / "rank42" / "ui_extensions.py").read_text()
    features = (root() / "rank42" / "feature_hooks.py").read_text()
    assert "load_extension(plugin, page)" in extensions
    assert "ExtensionContextV1(" in extensions
    assert "feature_hook_allowed(" in features
    assert "load_feature(plugin)" in features
    assert "FeatureContextV1(" in features


def test_moved_surfaces_emit_canonical_v1_feature_hook_ids():
    expected = {
        "results_page.py": ("manage.jobs.results.after_header", "results.after_header"),
        "external_catalog.py": ("data.catalogs.after_header", "external_catalog.after_header"),
        "analyze_page.py": ("analysis.work_center.after_header", "analysis.analyze.after_header"),
        "lattices_page.py": ("analysis.mw_geometry.after_header", "analysis.lattices.after_header"),
    }
    pages = root() / "rank42" / "ui_pages"
    for filename, (canonical, legacy) in expected.items():
        source = (pages / filename).read_text()
        assert canonical in source
        assert f'"{legacy}"' not in source
        assert f"'{legacy}'" not in source



def test_plugin_manager_surfaces_unavailable_historical_packages_without_delete_action():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()

    assert "unavailable_plugin_states" in source
    assert '"Unavailable"' in source
    assert "Unavailable historical" in source
    assert "not eligible for new work" in source
    assert "Last stored validation fingerprints" in source
    assert "Reinstall the matching Plugin package" in source
    assert '"Delete"' not in source
    assert '"Uninstall"' not in source



def test_extensions_status_filter_omits_unavailable_historical_state():
    source = (root() / "rank42" / "ui_pages" / "plugins_page.py").read_text()
    start = source.index("extension_status_filter = st.selectbox(")
    end = source.index("with sort_col:", start)
    status_block = source[start:end]
    landing = source[source.index("extension_plugins = [*all_features, *all_extensions]"):]

    assert '"Unavailable"' not in status_block
    assert 'if extension_status_filter == "Unavailable":' not in landing
    assert "_render_unavailable_plugins(" not in landing
    assert "def _render_unavailable_plugins(records, label, *, expanded=True):" in source
