from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def settings_source():
    return (ROOT / "rank42" / "ui_pages" / "settings_page.py").read_text(
        encoding="utf-8"
    )


def ui_source():
    return (ROOT / "rank42" / "ui.py").read_text(encoding="utf-8")


def block(text, start, end):
    left = text.index(start)
    right = text.index(end, left)
    return text[left:right]


def test_services_view_contains_icarm_without_diagnostics_handoff():
    text = settings_source()
    services = block(text, "def _services_settings", "def _about_settings")

    assert "_icarm_settings(ctx)" in services
    assert "_diagnostics_handoff" not in services
    assert '"Open Diagnostics"' not in services
    assert '"System health"' not in services
    assert 'st.session_state["rh_page"] = "Diagnostics"' not in services


def test_icarm_configuration_behavior_is_preserved():
    text = settings_source()
    icarm = block(text, "def _icarm_settings", "def _services_settings")

    assert "read_token(ctx.project_root)" in icarm
    assert "token_source(ctx.project_root)" in icarm
    assert "masked_token(ctx.project_root)" in icarm
    assert "delete_local_token(ctx.project_root)" in icarm
    assert "write_token(ctx.project_root, token)" in icarm
    assert '"Save token"' in icarm
    assert '"Clear local token"' in icarm


def test_diagnostics_remains_system_owned_in_canonical_navigation():
    text = ui_source()
    navigation = block(text, "def navigation(db, ctx, extension_rows):", "_PAGE_END_RUNWAY_HTML")

    assert '"System": [(' in navigation
    assert '("Diagnostics", "Diagnostics")' in navigation
    assert '("Database", "Database")' in navigation
    assert '("Settings", "Settings")' in navigation
