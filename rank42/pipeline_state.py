"""Durable saved-Builder pipeline, run, candidate, and transform-lineage state."""
from __future__ import annotations

import hashlib
import json

from rank42 import __version__ as CORE_VERSION
from rank42.campaign_lineage import (
    ensure_campaign_lineage_schema,
    replace_owned_campaign_link,
)
from rank42.campaign_schema import RESEARCH_CAMPAIGN_SCHEMA
from rank42.db import now
from rank42.schema_sql import execute_static_schema_script
from rank42.ratpoints import ratpoints_engine_identity
from rank42.pipeline_catalog import (
    CATALOG_VERSION,
    POINT_SEARCH_RETRY_STAGE_IDS,
    pipeline_catalog_fingerprint,
)


SCHEMA_VERSION = 8
RUN_MANIFEST_VERSION = 3

PIPELINE_SCHEMA = RESEARCH_CAMPAIGN_SCHEMA + """
CREATE TABLE IF NOT EXISTS search_pipeline_definitions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target_mode TEXT NOT NULL,
    stages_json TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL DEFAULT '',
    pipeline_schema_version INTEGER NOT NULL DEFAULT 0,
    pipeline_catalog_version INTEGER NOT NULL DEFAULT 0,
    pipeline_catalog_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_pipeline_runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER,
    pipeline_name TEXT NOT NULL,
    target_mode TEXT NOT NULL,
    target_json TEXT NOT NULL DEFAULT '{}',
    stages_json TEXT NOT NULL,
    run_config_json TEXT NOT NULL DEFAULT '{}',
    campaign_id INTEGER REFERENCES research_campaigns(id) ON DELETE SET NULL,
    pipeline_schema_version INTEGER NOT NULL DEFAULT 0,
    pipeline_catalog_version INTEGER NOT NULL DEFAULT 0,
    pipeline_catalog_hash TEXT NOT NULL DEFAULT '',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage_index INTEGER,
    current_stage_id TEXT,
    candidates_total INTEGER NOT NULL DEFAULT 0,
    candidates_done INTEGER NOT NULL DEFAULT 0,
    best_lower INTEGER NOT NULL DEFAULT 0,
    best_curve_id INTEGER,
    error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(pipeline_id) REFERENCES search_pipeline_definitions(id) ON DELETE SET NULL,
    FOREIGN KEY(best_curve_id) REFERENCES curves(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS search_pipeline_candidates(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    provider_key TEXT NOT NULL DEFAULT '',
    parameter TEXT NOT NULL,
    score REAL,
    score_provenance_json TEXT NOT NULL DEFAULT '{}',
    curve_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage_index INTEGER,
    current_stage_id TEXT,
    rigorous_lower INTEGER NOT NULL DEFAULT 0,
    rigorous_upper INTEGER,
    exact_rank INTEGER,
    stage_results_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,provider_key,parameter),
    FOREIGN KEY(run_id) REFERENCES search_pipeline_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS search_pipeline_point_attempts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    candidate_id INTEGER NOT NULL,
    curve_id INTEGER,
    stage_index INTEGER NOT NULL,
    stage_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    center TEXT NOT NULL,
    scale TEXT NOT NULL,
    height INTEGER NOT NULL,
    denominator_low INTEGER,
    denominator_high INTEGER,
    effective_denominator_low INTEGER,
    effective_denominator_high INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    exact_points INTEGER NOT NULL DEFAULT 0,
    runtime REAL,
    timeout_seconds INTEGER,
    retry_policy TEXT NOT NULL DEFAULT 'manual',
    engine_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,candidate_id,stage_index,tier,center,scale,height),
    FOREIGN KEY(run_id) REFERENCES search_pipeline_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(candidate_id) REFERENCES search_pipeline_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS search_pipeline_strategy_steps(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    candidate_id INTEGER NOT NULL,
    curve_id INTEGER,
    stage_index INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    occurrence_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_key TEXT NOT NULL,
    step_kind TEXT NOT NULL DEFAULT 'substep',
    nested_stage_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0.0,
    wall_budget_seconds INTEGER,
    retry_policy TEXT NOT NULL DEFAULT 'automatic',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,candidate_id,stage_index,occurrence_id,step_key),
    FOREIGN KEY(run_id) REFERENCES search_pipeline_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(candidate_id) REFERENCES search_pipeline_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS search_pipeline_derivations(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    stage_index INTEGER NOT NULL,
    stage_id TEXT NOT NULL,
    parent_candidate_id INTEGER NOT NULL,
    child_candidate_id INTEGER NOT NULL,
    transform_kind TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(
        run_id,stage_index,parent_candidate_id,child_candidate_id,transform_kind
    ),
    FOREIGN KEY(run_id) REFERENCES search_pipeline_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_candidate_id) REFERENCES search_pipeline_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY(child_candidate_id) REFERENCES search_pipeline_candidates(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
    ON search_pipeline_runs(status, id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_campaign
    ON search_pipeline_runs(campaign_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_candidates_run
    ON search_pipeline_candidates(run_id, status, id);
CREATE INDEX IF NOT EXISTS idx_pipeline_point_attempts_stage
    ON search_pipeline_point_attempts(run_id, candidate_id, stage_index, status);
CREATE INDEX IF NOT EXISTS idx_pipeline_strategy_steps_stage
    ON search_pipeline_strategy_steps(run_id,candidate_id,stage_index,occurrence_id,step_index);
CREATE INDEX IF NOT EXISTS idx_pipeline_derivations_parent
    ON search_pipeline_derivations(run_id, parent_candidate_id, stage_index);
CREATE INDEX IF NOT EXISTS idx_pipeline_derivations_child
    ON search_pipeline_derivations(run_id, child_candidate_id, stage_index);
"""


def pipeline_runtime_identity():
    """Return the current core Pipeline runtime identity."""
    return {
        "core_version": str(CORE_VERSION),
        "pipeline_schema_version": int(SCHEMA_VERSION),
        "pipeline_catalog_version": int(CATALOG_VERSION),
        "pipeline_catalog_hash": str(pipeline_catalog_fingerprint()),
    }


def _pipeline_snapshot_hash(*, target_mode, target, stages, run_config):
    payload = {
        "target_mode": str(target_mode),
        "target": dict(target or {}),
        "stages": list(stages or []),
        "run_config": dict(run_config or {}),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pipeline_plugin_runtime(run_config, *, project_root=None):
    """Return fingerprints for plugins that can affect one Pipeline Run."""
    from pathlib import Path

    from rank42.plugins import (
        Plugin,
        discover_plugins,
        get_variant,
        plugin_fingerprints,
    )

    frozen = dict(run_config or {})
    primary_id = str(frozen.get("plugin_id") or "").strip()
    feature_ids = sorted(
        {str(value) for value in frozen.get("feature_plugin_ids") or []}
    )
    if not primary_id and not feature_ids:
        return {"primary": None, "features": []}

    root = Path(
        project_root or Path(__file__).resolve().parents[1]
    ).resolve()
    discovered = {
        plugin.id: plugin
        for plugin in discover_plugins(root)
        if isinstance(plugin, Plugin)
    }

    def snapshot(plugin_id, *, variant_id=None):
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            return None
        plugin = discovered.get(plugin_id)
        if plugin is None:
            return {
                "plugin_id": plugin_id,
                "variant_id": None if variant_id is None else str(variant_id),
                "missing": True,
            }
        variant = None
        if plugin.plugin_type == "family":
            if variant_id is not None:
                wanted = str(variant_id)
                variant = next(
                    (item for item in plugin.variants if item.id == wanted),
                    None,
                )
            if variant is None:
                variant = get_variant(plugin)
        return {
            "plugin_id": plugin.id,
            "plugin_version": plugin.version,
            "plugin_type": plugin.plugin_type,
            "variant_id": (
                None if variant is None else str(variant.id)
            ),
            "fingerprints": plugin_fingerprints(plugin, variant),
        }

    primary = snapshot(
        primary_id,
        variant_id=frozen.get("plugin_variant"),
    )
    features = []
    for plugin_id in feature_ids:
        rec = snapshot(plugin_id)
        if rec is not None:
            features.append(rec)
    return {
        "primary": primary,
        "features": features,
    }


def build_pipeline_run_manifest(
    *,
    pipeline_name,
    target_mode,
    target,
    stages,
    run_config,
    project_root=None,
):
    """Freeze the reproducibility identity known to core at Run creation."""
    frozen = dict(run_config or {})
    identity = pipeline_runtime_identity()
    point_search_configured = any(
        isinstance(stage, dict)
        and str(stage.get("id") or "") in POINT_SEARCH_RETRY_STAGE_IDS
        for stage in (stages or [])
    )
    ratpoints_engine = (
        ratpoints_engine_identity(
            frozen.get("ratpoints"),
            backend_hint=frozen.get("ratpoints_backend"),
        )
        if point_search_configured
        else None
    )
    manifest = {
        "manifest_version": RUN_MANIFEST_VERSION,
        **identity,
        "pipeline_name": str(pipeline_name),
        "target_mode": str(target_mode),
        "target": dict(target or {}),
        "launch_surface": frozen.get("launch_surface"),
        "plugin_id": frozen.get("plugin_id"),
        "plugin_variant": frozen.get("plugin_variant"),
        "feature_plugin_ids": list(frozen.get("feature_plugin_ids") or []),
        "ratpoints_backend": frozen.get("ratpoints_backend"),
        "ratpoints_engine": ratpoints_engine,
        "source_pipeline_id": frozen.get("source_pipeline_id"),
        "source_pipeline_revision": frozen.get("source_pipeline_revision"),
        "source_pipeline_hash": frozen.get("source_pipeline_hash"),
        "derived_from_pipeline_id": frozen.get("derived_from_pipeline_id"),
        "derived_from_pipeline_revision": frozen.get(
            "derived_from_pipeline_revision"
        ),
        "derived_from_pipeline_hash": frozen.get("derived_from_pipeline_hash"),
        "pipeline_source_relation": frozen.get("pipeline_source_relation"),
        "plugin_runtime": _pipeline_plugin_runtime(
            frozen,
            project_root=project_root,
        ),
    }
    manifest["run_snapshot_hash"] = _pipeline_snapshot_hash(
        target_mode=target_mode,
        target=target,
        stages=stages,
        run_config=frozen,
    )
    return manifest


def pipeline_definition_hash(*, target_mode, stages, config=None):
    """Return the deterministic executable-content fingerprint for a Pipeline."""
    payload = {
        "target_mode": str(target_mode),
        "stages": list(stages),
        "config": dict(config or {}),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ensure_pipeline_campaign_column(db, *, commit=True):
    columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(search_pipeline_runs)").fetchall()
    }
    changed = False
    if "campaign_id" not in columns:
        db.execute(
            "ALTER TABLE search_pipeline_runs ADD COLUMN campaign_id INTEGER "
            "REFERENCES research_campaigns(id) ON DELETE SET NULL"
        )
        changed = True
    index_exists = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='index' AND name='idx_pipeline_runs_campaign'"""
    ).fetchone()
    if index_exists is None:
        db.execute(
            "CREATE INDEX idx_pipeline_runs_campaign "
            "ON search_pipeline_runs(campaign_id, id DESC)"
        )
        changed = True
    if changed and commit:
        db.commit()
    return changed


def _ensure_pipeline_provenance_columns(db, *, commit=True):
    changed = False
    definition_columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_definitions)"
        ).fetchall()
    }
    for name, ddl in (
        (
            "pipeline_schema_version",
            "ALTER TABLE search_pipeline_definitions "
            "ADD COLUMN pipeline_schema_version INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "pipeline_catalog_version",
            "ALTER TABLE search_pipeline_definitions "
            "ADD COLUMN pipeline_catalog_version INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "pipeline_catalog_hash",
            "ALTER TABLE search_pipeline_definitions "
            "ADD COLUMN pipeline_catalog_hash TEXT NOT NULL DEFAULT ''",
        ),
    ):
        if name not in definition_columns:
            db.execute(ddl)
            changed = True

    run_columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(search_pipeline_runs)").fetchall()
    }
    for name, ddl in (
        (
            "pipeline_schema_version",
            "ALTER TABLE search_pipeline_runs "
            "ADD COLUMN pipeline_schema_version INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "pipeline_catalog_version",
            "ALTER TABLE search_pipeline_runs "
            "ADD COLUMN pipeline_catalog_version INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "pipeline_catalog_hash",
            "ALTER TABLE search_pipeline_runs "
            "ADD COLUMN pipeline_catalog_hash TEXT NOT NULL DEFAULT ''",
        ),
        (
            "manifest_json",
            "ALTER TABLE search_pipeline_runs "
            "ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}'",
        ),
    ):
        if name not in run_columns:
            db.execute(ddl)
            changed = True

    if changed and commit:
        db.commit()
    return changed


def _ensure_pipeline_candidate_provenance_column(db, *, commit=True):
    columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_candidates)"
        ).fetchall()
    }
    if "score_provenance_json" in columns:
        return False
    db.execute(
        "ALTER TABLE search_pipeline_candidates "
        "ADD COLUMN score_provenance_json TEXT NOT NULL DEFAULT '{}'"
    )
    if commit:
        db.commit()
    return True


def _ensure_pipeline_point_attempt_region_columns(db, *, commit=True):
    """Migrate v5 point-attempt rows to explicit effective denominator regions."""
    table = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='search_pipeline_point_attempts'"""
    ).fetchone()
    if table is None:
        return False
    columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    changed = False
    for name in ("effective_denominator_low", "effective_denominator_high"):
        if name not in columns:
            db.execute(
                f"ALTER TABLE search_pipeline_point_attempts ADD COLUMN {name} INTEGER"
            )
            changed = True
    if changed and commit:
        db.commit()
    return changed


def _ensure_pipeline_point_attempt_engine_column(db, *, commit=True):
    """Migrate pre-v7 Pipeline point attempts to engine provenance storage."""
    table = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='search_pipeline_point_attempts'"""
    ).fetchone()
    if table is None:
        return False
    columns = {
        str(row["name"])
        for row in db.execute(
            "PRAGMA table_info(search_pipeline_point_attempts)"
        ).fetchall()
    }
    if "engine_json" in columns:
        return False
    db.execute(
        "ALTER TABLE search_pipeline_point_attempts "
        "ADD COLUMN engine_json TEXT NOT NULL DEFAULT '{}'"
    )
    if commit:
        db.commit()
    return True


def _ensure_pipeline_definition_version_columns(db, *, commit=True):
    columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(search_pipeline_definitions)").fetchall()
    }
    changed = False
    if "revision" not in columns:
        db.execute(
            "ALTER TABLE search_pipeline_definitions "
            "ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
        )
        changed = True
    if "content_hash" not in columns:
        db.execute(
            "ALTER TABLE search_pipeline_definitions "
            "ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
        )
        changed = True

    rows = db.execute(
        """SELECT id,target_mode,stages_json,config_json,content_hash
           FROM search_pipeline_definitions
           WHERE content_hash IS NULL OR content_hash=''"""
    ).fetchall()
    for row in rows:
        digest = pipeline_definition_hash(
            target_mode=row["target_mode"],
            stages=_json(row["stages_json"], []),
            config=_json(row["config_json"], {}),
        )
        db.execute(
            "UPDATE search_pipeline_definitions SET content_hash=? WHERE id=?",
            (digest, int(row["id"])),
        )
        changed = True
    if changed and commit:
        db.commit()
    return changed


def ensure_pipeline_schema(db, *, commit=True):
    """Initialize/migrate Builder state; steady-state checks are read-only."""
    parent_change = False
    parent = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='research_campaigns'"""
    ).fetchone()
    if parent is None:
        execute_static_schema_script(db, RESEARCH_CAMPAIGN_SCHEMA)
        parent_change = True
    lineage_change = ensure_campaign_lineage_schema(db, commit=False)
    wanted_tables = {
        "search_pipeline_definitions",
        "search_pipeline_runs",
        "search_pipeline_candidates",
        "search_pipeline_point_attempts",
        "search_pipeline_strategy_steps",
        "search_pipeline_derivations",
    }
    tables = {
        str(row["name"])
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                 'search_pipeline_definitions',
                 'search_pipeline_runs',
                 'search_pipeline_candidates',
                 'search_pipeline_point_attempts',
                 'search_pipeline_strategy_steps',
                 'search_pipeline_derivations'
               )"""
        ).fetchall()
    }
    indexes = set()
    if tables == wanted_tables:
        indexes = {
            str(row["name"])
            for row in db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='index' AND name IN (
                     'idx_pipeline_runs_status',
                     'idx_pipeline_candidates_run',
                     'idx_pipeline_point_attempts_stage',
                     'idx_pipeline_strategy_steps_stage',
                     'idx_pipeline_derivations_parent',
                     'idx_pipeline_derivations_child'
                   )"""
            ).fetchall()
        }

    campaign_change = False
    if "search_pipeline_runs" in tables:
        campaign_change = _ensure_pipeline_campaign_column(db, commit=False)

    structural_change = (
        tables != wanted_tables
        or not {
            "idx_pipeline_runs_status",
            "idx_pipeline_candidates_run",
            "idx_pipeline_point_attempts_stage",
            "idx_pipeline_strategy_steps_stage",
            "idx_pipeline_derivations_parent",
            "idx_pipeline_derivations_child",
        }.issubset(indexes)
    )
    if structural_change:
        execute_static_schema_script(db, PIPELINE_SCHEMA)
    if not campaign_change:
        campaign_change = _ensure_pipeline_campaign_column(db, commit=False)
    version_change = _ensure_pipeline_definition_version_columns(db, commit=False)
    provenance_change = _ensure_pipeline_provenance_columns(db, commit=False)
    candidate_provenance_change = _ensure_pipeline_candidate_provenance_column(
        db, commit=False
    )
    point_attempt_region_change = _ensure_pipeline_point_attempt_region_columns(
        db, commit=False
    )
    point_attempt_engine_change = _ensure_pipeline_point_attempt_engine_column(
        db, commit=False
    )
    changed = bool(
        parent_change
        or lineage_change
        or structural_change
        or campaign_change
        or version_change
        or provenance_change
        or candidate_provenance_change
        or point_attempt_region_change
        or point_attempt_engine_change
    )
    if changed and commit:
        db.commit()
    return changed


def _json(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def save_pipeline(db, *, name, target_mode, stages, config=None, pipeline_id=None):
    ensure_pipeline_schema(db)
    ts = now()
    name = str(name)
    target_mode = str(target_mode)
    normalized_stages = list(stages)
    normalized_config = dict(config or {})
    payload = json.dumps(normalized_stages, sort_keys=True)
    cfg = json.dumps(normalized_config, sort_keys=True)
    digest = pipeline_definition_hash(
        target_mode=target_mode,
        stages=normalized_stages,
        config=normalized_config,
    )
    runtime = pipeline_runtime_identity()
    if pipeline_id is None:
        cur = db.execute(
            """INSERT INTO search_pipeline_definitions(
                   name,target_mode,stages_json,config_json,revision,content_hash,
                   pipeline_schema_version,pipeline_catalog_version,
                   pipeline_catalog_hash,created_at,updated_at
               ) VALUES(?,?,?,?,1,?,?,?,?,?,?)""",
            (
                name,
                target_mode,
                payload,
                cfg,
                digest,
                runtime["pipeline_schema_version"],
                runtime["pipeline_catalog_version"],
                runtime["pipeline_catalog_hash"],
                ts,
                ts,
            ),
        )
        db.commit()
        return int(cur.lastrowid)

    current = get_pipeline(db, int(pipeline_id))
    if current is None:
        raise ValueError(f"pipeline #{int(pipeline_id)} not found")
    unchanged = (
        str(current["name"]) == name
        and str(current["target_mode"]) == target_mode
        and str(current["stages_json"]) == payload
        and str(current["config_json"]) == cfg
    )
    if unchanged:
        if (
            int(_row_optional(current, "pipeline_schema_version", 0) or 0)
            != runtime["pipeline_schema_version"]
            or int(_row_optional(current, "pipeline_catalog_version", 0) or 0)
            != runtime["pipeline_catalog_version"]
            or str(_row_optional(current, "pipeline_catalog_hash", "") or "")
            != runtime["pipeline_catalog_hash"]
        ):
            db.execute(
                """UPDATE search_pipeline_definitions
                   SET pipeline_schema_version=?,pipeline_catalog_version=?,
                       pipeline_catalog_hash=?,updated_at=?
                   WHERE id=?""",
                (
                    runtime["pipeline_schema_version"],
                    runtime["pipeline_catalog_version"],
                    runtime["pipeline_catalog_hash"],
                    ts,
                    int(pipeline_id),
                ),
            )
            db.commit()
        return int(pipeline_id)

    revision = max(1, int(current["revision"] or 1)) + 1
    db.execute(
        """UPDATE search_pipeline_definitions
           SET name=?,target_mode=?,stages_json=?,config_json=?,
               revision=?,content_hash=?,pipeline_schema_version=?,
               pipeline_catalog_version=?,pipeline_catalog_hash=?,updated_at=?
           WHERE id=?""",
        (
            name,
            target_mode,
            payload,
            cfg,
            revision,
            digest,
            runtime["pipeline_schema_version"],
            runtime["pipeline_catalog_version"],
            runtime["pipeline_catalog_hash"],
            ts,
            int(pipeline_id),
        ),
    )
    db.commit()
    return int(pipeline_id)


def get_pipeline(db, pipeline_id):
    ensure_pipeline_schema(db)
    return db.execute(
        "SELECT * FROM search_pipeline_definitions WHERE id=?",
        (int(pipeline_id),),
    ).fetchone()


def list_pipelines(db, *, limit=100):
    ensure_pipeline_schema(db)
    return db.execute(
        "SELECT * FROM search_pipeline_definitions ORDER BY updated_at DESC,id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()


def delete_pipeline(db, pipeline_id):
    ensure_pipeline_schema(db)
    db.execute(
        "DELETE FROM search_pipeline_definitions WHERE id=?",
        (int(pipeline_id),),
    )
    db.commit()


def _row_optional(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def pipeline_payload(row):
    if row is None:
        return None
    target_mode = str(row["target_mode"])
    stages = _json(row["stages_json"], [])
    config = _json(row["config_json"], {})
    revision = max(1, int(_row_optional(row, "revision", 1) or 1))
    content_hash = str(_row_optional(row, "content_hash", "") or "")
    if not content_hash:
        content_hash = pipeline_definition_hash(
            target_mode=target_mode,
            stages=stages,
            config=config,
        )
    raw_launch_defaults = config.get("launch_defaults")
    if isinstance(raw_launch_defaults, dict):
        launch_defaults = dict(raw_launch_defaults)
    else:
        launch_defaults = {}
    if "target" not in launch_defaults:
        legacy_target = config.get("target")
        if isinstance(legacy_target, dict):
            launch_defaults["target"] = dict(legacy_target)
    recipe_config = {
        key: value
        for key, value in config.items()
        if key not in {"target", "launch_defaults"}
    }
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "target_mode": target_mode,
        "stages": stages,
        "config": config,
        "recipe_config": recipe_config,
        "launch_defaults": launch_defaults,
        "revision": revision,
        "content_hash": content_hash,
        "pipeline_schema_version": int(
            _row_optional(row, "pipeline_schema_version", 0) or 0
        ),
        "pipeline_catalog_version": int(
            _row_optional(row, "pipeline_catalog_version", 0) or 0
        ),
        "pipeline_catalog_hash": str(
            _row_optional(row, "pipeline_catalog_hash", "") or ""
        ),
    }



def record_pipeline_derivation(
    db,
    *,
    run_id,
    stage_index,
    stage_id,
    parent_candidate_id,
    child_candidate_id,
    transform_kind,
    metadata=None,
):
    """Persist one exact parent -> child Builder transform edge."""
    ensure_pipeline_schema(db)
    db.execute(
        """INSERT INTO search_pipeline_derivations(
               run_id,stage_index,stage_id,parent_candidate_id,child_candidate_id,
               transform_kind,metadata_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(
               run_id,stage_index,parent_candidate_id,child_candidate_id,transform_kind
           ) DO UPDATE SET
               stage_id=excluded.stage_id,
               metadata_json=excluded.metadata_json""",
        (
            int(run_id),
            int(stage_index),
            str(stage_id),
            int(parent_candidate_id),
            int(child_candidate_id),
            str(transform_kind),
            json.dumps(dict(metadata or {}), sort_keys=True),
            now(),
        ),
    )
    db.commit()


def pipeline_derivations(
    db,
    run_id,
    *,
    parent_candidate_id=None,
    child_candidate_id=None,
    stage_index=None,
    limit=10000,
):
    ensure_pipeline_schema(db)
    sql = """SELECT d.*,
                    p.provider_key AS parent_provider_key,
                    p.parameter AS parent_parameter,
                    p.curve_id AS parent_curve_id,
                    c.provider_key AS child_provider_key,
                    c.parameter AS child_parameter,
                    c.curve_id AS child_curve_id
             FROM search_pipeline_derivations d
             JOIN search_pipeline_candidates p ON p.id=d.parent_candidate_id
             JOIN search_pipeline_candidates c ON c.id=d.child_candidate_id
             WHERE d.run_id=?"""
    vals = [int(run_id)]
    if parent_candidate_id is not None:
        sql += " AND d.parent_candidate_id=?"
        vals.append(int(parent_candidate_id))
    if child_candidate_id is not None:
        sql += " AND d.child_candidate_id=?"
        vals.append(int(child_candidate_id))
    if stage_index is not None:
        sql += " AND d.stage_index=?"
        vals.append(int(stage_index))
    sql += " ORDER BY d.stage_index,d.id LIMIT ?"
    vals.append(int(limit))
    return db.execute(sql, vals).fetchall()



def _run_config_campaign_id(db, run_config):
    config = dict(run_config or {})
    value = config.get("campaign_id")
    try:
        campaign_id = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if campaign_id is None:
        return None
    table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_campaigns'"
    ).fetchone()
    if table is None:
        return None
    row = db.execute(
        "SELECT created_at FROM research_campaigns WHERE id=?",
        (campaign_id,),
    ).fetchone()
    if row is None:
        return None
    marker = config.get("campaign_created_at")
    if marker and str(marker) != str(row["created_at"]):
        return None
    return campaign_id


def create_pipeline_run(
    db,
    *,
    pipeline_id=None,
    pipeline_name,
    target_mode,
    target,
    stages,
    run_config=None,
    project_root=None,
    commit=True,
):
    ensure_pipeline_schema(db)
    ts = now()
    frozen_config = dict(run_config or {})
    if pipeline_id is not None:
        source = get_pipeline(db, int(pipeline_id))
        if source is not None:
            source_payload = pipeline_payload(source)
            frozen_config.setdefault("source_pipeline_id", int(source_payload["id"]))
            frozen_config.setdefault(
                "source_pipeline_revision",
                int(source_payload["revision"]),
            )
            frozen_config.setdefault(
                "source_pipeline_hash",
                str(source_payload["content_hash"]),
            )
    campaign_id = _run_config_campaign_id(db, frozen_config)
    runtime = pipeline_runtime_identity()
    manifest = build_pipeline_run_manifest(
        pipeline_name=pipeline_name,
        target_mode=target_mode,
        target=target,
        stages=stages,
        run_config=frozen_config,
        project_root=project_root,
    )
    cur = db.execute(
        """INSERT INTO search_pipeline_runs(
               pipeline_id,pipeline_name,target_mode,target_json,stages_json,
               run_config_json,campaign_id,pipeline_schema_version,
               pipeline_catalog_version,pipeline_catalog_hash,manifest_json,
               status,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)""",
        (
            None if pipeline_id is None else int(pipeline_id),
            str(pipeline_name),
            str(target_mode),
            json.dumps(dict(target or {}), sort_keys=True),
            json.dumps(list(stages), sort_keys=True),
            json.dumps(frozen_config, sort_keys=True),
            campaign_id,
            runtime["pipeline_schema_version"],
            runtime["pipeline_catalog_version"],
            runtime["pipeline_catalog_hash"],
            json.dumps(manifest, sort_keys=True),
            ts,
            ts,
        ),
    )
    run_id = int(cur.lastrowid)
    replace_owned_campaign_link(
        db,
        artifact_kind="pipeline_run",
        artifact_id=run_id,
        campaign_id=campaign_id,
        commit=False,
    )
    if commit:
        db.commit()
    return run_id


def get_pipeline_run(db, run_id):
    ensure_pipeline_schema(db)
    return db.execute(
        "SELECT * FROM search_pipeline_runs WHERE id=?",
        (int(run_id),),
    ).fetchone()


def list_pipeline_runs_readonly(db, *, limit=50):
    """List Pipeline Runs without creating or migrating Pipeline schema."""
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_pipeline_runs'"
    ).fetchone()
    if exists is None:
        return []
    return db.execute(
        "SELECT * FROM search_pipeline_runs ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()


def pipeline_run_status_counts_readonly(db):
    """Return exact Pipeline Run counts without creating Pipeline schema."""
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_pipeline_runs'"
    ).fetchone()
    if exists is None:
        return {}
    rows = db.execute(
        "SELECT status, COUNT(*) AS n FROM search_pipeline_runs GROUP BY status"
    ).fetchall()
    return {str(row["status"]): int(row["n"] or 0) for row in rows}


def list_pipeline_runs(db, *, limit=50):
    ensure_pipeline_schema(db)
    return list_pipeline_runs_readonly(db, limit=limit)


def update_pipeline_run(db, run_id, **fields):
    ensure_pipeline_schema(db)
    allowed = {
        "status", "current_stage_index", "current_stage_id",
        "candidates_total", "candidates_done", "best_lower", "best_curve_id",
        "error", "result_json", "started_at", "finished_at",
    }
    values = {k: v for k, v in fields.items() if k in allowed}
    if not values:
        return
    values["updated_at"] = now()
    cols = ", ".join(f"{key}=?" for key in values)
    db.execute(
        f"UPDATE search_pipeline_runs SET {cols} WHERE id=?",
        [*values.values(), int(run_id)],
    )
    db.commit()


def pipeline_run_payload(row):
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "pipeline_id": row["pipeline_id"],
        "pipeline_name": str(row["pipeline_name"]),
        "target_mode": str(row["target_mode"]),
        "target": _json(row["target_json"], {}),
        "stages": _json(row["stages_json"], []),
        "run_config": _json(row["run_config_json"], {}),
        "campaign_id": _row_optional(row, "campaign_id", None),
        "pipeline_schema_version": int(
            _row_optional(row, "pipeline_schema_version", 0) or 0
        ),
        "pipeline_catalog_version": int(
            _row_optional(row, "pipeline_catalog_version", 0) or 0
        ),
        "pipeline_catalog_hash": str(
            _row_optional(row, "pipeline_catalog_hash", "") or ""
        ),
        "manifest": _json(_row_optional(row, "manifest_json", "{}"), {}),
        "status": str(row["status"]),
    }


def pipeline_run_runtime_drift(row, *, project_root=None):
    """Compare one frozen Run manifest with the current Pipeline runtime."""
    payload = pipeline_run_payload(row)
    current = pipeline_runtime_identity()
    manifest = dict((payload or {}).get("manifest") or {})
    if not manifest:
        return {
            "legacy": True,
            "blocking": [],
            "warnings": ["legacy run has no frozen runtime manifest"],
            "current": current,
            "stored": {},
        }

    blocking = []
    warnings = []
    stored_schema = int(manifest.get("pipeline_schema_version") or 0)
    current_schema = int(current["pipeline_schema_version"])
    if stored_schema > current_schema:
        blocking.append(
            f"run schema {stored_schema} is newer than current schema {current_schema}"
        )
    elif stored_schema and stored_schema != current_schema:
        warnings.append(
            f"pipeline schema changed {stored_schema} → {current_schema}"
        )

    stored_catalog = str(manifest.get("pipeline_catalog_hash") or "")
    current_catalog = str(current["pipeline_catalog_hash"])
    if stored_catalog and stored_catalog != current_catalog:
        warnings.append("Pipeline module catalog fingerprint changed")

    stored_core = str(manifest.get("core_version") or "")
    current_core = str(current["core_version"])
    if stored_core and stored_core != current_core:
        warnings.append(f"Rank Hunter core changed {stored_core} → {current_core}")

    stored_plugins = manifest.get("plugin_runtime")
    if isinstance(stored_plugins, dict):
        current_plugins = _pipeline_plugin_runtime(
            {
                "plugin_id": manifest.get("plugin_id"),
                "plugin_variant": manifest.get("plugin_variant"),
                "feature_plugin_ids": manifest.get("feature_plugin_ids") or [],
            },
            project_root=project_root,
        )

        def by_id(records):
            return {
                str(rec.get("plugin_id")): rec
                for rec in records or []
                if isinstance(rec, dict) and rec.get("plugin_id")
            }

        stored_primary = stored_plugins.get("primary")
        current_primary = current_plugins.get("primary")
        if stored_primary != current_primary:
            plugin_id = (
                (stored_primary or {}).get("plugin_id")
                or (current_primary or {}).get("plugin_id")
                or manifest.get("plugin_id")
                or "primary plugin"
            )
            warnings.append(f"Plugin runtime fingerprint changed: {plugin_id}")

        stored_features = by_id(stored_plugins.get("features"))
        current_features = by_id(current_plugins.get("features"))
        for plugin_id in sorted(set(stored_features) | set(current_features)):
            if stored_features.get(plugin_id) != current_features.get(plugin_id):
                warnings.append(
                    f"Feature plugin runtime fingerprint changed: {plugin_id}"
                )
    elif int(manifest.get("manifest_version") or 0) < RUN_MANIFEST_VERSION:
        warnings.append("legacy run has no frozen plugin runtime fingerprints")

    return {
        "legacy": False,
        "blocking": blocking,
        "warnings": warnings,
        "current": current,
        "stored": manifest,
    }


def upsert_pipeline_candidate(
    db,
    *,
    run_id,
    parameter,
    provider_key="",
    score=None,
    score_provenance=None,
    curve_id=None,
    status="queued",
    current_stage_index=None,
    current_stage_id=None,
    rigorous_lower=0,
    rigorous_upper=None,
    exact_rank=None,
    stage_results=None,
    error=None,
):
    ensure_pipeline_schema(db)
    ts = now()
    db.execute(
        """INSERT INTO search_pipeline_candidates(
               run_id,provider_key,parameter,score,score_provenance_json,curve_id,status,
               current_stage_index,current_stage_id,rigorous_lower,rigorous_upper,
               exact_rank,stage_results_json,error,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,provider_key,parameter) DO UPDATE SET
               score=COALESCE(excluded.score,search_pipeline_candidates.score),
               score_provenance_json=CASE
                   WHEN excluded.score_provenance_json!='{}'
                       THEN excluded.score_provenance_json
                   ELSE search_pipeline_candidates.score_provenance_json
               END,
               curve_id=COALESCE(excluded.curve_id,search_pipeline_candidates.curve_id),
               status=excluded.status,
               current_stage_index=excluded.current_stage_index,
               current_stage_id=excluded.current_stage_id,
               rigorous_lower=excluded.rigorous_lower,
               rigorous_upper=excluded.rigorous_upper,
               exact_rank=excluded.exact_rank,
               stage_results_json=excluded.stage_results_json,
               error=excluded.error,
               updated_at=excluded.updated_at""",
        (
            int(run_id),
            str(provider_key or ""),
            str(parameter),
            None if score is None else float(score),
            json.dumps(dict(score_provenance or {}), sort_keys=True),
            None if curve_id is None else int(curve_id),
            str(status),
            current_stage_index,
            current_stage_id,
            int(rigorous_lower or 0),
            None if rigorous_upper is None else int(rigorous_upper),
            None if exact_rank is None else int(exact_rank),
            json.dumps(dict(stage_results or {}), sort_keys=True),
            error,
            ts,
            ts,
        ),
    )
    db.commit()
    return db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE run_id=? AND provider_key=? AND parameter=?""",
        (int(run_id), str(provider_key or ""), str(parameter)),
    ).fetchone()


def update_pipeline_candidate(db, candidate_id, *, commit=True, **fields):
    ensure_pipeline_schema(db, commit=commit)
    allowed = {
        "score", "score_provenance_json", "curve_id", "status", "current_stage_index", "current_stage_id",
        "rigorous_lower", "rigorous_upper", "exact_rank", "stage_results_json",
        "error",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if "score_provenance_json" in values and not isinstance(
        values["score_provenance_json"], str
    ):
        values["score_provenance_json"] = json.dumps(
            dict(values["score_provenance_json"] or {}),
            sort_keys=True,
        )
    if "stage_results_json" in values and not isinstance(values["stage_results_json"], str):
        values["stage_results_json"] = json.dumps(
            dict(values["stage_results_json"] or {}),
            sort_keys=True,
        )
    if not values:
        return
    values["updated_at"] = now()
    cols = ", ".join(f"{key}=?" for key in values)
    db.execute(
        f"UPDATE search_pipeline_candidates SET {cols} WHERE id=?",
        [*values.values(), int(candidate_id)],
    )
    if commit:
        db.commit()


def get_pipeline_candidate(db, run_id, provider_key, parameter):
    ensure_pipeline_schema(db)
    return db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE run_id=? AND provider_key=? AND parameter=?""",
        (int(run_id), str(provider_key or ""), str(parameter)),
    ).fetchone()


def get_pipeline_point_attempt(
    db,
    *,
    run_id,
    candidate_id,
    stage_index,
    tier,
    center,
    scale,
    height,
):
    """Return one durable Pipeline point-search chart/height attempt."""
    ensure_pipeline_schema(db)
    return db.execute(
        """SELECT * FROM search_pipeline_point_attempts
           WHERE run_id=? AND candidate_id=? AND stage_index=?
             AND tier=? AND center=? AND scale=? AND height=?""",
        (
            int(run_id), int(candidate_id), int(stage_index), str(tier),
            str(center), str(scale), int(height),
        ),
    ).fetchone()


def upsert_pipeline_point_attempt(
    db,
    *,
    run_id,
    candidate_id,
    curve_id,
    stage_index,
    stage_id,
    tier,
    center,
    scale,
    height,
    denominator_low=None,
    denominator_high=None,
    effective_denominator_low=None,
    effective_denominator_high=None,
    status,
    exact_points=0,
    runtime=None,
    timeout_seconds=None,
    retry_policy="manual",
    engine=None,
    error=None,
):
    """Persist one Pipeline-owned point-search chart/height attempt."""
    ensure_pipeline_schema(db)
    ts = now()
    start_count = 1 if str(status) == "running" else 0
    db.execute(
        """INSERT INTO search_pipeline_point_attempts(
               run_id,candidate_id,curve_id,stage_index,stage_id,tier,
               center,scale,height,denominator_low,denominator_high,
               effective_denominator_low,effective_denominator_high,status,
               attempt_count,exact_points,runtime,timeout_seconds,retry_policy,
               engine_json,error,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,candidate_id,stage_index,tier,center,scale,height)
           DO UPDATE SET
               curve_id=excluded.curve_id,
               stage_id=excluded.stage_id,
               denominator_low=excluded.denominator_low,
               denominator_high=excluded.denominator_high,
               effective_denominator_low=excluded.effective_denominator_low,
               effective_denominator_high=excluded.effective_denominator_high,
               status=excluded.status,
               attempt_count=CASE
                   WHEN excluded.status='running'
                   THEN search_pipeline_point_attempts.attempt_count + 1
                   ELSE search_pipeline_point_attempts.attempt_count
               END,
               exact_points=excluded.exact_points,
               runtime=excluded.runtime,
               timeout_seconds=excluded.timeout_seconds,
               retry_policy=excluded.retry_policy,
               engine_json=CASE
                   WHEN excluded.engine_json='{}'
                   THEN search_pipeline_point_attempts.engine_json
                   ELSE excluded.engine_json
               END,
               error=excluded.error,
               updated_at=excluded.updated_at""",
        (
            int(run_id),
            int(candidate_id),
            None if curve_id is None else int(curve_id),
            int(stage_index),
            str(stage_id),
            str(tier),
            str(center),
            str(scale),
            int(height),
            None if denominator_low is None else int(denominator_low),
            None if denominator_high is None else int(denominator_high),
            (
                None
                if effective_denominator_low is None
                else int(effective_denominator_low)
            ),
            (
                None
                if effective_denominator_high is None
                else int(effective_denominator_high)
            ),
            str(status),
            int(start_count),
            int(exact_points or 0),
            None if runtime is None else float(runtime),
            None if timeout_seconds is None else int(timeout_seconds),
            str(retry_policy or "manual"),
            json.dumps(dict(engine or {}), sort_keys=True),
            error,
            ts,
            ts,
        ),
    )
    db.commit()
    return get_pipeline_point_attempt(
        db,
        run_id=run_id,
        candidate_id=candidate_id,
        stage_index=stage_index,
        tier=tier,
        center=center,
        scale=scale,
        height=height,
    )


def pipeline_point_attempts(db, run_id, *, candidate_id=None, stage_index=None):
    """List durable Pipeline point attempts for diagnostics and resume inspection."""
    ensure_pipeline_schema(db)
    where = ["run_id=?"]
    values = [int(run_id)]
    if candidate_id is not None:
        where.append("candidate_id=?")
        values.append(int(candidate_id))
    if stage_index is not None:
        where.append("stage_index=?")
        values.append(int(stage_index))
    return db.execute(
        "SELECT * FROM search_pipeline_point_attempts WHERE "
        + " AND ".join(where)
        + " ORDER BY candidate_id,stage_index,id",
        values,
    ).fetchall()


def get_pipeline_strategy_step(db, *, run_id, candidate_id, stage_index, occurrence_id, step_key):
    ensure_pipeline_schema(db)
    return db.execute(
        """SELECT * FROM search_pipeline_strategy_steps
           WHERE run_id=? AND candidate_id=? AND stage_index=?
             AND occurrence_id=? AND step_key=?""",
        (int(run_id), int(candidate_id), int(stage_index), str(occurrence_id), str(step_key)),
    ).fetchone()


def upsert_pipeline_strategy_step(
    db, *, run_id, candidate_id, curve_id, stage_index, strategy_id,
    occurrence_id, step_index, step_key, step_kind, nested_stage_id,
    status, elapsed_seconds=0.0, wall_budget_seconds=None,
    retry_policy="automatic", result=None, error=None,
):
    ensure_pipeline_schema(db)
    ts = now()
    db.execute(
        """INSERT INTO search_pipeline_strategy_steps(
               run_id,candidate_id,curve_id,stage_index,strategy_id,occurrence_id,
               step_index,step_key,step_kind,nested_stage_id,status,attempt_count,
               elapsed_seconds,wall_budget_seconds,retry_policy,result_json,error,
               created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id,candidate_id,stage_index,occurrence_id,step_key)
           DO UPDATE SET
               curve_id=excluded.curve_id,
               strategy_id=excluded.strategy_id,
               step_index=excluded.step_index,
               step_kind=excluded.step_kind,
               nested_stage_id=excluded.nested_stage_id,
               status=excluded.status,
               attempt_count=CASE WHEN excluded.status='running'
                   THEN search_pipeline_strategy_steps.attempt_count + 1
                   ELSE search_pipeline_strategy_steps.attempt_count END,
               elapsed_seconds=CASE WHEN excluded.status='running'
                   THEN search_pipeline_strategy_steps.elapsed_seconds
                   ELSE search_pipeline_strategy_steps.elapsed_seconds + excluded.elapsed_seconds END,
               wall_budget_seconds=excluded.wall_budget_seconds,
               retry_policy=excluded.retry_policy,
               result_json=CASE WHEN excluded.status='running' AND excluded.result_json='{}'
                   THEN search_pipeline_strategy_steps.result_json ELSE excluded.result_json END,
               error=excluded.error,
               updated_at=excluded.updated_at""",
        (
            int(run_id), int(candidate_id), None if curve_id is None else int(curve_id),
            int(stage_index), str(strategy_id), str(occurrence_id), int(step_index),
            str(step_key), str(step_kind), str(nested_stage_id), str(status),
            1 if str(status) == "running" else 0, float(elapsed_seconds or 0.0),
            None if wall_budget_seconds is None else int(wall_budget_seconds),
            str(retry_policy or "automatic"),
            json.dumps(dict(result or {}), sort_keys=True, default=str),
            error, ts, ts,
        ),
    )
    db.commit()
    return get_pipeline_strategy_step(
        db, run_id=run_id, candidate_id=candidate_id, stage_index=stage_index,
        occurrence_id=occurrence_id, step_key=step_key,
    )


def pipeline_strategy_steps(db, run_id, *, candidate_id, stage_index, occurrence_id):
    ensure_pipeline_schema(db)
    return db.execute(
        """SELECT * FROM search_pipeline_strategy_steps
           WHERE run_id=? AND candidate_id=? AND stage_index=? AND occurrence_id=?
           ORDER BY step_index,id""",
        (int(run_id), int(candidate_id), int(stage_index), str(occurrence_id)),
    ).fetchall()


def pipeline_candidates(db, run_id, *, limit=1000):
    ensure_pipeline_schema(db)
    return db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE run_id=?
           ORDER BY rigorous_lower DESC,COALESCE(score,-1e99) DESC,id ASC
           LIMIT ?""",
        (int(run_id), int(limit)),
    ).fetchall()


def _torsion_verified_from_stage_results(raw):
    data = _json(raw, {})
    for rec in data.values():
        if not isinstance(rec, dict) or rec.get("stage_id") != "exact_torsion":
            continue
        result = rec.get("result") or {}
        return isinstance(result, dict) and str(result.get("status") or "") == "completed"
    return False


def run_progress(db, run_id):
    ensure_pipeline_schema(db)
    run = get_pipeline_run(db, int(run_id))
    target_mode = str(run["target_mode"] if run is not None else "")
    rows = db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE run_id=? ORDER BY id ASC""",
        (int(run_id),),
    ).fetchall()
    terminal = {
        "completed", "filtered", "constraint_filtered", "torsion_mismatch",
        "goal_filtered", "funnel_pruned", "transformed", "inconclusive", "error",
    }
    ineligible = {
        "filtered", "constraint_filtered", "torsion_mismatch",
        "inconclusive", "error",
    }

    eligible = []
    for row in rows:
        if str(row["status"] or "") in ineligible:
            continue
        if target_mode == "torsion" and not _torsion_verified_from_stage_results(
            row["stage_results_json"]
        ):
            continue
        eligible.append(row)

    best = None
    if eligible:
        best = sorted(
            eligible,
            key=lambda row: (
                -int(row["rigorous_lower"] or 0),
                -float(row["score"] if row["score"] is not None else -1e99),
                int(row["id"]),
            ),
        )[0]
    return {
        "total": len(rows),
        "done": sum(str(row["status"] or "") in terminal for row in rows),
        "best_lower": 0 if best is None else int(best["rigorous_lower"] or 0),
        "best_curve_id": (
            None
            if best is None or best["curve_id"] is None
            else int(best["curve_id"])
        ),
    }
