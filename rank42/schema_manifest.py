"""Read-only schema readiness contracts for Rank Hunter.

This module describes the schema that the current core expects to *observe*.
It never creates, alters, migrates, repairs, or backfills database state.

Migration execution remains owned by the existing schema owners until the
System Migration Manager work is implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class SchemaManifest:
    name: str
    expected_user_version: int | None
    required_tables: Mapping[str, frozenset[str]]
    required_indexes: frozenset[str]
    required_column_types: Mapping[str, Mapping[str, str]]


def _manifest(
    name: str,
    *,
    expected_user_version: int | None = None,
    tables: Mapping[str, set[str] | frozenset[str] | tuple[str, ...]],
    indexes: set[str] | frozenset[str] | tuple[str, ...],
    column_types: Mapping[str, Mapping[str, str]] | None = None,
) -> SchemaManifest:
    normalized = {
        str(table): frozenset(str(column) for column in columns)
        for table, columns in tables.items()
    }
    return SchemaManifest(
        name=str(name),
        expected_user_version=(
            None if expected_user_version is None else int(expected_user_version)
        ),
        required_tables=MappingProxyType(normalized),
        required_indexes=frozenset(str(index) for index in indexes),
        required_column_types=MappingProxyType({
            str(table): MappingProxyType({
                str(column): str(type_name).strip().upper()
                for column, type_name in columns.items()
            })
            for table, columns in dict(column_types or {}).items()
        }),
    )


CORE_SCHEMA_MANIFEST = _manifest(
    "core",
    expected_user_version=813,
    tables={
        "curves": {
            "id", "family", "parameter", "score", "a_invariants_json",
            "conductor", "discriminant", "bad_primes_json", "root_number",
            "generic_lower", "quick_upper", "descent_lower", "descent_upper",
            "exact_rank", "certain", "regulator", "generators_json",
            "plugin_id", "plugin_version", "family_spec", "family_sha256",
            "adapter_sha256", "plugin_manifest_sha256", "native_fiber_id",
            "native_family_key", "native_family_spec", "native_parameter",
            "chart_id", "chart_parameter", "chart_map_fingerprint", "status",
            "error", "created_at", "updated_at",
        },
        "events": {"id", "curve_id", "level", "message", "created_at"},
        "rank_evidence": {
            "id", "curve_id", "evidence_key", "engine", "engine_version",
            "sage_version", "evidence_type", "rigorous", "rigorous_lower",
            "rigorous_upper", "exact_rank", "conditional_analytic_upper",
            "conditional_mw_upper", "numerical_rank_signal", "assumptions_json", "status", "timed_out",
            "partial", "model_a_invariants_json",
            "minimal_model_a_invariants_json", "options_json", "points_json",
            "stdout_summary", "stderr_summary", "started_at", "finished_at",
            "elapsed_seconds", "created_at", "updated_at",
        },
        "family_evidence": {
            "id", "evidence_key", "family_key", "family_spec", "family_sha256",
            "plugin_id", "plugin_version", "criterion",
            "specialization_parameter", "specialized_curve_id",
            "generic_lower", "generic_upper", "exact_generic_rank",
            "certificate_json", "status", "created_at", "updated_at",
        },
        "quartic_searches": {
            "id", "search_key", "curve_id", "family", "parameter", "hole_label",
            "polynomial_json", "integer_polynomial_json", "y_scale", "degree",
            "height_bound", "denominator_low", "denominator_high", "options_json",
            "metadata_json", "status", "ratpoints_executable", "point_count",
            "runtime", "error", "started_at", "finished_at", "created_at",
            "updated_at",
        },
        "quartic_points": {
            "id", "search_id", "x", "y", "projective_json", "exact_verified",
            "mapped_point_json", "metadata_json", "created_at",
        },
        "mw_lattices": {
            "id", "lattice_key", "curve_id", "source", "family_spec",
            "parameter", "basis_json", "gram_json", "precision_bits",
            "basis_count", "determinant", "min_eigenvalue",
            "positive_definite_screen", "status", "metadata_json", "created_at",
            "updated_at",
        },
        "lattice_holes": {
            "id", "lattice_id", "hole_key", "bits", "representative_json",
            "norm2", "method", "rank_order", "status", "metadata_json",
            "created_at", "updated_at",
        },
        "coverings": {
            "id", "covering_key", "curve_id", "lattice_id", "hole_id",
            "schema_version", "quartic_json", "map_x", "map_y", "metadata_json",
            "status", "quartic_search_id", "error", "created_at", "updated_at",
        },
        "covering_search_attempts": {
            "id", "covering_id", "curve_id", "pipeline_run_id",
            "pipeline_stage_index", "pipeline_stage_id", "height",
            "timeout_seconds", "backend", "one_point", "outcome",
            "ratpoints_hits", "mapped_points", "runtime_seconds", "error",
            "metadata_json", "created_at",
        },
        "external_catalogs": {
            "source", "source_url", "curve_count", "best_rank_lower",
            "snapshot_path", "raw_sha256", "fetched_at", "metadata_json",
            "updated_at",
        },
        "external_curves": {
            "id", "source", "source_id", "curve_key", "a_invariants_json",
            "rank_lower_bound", "proof_method", "points_json", "conductor",
            "bad_primes_json", "discriminant", "naive_height", "faltings_height",
            "regulator", "submitter", "commentary", "source_created_at",
            "source_updated_at", "source_url", "raw_json", "active",
            "last_seen_at", "imported_at", "updated_at",
        },
        "external_curve_scores": {
            "id", "source", "source_id", "prime_bound", "score", "primes_used",
            "runtime", "metadata_json", "created_at", "updated_at",
        },
        "external_curve_searches": {
            "id", "search_key", "source", "source_id", "stages_json",
            "timeout_seconds", "ratpoints_executable", "status", "points_found",
            "certified_rank_lower", "metadata_json", "error", "started_at",
            "finished_at", "created_at", "updated_at",
        },
        "external_extra_points": {
            "id", "search_id", "x", "y", "short_x", "short_y",
            "exact_verified", "relation_status", "certified_rank_lower",
            "certificate_json", "metadata_json", "created_at",
        },
        "curve_catalog_checks": {
            "id", "curve_id", "source", "status", "source_label", "source_url",
            "source_rank", "metadata_json", "checked_at",
        },
        "catalog_query_cache": {
            "source", "query_key", "status", "payload_json", "metadata_json",
            "fetched_at",
        },
        "plugin_states": {
            "plugin_id", "enabled", "status", "validation_json", "updated_at",
        },
        "candidate_pools": {
            "id", "name", "plugin_id", "plugin_version", "family_spec",
            "family_sha256", "adapter_sha256", "plugin_manifest_sha256",
            "generation_json", "status", "candidate_count", "source_path",
            "created_at", "updated_at",
        },
        "native_fibers": {
            "id", "native_family_key", "native_parameter",
            "native_family_spec", "created_at", "updated_at",
        },
        "candidates": {
            "id", "pool_id", "rank_order", "parameter", "a", "b", "score",
            "prime_bound", "prime_terms", "status", "curve_id",
            "native_fiber_id", "native_family_key", "native_family_spec",
            "native_parameter", "chart_id", "chart_parameter",
            "chart_map_fingerprint", "metadata_json", "created_at", "updated_at",
        },
        "points": {
            "id", "curve_id", "x", "y", "source", "role", "exact_verified",
            "independence_status", "rigorous_independent", "hard_flag",
            "hard_reason", "hard_flagged_at", "search_ref", "plugin_id",
            "metadata_json", "created_at", "updated_at",
        },
        "point_discoveries": {
            "id", "curve_id", "point_id", "source", "outcome", "tier",
            "search_ref", "campaign_id", "parameter", "pipeline_run_id",
            "pipeline_candidate_id", "pipeline_stage_index", "pipeline_stage_id",
            "chart_index", "center", "scale", "height",
            "requested_denominator_low", "requested_denominator_high",
            "effective_denominator_low", "effective_denominator_high",
            "chart_x", "chart_w", "projective_json", "stored_x", "stored_y",
            "exact_verified", "error_class", "error", "metadata_json",
            "created_at",
        },
        "general_hunt_trials": {
            "id", "trial_key", "mode", "source_a", "source_b", "short_a",
            "short_b", "x_bound", "target_lower", "status", "point_candidates",
            "screened_rank", "rigorous_lower", "curve_id", "certificate_json",
            "metadata_json", "error", "created_at", "updated_at",
        },
    },
    indexes={
        "idx_curves_score",
        "idx_curves_exact_rank",
        "idx_curves_descent_lower",
        "idx_curves_status",
        "idx_rank_evidence_curve",
        "idx_rank_evidence_engine",
        "idx_family_evidence_family",
        "idx_quartic_searches_status",
        "idx_quartic_searches_curve",
        "idx_mw_lattices_curve",
        "idx_lattice_holes_lattice_norm",
        "idx_coverings_curve_status",
        "idx_covering_attempts_covering",
        "idx_covering_attempts_pipeline",
        "idx_external_curves_rank",
        "idx_external_scores_bound",
        "idx_external_searches_curve",
        "idx_external_extra_search",
        "idx_candidate_pools_plugin",
        "idx_candidates_pool_score",
        "idx_candidates_pool_status_rank",
        "idx_candidates_pool_rank",
        "idx_candidates_curve",
        "idx_points_curve",
        "idx_points_independence",
        "idx_point_discoveries_curve",
        "idx_point_discoveries_point",
        "idx_point_discoveries_pipeline",
        "idx_general_hunt_status",
        "idx_general_hunt_curve",
        "idx_curve_catalog_checks_curve",
        "idx_catalog_query_cache_fetched",
        "idx_candidates_native_fiber",
        "idx_curves_native_fiber",
        "idx_points_hard",
    },
)


UI_SCHEMA_MANIFEST = _manifest(
    "ui",
    tables={
        "research_campaigns": {
            "id", "name", "objective", "status", "target_rank", "plugin_id",
            "variant_id", "family", "notes", "completed_at",
            "completion_snapshot_json", "created_at", "updated_at",
        },
        "ui_jobs": {
            "id", "kind", "label", "command_json", "cwd", "log_path", "pid",
            "status", "exit_code", "metadata_json", "campaign_id", "created_at",
            "started_at", "finished_at", "updated_at",
        },
        "ui_job_queue": {
            "id", "job_id", "state", "priority", "resource_class",
            "available_at", "attempts", "lease_owner", "lease_expires_at",
            "claimed_at", "last_error", "created_at", "updated_at",
        },
        "ui_settings": {"key", "value_json", "updated_at"},
        "ui_service_heartbeats": {
            "service_id", "pid", "worker_id", "status", "max_workers",
            "resource_limits_json", "updated_at",
        },
        "ui_application_state": {
            "state_key", "value_json", "updated_at",
        },
        "analysis_cases": {
            "id", "curve_id", "campaign_id", "priority", "workflow_status",
            "research_goal", "reason", "note", "created_at", "updated_at",
        },
        "research_campaign_artifacts": {
            "id", "campaign_id", "artifact_kind", "artifact_id", "relation",
            "created_at",
        },
    },
    indexes={
        "idx_research_campaigns_status",
        "idx_ui_jobs_status_created",
        "idx_ui_jobs_campaign",
        "idx_ui_job_queue_dispatch",
        "idx_ui_job_queue_job",
        "idx_analysis_cases_status_priority",
        "idx_analysis_cases_campaign",
        "idx_campaign_artifacts_campaign",
        "idx_campaign_artifacts_artifact",
    },
)


MANAGE_SCHEMA_MANIFEST = _manifest(
    "manage",
    tables={
        "research_campaigns": {
            "id", "name", "objective", "status", "target_rank", "plugin_id",
            "variant_id", "family", "notes", "completed_at",
            "completion_snapshot_json", "created_at", "updated_at",
        },
        "research_campaign_notes": {
            "id", "campaign_id", "body", "entry_type", "handoff_state",
            "created_at",
        },
        "research_campaign_note_links": {
            "id", "note_id", "object_kind", "object_id", "created_at",
        },
        "ui_job_schedules": {
            "id", "label", "kind", "command_json", "cwd", "metadata_json",
            "campaign_id", "enabled", "recurrence", "interval_minutes",
            "catch_up_policy", "next_run_at", "last_run_at", "last_job_id",
            "last_error", "created_at", "updated_at",
        },
        "ui_job_schedule_occurrences": {
            "id", "schedule_id", "scheduled_for", "state", "job_id",
            "snapshot_json", "last_error", "created_at", "updated_at",
        },
        "research_campaign_artifacts": {
            "id", "campaign_id", "artifact_kind", "artifact_id", "relation",
            "created_at",
        },
    },
    indexes={
        "idx_research_campaigns_status",
        "idx_research_campaign_notes_campaign",
        "idx_research_campaign_note_links_note",
        "idx_ui_job_schedules_due",
        "idx_ui_job_schedule_occurrences_pending",
        "idx_ui_job_schedule_occurrences_schedule",
        "idx_campaign_artifacts_campaign",
        "idx_campaign_artifacts_artifact",
    },
)


PIPELINE_SCHEMA_MANIFEST = _manifest(
    "pipeline",
    tables={
        "search_pipeline_definitions": {
            "id", "name", "target_mode", "stages_json", "config_json",
            "revision", "content_hash", "pipeline_schema_version",
            "pipeline_catalog_version", "pipeline_catalog_hash",
            "created_at", "updated_at",
        },
        "search_pipeline_runs": {
            "id", "pipeline_id", "pipeline_name", "target_mode", "target_json",
            "stages_json", "run_config_json", "campaign_id",
            "pipeline_schema_version", "pipeline_catalog_version",
            "pipeline_catalog_hash", "manifest_json", "status",
            "current_stage_index", "current_stage_id", "candidates_total",
            "candidates_done", "best_lower", "best_curve_id", "error",
            "result_json", "created_at", "started_at", "finished_at",
            "updated_at",
        },
        "search_pipeline_candidates": {
            "id", "run_id", "provider_key", "parameter", "score", "curve_id",
            "status", "current_stage_index", "current_stage_id",
            "rigorous_lower", "rigorous_upper", "exact_rank",
            "stage_results_json", "error", "created_at", "updated_at",
        },
        "search_pipeline_derivations": {
            "id", "run_id", "stage_index", "stage_id", "parent_candidate_id",
            "child_candidate_id", "transform_kind", "metadata_json",
            "created_at",
        },
    },
    indexes={
        "idx_pipeline_runs_status",
        "idx_pipeline_runs_campaign",
        "idx_pipeline_candidates_run",
        "idx_pipeline_derivations_parent",
        "idx_pipeline_derivations_child",
    },
)


AUTO_SEARCH_SCHEMA_MANIFEST = _manifest(
    "auto_search",
    tables={
        "auto_search_campaigns": {
            "id", "plugin_id", "plugin_version", "variant_id", "family_spec",
            "family_name", "target_rank", "search_mode", "torsion_group",
            "retention_floor", "stop_on_target", "parent_campaign_id",
            "provider_key", "status", "pool_id", "best_curve_id",
            "best_lower", "candidates_total", "candidates_done",
            "exact_count", "exhausted_count", "target_hit_count",
            "torsion_mismatch_count", "config_json", "error", "created_at",
            "started_at", "finished_at", "updated_at",
        },
        "auto_search_trials": {
            "id", "campaign_id", "candidate_id", "curve_id", "parameter",
            "score", "status", "tier", "rigorous_lower", "rigorous_upper",
            "exact_rank", "exact_points", "rank_growth", "metadata_json",
            "error", "created_at", "updated_at",
        },
        "auto_search_steps": {
            "id", "campaign_id", "parameter", "tier", "center", "scale",
            "height", "status", "exact_points", "runtime", "error",
            "created_at", "updated_at",
        },
    },
    indexes={
        "idx_auto_search_campaign_status",
        "idx_auto_search_campaign_parent",
        "idx_auto_search_child_provider",
        "idx_auto_search_trials_campaign",
        "idx_auto_search_steps_campaign",
    },
)


PRIME_LOCAL_SCHEMA_MANIFEST = _manifest(
    "prime_local",
    tables={
        "curve_prime_cache_state": {
            "curve_id", "model_key", "good_prime_bound",
            "bad_primes_complete", "bad_primes_json", "error", "updated_at",
        },
        "curve_prime_data": {
            "curve_id", "p", "model_key", "good_reduction", "ap",
            "cardinality", "normalized_ap", "discriminant_valuation",
            "conductor_valuation", "tamagawa_number", "kodaira_symbol",
            "local_root_number", "computed_at",
        },
        "quartic_local_tests": {
            "search_id", "p", "status", "detail_json", "computed_at",
        },
    },
    indexes={
        "idx_curve_prime_data_curve",
        "idx_quartic_local_tests_search",
    },
    column_types={
        "curve_prime_data": {"p": "TEXT"},
    },
)


TORSION_SCHEMA_MANIFEST = _manifest(
    "torsion",
    tables={
        "curves": {
            "torsion_order",
            "torsion_invariants_json",
            "torsion_label",
            "torsion_computed_at",
            "torsion_algorithm",
            "torsion_error",
        },
    },
    indexes={"idx_curves_torsion_label"},
)


SCHEMA_MANIFESTS = MappingProxyType({
    manifest.name: manifest
    for manifest in (
        CORE_SCHEMA_MANIFEST,
        UI_SCHEMA_MANIFEST,
        MANAGE_SCHEMA_MANIFEST,
        PIPELINE_SCHEMA_MANIFEST,
        AUTO_SEARCH_SCHEMA_MANIFEST,
        PRIME_LOCAL_SCHEMA_MANIFEST,
        TORSION_SCHEMA_MANIFEST,
    )
})


def inspect_schema(db, manifest: SchemaManifest) -> dict[str, object]:
    """Inspect one schema component without mutating the database."""
    actual_user_version = int(
        db.execute("PRAGMA user_version").fetchone()[0] or 0
    )
    objects = {
        (str(row[0]), str(row[1]))
        for row in db.execute(
            "SELECT type,name FROM sqlite_master "
            "WHERE type IN ('table','index')"
        ).fetchall()
    }

    missing_tables = sorted(
        table
        for table in manifest.required_tables
        if ("table", table) not in objects
    )

    missing_columns: dict[str, list[str]] = {}
    column_type_mismatches: dict[str, dict[str, dict[str, str]]] = {}
    for table, required in manifest.required_tables.items():
        if table in missing_tables:
            continue
        column_rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        actual_columns = {str(row[1]) for row in column_rows}
        actual_types = {
            str(row[1]): str(row[2] or "").strip().upper()
            for row in column_rows
        }
        missing = sorted(required - actual_columns)
        if missing:
            missing_columns[table] = missing

        expected_types = manifest.required_column_types.get(table, {})
        mismatches = {}
        for column, expected_type in expected_types.items():
            if column not in actual_columns:
                continue
            actual_type = actual_types.get(column, "")
            if actual_type != expected_type:
                mismatches[column] = {
                    "actual": actual_type,
                    "expected": expected_type,
                }
        if mismatches:
            column_type_mismatches[table] = mismatches

    missing_indexes = sorted(
        index
        for index in manifest.required_indexes
        if ("index", index) not in objects
    )

    expected = manifest.expected_user_version
    version_ok = expected is None or actual_user_version == expected
    ready = (
        version_ok
        and not missing_tables
        and not missing_columns
        and not column_type_mismatches
        and not missing_indexes
    )
    return {
        "name": manifest.name,
        "ready": bool(ready),
        "expected_user_version": expected,
        "actual_user_version": actual_user_version,
        "version_ok": bool(version_ok),
        "versioned": expected is not None,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "column_type_mismatches": column_type_mismatches,
        "missing_indexes": missing_indexes,
    }


def schema_issue_summary(status: Mapping[str, object]) -> str:
    """Return a concise human-readable explanation of readiness failures."""
    issues: list[str] = []
    expected = status.get("expected_user_version")
    actual = status.get("actual_user_version")
    if expected is not None and not bool(status.get("version_ok")):
        issues.append(f"user_version {actual}, expected {expected}")

    missing_tables = list(status.get("missing_tables") or [])
    if missing_tables:
        issues.append("missing tables: " + ", ".join(str(x) for x in missing_tables))

    missing_columns = dict(status.get("missing_columns") or {})
    if missing_columns:
        rendered = []
        for table in sorted(missing_columns):
            rendered.append(
                f"{table}({', '.join(str(x) for x in missing_columns[table])})"
            )
        issues.append("missing columns: " + "; ".join(rendered))

    type_mismatches = dict(status.get("column_type_mismatches") or {})
    if type_mismatches:
        rendered = []
        for table in sorted(type_mismatches):
            for column in sorted(type_mismatches[table]):
                detail = type_mismatches[table][column]
                rendered.append(
                    f"{table}.{column}={detail['actual'] or '<empty>'}"
                    f" (expected {detail['expected']})"
                )
        issues.append("column types: " + "; ".join(rendered))

    missing_indexes = list(status.get("missing_indexes") or [])
    if missing_indexes:
        issues.append("missing indexes: " + ", ".join(str(x) for x in missing_indexes))

    return "; ".join(issues)
