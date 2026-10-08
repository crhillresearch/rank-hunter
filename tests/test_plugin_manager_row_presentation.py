from pathlib import Path


def source():
    return (Path(__file__).resolve().parents[1] / "rank42" / "ui_pages" / "plugins_page.py").read_text()


def block(text, start, end):
    left = text.index(start)
    right = text.index(end, left)
    return text[left:right]


def test_disabled_family_rows_are_not_dimmed():
    text = source()
    assert ':has(.rh-status-dot.off) .rh-family-header-row' not in text
    assert ':has(.rh-status-dot.off) .rh-family-description' not in text
    assert ':has(.rh-status-dot.off) .rh-family-capabilities' not in text


def test_family_capability_tags_are_soft_neutral_and_full_opacity():
    text = source()
    css = block(text, "        .rh-family-capability {", "        .rh-extension-tag {")
    assert "opacity: 1;" in css
    assert "opacity: .92;" not in css
    assert "color: var(--rh-text-secondary, #5c6370);" in css
    assert "var(--rh-text-secondary, #5c6370) 6%" in css
    assert "var(--rh-text-secondary, #5c6370) 10%" in css
    assert "var(--rh-card, #fff)" in css
    assert "currentColor 12%" not in css


def test_family_manager_status_badges_are_inline_with_name():
    text = source()
    row = block(text, "def _family_manager_row", "def _family_card")
    title_close = row.index("f'</div>'")
    status_call = row.index("_plugin_status_bubbles_html(db, plugin, superseded=superseded)")
    assert status_call < title_close
    assert "_family_status_bubbles(" not in row


def test_extension_manager_status_badges_are_inline_with_name():
    text = source()
    row = block(text, "def _extension_manager_row", "def _extension_detail")
    title_close = row.index("f'</div>'")
    status_call = row.index("_plugin_status_bubbles_html(db, plugin, superseded=superseded)")
    assert status_call < title_close
    assert "_family_status_bubbles(" not in row


def test_status_badge_helper_reuses_existing_status_authority():
    text = source()
    helper = block(text, "def _plugin_status_bubbles_html", "def _family_row_actions")
    assert "_family_status_tags(db, plugin, superseded=superseded)" in helper
    assert "rh-plugin-status-bubble" in helper
    assert "return '<span class=\"rh-plugin-status-bubbles\">'" in helper
