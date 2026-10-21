import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

from rank42.db import CURRENT_SCHEMA_VERSION, connect
from rank42.db_backup import backup_metadata, list_backups
from rank42.maintenance import maintenance_status
from rank42.migration_journal import latest_migration_journal
from rank42.manage_store import ensure_manage_schema
from rank42.migration_manager import (
    MIGRATION_COMPONENTS,
    migration_inventory,
    migration_plan,
    open_database_with_migrations,
    pending_component_ids,
    schema_entrypoint_inventory,
)
from rank42.ui_store import ensure_ui_schema


def _current_database(tmp_path):
    path = tmp_path / "rank42.db"
    db = open_database_with_migrations(path)
    return path, db


def test_migration_inventory_names_current_baseline_owners():
    inventory = migration_inventory()

    assert [row["id"] for row in inventory] == [
        "core",
        "ui",
        "manage",
        "pipeline",
        "auto_search",
        "prime_local",
        "torsion",
    ]
    assert [row["owner_entrypoint"] for row in inventory] == [
        "rank42.db.migrate_core_schema",
        "rank42.ui_store.ensure_ui_schema",
        "rank42.manage_store.ensure_manage_schema",
        "rank42.pipeline_state.ensure_pipeline_schema",
        "rank42.auto_search_state.ensure_auto_search_schema",
        "rank42.prime_schema.ensure_prime_schema",
        "rank42.torsion.ensure_torsion_schema",
    ]
    assert inventory[0]["target_user_version"] == CURRENT_SCHEMA_VERSION
    assert inventory[0]["target_kind"] == "exact_user_version"
    assert inventory[0]["idempotence_contract"] == (
        "repeat_safe_schema_mutator_system10_repair_separate"
    )
    assert all(
        row["idempotence_contract"] == "repeat_safe_schema_mutator"
        for row in inventory[1:]
    )
    assert all(row["target_user_version"] is None for row in inventory[1:])
    assert all(row["target_kind"] == "structural_manifest" for row in inventory[1:])
    boundaries = {
        row["id"]: row["transaction_boundary"]
        for row in inventory
    }
    assert boundaries == {
        "core": "manager_transaction",
        "ui": "manager_transaction",
        "manage": "manager_transaction",
        "pipeline": "manager_transaction",
        "auto_search": "manager_transaction",
        "prime_local": "manager_transaction",
        "torsion": "manager_transaction",
    }

    assert "core.v089_point_hard_flags" in inventory[0]["migration_ids"]
    assert "core.v091_covering_search_attempts" in inventory[0]["migration_ids"]
    assert "core.v092_conditional_mw_upper" in inventory[0]["migration_ids"]
    assert "ui.ensure_ui_schema" in inventory[1]["migration_ids"]
    assert "ui.analysis_cases" in inventory[1]["migration_ids"]
    assert "manage.ensure_manage_schema" in inventory[2]["migration_ids"]
    assert "manage.typed_campaign_notebook" in inventory[2]["migration_ids"]
    assert "pipeline.ensure_pipeline_schema" in inventory[3]["migration_ids"]
    assert "auto_search.ensure_auto_search_schema" in inventory[4]["migration_ids"]
    assert "prime_local.ensure_prime_schema" in inventory[5]["migration_ids"]
    assert "torsion.ensure_torsion_schema" in inventory[6]["migration_ids"]


def test_current_database_has_empty_migration_plan(tmp_path):
    _, db = _current_database(tmp_path)
    try:
        plan = migration_plan(db)
    finally:
        db.close()

    assert plan["supported"] is True
    assert plan["ready"] is True
    assert plan["order"] == [
        "core", "ui", "manage", "pipeline", "auto_search", "prime_local", "torsion"
    ]
    assert plan["pending"] == []
    assert plan["blocked"] == []


def test_open_database_migrates_legacy_campaign_notebook_schema(tmp_path):
    path, db = _current_database(tmp_path)
    db.execute("DROP TABLE research_campaign_note_links")
    db.execute(
        "ALTER TABLE research_campaign_notes DROP COLUMN handoff_state"
    )
    db.execute(
        "ALTER TABLE research_campaign_notes DROP COLUMN entry_type"
    )
    db.commit()
    db.close()

    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    try:
        plan = migration_plan(raw)
        assert "manage" in pending_component_ids(plan)
        assert "handoff_state" in plan["components"]["manage"][
            "missing_columns"
        ]["research_campaign_notes"]
    finally:
        raw.close()

    migrated = open_database_with_migrations(path)
    try:
        note_columns = {
            str(row["name"])
            for row in migrated.execute(
                "PRAGMA table_info(research_campaign_notes)"
            ).fetchall()
        }
        note_links = migrated.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='research_campaign_note_links'"""
        ).fetchone()
        assert {"entry_type", "handoff_state"}.issubset(note_columns)
        assert note_links is not None
        migrated.execute(
            """SELECT * FROM research_campaign_notes
               WHERE campaign_id=? AND handoff_state=?""",
            (1, "include"),
        ).fetchall()
        assert migration_plan(migrated)["ready"] is True
    finally:
        migrated.close()


def test_empty_database_has_ordered_pending_components(tmp_path):
    db = sqlite3.connect(tmp_path / "empty.db")
    db.row_factory = sqlite3.Row
    try:
        plan = migration_plan(db)
    finally:
        db.close()

    assert plan["supported"] is True
    assert plan["ready"] is False
    assert pending_component_ids(plan) == [
        "core",
        "ui",
        "manage",
        "pipeline",
        "auto_search",
        "prime_local",
        "torsion",
    ]

    pending = {item["id"]: item for item in plan["pending"]}
    assert pending["core"]["blocked_by"] == []
    assert pending["ui"]["blocked_by"] == ["core"]
    assert pending["manage"]["blocked_by"] == ["ui"]
    assert pending["pipeline"]["blocked_by"] == ["manage"]
    assert pending["auto_search"]["blocked_by"] == ["core"]
    assert pending["prime_local"]["blocked_by"] == ["core"]
    assert pending["torsion"]["blocked_by"] == ["core"]
    for item in pending.values():
        assert "missing" in item["reason"]


def test_migration_plan_is_read_only(tmp_path):
    _, db = _current_database(tmp_path)
    traced = []
    db.set_trace_callback(traced.append)
    try:
        plan = migration_plan(db)
    finally:
        db.set_trace_callback(None)
        db.close()

    assert plan["ready"] is True
    writes = [
        statement
        for statement in traced
        if statement.lstrip().upper().startswith(
            ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ", "DROP ")
        )
    ]
    assert writes == []


def test_future_core_schema_is_blocked_not_planned_for_downgrade(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
    raw.commit()
    try:
        plan = migration_plan(raw)
    finally:
        raw.close()

    assert plan["supported"] is False
    assert plan["ready"] is False
    assert plan["pending"] == []
    assert plan["blocked"][0]["id"] == "core"
    assert "downgrade is not planned" in plan["blocked"][0]["reason"]


def test_component_prerequisites_are_stable_and_acyclic():
    ids = [component.id for component in MIGRATION_COMPONENTS]
    seen = set()
    for component in MIGRATION_COMPONENTS:
        assert all(prerequisite in seen for prerequisite in component.prerequisites)
        seen.add(component.id)
    assert ids == [
        "core",
        "ui",
        "manage",
        "pipeline",
        "auto_search",
        "prime_local",
        "torsion",
    ]



def test_shell_startup_uses_manager_owned_schema_boundary():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "rank42" / "ui.py"
    ).read_text(encoding="utf-8")

    assert "db = open_database_with_migrations(dbp)" in source
    assert "db = connect(dbp)" not in source
    assert "ensure_ui_schema(db)" not in source
    assert "ensure_manage_schema(db)" not in source



def test_execution_boundary_runs_core_ui_manage_in_order(tmp_path, monkeypatch):
    from rank42 import migration_manager

    events = []

    class FakeDB:
        def __init__(self):
            self.in_transaction = False

        def execute(self, sql):
            if str(sql).strip().upper() == "BEGIN IMMEDIATE":
                self.in_transaction = True
            return self

        def commit(self):
            self.in_transaction = False

        def rollback(self):
            self.in_transaction = False

        def close(self):
            events.append("close")

    db = FakeDB()
    monkeypatch.setattr(
        migration_manager,
        "_open_core_connection",
        lambda path: events.append(("open", str(path))) or db,
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_core_component",
        lambda current, commit=True: events.append(
            ("core", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_ui_component",
        lambda current, commit=True: events.append(
            ("ui", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_manage_component",
        lambda current, commit=True: events.append(
            ("manage", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_pipeline_component",
        lambda current, commit=True: events.append(
            ("pipeline", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_auto_search_component",
        lambda current, commit=True: events.append(
            ("auto_search", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_prime_local_component",
        lambda current, commit=True: events.append(
            ("prime_local", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_torsion_component",
        lambda current, commit=True: events.append(
            ("torsion", current is db, commit)
        ),
    )
    monkeypatch.setattr(
        migration_manager,
        "migration_plan",
        lambda current: {
            "ready": True,
            "supported": True,
            "pending": [],
            "blocked": [],
        },
    )
    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        lambda current, manifest: {"ready": True},
    )

    path = tmp_path / "rank42.db"
    assert migration_manager.open_database_with_migrations(path) is db
    assert events == [
        ("open", str(path.resolve())),
        ("core", True, False),
        ("ui", True, False),
        ("manage", True, False),
        ("pipeline", True, False),
        ("auto_search", True, False),
        ("prime_local", True, False),
        ("torsion", True, False),
    ]


def test_execution_boundary_closes_database_when_later_owner_fails(tmp_path, monkeypatch):
    from rank42 import migration_manager

    events = []

    class FakeDB:
        def __init__(self):
            self.in_transaction = False

        def execute(self, sql):
            if str(sql).strip().upper() == "BEGIN IMMEDIATE":
                self.in_transaction = True
            return self

        def commit(self):
            self.in_transaction = False

        def rollback(self):
            self.in_transaction = False

        def close(self):
            events.append("close")

    db = FakeDB()
    monkeypatch.setattr(migration_manager, "_open_core_connection", lambda path: db)
    monkeypatch.setattr(
        migration_manager,
        "_ensure_core_component",
        lambda current, commit=True: events.append(("core", commit)),
    )
    monkeypatch.setattr(
        migration_manager,
        "_ensure_ui_component",
        lambda current, commit=True: events.append(("ui", commit)),
    )
    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        lambda current, manifest: {"ready": True},
    )

    def fail_manage(current, *, commit=True):
        events.append(("manage", commit))
        raise RuntimeError("manage migration failed")

    monkeypatch.setattr(
        migration_manager,
        "_ensure_manage_component",
        fail_manage,
    )

    import pytest

    with pytest.raises(RuntimeError, match="manage migration failed"):
        migration_manager.open_database_with_migrations(tmp_path / "rank42.db")

    assert events == [
        ("core", False),
        ("ui", False),
        ("manage", False),
        "close",
    ]



def test_execution_boundary_initializes_blank_database_to_current_target(tmp_path):
    from rank42.migration_manager import open_database_with_migrations

    path = tmp_path / "rank42.db"
    db = open_database_with_migrations(path)
    try:
        plan = migration_plan(db)
        assert plan["ready"] is True
        assert plan["pending"] == []
        assert int(db.execute("PRAGMA user_version").fetchone()[0]) == (
            CURRENT_SCHEMA_VERSION
        )
    finally:
        db.close()



def test_runtime_store_schema_mutators_are_confined_to_initialization_boundaries():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ui_source = (root / "rank42" / "ui_store.py").read_text(encoding="utf-8")
    manage_source = (root / "rank42" / "manage_store.py").read_text(encoding="utf-8")

    # UI keeps one explicit mutator definition and one explicit standalone
    # connection initializer. Ordinary getters/actions no longer invoke it.
    assert "def ensure_ui_schema(db, *, commit=True):" in ui_source
    assert "def _connect(db_path):" in ui_source
    assert "ensure_ui_schema(db)" in ui_source

    # Manage keeps its explicit mutator definition. Its internal ownership
    # backfill may require UI/Pipeline schema as part of migration execution,
    # but steady-state Manage actions do not call ensure_manage_schema().
    assert "def ensure_manage_schema(db, *, commit=True):" in manage_source


def test_dispatcher_startup_uses_migration_manager_boundary():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "rank42" / "dispatcher.py"
    ).read_text(encoding="utf-8")

    assert "open_database_with_migrations(db_path)" in source
    assert "db = _connect(db_path)" not in source



def test_ui_job_runner_uses_existing_database_without_schema_migration():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "rank42" / "ui_job_runner.py"
    ).read_text(encoding="utf-8")

    assert "db = connect_existing(db_path)" in source
    assert "ensure_ui_schema(db)" not in source
    assert "sqlite3.connect(db_path" not in source



def test_schema_evolution_helper_inventory_is_explicit():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    found = set()
    pattern = re.compile(r"^def\s+(ensure_[A-Za-z0-9_]*schema)\s*\(", re.MULTILINE)
    for path in sorted((root / "rank42").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            found.add((str(path.relative_to(root)), match.group(1)))

    assert found == {
        ("rank42/auto_search_state.py", "ensure_auto_search_schema"),
        ("rank42/campaign_lineage.py", "ensure_campaign_lineage_schema"),
        ("rank42/conductor_backfill.py", "ensure_schema"),
        ("rank42/curve_size_metrics.py", "ensure_curve_size_metrics_schema"),
        ("rank42/manage_store.py", "ensure_manage_schema"),
        ("rank42/pipeline_state.py", "ensure_pipeline_schema"),
        ("rank42/prime_schema.py", "ensure_prime_schema"),
        ("rank42/reverse_store.py", "ensure_reverse_schema"),
        ("rank42/torsion.py", "ensure_torsion_schema"),
        ("rank42/ui_store.py", "ensure_ui_schema"),
    }



def test_schema_entrypoint_inventory_classifies_startup_and_explicit_tools():
    inventory = {row["id"]: row for row in schema_entrypoint_inventory()}

    assert set(inventory) == {
        "auto_search",
        "campaign_lineage",
        "conductor_backfill",
        "curve_size_metrics",
        "manage",
        "pipeline",
        "prime_local",
        "reverse",
        "torsion",
        "ui",
    }
    assert inventory["campaign_lineage"]["classification"] == "shared_nested"
    assert inventory["campaign_lineage"]["startup_managed"] is True

    for component_id in (
        "auto_search",
        "manage",
        "pipeline",
        "prime_local",
        "torsion",
        "ui",
    ):
        assert inventory[component_id]["classification"] == "baseline_component"
        assert inventory[component_id]["startup_managed"] is True

    for component_id in ("conductor_backfill", "curve_size_metrics", "reverse"):
        assert inventory[component_id]["classification"] == "explicit_research_tool"
        assert inventory[component_id]["startup_managed"] is False


def test_explicit_research_tool_tables_are_not_created_by_baseline_startup(tmp_path):
    path, db = _current_database(tmp_path)
    try:
        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        db.close()

    assert "conductor_backfill_attempts" not in tables
    assert "curve_size_metrics" not in tables
    assert "reverse_engineering_reports" not in tables


def test_migration_plan_detects_legacy_prime_integer_type(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        DROP INDEX idx_curve_prime_data_curve;
        ALTER TABLE curve_prime_data RENAME TO curve_prime_data_text;
        CREATE TABLE curve_prime_data(
            curve_id INTEGER NOT NULL,
            p INTEGER NOT NULL,
            model_key TEXT NOT NULL,
            good_reduction INTEGER NOT NULL,
            ap INTEGER,
            cardinality INTEGER,
            normalized_ap REAL,
            discriminant_valuation INTEGER,
            conductor_valuation INTEGER,
            tamagawa_number INTEGER,
            kodaira_symbol TEXT,
            local_root_number INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY(curve_id,p)
        );
        CREATE INDEX idx_curve_prime_data_curve
            ON curve_prime_data(curve_id,p);
        DROP TABLE curve_prime_data_text;
        """
    )
    raw.commit()
    try:
        plan = migration_plan(raw)
    finally:
        raw.close()

    assert plan["ready"] is False
    pending = {item["id"]: item for item in plan["pending"]}
    assert "prime_local" in pending
    assert "column types" in pending["prime_local"]["reason"]
    mismatch = pending["prime_local"]["status"]["column_type_mismatches"]
    assert mismatch["curve_prime_data"]["p"] == {
        "actual": "INTEGER",
        "expected": "TEXT",
    }



def test_remaining_schema_evolution_helpers_are_explicitly_inventoryable():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    expected = {
        "rank42/auto_search_state.py": "def ensure_auto_search_schema(",
        "rank42/campaign_lineage.py": "def ensure_campaign_lineage_schema(",
        "rank42/conductor_backfill.py": "def ensure_schema(",
        "rank42/curve_size_metrics.py": "def ensure_curve_size_metrics_schema(",
        "rank42/manage_store.py": "def ensure_manage_schema(",
        "rank42/pipeline_state.py": "def ensure_pipeline_schema(",
        "rank42/prime_schema.py": "def ensure_prime_schema(",
        "rank42/reverse_store.py": "def ensure_reverse_schema(",
        "rank42/torsion.py": "def ensure_torsion_schema(",
        "rank42/ui_store.py": "def ensure_ui_schema(",
    }

    for relative, marker in expected.items():
        source = (root / relative).read_text(encoding="utf-8")
        assert marker in source



def test_migration_startup_does_not_require_sage_import(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "ui-startup.db"
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockSage(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "sage" or fullname.startswith("sage."):
                    raise ModuleNotFoundError("sage import blocked by UI regression test")
                return None

        sys.meta_path.insert(0, BlockSage())

        from rank42.migration_manager import open_database_with_migrations

        db = open_database_with_migrations(sys.argv[1])
        try:
            assert db.execute("PRAGMA user_version").fetchone()[0] > 0
        finally:
            db.close()
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", code, str(db_path)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "sage import blocked" not in result.stderr


def test_prime_local_keeps_schema_api_as_compatibility_reexport():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "prime_local.py"
    ).read_text(encoding="utf-8")

    assert (
        "from rank42.prime_schema import PRIME_SCHEMA, ensure_prime_schema, "
        "require_prime_schema"
    ) in source
    assert "def ensure_prime_schema(" not in source
    assert "ensure_prime_schema(db)" not in source
    assert "require_prime_schema(db)" in source



def test_torsion_runtime_schema_access_is_read_only():
    root = Path(__file__).resolve().parents[1]
    torsion_source = (root / "rank42" / "torsion.py").read_text(
        encoding="utf-8"
    )
    auto_source = (root / "rank42" / "auto_search.py").read_text(
        encoding="utf-8"
    )

    assert "def ensure_torsion_schema(db, *, commit=True):" in torsion_source
    assert "ensure_torsion_schema(db)" not in torsion_source
    assert "require_torsion_schema(db)" in torsion_source
    assert "ensure_torsion_schema(" not in auto_source
    assert "require_torsion_schema(db)" in auto_source


def test_fresh_database_does_not_create_pre_migration_backup(tmp_path):
    path = tmp_path / "rank42.db"
    db = open_database_with_migrations(path)
    db.close()

    assert list_backups(tmp_path) == []


def test_existing_empty_database_does_not_create_pre_migration_backup(tmp_path):
    path = tmp_path / "rank42.db"
    raw = sqlite3.connect(path)
    raw.close()

    db = open_database_with_migrations(path)
    db.close()

    assert list_backups(tmp_path) == []


def test_existing_older_database_gets_one_backup_before_migration(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    old_version = CURRENT_SCHEMA_VERSION - 1
    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={old_version}")
        raw.commit()
    finally:
        raw.close()

    migrated = open_database_with_migrations(path)
    try:
        assert int(migrated.execute("PRAGMA user_version").fetchone()[0]) == (
            CURRENT_SCHEMA_VERSION
        )
    finally:
        migrated.close()

    backups = list_backups(tmp_path)
    assert len(backups) == 1
    assert "pre-migration" in backups[0].name
    assert int(backup_metadata(backups[0])["user_version"]) == old_version

    backup_db = sqlite3.connect(backups[0])
    try:
        assert int(backup_db.execute("PRAGMA user_version").fetchone()[0]) == (
            old_version
        )
    finally:
        backup_db.close()

    # Reopening a now-current database must not make a second migration backup.
    reopened = open_database_with_migrations(path)
    reopened.close()
    assert list_backups(tmp_path) == backups


def test_pre_migration_backup_is_created_while_maintenance_is_active(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path, db = _current_database(tmp_path)
    db.close()

    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION - 1}")
        raw.commit()
    finally:
        raw.close()

    original = migration_manager._create_pre_migration_backup
    observed = []

    def guarded_backup(current, current_path):
        observed.append(maintenance_status(current_path)["active"])
        return original(current, current_path)

    monkeypatch.setattr(
        migration_manager,
        "_create_pre_migration_backup",
        guarded_backup,
    )

    migrated = open_database_with_migrations(path)
    migrated.close()

    assert observed == [True]
    assert maintenance_status(path)["active"] is False


def test_backup_failure_prevents_migration_mutation(tmp_path, monkeypatch):
    from rank42 import migration_manager

    path, db = _current_database(tmp_path)
    db.close()

    old_version = CURRENT_SCHEMA_VERSION - 1
    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={old_version}")
        raw.commit()
    finally:
        raw.close()

    def fail_backup(current, current_path):
        raise RuntimeError("backup failed")

    monkeypatch.setattr(
        migration_manager,
        "_create_pre_migration_backup",
        fail_backup,
    )

    import pytest

    with pytest.raises(RuntimeError, match="backup failed"):
        open_database_with_migrations(path)

    raw = sqlite3.connect(path)
    try:
        assert int(raw.execute("PRAGMA user_version").fetchone()[0]) == old_version
    finally:
        raw.close()

    assert maintenance_status(path)["active"] is False



def test_future_schema_startup_fails_without_backup_or_downgrade(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    future = CURRENT_SCHEMA_VERSION + 1
    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={future}")
        raw.commit()
    finally:
        raw.close()

    import pytest

    with pytest.raises(sqlite3.DatabaseError, match="newer than supported"):
        open_database_with_migrations(path)

    raw = sqlite3.connect(path)
    try:
        assert int(raw.execute("PRAGMA user_version").fetchone()[0]) == future
    finally:
        raw.close()
    assert list_backups(tmp_path) == []



def test_existing_database_migration_path_does_not_require_sage_import(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION - 1}")
        raw.commit()
    finally:
        raw.close()

    root = Path(__file__).resolve().parents[1]
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockSage(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "sage" or fullname.startswith("sage."):
                    raise ModuleNotFoundError("sage import blocked by UI regression test")
                return None

        sys.meta_path.insert(0, BlockSage())

        from rank42.migration_manager import open_database_with_migrations

        db = open_database_with_migrations(sys.argv[1])
        try:
            assert db.execute("PRAGMA user_version").fetchone()[0] > 0
        finally:
            db.close()
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "sage import blocked" not in result.stderr
    backups = list_backups(tmp_path)
    assert len(backups) == 1
    assert "pre-migration" in backups[0].name



def test_guarded_migration_writes_completed_durable_journal(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    old_version = CURRENT_SCHEMA_VERSION - 1
    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={old_version}")
        raw.commit()
    finally:
        raw.close()

    migrated = open_database_with_migrations(path)
    migrated.close()

    journal = latest_migration_journal(path)
    assert journal is not None
    assert journal["status"] == "completed"
    assert journal["target_user_version"] == CURRENT_SCHEMA_VERSION
    assert journal["transaction_model"] == "manager_global_schema_transaction"
    assert "baseline_schema_atomic" in journal["atomicity"]
    assert journal["pending_components"] == ["core"]
    assert journal["backup_path"]
    assert Path(journal["backup_path"]).exists()
    assert journal["recovery_message"].endswith(journal["backup_path"])

    components = journal["components"]
    assert [item["id"] for item in components] == [
        "core",
        "ui",
        "manage",
        "pipeline",
        "auto_search",
        "prime_local",
        "torsion",
    ]
    assert all(item["status"] == "completed" for item in components)
    assert {
        item["id"]: item["transaction_boundary"]
        for item in components
    } == {
        "core": "manager_transaction",
        "ui": "manager_transaction",
        "manage": "manager_transaction",
        "pipeline": "manager_transaction",
        "auto_search": "manager_transaction",
        "prime_local": "manager_transaction",
        "torsion": "manager_transaction",
    }
    assert journal["completed_at"]


def test_component_failure_is_durably_journaled_with_backup_recovery(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path, db = _current_database(tmp_path)
    db.close()

    old_version = CURRENT_SCHEMA_VERSION - 1
    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={old_version}")
        raw.commit()
    finally:
        raw.close()

    def fail_pipeline(current, *, commit=True):
        assert commit is False
        raise RuntimeError("pipeline migration exploded")

    monkeypatch.setattr(
        migration_manager,
        "_ensure_pipeline_component",
        fail_pipeline,
    )

    import pytest

    with pytest.raises(
        migration_manager.MigrationRunError,
        match="Restore the automatic pre-migration backup",
    ):
        open_database_with_migrations(path)

    journal = latest_migration_journal(path)
    assert journal is not None
    assert journal["status"] == "failed"
    assert journal["failed_component"] == "pipeline"
    assert "pipeline migration exploded" in journal["error"]
    assert journal["backup_path"]
    assert Path(journal["backup_path"]).exists()
    assert journal["backup_path"] in journal["recovery_message"]

    components = journal["components"]
    assert [item["id"] for item in components] == [
        "core",
        "ui",
        "manage",
        "pipeline",
    ]
    assert [item["status"] for item in components] == [
        "completed",
        "completed",
        "completed",
        "failed",
    ]

    # The baseline schema upgrade is one manager-owned transaction: a later
    # component failure rolls Core back to the pre-migration schema version.
    raw = sqlite3.connect(path)
    try:
        assert int(raw.execute("PRAGMA user_version").fetchone()[0]) == old_version
    finally:
        raw.close()
    assert maintenance_status(path)["active"] is False


def test_backup_failure_is_journaled_before_any_component_starts(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path, db = _current_database(tmp_path)
    db.close()

    old_version = CURRENT_SCHEMA_VERSION - 1
    raw = sqlite3.connect(path)
    try:
        raw.execute(f"PRAGMA user_version={old_version}")
        raw.commit()
    finally:
        raw.close()

    monkeypatch.setattr(
        migration_manager,
        "_create_pre_migration_backup",
        lambda current, current_path: (_ for _ in ()).throw(
            RuntimeError("backup unavailable")
        ),
    )

    import pytest

    with pytest.raises(
        migration_manager.MigrationRunError,
        match="before schema mutation began",
    ):
        open_database_with_migrations(path)

    journal = latest_migration_journal(path)
    assert journal is not None
    assert journal["status"] == "failed"
    assert journal["failed_component"] == "pre_migration_backup"
    assert journal["components"] == []
    assert journal["backup_path"] is None

    raw = sqlite3.connect(path)
    try:
        assert int(raw.execute("PRAGMA user_version").fetchone()[0]) == old_version
    finally:
        raw.close()



def test_baseline_schema_mutators_are_repeat_safe_on_current_database(tmp_path):
    from rank42.auto_search_state import ensure_auto_search_schema
    from rank42.pipeline_state import ensure_pipeline_schema
    from rank42.prime_schema import ensure_prime_schema
    from rank42.torsion import ensure_torsion_schema

    path, db = _current_database(tmp_path)
    try:
        assert ensure_ui_schema(db) is False
        assert ensure_manage_schema(db) is False
        assert ensure_pipeline_schema(db) is False
        assert ensure_auto_search_schema(db) is False
        assert ensure_prime_schema(db) is False
        assert ensure_torsion_schema(db) is False
        assert migration_plan(db)["ready"] is True
    finally:
        db.close()

    # Core schema reopening is repeat-safe on a healthy database. SYSTEM-10's
    # legacy orphan repair remains a separate scientific-state concern.
    reopened = connect(path)
    try:
        assert migration_plan(reopened)["ready"] is True
        assert int(reopened.execute("PRAGMA user_version").fetchone()[0]) == (
            CURRENT_SCHEMA_VERSION
        )
    finally:
        reopened.close()


def test_torsion_manager_transaction_rolls_back_on_verification_failure(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "core-only.db"
    db = connect(path)
    real_inspect = migration_manager.inspect_schema

    def fail_torsion_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "torsion":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_torsion_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="torsion migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "torsion")

        assert db.in_transaction is False
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(curves)").fetchall()
        }
        assert "torsion_order" not in columns
        assert "torsion_label" not in columns
        index = db.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='index' AND name='idx_curves_torsion_label'"""
        ).fetchone()
        assert index is None
    finally:
        db.close()



def test_transaction_boundary_classification_matches_current_mutator_shapes():
    root = Path(__file__).resolve().parents[1]

    core_source = (root / "rank42" / "db.py").read_text(encoding="utf-8")
    assert "def migrate_core_schema(db, *, commit=True):" in core_source
    assert "execute_static_schema_script(db, SCHEMA)" in core_source
    assert "db.executescript(" not in core_source

    ui_source = (
        root / "rank42" / "ui_store.py"
    ).read_text(encoding="utf-8")
    assert "def ensure_ui_schema(db, *, commit=True):" in ui_source
    assert "execute_static_schema_script(db, UI_SCHEMA)" in ui_source
    assert "db.executescript(" not in ui_source

    manage_source = (
        root / "rank42" / "manage_store.py"
    ).read_text(encoding="utf-8")
    assert "def ensure_manage_schema(db, *, commit=True):" in manage_source
    assert "execute_static_schema_script(db, MANAGE_SCHEMA)" in manage_source
    assert "def _ensure_campaign_ownership_links(db, *, commit=True):" in manage_source
    assert "db.executescript(" not in manage_source

    pipeline_source = (
        root / "rank42" / "pipeline_state.py"
    ).read_text(encoding="utf-8")
    assert "def ensure_pipeline_schema(db, *, commit=True):" in pipeline_source
    assert "execute_static_schema_script(db, PIPELINE_SCHEMA)" in pipeline_source
    assert "db.executescript(" not in pipeline_source

    auto_source = (
        root / "rank42" / "auto_search_state.py"
    ).read_text(encoding="utf-8")
    assert "def ensure_auto_search_schema(db, *, commit=True):" in auto_source
    assert "execute_static_schema_script(db, AUTO_SEARCH_SCHEMA)" in auto_source
    assert "db.executescript(" not in auto_source

    prime_source = (
        root / "rank42" / "prime_schema.py"
    ).read_text(encoding="utf-8")
    assert "def ensure_prime_schema(db, *, commit=True):" in prime_source
    assert "execute_static_schema_script(db, PRIME_SCHEMA)" in prime_source
    assert "db.executescript(" not in prime_source

    torsion_source = (
        root / "rank42" / "torsion.py"
    ).read_text(encoding="utf-8")
    assert "def ensure_torsion_schema(db, *, commit=True):" in torsion_source
    assert "if changed and commit:" in torsion_source

    boundaries = {
        component.id: component.transaction_boundary
        for component in MIGRATION_COMPONENTS
    }
    for component_id in (
        "core",
        "ui",
        "manage",
        "pipeline",
        "auto_search",
        "prime_local",
        "torsion",
    ):
        assert boundaries[component_id] == "manager_transaction"


def test_core_manager_transaction_rolls_back_on_verification_failure(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "core-rollback.db"
    db = migration_manager._open_core_connection(path)
    real_inspect = migration_manager.inspect_schema

    def fail_core_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "core":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_core_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="core migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "core")

        assert db.in_transaction is False
        assert int(db.execute("PRAGMA user_version").fetchone()[0]) == 0
        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "curves" not in tables
        assert "points" not in tables
    finally:
        db.close()



def test_auto_search_manager_transaction_rolls_back_on_verification_failure(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "core-only-auto.db"
    db = connect(path)
    real_inspect = migration_manager.inspect_schema

    def fail_auto_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "auto_search":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_auto_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="auto_search migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "auto_search")

        assert db.in_transaction is False
        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "auto_search_campaigns" not in tables
        assert "auto_search_trials" not in tables
        assert "auto_search_steps" not in tables
    finally:
        db.close()


def test_prime_manager_transaction_rolls_back_integer_to_text_rebuild(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "core-only-prime.db"
    db = connect(path)
    db.execute(
        """INSERT INTO curves(
               id,family,parameter,created_at,updated_at
           ) VALUES(1,'fixture','1','now','now')"""
    )
    db.execute(
        """CREATE TABLE curve_prime_data(
               curve_id INTEGER NOT NULL,
               p INTEGER NOT NULL,
               model_key TEXT NOT NULL,
               good_reduction INTEGER NOT NULL,
               ap INTEGER,
               cardinality INTEGER,
               normalized_ap REAL,
               discriminant_valuation INTEGER,
               conductor_valuation INTEGER,
               tamagawa_number INTEGER,
               kodaira_symbol TEXT,
               local_root_number INTEGER,
               computed_at TEXT NOT NULL,
               PRIMARY KEY(curve_id,p)
           )"""
    )
    db.execute(
        """INSERT INTO curve_prime_data(
               curve_id,p,model_key,good_reduction,ap,cardinality,
               normalized_ap,computed_at
           ) VALUES(1,5,'legacy',1,-2,8,-0.5,'now')"""
    )
    db.commit()

    real_inspect = migration_manager.inspect_schema

    def fail_prime_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "prime_local":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_prime_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="prime_local migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "prime_local")

        assert db.in_transaction is False
        p_type = next(
            str(row["type"]).upper()
            for row in db.execute("PRAGMA table_info(curve_prime_data)")
            if row["name"] == "p"
        )
        assert p_type == "INTEGER"
        row = db.execute(
            "SELECT p,model_key FROM curve_prime_data WHERE curve_id=1"
        ).fetchone()
        assert int(row["p"]) == 5
        assert row["model_key"] == "legacy"

        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "curve_prime_cache_state" not in tables
        assert "quartic_local_tests" not in tables
    finally:
        db.close()



def test_full_manager_commits_prime_integer_to_text_rebuild(tmp_path):
    path, db = _current_database(tmp_path)
    db.close()

    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP INDEX idx_curve_prime_data_curve")
        raw.execute("ALTER TABLE curve_prime_data RENAME TO curve_prime_data_text")
        raw.execute(
            """CREATE TABLE curve_prime_data(
                   curve_id INTEGER NOT NULL,
                   p INTEGER NOT NULL,
                   model_key TEXT NOT NULL,
                   good_reduction INTEGER NOT NULL,
                   ap INTEGER,
                   cardinality INTEGER,
                   normalized_ap REAL,
                   discriminant_valuation INTEGER,
                   conductor_valuation INTEGER,
                   tamagawa_number INTEGER,
                   kodaira_symbol TEXT,
                   local_root_number INTEGER,
                   computed_at TEXT NOT NULL,
                   PRIMARY KEY(curve_id,p)
               )"""
        )
        raw.execute(
            """INSERT INTO curve_prime_data(
                   curve_id,p,model_key,good_reduction,ap,cardinality,
                   normalized_ap,computed_at
               )
               SELECT curve_id,CAST(p AS INTEGER),model_key,good_reduction,
                      ap,cardinality,normalized_ap,computed_at
               FROM curve_prime_data_text"""
        )
        raw.execute(
            "CREATE INDEX idx_curve_prime_data_curve "
            "ON curve_prime_data(curve_id,p)"
        )
        raw.execute("DROP TABLE curve_prime_data_text")
        raw.commit()
    finally:
        raw.close()

    migrated = open_database_with_migrations(path)
    try:
        p_type = next(
            str(row["type"]).upper()
            for row in migrated.execute("PRAGMA table_info(curve_prime_data)")
            if row["name"] == "p"
        )
        assert p_type == "TEXT"
        assert migration_plan(migrated)["ready"] is True
    finally:
        migrated.close()

    journal = latest_migration_journal(path)
    assert journal is not None
    prime = next(
        item for item in journal["components"]
        if item["id"] == "prime_local"
    )
    assert prime["status"] == "completed"
    assert prime["transaction_boundary"] == "manager_transaction"



def test_pipeline_manager_transaction_rolls_back_parent_lineage_and_schema(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "core-only-pipeline.db"
    db = connect(path)
    real_inspect = migration_manager.inspect_schema

    def fail_pipeline_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "pipeline":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_pipeline_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="pipeline migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "pipeline")

        assert db.in_transaction is False
        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "research_campaigns" not in tables
        assert "research_campaign_artifacts" not in tables
        assert "search_pipeline_definitions" not in tables
        assert "search_pipeline_runs" not in tables
        assert "search_pipeline_candidates" not in tables
        assert "search_pipeline_derivations" not in tables
    finally:
        db.close()


def test_campaign_lineage_schema_can_participate_in_outer_transaction(tmp_path):
    from rank42.campaign_lineage import ensure_campaign_lineage_schema
    from rank42.schema_sql import execute_static_schema_script
    from rank42.campaign_schema import RESEARCH_CAMPAIGN_SCHEMA

    path = tmp_path / "lineage.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        execute_static_schema_script(db, RESEARCH_CAMPAIGN_SCHEMA)
        assert ensure_campaign_lineage_schema(db, commit=False) is True
        assert db.in_transaction is True

        db.rollback()
        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "research_campaigns" not in tables
        assert "research_campaign_artifacts" not in tables
    finally:
        db.close()



def test_ui_manager_transaction_rolls_back_schema_queue_and_lineage(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "core-only-ui.db"
    db = connect(path)
    real_inspect = migration_manager.inspect_schema

    def fail_ui_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "ui":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_ui_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="ui migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "ui")

        assert db.in_transaction is False
        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "research_campaigns" not in tables
        assert "research_campaign_artifacts" not in tables
        assert "ui_jobs" not in tables
        assert "ui_job_queue" not in tables
        assert "ui_settings" not in tables
        assert "ui_service_heartbeats" not in tables
        assert "ui_application_state" not in tables
    finally:
        db.close()


def test_manage_manager_transaction_rolls_back_ownership_and_lineage_backfill(
    tmp_path,
    monkeypatch,
):
    from rank42 import migration_manager

    path = tmp_path / "ui-ready-manage.db"
    db = connect(path)
    ensure_ui_schema(db)

    created = "2026-09-25T12:00:00+00:00"
    job_created = "2026-09-25T12:01:00+00:00"
    db.execute(
        """INSERT INTO research_campaigns(
               id,name,objective,status,notes,created_at,updated_at
           ) VALUES(1,'fixture','','active','',?,?)""",
        (created, created),
    )
    db.execute(
        """INSERT INTO ui_jobs(
               id,kind,label,command_json,cwd,log_path,status,metadata_json,
               campaign_id,created_at,updated_at
           ) VALUES(1,'test','legacy','[]','/tmp','/tmp/x.log','queued',?,
                    NULL,?,?)""",
        ('{"campaign_id":1}', job_created, job_created),
    )
    db.commit()

    real_inspect = migration_manager.inspect_schema

    def fail_manage_verification(current, manifest):
        status = real_inspect(current, manifest)
        if manifest.name == "manage":
            status = dict(status)
            status["ready"] = False
        return status

    monkeypatch.setattr(
        migration_manager,
        "inspect_schema",
        fail_manage_verification,
    )

    import pytest

    try:
        with pytest.raises(
            RuntimeError,
            match="manage migration did not reach its schema target",
        ):
            migration_manager._run_component_mutator(db, "manage")

        assert db.in_transaction is False
        job = db.execute(
            "SELECT campaign_id FROM ui_jobs WHERE id=1"
        ).fetchone()
        assert job["campaign_id"] is None

        lineage = db.execute(
            """SELECT COUNT(*) FROM research_campaign_artifacts
               WHERE artifact_kind='job' AND artifact_id=1
                 AND relation='owned'"""
        ).fetchone()[0]
        assert int(lineage) == 0

        tables = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "research_campaign_notes" not in tables
        assert "ui_job_schedules" not in tables
        assert "ui_job_schedule_occurrences" not in tables
    finally:
        db.close()
