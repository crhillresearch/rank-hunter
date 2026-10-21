import inspect
import sqlite3
import types

from rank42.ui_pages import common
from rank42.ui_pages.common import active_campaign_pin_payload


def test_active_campaign_pin_payload_includes_research_context():
    row = {
        "id": 7,
        "name": "CEF Rank 11 Hunt",
        "target_rank": 11,
        "family": "CEF",
        "plugin_id": "dmt_triad_rank4",
    }
    payload = active_campaign_pin_payload(row)
    assert payload == {
        "id": 7,
        "name": "CEF Rank 11 Hunt",
        "detail": "target ≥11 · family CEF · plugin dmt_triad_rank4",
    }


def test_active_campaign_pin_payload_handles_sparse_campaign():
    row = {
        "id": 2,
        "name": "Exploration",
        "target_rank": None,
        "family": None,
        "plugin_id": None,
    }
    assert active_campaign_pin_payload(row) == {
        "id": 2,
        "name": "Exploration",
        "detail": "",
    }


def test_active_campaign_pin_payload_omits_when_none():
    assert active_campaign_pin_payload(None) is None


def test_active_campaign_pin_is_soft_page_width_status_bar():
    source = inspect.getsource(common.active_campaign_pin)

    assert 'rh-active-campaign-screen-strip' not in source
    assert 'position: fixed;' not in source
    assert 'width: 100vw;' not in source

    assert 'div[class*="st-key-rh-active-campaign-pin"] {' in source
    assert "position: sticky;" in source
    assert "top: 0;" in source
    assert "z-index: 990;" in source
    assert "width: 100% !important;" in source
    assert "var(--rh-success, #1fa65d)" in source
    assert "var(--rh-raised, transparent)" in source
    assert "var(--rh-text-on-accent, white)" in source
    assert "rgba(31, 166, 93, 0.72)" not in source
    assert "margin: -1rem 0 .7rem 0 !important;" in source


def test_active_campaign_pin_status_line_opens_campaign_without_open_label():
    source = inspect.getsource(common.active_campaign_pin)

    assert 'label = f"Current · #{payload[\'id\']} · {payload[\'name\']}{detail}"' in source
    assert 'icon=":material/flag:"' in source
    assert 'key=rh_key("active-campaign-open")' in source
    assert 'help="Open current campaign"' in source
    assert '"Open"' not in source
    assert 'text-decoration: underline' not in source
    assert 'st.session_state["manage_campaign_id"] = int(payload["id"])' in source
    assert 'st.session_state["rh_page"] = "Campaigns"' in source



def test_active_campaign_lookup_distinguishes_no_campaign_from_failure(monkeypatch):
    from rank42 import manage_store

    monkeypatch.setattr(manage_store, "active_campaign", lambda db: None)
    row, failure = common._active_campaign_lookup(object())

    assert row is None
    assert failure is None


def test_active_campaign_lookup_hides_paused_current_context(monkeypatch):
    from rank42 import manage_store

    monkeypatch.setattr(manage_store, "active_campaign", lambda db: None)
    row, failure = common._active_campaign_lookup(object())

    assert row is None
    assert failure is None


def test_active_campaign_pin_surfaces_transient_database_lock(monkeypatch):
    from rank42 import manage_store

    def locked(_db):
        raise sqlite3.OperationalError("database is locked")

    warnings = []
    errors = []
    monkeypatch.setattr(manage_store, "active_campaign", locked)
    monkeypatch.setattr(
        common,
        "st",
        types.SimpleNamespace(
            warning=warnings.append,
            error=errors.append,
        ),
    )

    assert common.active_campaign_pin(object()) is None
    assert errors == []
    assert warnings == [
        "Current campaign temporarily unavailable: database is locked"
    ]


def test_active_campaign_pin_logs_and_surfaces_unexpected_failure(monkeypatch):
    from rank42 import manage_store

    def broken(_db):
        raise RuntimeError("campaign state exploded")

    errors = []
    logged = []
    monkeypatch.setattr(manage_store, "active_campaign", broken)
    monkeypatch.setattr(common.logger, "exception", lambda *args, **kwargs: logged.append(args))
    monkeypatch.setattr(
        common,
        "st",
        types.SimpleNamespace(
            warning=lambda message: None,
            error=errors.append,
        ),
    )

    assert common.active_campaign_pin(object()) is None
    assert logged
    assert errors == [
        "Current campaign lookup failed: RuntimeError: campaign state exploded"
    ]


def test_launch_records_campaign_lookup_failure_in_job_metadata(monkeypatch):
    from rank42 import launch_context, plugins

    warnings = []
    captured = {}
    fake_st = types.SimpleNamespace(
        session_state={},
        warning=warnings.append,
        error=lambda message: None,
        caption=lambda message: None,
    )
    monkeypatch.setattr(common, "st", fake_st)
    monkeypatch.setattr(
        launch_context,
        "lookup_active_campaign",
        lambda db, logger=None: (
            None,
            {
                "level": "warning",
                "message": "Active campaign temporarily unavailable: database is locked",
            },
        ),
    )
    monkeypatch.setattr(
        plugins,
        "apply_search_command_features",
        lambda root, db, command, **kwargs: (list(command), []),
    )

    def fake_enqueue(db_path, **kwargs):
        captured["db_path"] = db_path
        captured.update(kwargs)
        return 77

    monkeypatch.setattr(common, "enqueue_job", fake_enqueue)
    ctx = types.SimpleNamespace(db_path="rank42.db", project_root=".")

    assert common.launch(
        ctx,
        object(),
        kind="test",
        label="demo",
        command=["python", "-V"],
    ) == 77

    assert "campaign_id" not in captured["metadata"]
    assert captured["metadata"]["campaign_context_error"] == {
        "level": "warning",
        "message": "Active campaign temporarily unavailable: database is locked",
    }
    assert captured["metadata"]["launch_surface"] == "test"
    assert warnings == [
        "Active campaign temporarily unavailable: database is locked "
        "Launching without automatic campaign attachment."
    ]
    assert fake_st.session_state["_rh_job_just_started"] == 77
