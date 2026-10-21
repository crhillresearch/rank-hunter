import sqlite3

from rank42.db import connect, connect_existing
from rank42.manage_store import ensure_manage_schema
from rank42.ui_store import ensure_ui_schema


def test_current_schema_connect_does_not_need_writer_lock(tmp_path):
    path = tmp_path / "rank42.db"

    db = connect(path)
    db.close()

    locker = sqlite3.connect(path, timeout=0.1)
    locker.execute("PRAGMA journal_mode=WAL")
    locker.execute("BEGIN IMMEDIATE")
    try:
        live = connect(path)
        try:
            assert int(live.execute("PRAGMA user_version").fetchone()[0]) >= 809
            point_columns = {row[1] for row in live.execute("PRAGMA table_info(points)").fetchall()}
            assert {"hard_flag", "hard_reason", "hard_flagged_at"} <= point_columns
            assert live.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0
        finally:
            live.close()
    finally:
        locker.rollback()
        locker.close()


def test_connect_existing_is_schema_write_free_under_writer_lock(tmp_path):
    path = tmp_path / "rank42.db"

    db = connect(path)
    db.close()

    locker = sqlite3.connect(path, timeout=0.1)
    locker.execute("PRAGMA journal_mode=WAL")
    locker.execute("BEGIN IMMEDIATE")
    try:
        live = connect_existing(path)
        try:
            assert live.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0
        finally:
            live.close()
    finally:
        locker.rollback()
        locker.close()


def test_ui_schema_check_is_read_only_after_initialization(tmp_path):
    path = tmp_path / "ui.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    assert ensure_ui_schema(db) is True

    traced = []
    db.set_trace_callback(traced.append)
    assert ensure_ui_schema(db) is False

    writes = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE "))
    ]
    assert writes == []
    db.close()


def test_v809_missing_hard_columns_repairs_before_index_creation(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.close()

    raw = sqlite3.connect(path)
    raw.execute("DROP INDEX IF EXISTS idx_points_hard")
    raw.execute("ALTER TABLE points DROP COLUMN hard_flag")
    raw.execute("ALTER TABLE points DROP COLUMN hard_reason")
    raw.execute("ALTER TABLE points DROP COLUMN hard_flagged_at")
    raw.execute("PRAGMA user_version=809")
    raw.commit()
    raw.close()

    repaired = connect(path)
    try:
        columns = {
            row["name"]
            for row in repaired.execute("PRAGMA table_info(points)").fetchall()
        }
        assert {"hard_flag", "hard_reason", "hard_flagged_at"} <= columns
        index = repaired.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_points_hard'"
        ).fetchone()
        assert index is not None
    finally:
        repaired.close()



def test_current_core_schema_includes_migration_indexes(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    try:
        indexes = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {
            "idx_candidates_native_fiber",
            "idx_curves_native_fiber",
            "idx_points_hard",
        }.issubset(indexes)
    finally:
        db.close()


def test_manage_schema_check_is_read_only_after_initialization(tmp_path):
    path = tmp_path / "manage.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    assert ensure_manage_schema(db) is True

    traced = []
    db.set_trace_callback(traced.append)
    assert ensure_manage_schema(db) is False

    writes = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(
            ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")
        )
    ]
    assert writes == []
    db.close()



def test_initialized_ui_store_steady_state_actions_need_no_ddl(tmp_path):
    from rank42.ui_store import create_job, get_job, get_setting, list_jobs, set_setting

    path = tmp_path / "ui-steady.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)

    traced = []
    db.set_trace_callback(traced.append)
    try:
        set_setting(db, "steady", 1)
        assert get_setting(db, "steady") == 1
        job_id = create_job(
            db,
            kind="test",
            label="steady",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "steady.log",
        )
        assert get_job(db, job_id)["label"] == "steady"
        assert [row["id"] for row in list_jobs(db)] == [job_id]
    finally:
        db.set_trace_callback(None)
        db.close()

    ddl = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
    ]
    assert ddl == []


def test_initialized_manage_store_steady_state_actions_need_no_ddl(tmp_path):
    from rank42.manage_store import (
        create_campaign,
        get_campaign,
        list_campaigns,
    )

    path = tmp_path / "manage-steady.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    ensure_manage_schema(db)

    traced = []
    db.set_trace_callback(traced.append)
    try:
        campaign_id = create_campaign(db, name="Steady")
        assert get_campaign(db, campaign_id)["name"] == "Steady"
        assert [row["id"] for row in list_campaigns(db)] == [campaign_id]
    finally:
        db.set_trace_callback(None)
        db.close()

    ddl = [
        stmt for stmt in traced
        if stmt.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
    ]
    assert ddl == []



def test_ui_store_steady_state_does_not_invoke_schema_mutator(tmp_path, monkeypatch):
    from rank42 import ui_store

    path = tmp_path / "ui-no-migrate.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    ui_store.ensure_ui_schema(db)

    monkeypatch.setattr(
        ui_store,
        "ensure_ui_schema",
        lambda db_: (_ for _ in ()).throw(
            AssertionError("steady-state UI operation invoked schema migration")
        ),
    )
    try:
        ui_store.set_setting(db, "steady", 7)
        assert ui_store.get_setting(db, "steady") == 7
        job_id = ui_store.create_job(
            db,
            kind="test",
            label="steady",
            command=["python"],
            cwd=tmp_path,
            log_path=tmp_path / "steady.log",
        )
        assert ui_store.get_job(db, job_id)["id"] == job_id
        queue_id = ui_store.create_queue_item(db, job_id=job_id)
        assert ui_store.get_queue_item(db, queue_id)["job_id"] == job_id
        assert ui_store.list_jobs(db)[0]["id"] == job_id
        assert ui_store.list_queue(db)[0]["job_id"] == job_id
    finally:
        db.close()


def test_manage_store_steady_state_does_not_invoke_schema_mutator(tmp_path, monkeypatch):
    from rank42 import manage_store, ui_store

    path = tmp_path / "manage-no-migrate.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    ui_store.ensure_ui_schema(db)
    manage_store.ensure_manage_schema(db)

    monkeypatch.setattr(
        manage_store,
        "ensure_manage_schema",
        lambda db_: (_ for _ in ()).throw(
            AssertionError("steady-state Manage operation invoked schema migration")
        ),
    )
    try:
        campaign_id = manage_store.create_campaign(db, name="Steady")
        assert manage_store.get_campaign(db, campaign_id)["id"] == campaign_id
        assert manage_store.list_campaigns(db)[0]["id"] == campaign_id
        note_id = manage_store.add_campaign_note(db, campaign_id, "note")
        assert manage_store.campaign_notes(db, campaign_id)[0]["id"] == note_id
        schedule_id = manage_store.create_schedule(
            db,
            label="once",
            kind="test",
            command=["python"],
            cwd=tmp_path,
            next_run_at="2099-01-01T00:00:00+00:00",
            campaign_id=campaign_id,
        )
        assert manage_store.list_schedules(db)[0]["id"] == schedule_id
    finally:
        db.close()
