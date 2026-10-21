import inspect

from rank42.ui_pages import campaign_handoff_panel


def test_campaign_handoff_embeds_explicit_research_bundle_action():
    source = inspect.getsource(campaign_handoff_panel.render_handoff)

    assert '"#### Research Bundle"' in source
    assert '"Prepare / rebuild Research Bundle"' in source
    assert "build_campaign_bundle_manifest(" in source
    assert "build_campaign_bundle_archive(" in source
    assert '"Download Research Bundle ZIP"' in source
    assert "campaign-research-bundle-" in source
    assert "application/zip" in source
    assert "Private scratch / Resolved" in source


def test_campaign_bundle_is_prepared_only_after_explicit_button_action():
    source = inspect.getsource(campaign_handoff_panel.render_handoff)

    button = source.index('"Prepare / rebuild Research Bundle"')
    condition = source.index("if st.button(", 0, button)
    build = source.index("build_campaign_bundle_manifest(", button)
    download = source.index('"Download Research Bundle ZIP"', build)

    assert condition < button < build < download
