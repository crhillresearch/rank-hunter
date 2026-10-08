from pathlib import Path


def source():
    return (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "plugins_page.py"
    ).read_text()


def block(text, start, end):
    left = text.index(start)
    right = text.index(end, left)
    return text[left:right]


def test_family_detail_summary_card_no_longer_contains_compact_actions():
    text = source()
    card = block(text, "def _family_card", "def _feature_card")

    assert "_family_capabilities(plugin)" in card
    assert "_family_toggle(" not in card
    assert 'st.columns([1, 0.28]' not in card


def test_family_detail_actions_render_below_summary_before_detail_content():
    text = source()
    detail = block(text, "def _family_detail", "def _extension_type_label")

    card_pos = detail.index("_family_card(db, ctx, plugin)")
    actions_pos = detail.index("_family_toggle(db, ctx, plugin)")
    status_pos = detail.index("st.caption(")

    assert card_pos < actions_pos < status_pos


def test_family_detail_lifecycle_actions_are_full_width_and_semantics_are_unchanged():
    text = source()
    toggle = block(text, "def _family_toggle", "def _family_rank_display")

    assert 'width="content"' not in toggle
    assert toggle.count('width="stretch"') == 5
    assert 'with st.container(key=rh_key(f"plugin-family-action-{plugin.id}"))' not in toggle

    assert 'semantic=f"plugin-restore-{plugin.id}"' in toggle
    assert 'semantic=f"plugin-disable-{plugin.id}"' in toggle
    assert 'semantic=f"plugin-activate-{plugin.id}"' in toggle
    assert 'semantic=f"plugin-revalidate-{plugin.id}"' in toggle
    assert 'semantic=f"plugin-archive-{plugin.id}"' in toggle

    assert "restore_plugin(db, plugin)" in toggle
    assert 'set_plugin_state(db, plugin.id, enabled=False, status="disabled")' in toggle
    assert toggle.count("_activate(db, ctx, plugin)") == 2
    assert "archive_plugin(db, plugin)" in toggle


def test_family_manager_rows_keep_separate_row_action_helper():
    text = source()
    row = block(text, "def _family_manager_row", "def _family_card")

    assert "_family_row_actions(db, ctx, plugin)" in row
    assert "_family_toggle(" not in row
