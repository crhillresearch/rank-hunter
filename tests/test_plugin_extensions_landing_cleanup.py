from pathlib import Path


def source():
    return (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "plugins_page.py"
    ).read_text()


def block(text, start, end=None):
    left = text.index(start)
    if end is None:
        return text[left:]
    right = text.index(end, left)
    return text[left:right]


def test_workspace_manager_rows_do_not_render_route_chips():
    text = source()
    row = block(text, "def _extension_manager_row", "def _extension_detail")

    assert "plugin.extension_pages" in row
    assert 'meta = f"v{plugin.version} · Workspace · {page_count} {page_word}"' in row
    assert 'f"{page.section} → {page.label}"' not in row
    assert 'if plugin.plugin_type == "feature":' in row


def test_feature_manager_rows_do_not_render_interface_bubbles():
    text = source()
    row = block(text, "def _extension_manager_row", "def _extension_detail")

    assert "plugin.feature_hooks" not in row
    assert "plugin.feature_command_hooks" not in row
    assert "_extension_tags(" not in row
    assert "search_command" not in row


def test_feature_hook_metadata_remains_available_in_detail_and_search():
    text = source()
    search = block(text, "def _extension_search_text", "def _extension_interface_rows")
    detail_rows = block(text, "def _extension_interface_rows", "def _extension_row_actions")

    assert "plugin.feature_hooks" in search
    assert "plugin.feature_command_hooks" in search
    assert "plugin.feature_hooks" in detail_rows
    assert "plugin.feature_command_hooks" in detail_rows


def test_extensions_landing_does_not_surface_historical_unavailable_plugins():
    text = source()
    landing = block(text, "extension_plugins = [*all_features, *all_extensions]")

    status_start = landing.index("extension_status_filter = st.selectbox(")
    status_end = landing.index("with sort_col:", status_start)
    status_block = landing[status_start:status_end]

    assert '"Unavailable"' not in status_block
    assert "unavailable_extensions" not in landing
    assert "_render_unavailable_plugins(" not in landing
    assert "No unavailable Extensions match" not in landing
    assert "Other historical plugins" not in landing


def test_family_historical_unavailable_presentation_is_preserved():
    text = source()
    families = block(text, 'if choice == "Families":', "extension_plugins = [*all_features, *all_extensions]")

    assert '"Unavailable"' in families
    assert 'if family_status_filter == "Unavailable":' in families
    assert '_render_unavailable_plugins(rows, "Families")' in families
    assert "def _render_unavailable_plugins(records, label, *, expanded=True):" in text


def test_extensions_landing_keeps_management_and_grouping():
    text = source()
    landing = block(text, "extension_plugins = [*all_features, *all_extensions]")

    assert '"plugin-extension-manager-search"' in landing
    assert '"plugin-extension-manager-status"' in landing
    assert '"plugin-extension-manager-sort"' in landing
    assert 'st.markdown("### Features")' in landing
    assert 'st.markdown("### Workspaces")' in landing
    assert "_extension_manager_row(" in landing
