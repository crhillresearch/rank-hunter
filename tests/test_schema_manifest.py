import sqlite3

import pytest

from rank42.db import CURRENT_SCHEMA_VERSION, connect
from rank42.manage_store import ensure_manage_schema
from rank42.schema_manifest import (
    CORE_SCHEMA_MANIFEST,
    MANAGE_SCHEMA_MANIFEST,
    UI_SCHEMA_MANIFEST,
    inspect_schema,
)
from rank42.ui_store import ensure_ui_schema


def _full_db(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    ensure_ui_schema(db)
    ensure_manage_schema(db)
    return path, db


def test_current_schema_manifests_are_ready_after_initialization(tmp_path):
    _, db = _full_db(tmp_path)
    try:
        core = inspect_schema(db, CORE_SCHEMA_MANIFEST)
        ui = inspect_schema(db, UI_SCHEMA_MANIFEST)
        manage = inspect_schema(db, MANAGE_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert core["ready"] is True
    assert core["actual_user_version"] == CURRENT_SCHEMA_VERSION
    assert core["expected_user_version"] == CURRENT_SCHEMA_VERSION
    assert ui["ready"] is True
    assert ui["expected_user_version"] is None
    assert manage["ready"] is True
    assert manage["expected_user_version"] is None


def test_schema_inspection_is_read_only(tmp_path):
    _, db = _full_db(tmp_path)
    traced = []
    db.set_trace_callback(traced.append)
    try:
        assert inspect_schema(db, CORE_SCHEMA_MANIFEST)["ready"] is True
        assert inspect_schema(db, UI_SCHEMA_MANIFEST)["ready"] is True
        assert inspect_schema(db, MANAGE_SCHEMA_MANIFEST)["ready"] is True
    finally:
        db.set_trace_callback(None)
        db.close()

    writes = [
        stmt
        for stmt in traced
        if stmt.lstrip().upper().startswith(
            ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")
        )
    ]
    assert writes == []


def test_core_manifest_requires_exact_user_version(tmp_path):
    path, db = _full_db(tmp_path)
    db.close()

    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
    raw.commit()
    raw.row_factory = sqlite3.Row
    try:
        status = inspect_schema(raw, CORE_SCHEMA_MANIFEST)
    finally:
        raw.close()

    assert status["ready"] is False
    assert status["version_ok"] is False
    assert status["actual_user_version"] == CURRENT_SCHEMA_VERSION + 1


def test_core_manifest_reports_missing_index(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP INDEX idx_points_hard")
    db.commit()
    try:
        status = inspect_schema(db, CORE_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "idx_points_hard" in status["missing_indexes"]


def test_core_manifest_reports_missing_column(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP INDEX idx_points_hard")
    db.execute("ALTER TABLE points DROP COLUMN hard_reason")
    db.commit()
    try:
        status = inspect_schema(db, CORE_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "hard_reason" in status["missing_columns"]["points"]


def test_core_manifest_reports_missing_table(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP TABLE general_hunt_trials")
    db.commit()
    try:
        status = inspect_schema(db, CORE_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "general_hunt_trials" in status["missing_tables"]


def test_connect_refuses_future_schema_without_downgrading(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.close()

    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()

    with pytest.raises(sqlite3.DatabaseError, match="newer than this core supports"):
        connect(path)

    check = sqlite3.connect(path)
    try:
        assert int(check.execute("PRAGMA user_version").fetchone()[0]) == (
            CURRENT_SCHEMA_VERSION + 1
        )
    finally:
        check.close()



def test_ui_manifest_requires_service_heartbeat_table(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP TABLE ui_service_heartbeats")
    db.commit()
    try:
        status = inspect_schema(db, UI_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "ui_service_heartbeats" in status["missing_tables"]


def test_ui_manifest_requires_analysis_case_table(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP TABLE analysis_cases")
    db.commit()
    try:
        status = inspect_schema(db, UI_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "analysis_cases" in status["missing_tables"]


def test_ui_schema_repairs_missing_analysis_case_table(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP TABLE analysis_cases")
    db.commit()

    changed = ensure_ui_schema(db)
    try:
        status = inspect_schema(db, UI_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert changed is True
    assert status["ready"] is True


def test_ui_manifest_requires_application_state_table(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP TABLE ui_application_state")
    db.commit()
    try:
        status = inspect_schema(db, UI_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "ui_application_state" in status["missing_tables"]


def test_ui_manifest_reports_missing_index(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP INDEX idx_ui_job_queue_job")
    db.commit()
    try:
        status = inspect_schema(db, UI_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert "idx_ui_job_queue_job" in status["missing_indexes"]


def test_manage_manifest_reports_missing_column(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute(
        "ALTER TABLE ui_job_schedule_occurrences DROP COLUMN snapshot_json"
    )
    db.commit()
    try:
        status = inspect_schema(db, MANAGE_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert (
        "snapshot_json"
        in status["missing_columns"]["ui_job_schedule_occurrences"]
    )


def test_manage_manifest_requires_typed_notebook_columns_and_links(tmp_path):
    _, db = _full_db(tmp_path)
    db.execute("DROP TABLE research_campaign_note_links")
    db.execute(
        "ALTER TABLE research_campaign_notes DROP COLUMN handoff_state"
    )
    db.commit()
    try:
        status = inspect_schema(db, MANAGE_SCHEMA_MANIFEST)
    finally:
        db.close()

    assert status["ready"] is False
    assert (
        "handoff_state"
        in status["missing_columns"]["research_campaign_notes"]
    )
    assert "research_campaign_note_links" in status["missing_tables"]
    assert "idx_research_campaign_note_links_note" in status["missing_indexes"]



def test_schema_component_readiness_has_stable_dependency_order(tmp_path):
    from rank42.migration_manager import (
        MIGRATION_COMPONENTS,
        migration_plan,
        open_database_with_migrations,
    )

    assert [component.id for component in MIGRATION_COMPONENTS] == [
        "core",
        "ui",
        "manage",
        "pipeline",
        "auto_search",
        "prime_local",
        "torsion",
    ]
    assert [component.prerequisites for component in MIGRATION_COMPONENTS] == [
        (),
        ("core",),
        ("ui",),
        ("manage",),
        ("core",),
        ("core",),
        ("core",),
    ]

    db = open_database_with_migrations(tmp_path / "rank42.db")
    try:
        plan = migration_plan(db)
        assert plan["ready"] is True
        assert plan["pending"] == []
    finally:
        db.close()



def test_v809_migrates_append_only_point_discovery_ledger(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.execute("DROP INDEX idx_point_discoveries_pipeline")
    db.execute("DROP INDEX idx_point_discoveries_point")
    db.execute("DROP INDEX idx_point_discoveries_curve")
    db.execute("DROP TABLE point_discoveries")
    db.execute("PRAGMA user_version=809")
    db.commit()
    db.close()

    migrated = connect(path)
    try:
        status = inspect_schema(migrated, CORE_SCHEMA_MANIFEST)
        assert status["ready"] is True
        assert status["actual_user_version"] == CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 813
        tables = {
            row["name"]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "point_discoveries" in tables
        indexes = {
            row["name"]
            for row in migrated.execute(
                "PRAGMA index_list(point_discoveries)"
            ).fetchall()
        }
        assert "idx_point_discoveries_curve" in indexes
        assert "idx_point_discoveries_point" in indexes
        assert "idx_point_discoveries_pipeline" in indexes
    finally:
        migrated.close()



def test_v810_migrates_covering_search_attempt_ledger(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.execute("DROP INDEX idx_covering_attempts_pipeline")
    db.execute("DROP INDEX idx_covering_attempts_covering")
    db.execute("DROP TABLE covering_search_attempts")
    db.execute("PRAGMA user_version=810")
    db.commit()
    db.close()

    migrated = connect(path)
    try:
        status = inspect_schema(migrated, CORE_SCHEMA_MANIFEST)
        assert status["ready"] is True
        assert status["actual_user_version"] == CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 813
        tables = {
            row["name"]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "covering_search_attempts" in tables
        indexes = {
            row["name"]
            for row in migrated.execute(
                "PRAGMA index_list(covering_search_attempts)"
            ).fetchall()
        }
        assert "idx_covering_attempts_covering" in indexes
        assert "idx_covering_attempts_pipeline" in indexes
    finally:
        migrated.close()


def test_v811_migrates_conditional_mw_upper_column(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.execute("ALTER TABLE rank_evidence DROP COLUMN conditional_mw_upper")
    db.execute("PRAGMA user_version=811")
    db.commit()
    db.close()

    migrated = connect(path)
    try:
        status = inspect_schema(migrated, CORE_SCHEMA_MANIFEST)
        assert status["ready"] is True
        assert status["actual_user_version"] == CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 813
        columns = {
            row["name"]
            for row in migrated.execute("PRAGMA table_info(rank_evidence)")
        }
        assert "conditional_mw_upper" in columns
    finally:
        migrated.close()


def test_v812_migrates_family_evidence_authority(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.execute("DROP INDEX idx_family_evidence_family")
    db.execute("DROP TABLE family_evidence")
    db.execute("PRAGMA user_version=812")
    db.commit()
    db.close()

    migrated = connect(path)
    try:
        status = inspect_schema(migrated, CORE_SCHEMA_MANIFEST)
        assert status["ready"] is True
        assert status["actual_user_version"] == CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION == 813
        tables = {
            row["name"]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "family_evidence" in tables
        columns = {
            row["name"]
            for row in migrated.execute("PRAGMA table_info(family_evidence)")
        }
        assert {
            "family_key",
            "family_spec",
            "criterion",
            "specialization_parameter",
            "generic_lower",
            "generic_upper",
            "exact_generic_rank",
            "certificate_json",
        } <= columns
    finally:
        migrated.close()
