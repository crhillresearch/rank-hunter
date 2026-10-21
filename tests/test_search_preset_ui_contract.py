from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_candidate_page_renders_manifest_search_styles():
    text = (ROOT / "rank42" / "ui_pages" / "candidate_generate_page.py").read_text(encoding="utf-8")
    assert "search_presets_for_variant" in text
    assert "_apply_candidate_preset" in text
    assert "Search style" in text
    assert "cand-amin-" in text
    assert "cand-bmax-" in text
    assert "cand-bounds-" in text
    assert "cand-keeps-" in text


def test_family_search_renders_manifest_search_styles_in_right_column():
    text = (ROOT / "rank42" / "ui_pages" / "family_search.py").read_text(encoding="utf-8")
    assert "search_presets_for_variant" in text
    assert "_apply_family_preset" in text
    assert 'with guide_col:' in text
    assert '"Search style"' in text
    assert 'f"fam-{plugin_id}-{name}"' in text
    assert 'f"family-count-{pool_id}"' in text


def test_presets_are_visible_and_editable_not_hidden_modes():
    candidate = (ROOT / "rank42" / "ui_pages" / "candidate_generate_page.py").read_text(encoding="utf-8")
    family = (ROOT / "rank42" / "ui_pages" / "family_search.py").read_text(encoding="utf-8")
    assert "Clicking one fills the visible controls" in candidate
    assert "fill the visible controls" in family
    assert "Modified" in candidate
    assert "Modified" in family


def test_preset_controlled_widgets_do_not_mix_explicit_defaults_with_session_state():
    candidate = (ROOT / "rank42" / "ui_pages" / "candidate_generate_page.py").read_text(encoding="utf-8")
    family = (ROOT / "rank42" / "ui_pages" / "family_search.py").read_text(encoding="utf-8")
    assert "def _widget_default_kwargs" in candidate
    assert "def _widget_default_kwargs" in family
    assert "if key in st.session_state:" in candidate
    assert "if key in st.session_state:" in family
    assert "**_widget_default_kwargs(amin_key" in candidate
    assert "**_widget_default_kwargs(engine_key" in candidate
    assert "**_widget_default_kwargs(" in family
    assert 'count_key = f"family-count-{pool[\'id\']}"' in family


def test_candidate_plugin_version_change_discards_stale_generation_defaults(monkeypatch):
    from rank42.ui_pages import candidate_generate_page

    state = {}
    monkeypatch.setattr(candidate_generate_page.st, "session_state", state)
    keys = candidate_generate_page._candidate_control_keys("demo", "v1")
    state[keys["stage_bounds"]] = "500,1500,8000"
    state[keys["top"]] = 1000

    assert candidate_generate_page._sync_candidate_state_version("demo", "v1", "0.2.0") is True
    assert keys["stage_bounds"] not in state
    assert keys["top"] not in state

    state[keys["stage_bounds"]] = "500,1000,2000"
    assert candidate_generate_page._sync_candidate_state_version("demo", "v1", "0.2.0") is False
    assert state[keys["stage_bounds"]] == "500,1000,2000"
