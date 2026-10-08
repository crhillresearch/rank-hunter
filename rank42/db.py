import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

from rank42.schema_manifest import CORE_SCHEMA_MANIFEST, inspect_schema
from rank42.schema_sql import execute_static_schema_script

SCHEMA = """
PRAGMA user_version=813;

CREATE TABLE IF NOT EXISTS curves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family TEXT NOT NULL,
    parameter TEXT NOT NULL,
    score REAL,
    a_invariants_json TEXT,
    conductor TEXT,
    discriminant TEXT,
    bad_primes_json TEXT,
    root_number INTEGER,
    generic_lower INTEGER,
    quick_upper INTEGER,
    descent_lower INTEGER,
    descent_upper INTEGER,
    exact_rank INTEGER,
    certain INTEGER DEFAULT 0,
    regulator TEXT,
    generators_json TEXT,
    plugin_id TEXT,
    plugin_version TEXT,
    family_spec TEXT,
    family_sha256 TEXT,
    adapter_sha256 TEXT,
    plugin_manifest_sha256 TEXT,
    native_fiber_id INTEGER,
    native_family_key TEXT,
    native_family_spec TEXT,
    native_parameter TEXT,
    chart_id TEXT,
    chart_parameter TEXT,
    chart_map_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(family, parameter)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curve_id INTEGER,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id)
);

CREATE TABLE IF NOT EXISTS rank_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curve_id INTEGER NOT NULL,
    evidence_key TEXT NOT NULL UNIQUE,
    engine TEXT NOT NULL,
    engine_version TEXT,
    sage_version TEXT,
    evidence_type TEXT NOT NULL DEFAULT 'rank_bounds',
    rigorous INTEGER NOT NULL DEFAULT 1,
    rigorous_lower INTEGER,
    rigorous_upper INTEGER,
    exact_rank INTEGER,
    conditional_analytic_upper INTEGER,
    conditional_mw_upper INTEGER,
    numerical_rank_signal INTEGER,
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    timed_out INTEGER NOT NULL DEFAULT 0,
    partial INTEGER NOT NULL DEFAULT 0,
    model_a_invariants_json TEXT NOT NULL,
    minimal_model_a_invariants_json TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    points_json TEXT NOT NULL DEFAULT '[]',
    stdout_summary TEXT,
    stderr_summary TEXT,
    started_at TEXT,
    finished_at TEXT,
    elapsed_seconds REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS family_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_key TEXT NOT NULL UNIQUE,
    family_key TEXT NOT NULL,
    family_spec TEXT NOT NULL,
    family_sha256 TEXT,
    plugin_id TEXT,
    plugin_version TEXT,
    criterion TEXT NOT NULL,
    specialization_parameter TEXT NOT NULL,
    specialized_curve_id INTEGER,
    generic_lower INTEGER,
    generic_upper INTEGER,
    exact_generic_rank INTEGER,
    certificate_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(specialized_curve_id) REFERENCES curves(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_family_evidence_family
    ON family_evidence(family_key, created_at DESC);

CREATE TABLE IF NOT EXISTS quartic_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_key TEXT NOT NULL UNIQUE,
    curve_id INTEGER,
    family TEXT,
    parameter TEXT,
    hole_label TEXT,
    polynomial_json TEXT NOT NULL,
    integer_polynomial_json TEXT NOT NULL,
    y_scale TEXT NOT NULL,
    degree INTEGER NOT NULL,
    height_bound INTEGER NOT NULL,
    denominator_low INTEGER,
    denominator_high INTEGER,
    options_json TEXT,
    metadata_json TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    ratpoints_executable TEXT,
    point_count INTEGER DEFAULT 0,
    runtime REAL,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS quartic_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL,
    x TEXT NOT NULL,
    y TEXT NOT NULL,
    projective_json TEXT NOT NULL,
    exact_verified INTEGER NOT NULL DEFAULT 0,
    mapped_point_json TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(search_id, x, y),
    FOREIGN KEY(search_id) REFERENCES quartic_searches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mw_lattices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lattice_key TEXT NOT NULL UNIQUE,
    curve_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    family_spec TEXT,
    parameter TEXT,
    basis_json TEXT NOT NULL,
    gram_json TEXT NOT NULL,
    precision_bits INTEGER NOT NULL,
    basis_count INTEGER NOT NULL,
    determinant TEXT,
    min_eigenvalue TEXT,
    positive_definite_screen INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'screened',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lattice_holes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lattice_id INTEGER NOT NULL,
    hole_key TEXT NOT NULL,
    bits TEXT NOT NULL,
    representative_json TEXT NOT NULL,
    norm2 REAL NOT NULL,
    method TEXT NOT NULL,
    rank_order INTEGER,
    status TEXT NOT NULL DEFAULT 'new',
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(lattice_id, hole_key),
    FOREIGN KEY(lattice_id) REFERENCES mw_lattices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS coverings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    covering_key TEXT NOT NULL UNIQUE,
    curve_id INTEGER NOT NULL,
    lattice_id INTEGER,
    hole_id INTEGER,
    schema_version TEXT NOT NULL,
    quartic_json TEXT NOT NULL,
    map_x TEXT NOT NULL,
    map_y TEXT NOT NULL,
    metadata_json TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    quartic_search_id INTEGER,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE,
    FOREIGN KEY(lattice_id) REFERENCES mw_lattices(id) ON DELETE SET NULL,
    FOREIGN KEY(hole_id) REFERENCES lattice_holes(id) ON DELETE SET NULL,
    FOREIGN KEY(quartic_search_id) REFERENCES quartic_searches(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS covering_search_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    covering_id INTEGER NOT NULL,
    curve_id INTEGER NOT NULL,
    pipeline_run_id INTEGER,
    pipeline_stage_index INTEGER,
    pipeline_stage_id TEXT NOT NULL,
    height INTEGER NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    backend TEXT NOT NULL,
    one_point INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL,
    ratpoints_hits INTEGER NOT NULL DEFAULT 0,
    mapped_points INTEGER NOT NULL DEFAULT 0,
    runtime_seconds REAL,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(covering_id) REFERENCES coverings(id) ON DELETE CASCADE,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS external_catalogs (
    source TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    curve_count INTEGER NOT NULL DEFAULT 0,
    best_rank_lower INTEGER NOT NULL DEFAULT 0,
    snapshot_path TEXT,
    raw_sha256 TEXT,
    fetched_at TEXT,
    metadata_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_curves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    curve_key TEXT,
    a_invariants_json TEXT NOT NULL,
    rank_lower_bound INTEGER NOT NULL,
    proof_method TEXT,
    points_json TEXT NOT NULL,
    conductor TEXT,
    bad_primes_json TEXT,
    discriminant TEXT,
    naive_height TEXT,
    faltings_height TEXT,
    regulator TEXT,
    submitter TEXT,
    commentary TEXT,
    source_created_at TEXT,
    source_updated_at TEXT,
    source_url TEXT,
    raw_json TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_id),
    UNIQUE(source, curve_key),
    FOREIGN KEY(source) REFERENCES external_catalogs(source) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS external_curve_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    prime_bound INTEGER NOT NULL,
    score REAL NOT NULL,
    primes_used INTEGER NOT NULL,
    runtime REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_id, prime_bound),
    FOREIGN KEY(source, source_id) REFERENCES external_curves(source, source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS external_curve_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    stages_json TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    ratpoints_executable TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    points_found INTEGER NOT NULL DEFAULT 0,
    certified_rank_lower INTEGER,
    metadata_json TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source, source_id) REFERENCES external_curves(source, source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS external_extra_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL,
    x TEXT NOT NULL,
    y TEXT NOT NULL,
    short_x TEXT,
    short_y TEXT,
    exact_verified INTEGER NOT NULL DEFAULT 0,
    relation_status TEXT NOT NULL DEFAULT 'candidate',
    certified_rank_lower INTEGER,
    certificate_json TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(search_id, x, y),
    FOREIGN KEY(search_id) REFERENCES external_curve_searches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS curve_catalog_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curve_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    source_label TEXT,
    source_url TEXT,
    source_rank INTEGER,
    metadata_json TEXT,
    checked_at TEXT NOT NULL,
    UNIQUE(curve_id, source),
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS catalog_query_cache (
    source TEXT NOT NULL,
    query_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT,
    metadata_json TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(source, query_key)
);

CREATE TABLE IF NOT EXISTS plugin_states (
    plugin_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unvalidated',
    validation_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_pools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    plugin_id TEXT NOT NULL,
    plugin_version TEXT,
    family_spec TEXT NOT NULL,
    family_sha256 TEXT,
    adapter_sha256 TEXT,
    plugin_manifest_sha256 TEXT,
    generation_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ready',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    source_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS native_fibers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    native_family_key TEXT NOT NULL,
    native_parameter TEXT NOT NULL,
    native_family_spec TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(native_family_key, native_parameter)
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id INTEGER NOT NULL,
    rank_order INTEGER,
    parameter TEXT NOT NULL,
    a TEXT,
    b TEXT,
    score REAL NOT NULL DEFAULT 0,
    prime_bound INTEGER,
    prime_terms INTEGER,
    status TEXT NOT NULL DEFAULT 'unsearched',
    curve_id INTEGER,
    native_fiber_id INTEGER,
    native_family_key TEXT,
    native_family_spec TEXT,
    native_parameter TEXT,
    chart_id TEXT,
    chart_parameter TEXT,
    chart_map_fingerprint TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(pool_id, parameter),
    FOREIGN KEY(pool_id) REFERENCES candidate_pools(id) ON DELETE CASCADE,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL,
    FOREIGN KEY(native_fiber_id) REFERENCES native_fibers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curve_id INTEGER NOT NULL,
    x TEXT NOT NULL,
    y TEXT NOT NULL,
    source TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'candidate',
    exact_verified INTEGER NOT NULL DEFAULT 0,
    independence_status TEXT NOT NULL DEFAULT 'unknown',
    rigorous_independent INTEGER NOT NULL DEFAULT 0,
    hard_flag INTEGER NOT NULL DEFAULT 0,
    hard_reason TEXT,
    hard_flagged_at TEXT,
    search_ref TEXT,
    plugin_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(curve_id, x, y),
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS point_discoveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    curve_id INTEGER NOT NULL,
    point_id INTEGER,
    source TEXT NOT NULL,
    outcome TEXT NOT NULL,
    tier TEXT,
    search_ref TEXT,
    campaign_id INTEGER,
    parameter TEXT,
    pipeline_run_id INTEGER,
    pipeline_candidate_id INTEGER,
    pipeline_stage_index INTEGER,
    pipeline_stage_id TEXT,
    chart_index INTEGER,
    center TEXT,
    scale TEXT,
    height INTEGER,
    requested_denominator_low INTEGER,
    requested_denominator_high INTEGER,
    effective_denominator_low INTEGER,
    effective_denominator_high INTEGER,
    chart_x TEXT,
    chart_w TEXT,
    projective_json TEXT,
    stored_x TEXT,
    stored_y TEXT,
    exact_verified INTEGER NOT NULL DEFAULT 0,
    error_class TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE,
    FOREIGN KEY(point_id) REFERENCES points(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS general_hunt_trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_key TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    source_a INTEGER NOT NULL,
    source_b INTEGER NOT NULL,
    short_a INTEGER NOT NULL,
    short_b INTEGER NOT NULL,
    x_bound INTEGER NOT NULL,
    target_lower INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    point_candidates INTEGER NOT NULL DEFAULT 0,
    screened_rank INTEGER NOT NULL DEFAULT 0,
    rigorous_lower INTEGER NOT NULL DEFAULT 0,
    curve_id INTEGER,
    certificate_json TEXT,
    metadata_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_curves_score ON curves(score DESC);
CREATE INDEX IF NOT EXISTS idx_curves_exact_rank ON curves(exact_rank DESC);
CREATE INDEX IF NOT EXISTS idx_curves_descent_lower ON curves(descent_lower DESC);
CREATE INDEX IF NOT EXISTS idx_curves_status ON curves(status);
CREATE INDEX IF NOT EXISTS idx_rank_evidence_curve
    ON rank_evidence(curve_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rank_evidence_engine
    ON rank_evidence(engine, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_quartic_searches_status
    ON quartic_searches(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_quartic_searches_curve
    ON quartic_searches(curve_id, status);
CREATE INDEX IF NOT EXISTS idx_mw_lattices_curve
    ON mw_lattices(curve_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_lattice_holes_lattice_norm
    ON lattice_holes(lattice_id, norm2 DESC);
CREATE INDEX IF NOT EXISTS idx_coverings_curve_status
    ON coverings(curve_id, status);
CREATE INDEX IF NOT EXISTS idx_covering_attempts_covering
    ON covering_search_attempts(covering_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_covering_attempts_pipeline
    ON covering_search_attempts(
        pipeline_run_id, pipeline_stage_index, covering_id, created_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_external_curves_rank
    ON external_curves(source, rank_lower_bound DESC);
CREATE INDEX IF NOT EXISTS idx_external_scores_bound
    ON external_curve_scores(source, prime_bound, score DESC);
CREATE INDEX IF NOT EXISTS idx_external_searches_curve
    ON external_curve_searches(source, source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_extra_search
    ON external_extra_points(search_id, relation_status);
CREATE INDEX IF NOT EXISTS idx_candidate_pools_plugin
    ON candidate_pools(plugin_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_pool_score
    ON candidates(pool_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_pool_status_rank
    ON candidates(pool_id, status, rank_order);
CREATE INDEX IF NOT EXISTS idx_candidates_pool_rank
    ON candidates(pool_id, rank_order);
CREATE INDEX IF NOT EXISTS idx_candidates_curve
    ON candidates(curve_id);
CREATE INDEX IF NOT EXISTS idx_points_curve
    ON points(curve_id, rigorous_independent DESC, exact_verified DESC);
CREATE INDEX IF NOT EXISTS idx_points_independence
    ON points(independence_status, rigorous_independent DESC);
CREATE INDEX IF NOT EXISTS idx_point_discoveries_curve
    ON point_discoveries(curve_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_point_discoveries_point
    ON point_discoveries(point_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_point_discoveries_pipeline
    ON point_discoveries(pipeline_run_id, pipeline_candidate_id, pipeline_stage_index, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_general_hunt_status
    ON general_hunt_trials(status, rigorous_lower DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_general_hunt_curve
    ON general_hunt_trials(curve_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_curve_catalog_checks_curve
    ON curve_catalog_checks(curve_id, source);
CREATE INDEX IF NOT EXISTS idx_catalog_query_cache_fetched
    ON catalog_query_cache(source, fetched_at DESC);
"""

def now():
    return datetime.now(timezone.utc).isoformat()

def _row_lower(row):
    vals = []
    for key in ("exact_rank", "descent_lower", "generic_lower"):
        try:
            value = row[key]
        except (KeyError, IndexError):
            value = None
        if value is not None:
            vals.append(int(value))
    return max(vals) if vals else 0

def proven_lower(row):
    """Best rigorous lower bound stored on a curve row."""
    return _row_lower(row)

def _best_lower_bound_no_commit(db):
    row = db.execute(
        """
        SELECT MAX(
            MAX(
                COALESCE(exact_rank, 0),
                COALESCE(descent_lower, 0),
                COALESCE(generic_lower, 0)
            )
        ) AS r
        FROM curves
        """
    ).fetchone()
    return int(row["r"] or 0)



def _migrate_external_catalog_state(db, *, commit=True):
    """Additive migration for v0.6 external-catalog lifecycle columns."""
    cols = {row["name"] for row in db.execute("PRAGMA table_info(external_curves)")}
    if "active" not in cols:
        db.execute("ALTER TABLE external_curves ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "last_seen_at" not in cols:
        db.execute("ALTER TABLE external_curves ADD COLUMN last_seen_at TEXT")
    if commit:
        db.commit()


def _migrate_v080_points(db, *, commit=True):
    """Migrate legacy v0.7 mapped-point rows into the authoritative v0.8 ledger.

    Fresh v0.8 databases do not create ``extra_points``.  Existing databases may
    retain that historical table; migration is additive and never drops it.
    """
    legacy = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='extra_points'"
    ).fetchone()
    if legacy is None:
        return
    ts = now()
    db.execute(
        """
        INSERT OR IGNORE INTO points(
            curve_id,x,y,source,role,exact_verified,independence_status,
            rigorous_independent,search_ref,plugin_id,metadata_json,created_at,updated_at
        )
        SELECT curve_id,x,y,'legacy_extra_point','candidate_extra',exact_verified,
               CASE WHEN independence_screen=1 THEN 'numerical_novel' ELSE 'unknown' END,
               0,'covering:' || covering_id,NULL,COALESCE(metadata_json,'{}'),created_at,?
        FROM extra_points
        """,
        (ts,),
    )
    if commit:
        db.commit()


def _add_columns(db, table, columns):
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _migrate_v086_provenance(db, *, commit=True):
    """Add durable family/plugin provenance without rewriting historical rows."""
    _add_columns(db, "curves", {
        "plugin_id": "TEXT",
        "plugin_version": "TEXT",
        "family_spec": "TEXT",
        "family_sha256": "TEXT",
        "adapter_sha256": "TEXT",
        "plugin_manifest_sha256": "TEXT",
    })
    _add_columns(db, "candidate_pools", {
        "family_sha256": "TEXT",
        "adapter_sha256": "TEXT",
        "plugin_manifest_sha256": "TEXT",
    })
    db.execute("PRAGMA user_version=806")
    if commit:
        db.commit()


def _migrate_v087_family_charts(db, *, commit=True):
    """Add exact chart/native-fiber identity without rewriting historical rows."""
    db.execute("""CREATE TABLE IF NOT EXISTS native_fibers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, native_family_key TEXT NOT NULL,
        native_parameter TEXT NOT NULL, native_family_spec TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(native_family_key, native_parameter))""")
    cols = {
        "native_fiber_id": "INTEGER", "native_family_key": "TEXT",
        "native_family_spec": "TEXT", "native_parameter": "TEXT", "chart_id": "TEXT",
        "chart_parameter": "TEXT", "chart_map_fingerprint": "TEXT",
    }
    _add_columns(db, "curves", cols)
    _add_columns(db, "candidates", cols)
    db.execute("CREATE INDEX IF NOT EXISTS idx_candidates_native_fiber ON candidates(native_family_key,native_parameter)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_curves_native_fiber ON curves(native_family_key,native_parameter)")
    db.execute("PRAGMA user_version=807")
    if commit:
        db.commit()


def repair_orphan_quartic_curve_refs(db, *, commit=True):
    """Explicitly detach legacy quartic searches whose source curve is missing.

    This is a named scientific-state repair operation. Normal database opening
    does not invoke it. The qualifying-row semantics intentionally match the
    legacy repair exactly.
    """
    table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quartic_searches'"
    ).fetchone()
    if table is None:
        return 0
    orphan = db.execute(
        """
        SELECT 1
        FROM quartic_searches
        WHERE curve_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM curves WHERE curves.id=quartic_searches.curve_id
          )
        LIMIT 1
        """
    ).fetchone()
    if orphan is None:
        return 0
    cur = db.execute(
        """
        UPDATE quartic_searches
        SET curve_id=NULL
        WHERE curve_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM curves WHERE curves.id=quartic_searches.curve_id
          )
        """
    )
    repaired = max(0, int(cur.rowcount or 0))
    if repaired and commit:
        db.commit()
    return repaired

def _migrate_v088_rank_evidence(db, *, commit=True):
    """Add the generic scientific rank-evidence ledger (PARI-first pipeline)."""
    db.execute("""CREATE TABLE IF NOT EXISTS rank_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT, curve_id INTEGER NOT NULL,
        evidence_key TEXT NOT NULL UNIQUE, engine TEXT NOT NULL,
        engine_version TEXT, sage_version TEXT, evidence_type TEXT NOT NULL DEFAULT 'rank_bounds',
        rigorous INTEGER NOT NULL DEFAULT 1, rigorous_lower INTEGER, rigorous_upper INTEGER,
        exact_rank INTEGER, conditional_analytic_upper INTEGER, conditional_mw_upper INTEGER, numerical_rank_signal INTEGER,
        assumptions_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
        timed_out INTEGER NOT NULL DEFAULT 0, partial INTEGER NOT NULL DEFAULT 0,
        model_a_invariants_json TEXT NOT NULL, minimal_model_a_invariants_json TEXT,
        options_json TEXT NOT NULL DEFAULT '{}', points_json TEXT NOT NULL DEFAULT '[]',
        stdout_summary TEXT, stderr_summary TEXT, started_at TEXT, finished_at TEXT,
        elapsed_seconds REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_rank_evidence_curve ON rank_evidence(curve_id, created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_rank_evidence_engine ON rank_evidence(engine, status, updated_at DESC)")
    db.execute("PRAGMA user_version=808")
    if commit:
        db.commit()


def _migrate_v089_point_hard_flags(db, *, commit=True):
    """Add durable researcher-managed hard-case bookmarks to point records."""
    _add_columns(db, "points", {
        "hard_flag": "INTEGER NOT NULL DEFAULT 0",
        "hard_reason": "TEXT",
        "hard_flagged_at": "TEXT",
    })
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_points_hard "
        "ON points(hard_flag, curve_id, updated_at DESC)"
    )
    db.execute("PRAGMA user_version=809")
    if commit:
        db.commit()


def _migrate_v091_covering_search_attempts(db, *, commit=True):
    """Add append-only per-covering search-attempt provenance."""
    db.execute("""CREATE TABLE IF NOT EXISTS covering_search_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        covering_id INTEGER NOT NULL,
        curve_id INTEGER NOT NULL,
        pipeline_run_id INTEGER,
        pipeline_stage_index INTEGER,
        pipeline_stage_id TEXT NOT NULL,
        height INTEGER NOT NULL,
        timeout_seconds INTEGER NOT NULL,
        backend TEXT NOT NULL,
        one_point INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL,
        ratpoints_hits INTEGER NOT NULL DEFAULT 0,
        mapped_points INTEGER NOT NULL DEFAULT 0,
        runtime_seconds REAL,
        error TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(covering_id) REFERENCES coverings(id) ON DELETE CASCADE,
        FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
    )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_covering_attempts_covering "
        "ON covering_search_attempts(covering_id, created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_covering_attempts_pipeline "
        "ON covering_search_attempts("
        "pipeline_run_id, pipeline_stage_index, covering_id, created_at DESC)"
    )
    db.execute("PRAGMA user_version=811")
    if commit:
        db.commit()



def _migrate_v092_conditional_mw_upper(db, *, commit=True):
    """Add assumption-bound Mordell-Weil upper evidence separate from analytic rank."""
    _add_columns(db, "rank_evidence", {
        "conditional_mw_upper": "INTEGER",
    })
    db.execute("PRAGMA user_version=812")
    if commit:
        db.commit()


def _migrate_v093_family_evidence(db, *, commit=True):
    """Add generic-family evidence separate from specialization rank evidence."""
    db.execute("""CREATE TABLE IF NOT EXISTS family_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evidence_key TEXT NOT NULL UNIQUE,
        family_key TEXT NOT NULL,
        family_spec TEXT NOT NULL,
        family_sha256 TEXT,
        plugin_id TEXT,
        plugin_version TEXT,
        criterion TEXT NOT NULL,
        specialization_parameter TEXT NOT NULL,
        specialized_curve_id INTEGER,
        generic_lower INTEGER,
        generic_upper INTEGER,
        exact_generic_rank INTEGER,
        certificate_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(specialized_curve_id) REFERENCES curves(id) ON DELETE SET NULL
    )""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_family_evidence_family "
        "ON family_evidence(family_key, created_at DESC)"
    )
    db.execute("PRAGMA user_version=813")
    if commit:
        db.commit()


def ensure_native_fiber(db, *, native_family_key, native_parameter, native_family_spec=None):
    if not native_family_key or native_parameter is None:
        return None
    ts = now()
    db.execute(
        """INSERT INTO native_fibers(native_family_key,native_parameter,native_family_spec,created_at,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(native_family_key,native_parameter) DO UPDATE SET
             native_family_spec=COALESCE(excluded.native_family_spec,native_fibers.native_family_spec),
             updated_at=excluded.updated_at""",
        (str(native_family_key), str(native_parameter), native_family_spec, ts, ts),
    )
    row=db.execute("SELECT id FROM native_fibers WHERE native_family_key=? AND native_parameter=?",
                   (str(native_family_key),str(native_parameter))).fetchone()
    db.commit()
    return int(row["id"])


def get_curves_by_native_key(db, native_family_key, native_parameter):
    return db.execute(
        "SELECT * FROM curves WHERE native_family_key=? AND native_parameter=? ORDER BY id",
        (str(native_family_key), str(native_parameter)),
    ).fetchall()


CURRENT_SCHEMA_VERSION = int(CORE_SCHEMA_MANIFEST.expected_user_version)
_CURRENT_SCHEMA_TABLES = set(CORE_SCHEMA_MANIFEST.required_tables)


def _open_connection(path="rank42.db"):
    """Open one normal runtime connection without mutating database schema."""
    db = sqlite3.connect(str(Path(path)), timeout=30)
    db.row_factory = sqlite3.Row
    # Be explicit: sqlite3's timeout parameter installs a busy handler, but
    # recording it as a PRAGMA makes the intended runtime behavior inspectable.
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def _current_schema_ready(db):
    """Read-only exact readiness check for the current core schema."""
    try:
        return bool(inspect_schema(db, CORE_SCHEMA_MANIFEST)["ready"])
    except Exception:
        return False


def connect_existing(path="rank42.db"):
    """Open an already-initialized Rank Hunter database without schema writes.

    UI polling and other high-frequency readers must use this path.  The caller
    receives a normal read/write SQLite connection for ordinary DML, but opening
    it never executes CREATE/ALTER/repair work.
    """
    resolved = Path(path)
    if not resolved.exists():
        raise sqlite3.OperationalError(f"Rank Hunter database does not exist: {resolved}")
    return _open_connection(resolved)


def migrate_core_schema(db, *, commit=True):
    """Bring the core/scientific schema to the current manifest target.

    With commit=False, the caller owns an already-active SQLite transaction.
    This is the path used by the Database Migration Manager so core DDL cannot
    commit independently from the rest of a baseline schema upgrade.
    """
    schema_status = inspect_schema(db, CORE_SCHEMA_MANIFEST)
    if schema_status["ready"]:
        return False

    actual_version = int(schema_status["actual_user_version"])
    if actual_version > CURRENT_SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            "Rank Hunter database schema "
            f"{actual_version} is newer than this core supports "
            f"({CURRENT_SCHEMA_VERSION})"
        )

    if commit:
        if db.in_transaction:
            raise RuntimeError(
                "core schema migration cannot start while another SQLite "
                "transaction is active"
            )
        # journal_mode cannot be changed from inside a transaction. It is
        # connection/database preparation, not a schema migration step.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
    elif not db.in_transaction:
        raise RuntimeError(
            "commit=False core schema migration requires an active "
            "manager-owned transaction"
        )

    try:
        execute_static_schema_script(db, SCHEMA)
        _migrate_external_catalog_state(db, commit=False)
        _migrate_v080_points(db, commit=False)
        _migrate_v086_provenance(db, commit=False)
        _migrate_v087_family_charts(db, commit=False)
        _migrate_v088_rank_evidence(db, commit=False)
        _migrate_v089_point_hard_flags(db, commit=False)
        _migrate_v091_covering_search_attempts(db, commit=False)
        _migrate_v092_conditional_mw_upper(db, commit=False)
        _migrate_v093_family_evidence(db, commit=False)
        # Historical additive helpers stamp their own version milestones.
        # Reassert the exact current target after all sub-migrations so an
        # older helper cannot downgrade a newer static-schema target.
        db.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")

        final_status = inspect_schema(db, CORE_SCHEMA_MANIFEST)
        if not final_status["ready"]:
            raise sqlite3.DatabaseError(
                "core schema migration did not reach the current manifest target"
            )

        if commit:
            db.commit()
        return True
    except BaseException:
        if commit and db.in_transaction:
            db.rollback()
        raise


def connect(path="rank42.db"):
    db = _open_connection(path)
    schema_status = inspect_schema(db, CORE_SCHEMA_MANIFEST)
    if schema_status["ready"]:
        return db

    try:
        migrate_core_schema(db, commit=True)
        return db
    except BaseException:
        db.close()
        raise

def log_event(db, curve_id, level, message):
    db.execute(
        "INSERT INTO events(curve_id, level, message, created_at) VALUES(?,?,?,?)",
        (curve_id, level, message, now()),
    )
    db.commit()

def upsert_curve(db, *, family, parameter, score=None, **fields):
    ts = now()
    db.execute(
        """
        INSERT INTO curves(family, parameter, score, created_at, updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(family, parameter) DO UPDATE SET
            score=COALESCE(excluded.score, curves.score),
            updated_at=excluded.updated_at
        """,
        (family, parameter, score, ts, ts),
    )
    row = db.execute(
        "SELECT id FROM curves WHERE family=? AND parameter=?",
        (family, parameter),
    ).fetchone()
    curve_id = int(row["id"])
    if fields:
        update_curve(db, curve_id, **fields)
    else:
        db.commit()
    return curve_id

def update_curve(db, curve_id, **fields):
    allowed = {
        "score", "a_invariants_json", "conductor", "discriminant",
        "bad_primes_json", "root_number", "generic_lower", "quick_upper",
        "descent_lower", "descent_upper", "exact_rank", "certain",
        "regulator", "generators_json", "plugin_id", "plugin_version",
        "family_spec", "family_sha256", "adapter_sha256",
        "plugin_manifest_sha256", "native_fiber_id", "native_family_key", "native_family_spec",
        "native_parameter", "chart_id", "chart_parameter", "chart_map_fingerprint",
        "status", "error"
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [curve_id]
    db.execute(f"UPDATE curves SET {cols} WHERE id=?", vals)
    db.commit()
    if "generators_json" in fields and fields.get("generators_json"):
        _sync_generators_to_point_ledger(db, curve_id)

def _sync_generators_to_point_ledger(db, curve_id):
    """Mirror newly stored generator coordinates into the unified point ledger.

    The curve row remains the compatibility representation for older scientific
    workers; v0.8 exposes those exact coordinates as first-class point records.
    Only the witness-backed prefix (descent/exact lower bound) is labelled
    rigorous.
    """
    row = db.execute("SELECT * FROM curves WHERE id=?", (int(curve_id),)).fetchone()
    if row is None:
        return
    try:
        raw = json.loads(row["generators_json"] or "[]")
    except Exception:
        return
    rigorous_count = max(int(row["descent_lower"] or 0), int(row["exact_rank"] or 0))
    from rank42.points import upsert_point
    for i, rec in enumerate(raw):
        if not isinstance(rec, (list, tuple)) or len(rec) < 2:
            continue
        rigorous = i < rigorous_count
        upsert_point(
            db, curve_id=int(curve_id), x=rec[0], y=rec[1],
            source="stored_generators",
            role="rigorous_witness" if rigorous else "generator",
            exact_verified=True,
            independence_status="rigorous_independent" if rigorous else "unknown",
            rigorous_independent=rigorous,
            search_ref="curve:generators",
            metadata={"generator_index": i, "witness_backed_prefix": rigorous_count},
        )

def get_curve(db, curve_id):
    return db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()


def discard_transient_zero_evidence_curve(db, curve_id, *, created_not_before=None):
    """Remove a search-created curve row that contains no positive rank evidence.

    Candidate/job state is the durable record of a miss. ``curves`` is reserved
    for specializations worth keeping as mathematical objects.  The cleanup is
    intentionally conservative: rows with a positive rigorous lower bound,
    point-ledger entries, lattices, coverings, or quartic hits are preserved.
    ``created_not_before`` prevents a new pool run from deleting an older/manual
    zero-rank curve that merely happened to match the same parameter.
    """
    row = get_curve(db, int(curve_id))
    if row is None:
        return False
    if _row_lower(row) > 0:
        return False
    if created_not_before and str(row["created_at"] or "") < str(created_not_before):
        return False

    checks = (
        ("points", "SELECT COUNT(*) AS n FROM points WHERE curve_id=?"),
        ("rank_evidence", "SELECT COUNT(*) AS n FROM rank_evidence WHERE curve_id=?"),
        ("mw_lattices", "SELECT COUNT(*) AS n FROM mw_lattices WHERE curve_id=?"),
        ("coverings", "SELECT COUNT(*) AS n FROM coverings WHERE curve_id=?"),
        ("quartic_hits", "SELECT COUNT(*) AS n FROM quartic_searches WHERE curve_id=? AND COALESCE(point_count,0)>0"),
    )
    for _label, sql in checks:
        found = db.execute(sql, (int(curve_id),)).fetchone()
        if found is not None and int(found["n"] or 0) > 0:
            return False

    # Preserve event history without letting the non-cascading FK block delete.
    db.execute("UPDATE events SET curve_id=NULL WHERE curve_id=?", (int(curve_id),))
    # Older databases may contain reverse-engineering reports without an FK.
    has_reverse = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reverse_engineering_reports'"
    ).fetchone()
    if has_reverse is not None:
        db.execute("UPDATE reverse_engineering_reports SET curve_id=NULL WHERE curve_id=?", (int(curve_id),))
    # Explicitly preserve quartic history even on upgraded connections where
    # foreign-key actions may not have been enforced reliably in the past.
    db.execute("UPDATE quartic_searches SET curve_id=NULL WHERE curve_id=?", (int(curve_id),))
    db.execute("DELETE FROM curves WHERE id=?", (int(curve_id),))
    db.commit()
    return True


def purge_zero_evidence_curves(db):
    """Purge terminal curves with explicit zero rank evidence.

    NULL rank fields mean UNKNOWN and are never treated as mathematical zero.
    Active/new rows are deliberately excluded so concurrent workers are never
    disrupted. Candidate/job metadata remains the durable record of a miss.
    """
    terminal = (
        "exact", "proven_lower", "extra_done", "strong_done",
        "pruned", "quick_timeout", "quick_inconclusive", "strong_timeout",
        "descent_inconclusive", "error",
    )
    marks = ",".join("?" for _ in terminal)
    rows = db.execute(
        f"SELECT id FROM curves WHERE status IN ({marks}) AND "
        "(exact_rank IS NOT NULL OR descent_lower IS NOT NULL OR generic_lower IS NOT NULL) AND "
        "MAX(COALESCE(exact_rank,0),COALESCE(descent_lower,0),COALESCE(generic_lower,0))=0",
        terminal,
    ).fetchall()
    removed = 0
    for row in rows:
        removed += int(discard_transient_zero_evidence_curve(db, int(row["id"])))
    return removed

def get_curve_by_key(db, family, parameter):
    return db.execute(
        "SELECT * FROM curves WHERE family=? AND parameter=?",
        (family, parameter),
    ).fetchone()

def incumbent(db):
    return db.execute(
        """
        SELECT *
        FROM curves
        ORDER BY
          MAX(
            COALESCE(exact_rank, 0),
            COALESCE(descent_lower, 0),
            COALESCE(generic_lower, 0)
          ) DESC,
          certain DESC,
          score DESC
        LIMIT 1
        """
    ).fetchone()

def best_lower_bound(db):
    return _best_lower_bound_no_commit(db)

def best_exact_rank(db):
    row = db.execute("SELECT MAX(exact_rank) AS r FROM curves").fetchone()
    return int(row["r"] or 0)

def stats(db):
    row = db.execute(
        """
        SELECT
          COUNT(*) AS curves,
          SUM(status='pruned') AS pruned,
          SUM(status='promising') AS promising,
          SUM(status='exact') AS exact_count,
          SUM(status='strong_done') AS strong_done,
          SUM(status='quick_timeout') AS quick_timeout,
          SUM(status='strong_timeout') AS strong_timeout,
          SUM(status='error') AS errors
        FROM curves
        """
    ).fetchone()
    return dict(row)










def quartic_stats(db):
    row = db.execute(
        """
        SELECT
          COUNT(*) AS searches,
          SUM(status='new') AS new_count,
          SUM(status='running') AS running,
          SUM(status='done') AS done,
          SUM(status='locally_obstructed') AS locally_obstructed,
          SUM(status='timeout') AS timeouts,
          SUM(status='error') AS errors,
          COALESCE(SUM(point_count), 0) AS points
        FROM quartic_searches
        """
    ).fetchone()
    return dict(row)


def lattice_stats(db):
    row = db.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM mw_lattices) AS lattices,
          (SELECT COUNT(*) FROM lattice_holes) AS holes,
          (SELECT COUNT(*) FROM coverings) AS coverings,
          (SELECT COUNT(*) FROM points WHERE role='candidate_extra') AS extra_points,
          (SELECT COUNT(*) FROM points WHERE role='candidate_extra' AND independence_status IN ('numerical_novel','rigorous_independent')) AS independent_screens
        """
    ).fetchone()
    return dict(row)
