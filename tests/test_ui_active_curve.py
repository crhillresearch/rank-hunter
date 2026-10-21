from pathlib import Path

import pytest

from rank42.ui_active_curve import (
    ACTIVE_CURVE_KEY,
    get_active_curve_id,
    set_active_curve_id,
)


ROOT = Path(__file__).resolve().parents[1]


def test_active_curve_context_prefers_semantic_state_over_stale_legacy_key():
    state = {
        ACTIVE_CURVE_KEY: 17,
        "independence_curve_id": 3,
        "target_curve_id": 5,
    }

    assert get_active_curve_id(state, "independence_curve_id") == 17
    assert get_active_curve_id(state, "target_curve_id") == 17
    assert state[ACTIVE_CURVE_KEY] == 17


def test_active_curve_context_adopts_legacy_state_only_when_semantic_missing():
    state = {"target_curve_id": "23"}

    assert get_active_curve_id(state, "target_curve_id") == 23
    assert state[ACTIVE_CURVE_KEY] == 23


def test_active_curve_context_mirrors_only_requested_compatibility_keys():
    state = {"independence_curve_id": 4}

    assert set_active_curve_id(
        state,
        29,
        "descent_curve_id",
        "analysis_curve_id",
    ) == 29
    assert state == {
        "independence_curve_id": 4,
        ACTIVE_CURVE_KEY: 29,
        "descent_curve_id": 29,
        "analysis_curve_id": 29,
    }


@pytest.mark.parametrize("value", [None, "", 0, -1, "nope"])
def test_active_curve_context_rejects_invalid_new_selection(value):
    with pytest.raises(ValueError):
        set_active_curve_id({}, value)


def test_analysis_owner_pages_use_shared_active_curve_context():
    shared_selector_pages = (
        "analyze_page.py",
        "independence_page.py",
        "descent_page.py",
        "lattices_page.py",
        "quartics_page.py",
        "target_curve.py",
    )
    for name in shared_selector_pages:
        source = (ROOT / "rank42" / "ui_pages" / name).read_text(encoding="utf-8")
        assert "get_active_curve_id" in source
        assert "active_curve_selector" in source

    common_source = (
        ROOT / "rank42" / "ui_pages" / "common.py"
    ).read_text(encoding="utf-8")
    assert "def active_curve_selector(" in common_source
    assert "set_active_curve_id(" in common_source

    direct_pages = {
        "points_page.py": ("get_active_curve_id", "set_active_curve_id"),
        "curves.py": ("set_active_curve_id",),
    }
    for name, markers in direct_pages.items():
        source = (ROOT / "rank42" / "ui_pages" / name).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source

def test_analysis_handoff_writers_no_longer_directly_assign_page_curve_keys():
    sources = {
        name: (ROOT / "rank42" / "ui_pages" / name).read_text(encoding="utf-8")
        for name in (
            "analyze_page.py",
            "independence_page.py",
            "descent_page.py",
            "lattices_page.py",
            "quartics_page.py",
            "target_curve.py",
            "points_page.py",
            "curves.py",
        )
    }

    forbidden = (
        'st.session_state["independence_curve_id"] =',
        "st.session_state['independence_curve_id']=",
        'st.session_state["descent_curve_id"] =',
        "st.session_state['descent_curve_id']=",
        'st.session_state["analysis_curve_id"] =',
        "st.session_state['analysis_curve_id']=",
        'st.session_state["target_curve_id"] =',
        "st.session_state['target_curve_id']=",
    )
    combined = "\n".join(sources.values())
    for assignment in forbidden:
        assert assignment not in combined

    assert 'st.session_state["points_curve_id"] = int(chosen)' not in combined
    assert 'st.session_state["points_curve_id"] = curve_id' not in combined
