import sqlite3
from pathlib import Path

import pytest

from rank42.database_checks import pragma_rows_with_budget
from rank42.ui_pages.database_page import (
    _backup_browser_download_allowed,
    _deferred_backup_bytes,
    _restore_confirmed,
    _sql_is_readonly,
)


def test_database_sql_classifier_allows_queries_and_known_readonly_pragmas():
    assert _sql_is_readonly("SELECT 1")
    assert _sql_is_readonly("EXPLAIN SELECT 1")
    assert _sql_is_readonly("PRAGMA user_version")
    assert _sql_is_readonly("PRAGMA main.table_info(curves)")
    assert _sql_is_readonly("PRAGMA main.index_list(curves)")
    assert _sql_is_readonly("PRAGMA integrity_check")
    assert _sql_is_readonly("PRAGMA integrity_check(1)")
    assert _sql_is_readonly("PRAGMA foreign_key_check(curves)")


def test_database_sql_classifier_treats_pragma_assignments_as_writes():
    assert not _sql_is_readonly("PRAGMA user_version = 17")
    assert not _sql_is_readonly("PRAGMA journal_mode = WAL")
    assert not _sql_is_readonly("PRAGMA foreign_keys = OFF")
    assert not _sql_is_readonly("PRAGMA user_version(17)")
    assert not _sql_is_readonly("PRAGMA journal_mode(WAL)")
    assert not _sql_is_readonly("PRAGMA foreign_keys(OFF)")
    assert not _sql_is_readonly("PRAGMA application_id(1234)")
    assert not _sql_is_readonly("PRAGMA page_size(8192)")


def test_database_sql_classifier_treats_unknown_pragmas_conservatively():
    assert not _sql_is_readonly("PRAGMA writable_schema")
    assert not _sql_is_readonly("PRAGMA optimize")


def test_database_sql_classifier_rejects_multiple_statements():
    assert not _sql_is_readonly("SELECT 1; PRAGMA user_version = 17;")


def test_bounded_pragma_helper_rejects_write_capable_pragma():
    db = sqlite3.connect(":memory:")
    try:
        before = db.execute("PRAGMA user_version").fetchone()[0]
        with pytest.raises(
            ValueError,
            match="bounded database checks require a read-only PRAGMA",
        ):
            pragma_rows_with_budget(
                db,
                "PRAGMA user_version(17)",
                timeout_seconds=1,
            )
        after = db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()

    assert before == 0
    assert after == before


def test_bounded_pragma_helper_runs_safe_integrity_check():
    db = sqlite3.connect(":memory:")
    try:
        result = pragma_rows_with_budget(
            db,
            "PRAGMA integrity_check",
            timeout_seconds=1,
        )
    finally:
        db.close()

    assert result["completed"] is True
    assert result["error"] == ""
    assert result["rows"][0][0] == "ok"


def test_database_page_and_diagnostics_share_database_check_authority():
    root = Path(__file__).resolve().parents[1]
    database_source = (
        root / "rank42" / "ui_pages" / "database_page.py"
    ).read_text(encoding="utf-8")
    diagnostics_source = (
        root / "rank42" / "diagnostics.py"
    ).read_text(encoding="utf-8")

    assert "from rank42.database_checks import" in database_source
    assert "pragma_rows_with_budget" in database_source
    assert "sql_is_readonly" in database_source
    assert (
        "from rank42.database_checks import pragma_rows_with_budget"
        in diagnostics_source
    )
    assert "def pragma_rows_with_budget(" not in diagnostics_source


def test_database_page_uses_bounded_integrity_helper():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    assert "pragma_rows_with_budget(" in source
    assert 'db.execute("PRAGMA integrity_check")' not in source


def test_restore_confirmation_requires_exact_backup_filename():
    assert _restore_confirmed("rank-hunter-manual-20260925.db", "rank-hunter-manual-20260925.db")
    assert not _restore_confirmed("rank-hunter-manual-20260925.db", "")
    assert not _restore_confirmed("rank-hunter-manual-20260925.db", "rank-hunter-manual-20260925")
    assert not _restore_confirmed("rank-hunter-manual-20260925.db", "other.db")


def test_database_page_restore_requires_typed_confirmation():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    assert "Type the backup filename to confirm restore" in source
    assert "disabled=bool(active) or not confirmed" in source
    assert "backup_metadata(p)" in source



def test_database_backup_export_surface_remains_available():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    assert "list_backups(ctx.project_root)" in source
    assert '"Backup"' in source
    assert '"Download selected backup"' in source
    assert 'mime="application/octet-stream"' in source



def test_backup_browser_download_is_bounded():
    assert _backup_browser_download_allowed(0)
    assert _backup_browser_download_allowed(128 * 1024 * 1024)
    assert not _backup_browser_download_allowed(128 * 1024 * 1024 + 1)


def test_backup_bytes_are_loaded_only_by_deferred_callable(tmp_path):
    backup = tmp_path / "fixture.db"
    backup.write_bytes(b"rank-hunter-backup")
    loader = _deferred_backup_bytes(backup)

    assert callable(loader)
    assert loader() == b"rank-hunter-backup"


def test_database_backup_download_does_not_persist_payload_in_session_state():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    assert "database_backup_download" not in source
    assert ".read_bytes()" not in source
    assert "Local backup path:" in source
    assert "Browser download disabled for this large backup" in source
    assert "data=_deferred_backup_bytes(p)" in source



def test_database_restore_acquires_maintenance_before_restore():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    restore_button = source.index('"Restore selected backup"')
    acquire = source.index("with acquire_maintenance_lock(", restore_button)
    restore = source.index("restore_backup(", acquire)
    assert restore_button < acquire < restore
    assert 'reason=f"restore {p.name}"' in source


def test_database_write_sql_acquires_maintenance_before_backup_and_execution():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    function = source[source.index("def _database_advanced_sql"):]
    readonly_branch = function.index("if readonly:")
    readonly_return = function.index("        return", readonly_branch)
    acquire = function.index("with acquire_maintenance_lock(")
    safety = function.index('label="pre-sql"', acquire)
    execute = function.index("cur = db.execute(sql)", acquire)

    assert readonly_branch < readonly_return < acquire < safety < execute
    assert 'reason="Advanced SQL write"' in function


def test_database_page_surfaces_maintenance_state_and_external_process_warning():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    assert "_render_maintenance_state(ctx)" in source
    assert "Database maintenance mode is active in another Rank Hunter process/session" in source
    assert "Observed terminal-launched Rank Hunter work while entering maintenance" in source
    assert "These processes are not killed automatically" in source



def test_orphan_quartic_repair_is_explicit_and_maintenance_guarded():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    expander = source.index('with st.expander("Legacy repair", expanded=False):')
    button = source.index('"Repair orphan quartic references"', expander)
    acquire = source.index("with acquire_maintenance_lock(", button)
    repair = source.index("repair_orphan_quartic_curve_refs(db)", acquire)

    assert expander < button < acquire < repair
    assert 'reason="repair orphan quartic references"' in source
    assert "Normal database opening never performs this repair." in source
    assert 'st.subheader("Legacy repair")' not in source


def test_database_legacy_repair_is_collapsed_and_action_gated():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    start = source.index('with st.expander("Legacy repair", expanded=False):')
    end = source.index("\n\ndef _database_import_export", start)
    block = source[start:end]

    assert 'expanded=False' in block
    assert 'if st.button(' in block
    assert '"Repair orphan quartic references"' in block
    assert block.index('if st.button(') < block.index(
        "repair_orphan_quartic_curve_refs(db)"
    )


def test_full_database_verification_launch_belongs_to_database_page():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "database_page.py"
    ).read_text(encoding="utf-8")

    assert '"Queue Full Database Verification"' in source
    assert "enqueue_full_database_verification(" in source
    assert "database-maintenance Job" in source
    assert "Diagnostics only reads the persisted result" in source
