from pathlib import Path

from rank42.plugins import Plugin
from rank42.ui_pages import settings_page


ROOT = Path(__file__).resolve().parents[1]


def _plugin(tmp_path, *, pid, name, version, plugin_type):
    return Plugin(
        id=pid,
        name=name,
        version=version,
        plugin_type=plugin_type,
        root=tmp_path / pid,
        family_spec=None,
        manifest={},
        capabilities=frozenset(),
        adapter_path=None,
    )


def test_installed_plugin_rows_use_discovery_and_ignore_invalid_records(
    tmp_path,
    monkeypatch,
):
    records = [
        _plugin(
            tmp_path,
            pid="workspace-z",
            name="Zeta Workspace",
            version="3.0.0",
            plugin_type="extension",
        ),
        {"path": str(tmp_path / "broken"), "error": "invalid manifest"},
        _plugin(
            tmp_path,
            pid="family-b",
            name="Beta Family",
            version="2.0.0",
            plugin_type="family",
        ),
        _plugin(
            tmp_path,
            pid="feature-a",
            name="Alpha Feature",
            version="1.5.0",
            plugin_type="feature",
        ),
        _plugin(
            tmp_path,
            pid="family-a",
            name="Alpha Family",
            version="1.0.0",
            plugin_type="family",
        ),
    ]
    seen = {}

    def fake_discover(project_root):
        seen["project_root"] = project_root
        return records

    monkeypatch.setattr(settings_page, "discover_plugins", fake_discover)

    rows = settings_page._installed_plugin_rows(tmp_path)

    assert seen["project_root"] == tmp_path
    assert rows == [
        {"Type": "Family", "Plugin": "Alpha Family", "Version": "1.0.0"},
        {"Type": "Family", "Plugin": "Beta Family", "Version": "2.0.0"},
        {"Type": "Feature", "Plugin": "Alpha Feature", "Version": "1.5.0"},
        {"Type": "Workspace", "Plugin": "Zeta Workspace", "Version": "3.0.0"},
    ]


def test_about_inventory_is_read_only_and_rendered_beneath_github():
    source = (
        ROOT / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _about_settings(ctx):")
    end = source.index("def page(db, ctx):", start)
    about = source[start:end]

    github_pos = about.index("st.link_button(")
    inventory_pos = about.index('st.markdown("#### Installed plugins")')

    assert github_pos < inventory_pos
    assert "_installed_plugin_rows(ctx.project_root)" in about
    assert "st.dataframe(rows, width=\"stretch\", hide_index=True)" in about
    assert '"No installed plugins are currently discoverable."' in about

    for forbidden in (
        "plugin_state(",
        "set_plugin_state(",
        "archive_plugin(",
        "restore_plugin(",
        "_toggle(",
    ):
        assert forbidden not in about


def test_about_inventory_uses_filesystem_plugin_authority_only():
    source = (
        ROOT / "rank42" / "ui_pages" / "settings_page.py"
    ).read_text(encoding="utf-8")
    helper_start = source.index("def _installed_plugin_rows(project_root):")
    helper_end = source.index("def _about_settings(ctx):", helper_start)
    helper = source[helper_start:helper_end]

    assert "discover_plugins(project_root)" in helper
    assert "isinstance(rec, Plugin)" in helper
    assert "include_superseded=True" not in helper
    assert "unavailable_plugin_states" not in source
    assert "_about_settings(ctx)" in source
