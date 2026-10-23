from pathlib import Path

from streamlit.testing.v1 import AppTest

from rank42.ui_pages import common, dashboard
from rank42.db import connect


def test_dashboard_menu_matches_sidebar_search_order():
    search_pages = next(pages for label, pages in dashboard._DASHBOARD_NAV_GROUPS if label == "Search")
    assert search_pages == ("AI Hunt", "Search", "Pipelines", "Candidates")


def test_page_bar_switches_local_settings_view(tmp_path):
    app_path = tmp_path / "settings_bar.py"
    app_path.write_text(
        "import streamlit as st\n"
        "from rank42.ui_pages.common import title\n"
        "from rank42.ui_components import tabs\n"
        "st.session_state.setdefault('rh_page', 'Settings')\n"
        "title('Settings')\n"
        "choice = tabs(['General', 'Runtimes', 'Services', 'About'], "
        "value=st.session_state.get('settings_view') or 'General', "
        "key='settings-view-tabs', variant='line')\n"
        "st.session_state['settings_view'] = choice\n"
    )
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    app.button(key="rh-settings-pagebar-route-services").click().run()
    assert not app.exception
    assert app.session_state["settings_view"] == "Services"


def test_page_bar_switches_punctuated_views(tmp_path):
    for route, label, key, state in (
        ("Diagnostics", "Plugins & Libraries", "diagnostics-main-tabs", "diagnostics_tab"),
        ("Database", "Import / Export", "database-main-tabs", "database_tab"),
    ):
        app_path = tmp_path / f"{route.lower()}_bar.py"
        app_path.write_text(
            "import streamlit as st\n"
            "from rank42.ui_pages.common import title\n"
            "from rank42.ui_components import tabs\n"
            f"st.session_state.setdefault('rh_page', {route!r})\n"
            f"title({route!r})\n"
            f"choice = tabs({list(common._PAGE_LOCAL_VIEWS[route][0])!r}, "
            f"value=st.session_state.get({state!r}) or 'Overview', "
            f"key={key!r}, variant='line')\n"
            f"st.session_state[{state!r}] = choice\n"
        )
        app = AppTest.from_file(str(app_path), default_timeout=20).run()
        assert not app.exception
        bridge = f"rh-{route.lower()}-pagebar-route-{common.rh_key(label).removeprefix('rh-')}"
        app.button(key=bridge).click().run()
        assert not app.exception
        assert app.session_state[state] == label


def test_rank_lab_page_bar_uses_its_own_views(tmp_path):
    for route, target, state in (("Rank Lab", "Prove rank", "analysis-hub-lane"),):
        app_path = tmp_path / f"{route.lower().replace(' ', '_')}_bar.py"
        app_path.write_text(
            "import streamlit as st\n"
            "from rank42.ui_pages.common import title\n"
            f"st.session_state.setdefault('rh_page', {route!r})\n"
            f"title({route!r})\n"
        )
        app = AppTest.from_file(str(app_path), default_timeout=20).run()
        assert not app.exception
        labels = [button.label for button in app.button]
        assert labels == list(common._PAGE_LOCAL_VIEWS[route][0])
        bridge = f"rh-{common.rh_key(route).removeprefix('rh-')}-pagebar-route-{common.rh_key(target).removeprefix('rh-')}"
        app.button(key=bridge).click().run()
        assert not app.exception
        assert app.session_state[state] == target


def test_pages_with_short_local_navigation_do_not_need_menu_search():
    assert common._PAGE_LOCAL_VIEWS["Settings"][0] == ("General", "Runtimes", "Services", "About")
    assert "Casebook" not in common._PAGE_LOCAL_VIEWS
    assert len(common._PAGE_LOCAL_VIEWS["Candidates"][0]) == 2
    assert len(set(common._PAGE_LOCAL_ICONS["Candidates"].values())) == 2


def test_settings_header_exposes_lifecycle_actions(tmp_path):
    app_path = tmp_path / "settings_actions.py"
    app_path.write_text(
        "import streamlit as st\n"
        "from rank42.ui_pages.common import title\n"
        "st.session_state.setdefault('rh_page', 'Settings')\n"
        "title('Settings')\n"
    )
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    assert [button.label for button in app.button][-2:] == ["Restart", "Power off"]
    app.button(key="rh-settings-pagebar-route-restart").click().run()
    assert not app.exception
    assert app.session_state["settings_lifecycle_action"] == "Restart"
    app.button(key="rh-settings-pagebar-route-power-off").click().run()
    assert not app.exception
    assert app.session_state["settings_lifecycle_action"] == "Power off"


def test_campaigns_header_opens_create_action(tmp_path):
    app_path = tmp_path / "campaigns_bar.py"
    app_path.write_text(
        "import streamlit as st\n"
        "from rank42.ui_pages.common import title\n"
        "st.session_state.setdefault('rh_page', 'Campaigns')\n"
        "title('Campaigns')\n"
    )
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    assert [button.label for button in app.button] == ["Campaigns", "New Campaign"]
    app.button(key="rh-campaigns-pagebar-route-new-campaign").click().run()
    assert not app.exception
    assert app.session_state["campaign_create_from_pagebar"] is True


def test_candidates_pools_header_survives_shell_normalization(tmp_path):
    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    db.close()
    project_root = Path(__file__).resolve().parents[1]
    app_path = tmp_path / "candidates_bar.py"
    app_path.write_text(
        "import streamlit as st\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "from rank42.db import connect_existing\n"
        "from rank42.ui import _normalize_route\n"
        "from rank42.ui_pages import candidates_page\n"
        "st.session_state.setdefault('rh_page', 'Candidates')\n"
        "_normalize_route(st.session_state['rh_page'], st.session_state)\n"
        f"db = connect_existing({str(db_path)!r})\n"
        f"ctx = SimpleNamespace(project_root=Path({str(project_root)!r}), "
        f"db_path=Path({str(db_path)!r}))\n"
        "candidates_page.page(db, ctx)\n"
        "db.close()\n"
    )
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    assert not app.exception
    app.button(key="rh-candidates-pagebar-route-pools").click().run()
    assert not app.exception
    assert app.session_state["candidates_tab"] == "Pools"


def test_jobs_queue_contains_schedule_view(tmp_path):
    app_path = tmp_path / "jobs_queue.py"
    app_path.write_text(
        "import streamlit as st\n"
        "from rank42.ui_pages import jobs_page\n"
        "st.session_state.setdefault('rh_page', 'Jobs')\n"
        "if not st.session_state.get('_jobs_test_initialized'):\n"
        "    st.session_state['jobs_section_pending'] = 'Scheduled'\n"
        "    st.session_state['_jobs_test_initialized'] = True\n"
        "originals = (jobs_page.render_feature_hook, jobs_page._pipeline_run_handoff, "
        "jobs_page._queue, jobs_page._scheduled)\n"
        "try:\n"
        "    jobs_page.render_feature_hook = lambda *args: None\n"
        "    jobs_page._pipeline_run_handoff = lambda *args: None\n"
        "    jobs_page._queue = lambda *args: st.markdown('WAITING_VIEW')\n"
        "    jobs_page._scheduled = lambda *args: st.markdown('SCHEDULE_VIEW')\n"
        "    jobs_page.page(None, None)\n"
        "finally:\n"
        "    (jobs_page.render_feature_hook, jobs_page._pipeline_run_handoff, "
        "jobs_page._queue, jobs_page._scheduled) = originals\n"
    )
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    assert app.session_state["jobs_section"] == "Queue"
    assert app.session_state["jobs_queue_view"] == "Schedules"
    assert any("SCHEDULE_VIEW" in item.value for item in app.markdown)
    app.button_group(key="rh-jobs-queue-view").set_value("Waiting jobs").run()
    assert not app.exception
    assert app.session_state["jobs_queue_view"] == "Waiting jobs"
    assert any("WAITING_VIEW" in item.value for item in app.markdown)
