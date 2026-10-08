"""Database migration inventory, planning, and orchestration boundary.

Planning remains read-only. Runtime schema initialization is orchestrated here
for the baseline Rank Hunter application schema while component mutators stay
available as compatibility implementations. Existing nonempty databases that
require supported migration are protected by the system maintenance lease and
one automatic pre-migration backup. Explicit research tool/report schemas
remain opt-in. Baseline schema upgrades are durably journaled and execute inside
one manager-owned SQLite transaction so component helpers cannot commit partial
schema state independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Mapping

from rank42.schema_manifest import (
    AUTO_SEARCH_SCHEMA_MANIFEST,
    CORE_SCHEMA_MANIFEST,
    MANAGE_SCHEMA_MANIFEST,
    PIPELINE_SCHEMA_MANIFEST,
    PRIME_LOCAL_SCHEMA_MANIFEST,
    TORSION_SCHEMA_MANIFEST,
    UI_SCHEMA_MANIFEST,
    SchemaManifest,
    inspect_schema,
    schema_issue_summary,
)


@dataclass(frozen=True)
class MigrationComponent:
    """One application schema owner in dependency order."""

    id: str
    label: str
    manifest: SchemaManifest
    owner_entrypoint: str
    migration_ids: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()
    transaction_boundary: str = "legacy_internal_commits"
    idempotence_contract: str = "repeat_safe_schema_mutator"


MIGRATION_COMPONENTS = (
    MigrationComponent(
        id="core",
        label="Core / scientific schema",
        manifest=CORE_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.db.migrate_core_schema",
        migration_ids=(
            "core.base_schema",
            "core.external_catalog_state",
            "core.v080_points",
            "core.v086_provenance",
            "core.v087_family_charts",
            "core.v088_rank_evidence",
            "core.v089_point_hard_flags",
            "core.v091_covering_search_attempts",
            "core.v092_conditional_mw_upper",
        ),
        transaction_boundary="manager_transaction",
        idempotence_contract=(
            "repeat_safe_schema_mutator_system10_repair_separate"
        ),
    ),
    MigrationComponent(
        id="ui",
        label="UI / Jobs schema",
        manifest=UI_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.ui_store.ensure_ui_schema",
        migration_ids=(
            "ui.ensure_ui_schema",
            "ui.analysis_cases",
            "ui.campaign_lineage",
        ),
        prerequisites=("core",),
        transaction_boundary="manager_transaction",
    ),
    MigrationComponent(
        id="manage",
        label="Manage schema",
        manifest=MANAGE_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.manage_store.ensure_manage_schema",
        migration_ids=(
            "manage.ensure_manage_schema",
            "manage.typed_campaign_notebook",
            "manage.campaign_ownership_links",
            "manage.campaign_lineage",
        ),
        prerequisites=("ui",),
        transaction_boundary="manager_transaction",
    ),
    MigrationComponent(
        id="pipeline",
        label="Pipeline schema",
        manifest=PIPELINE_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.pipeline_state.ensure_pipeline_schema",
        migration_ids=("pipeline.ensure_pipeline_schema",),
        prerequisites=("manage",),
        transaction_boundary="manager_transaction",
    ),
    MigrationComponent(
        id="auto_search",
        label="Auto Search schema",
        manifest=AUTO_SEARCH_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.auto_search_state.ensure_auto_search_schema",
        migration_ids=("auto_search.ensure_auto_search_schema",),
        prerequisites=("core",),
        transaction_boundary="manager_transaction",
    ),
    MigrationComponent(
        id="prime_local",
        label="Prime / local cache schema",
        manifest=PRIME_LOCAL_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.prime_schema.ensure_prime_schema",
        migration_ids=("prime_local.ensure_prime_schema",),
        prerequisites=("core",),
        transaction_boundary="manager_transaction",
    ),
    MigrationComponent(
        id="torsion",
        label="Torsion schema",
        manifest=TORSION_SCHEMA_MANIFEST,
        owner_entrypoint="rank42.torsion.ensure_torsion_schema",
        migration_ids=("torsion.ensure_torsion_schema",),
        prerequisites=("core",),
        transaction_boundary="manager_transaction",
    ),
)


_COMPONENT_BY_ID = {component.id: component for component in MIGRATION_COMPONENTS}


@dataclass(frozen=True)
class SchemaEntrypoint:
    id: str
    owner_entrypoint: str
    classification: str
    startup_managed: bool
    note: str


SCHEMA_ENTRYPOINTS = (
    SchemaEntrypoint(
        "auto_search",
        "rank42.auto_search_state.ensure_auto_search_schema",
        "baseline_component",
        True,
        "durable Auto Search state used by a first-class shell surface",
    ),
    SchemaEntrypoint(
        "campaign_lineage",
        "rank42.campaign_lineage.ensure_campaign_lineage_schema",
        "shared_nested",
        True,
        "shared Campaign lineage owned transitively by UI/Manage/Pipeline",
    ),
    SchemaEntrypoint(
        "conductor_backfill",
        "rank42.conductor_backfill.ensure_schema",
        "explicit_research_tool",
        False,
        "attempt log created only by the explicit conductor backfill workflow",
    ),
    SchemaEntrypoint(
        "curve_size_metrics",
        "rank42.curve_size_metrics.ensure_curve_size_metrics_schema",
        "explicit_research_tool",
        False,
        "derived size-metric table created by an explicit backfill workflow",
    ),
    SchemaEntrypoint(
        "manage",
        "rank42.manage_store.ensure_manage_schema",
        "baseline_component",
        True,
        "Campaign and schedule state",
    ),
    SchemaEntrypoint(
        "pipeline",
        "rank42.pipeline_state.ensure_pipeline_schema",
        "baseline_component",
        True,
        "durable Pipeline definitions/runs used by a first-class shell surface",
    ),
    SchemaEntrypoint(
        "prime_local",
        "rank42.prime_schema.ensure_prime_schema",
        "baseline_component",
        True,
        "shared exact prime/local cache used by normal scientific workflows",
    ),
    SchemaEntrypoint(
        "reverse",
        "rank42.reverse_store.ensure_reverse_schema",
        "explicit_research_tool",
        False,
        "reverse-engineering report store created only by the explicit workflow",
    ),
    SchemaEntrypoint(
        "torsion",
        "rank42.torsion.ensure_torsion_schema",
        "baseline_component",
        True,
        "exact torsion state persisted on the authoritative curves table",
    ),
    SchemaEntrypoint(
        "ui",
        "rank42.ui_store.ensure_ui_schema",
        "baseline_component",
        True,
        "Jobs/settings/queue state",
    ),
)


def schema_entrypoint_inventory() -> list[dict[str, object]]:
    return [
        {
            "id": entry.id,
            "owner_entrypoint": entry.owner_entrypoint,
            "classification": entry.classification,
            "startup_managed": entry.startup_managed,
            "note": entry.note,
        }
        for entry in SCHEMA_ENTRYPOINTS
    ]


def migration_component(component_id: str) -> MigrationComponent:
    try:
        return _COMPONENT_BY_ID[str(component_id)]
    except KeyError as exc:
        raise KeyError(f"unknown migration component {component_id!r}") from exc


def migration_inventory() -> list[dict[str, object]]:
    """Return stable metadata for the currently fragmented mutation owners."""
    rows = []
    for component in MIGRATION_COMPONENTS:
        manifest = component.manifest
        rows.append({
            "id": component.id,
            "label": component.label,
            "owner_entrypoint": component.owner_entrypoint,
            "prerequisites": list(component.prerequisites),
            "migration_ids": list(component.migration_ids),
            "transaction_boundary": component.transaction_boundary,
            "idempotence_contract": component.idempotence_contract,
            "target_user_version": manifest.expected_user_version,
            "versioned": manifest.expected_user_version is not None,
            "target_kind": (
                "exact_user_version"
                if manifest.expected_user_version is not None
                else "structural_manifest"
            ),
        })
    return rows


def migration_plan(db) -> dict[str, object]:
    """Describe required schema work without mutating the database.

    The plan is intentionally component-grained for now. Existing legacy
    helper functions do not expose enough idempotent predicates to claim which
    individual internal migration id is required without executing them.
    migration_ids therefore inventories the mutations owned by the pending
    component; it is not a claim that every listed internal step must run.
    """
    statuses: dict[str, dict[str, object]] = {}
    pending: list[dict[str, object]] = []

    for component in MIGRATION_COMPONENTS:
        status = inspect_schema(db, component.manifest)
        statuses[component.id] = status

    core_status = statuses["core"]
    expected_core = core_status.get("expected_user_version")
    actual_core = int(core_status.get("actual_user_version") or 0)
    future_core = (
        expected_core is not None
        and actual_core > int(expected_core)
    )

    if future_core:
        return {
            "supported": False,
            "ready": False,
            "order": [component.id for component in MIGRATION_COMPONENTS],
            "inventory": migration_inventory(),
            "components": statuses,
            "pending": [],
            "blocked": [{
                "id": "core",
                "reason": (
                    f"core user_version {actual_core} is newer than supported "
                    f"target {expected_core}; downgrade is not planned"
                ),
            }],
        }

    for component in MIGRATION_COMPONENTS:
        status = statuses[component.id]
        if status["ready"]:
            continue

        blocked_by = [
            prerequisite
            for prerequisite in component.prerequisites
            if not statuses[prerequisite]["ready"]
        ]
        pending.append({
            "id": component.id,
            "label": component.label,
            "owner_entrypoint": component.owner_entrypoint,
            "prerequisites": list(component.prerequisites),
            "blocked_by": blocked_by,
            "migration_ids": list(component.migration_ids),
            "reason": schema_issue_summary(status) or "schema is not ready",
            "status": status,
        })

    return {
        "supported": True,
        "ready": not pending,
        "order": [component.id for component in MIGRATION_COMPONENTS],
        "inventory": migration_inventory(),
        "components": statuses,
        "pending": pending,
        "blocked": [],
    }


def pending_component_ids(plan: Mapping[str, object]) -> list[str]:
    return [
        str(item["id"])
        for item in list(plan.get("pending") or [])
    ]



def _open_core_connection(path):
    from rank42.db import _open_connection

    return _open_connection(path)


def _open_existing_readonly(path):
    resolved = Path(path).resolve()
    db = sqlite3.connect(
        resolved.as_uri() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    db.row_factory = sqlite3.Row
    return db


def _database_has_existing_state(db) -> bool:
    """Distinguish an existing research DB from a fresh/empty SQLite file."""
    version = int(db.execute("PRAGMA user_version").fetchone()[0] or 0)
    if version > 0:
        return True
    row = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name NOT LIKE 'sqlite_%'
           LIMIT 1"""
    ).fetchone()
    return row is not None


def _unsupported_plan_error(plan):
    blocked = list(plan.get("blocked") or [])
    reason = "; ".join(
        str(item.get("reason") or item)
        for item in blocked
    )
    return sqlite3.DatabaseError(
        reason or "Rank Hunter database schema is not supported by this core"
    )


def _create_pre_migration_backup(db, path):
    from rank42.db_backup import create_backup

    return create_backup(
        db,
        project_root=Path(path).resolve().parent,
        label="pre-migration",
    )


def _ensure_core_component(db, *, commit=True):
    from rank42.db import migrate_core_schema

    return migrate_core_schema(db, commit=commit)


def _ensure_ui_component(db, *, commit=True):
    from rank42.ui_store import ensure_ui_schema

    return ensure_ui_schema(db, commit=commit)


def _ensure_manage_component(db, *, commit=True):
    from rank42.manage_store import ensure_manage_schema

    return ensure_manage_schema(db, commit=commit)


def _ensure_pipeline_component(db, *, commit=True):
    from rank42.pipeline_state import ensure_pipeline_schema

    return ensure_pipeline_schema(db, commit=commit)


def _ensure_auto_search_component(db, *, commit=True):
    from rank42.auto_search_state import ensure_auto_search_schema

    return ensure_auto_search_schema(db, commit=commit)


def _ensure_prime_local_component(db, *, commit=True):
    from rank42.prime_schema import ensure_prime_schema

    return ensure_prime_schema(db, commit=commit)


def _ensure_torsion_component(db, *, commit=True):
    from rank42.torsion import ensure_torsion_schema

    return ensure_torsion_schema(db, commit=commit)


class MigrationRunError(RuntimeError):
    """Guarded migration failed after its durable journal was created."""


def _component_runner(component_id):
    runners = {
        "core": _ensure_core_component,
        "ui": _ensure_ui_component,
        "manage": _ensure_manage_component,
        "pipeline": _ensure_pipeline_component,
        "auto_search": _ensure_auto_search_component,
        "prime_local": _ensure_prime_local_component,
        "torsion": _ensure_torsion_component,
    }
    return runners[component_id]


def _migration_recovery_error(journal, component_id, exc):
    backup_path = journal.payload.get("backup_path")
    parts = [
        f"Database migration failed in {component_id}: {exc}.",
        f"Migration journal: {journal.path}.",
    ]
    if backup_path:
        parts.append(
            "Restore the automatic pre-migration backup if recovery is needed: "
            f"{backup_path}."
        )
    else:
        parts.append(
            "No migration component was started after a successful automatic "
            "pre-migration backup."
        )
    return MigrationRunError(" ".join(parts))


def _journal_component_start(journal, component_id):
    if journal is None:
        return
    component = migration_component(component_id)
    journal.component_started(
        component_id,
        migration_ids=component.migration_ids,
        transaction_boundary=component.transaction_boundary,
    )


def _journal_component_complete(journal, component_id):
    if journal is not None:
        journal.component_completed(component_id)


def _journal_component_failure(journal, component_id, exc):
    if journal is not None:
        journal.component_failed(component_id, exc)


def _component_schema_error(component_id, status):
    reason = schema_issue_summary(status) or "schema is not ready"
    return RuntimeError(
        f"{component_id} migration did not reach its schema target: {reason}"
    )


def _run_component_in_active_transaction(db, component_id):
    component = migration_component(component_id)
    if component.transaction_boundary != "manager_transaction":
        raise RuntimeError(
            f"{component_id} is not configured for manager-owned transactions"
        )
    if not db.in_transaction:
        raise RuntimeError(
            f"{component_id} requires an active manager-owned transaction"
        )

    changed = _component_runner(component_id)(db, commit=False)
    status = inspect_schema(db, component.manifest)
    if not status["ready"]:
        raise _component_schema_error(component_id, status)
    return changed


def _run_component_mutator(db, component_id):
    component = migration_component(component_id)
    if component.transaction_boundary == "manager_transaction":
        if db.in_transaction:
            raise RuntimeError(
                f"{component_id} cannot start manager transaction while "
                "another SQLite transaction is active"
            )
        if component_id == "core":
            db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        try:
            changed = _run_component_in_active_transaction(db, component_id)
            db.commit()
            return changed
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise

    changed = _component_runner(component_id)(db)
    status = inspect_schema(db, component.manifest)
    if not status["ready"]:
        raise _component_schema_error(component_id, status)
    return changed


def _open_and_migrate_baseline(path, *, journal=None):
    db = None
    current_component = "core"
    try:
        db = _open_core_connection(path)
        # journal_mode preparation must happen before the schema transaction.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")

        for component_id in (
            "core",
            "ui",
            "manage",
            "pipeline",
            "auto_search",
            "prime_local",
            "torsion",
        ):
            current_component = component_id
            _journal_component_start(journal, component_id)
            try:
                _run_component_in_active_transaction(db, component_id)
            except BaseException as exc:
                _journal_component_failure(journal, component_id, exc)
                if journal is not None:
                    raise _migration_recovery_error(
                        journal, component_id, exc
                    ) from exc
                raise
            _journal_component_complete(journal, component_id)

        current_component = "verification"
        plan = migration_plan(db)
        if not plan["ready"]:
            reasons = "; ".join(
                f"{item['id']}: {item['reason']}"
                for item in list(plan.get("pending") or [])
            )
            if not plan["supported"]:
                reasons = "; ".join(
                    str(item.get("reason") or item)
                    for item in list(plan.get("blocked") or [])
                )
            exc = RuntimeError(
                "Rank Hunter schema initialization did not reach the "
                f"current target: {reasons or 'unknown schema mismatch'}"
            )
            if journal is not None:
                journal.failed(exc, component_id=current_component)
                raise _migration_recovery_error(
                    journal, current_component, exc
                ) from exc
            raise exc

        current_component = "commit"
        try:
            db.commit()
        except BaseException as exc:
            if journal is not None:
                journal.failed(exc, component_id=current_component)
                raise _migration_recovery_error(
                    journal, current_component, exc
                ) from exc
            raise

        if journal is not None:
            journal.completed()
        return db
    except BaseException:
        if db is not None:
            if db.in_transaction:
                db.rollback()
            db.close()
        raise

def open_database_with_migrations(path):
    """Open the runtime database through the guarded migration boundary.

    Fresh databases initialize directly. Existing nonempty databases are
    inspected read-only first. When supported schema work is required, Rank
    Hunter acquires the system maintenance lease, re-checks the plan, creates
    one pre-migration backup, creates a durable component journal, runs the
    existing ordered mutators, and verifies the final manifest state.

    All baseline component helpers run with commit=False inside one
    manager-owned SQLite transaction. The durable journal and automatic backup
    remain the recovery record around that atomic schema change.
    """
    resolved = Path(path).resolve()

    # A brand-new database has no research state to preserve yet.
    if not resolved.exists():
        return _open_and_migrate_baseline(resolved)

    preflight = _open_existing_readonly(resolved)
    try:
        plan = migration_plan(preflight)
        if not plan["supported"]:
            raise _unsupported_plan_error(plan)

        # Current databases should not pay the maintenance/backup/journal cost.
        if plan["ready"]:
            preflight.close()
            preflight = None
            return _open_core_connection(resolved)

        # A pre-existing empty SQLite file is equivalent to first-run setup.
        if not _database_has_existing_state(preflight):
            preflight.close()
            preflight = None
            return _open_and_migrate_baseline(resolved)

        from rank42.maintenance import acquire_maintenance_lock
        from rank42.migration_journal import MigrationJournal

        with acquire_maintenance_lock(
            resolved,
            db=preflight,
            project_root=resolved.parent,
            reason="database schema migration",
        ):
            # The maintenance lease closes the cooperative launch/claim race.
            # Re-read the plan under exclusive ownership before journaling.
            plan = migration_plan(preflight)
            if not plan["supported"]:
                raise _unsupported_plan_error(plan)
            if plan["ready"]:
                preflight.close()
                preflight = None
                return _open_core_connection(resolved)

            journal = MigrationJournal.start(
                resolved,
                pending_components=pending_component_ids(plan),
                target_user_version=int(
                    CORE_SCHEMA_MANIFEST.expected_user_version
                ),
            )
            try:
                backup_path = _create_pre_migration_backup(
                    preflight, resolved
                )
            except BaseException as exc:
                journal.failed(
                    exc,
                    component_id="pre_migration_backup",
                )
                raise MigrationRunError(
                    "Automatic pre-migration backup failed before schema "
                    f"mutation began: {exc}. Migration journal: {journal.path}."
                ) from exc

            journal.set_backup(backup_path)
            preflight.close()
            preflight = None
            return _open_and_migrate_baseline(
                resolved,
                journal=journal,
            )
    finally:
        if preflight is not None:
            preflight.close()
