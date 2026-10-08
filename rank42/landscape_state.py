"""Frozen population and saved-analysis state for Analysis -> Landscape.

R5 defines reproducible cohort/query semantics underneath the existing Landscape
surface. It does not own scientific truth and never promotes rank/arithmetic
evidence.
"""
from __future__ import annotations

import hashlib
import json

from rank42 import __version__ as CORE_VERSION
from rank42.candidate_ranking_state import (
    pipeline_candidate_ranking_state,
    pool_candidate_ranking_state,
)
from rank42.curve_research_state import curve_research_state_map
from rank42.db import now
from rank42.landscape_dimensions import (
    LANDSCAPE_DIMENSION_REGISTRY_HASH,
    LANDSCAPE_DIMENSION_REGISTRY_VERSION,
    get_landscape_dimension,
    normalize_landscape_feature,
)
from rank42.manage_store import campaign_curve_ids_readonly


LANDSCAPE_COHORT_VERSION = 1
LANDSCAPE_ANALYSIS_SCHEMA_VERSION = 1
LANDSCAPE_SOURCE_KINDS = (
    "campaign",
    "family",
    "candidate_pool",
    "pipeline_run",
    "explicit_curves",
)

LANDSCAPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_landscape_analyses(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    analysis_schema_version INTEGER NOT NULL,
    primary_cohort_json TEXT NOT NULL,
    comparison_cohort_json TEXT,
    dimensions_json TEXT NOT NULL,
    feature_definitions_json TEXT NOT NULL DEFAULT '[]',
    dimension_registry_version INTEGER NOT NULL,
    dimension_registry_hash TEXT NOT NULL,
    core_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_landscape_analyses_created
    ON research_landscape_analyses(created_at DESC, id DESC);
"""


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _json(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def ensure_landscape_schema(db, *, commit=True):
    table = _table_exists(db, "research_landscape_analyses")
    index = db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='index' AND name='idx_research_landscape_analyses_created'"""
    ).fetchone() is not None
    changed = not (table and index)
    if changed:
        db.executescript(LANDSCAPE_SCHEMA)
        if commit:
            db.commit()
    return changed


def _normalize_filters(filters):
    raw = dict(filters or {})
    allowed = {
        "minimum_rigorous_lower",
        "candidate_status",
        "curve_status",
        "only_with_curve",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            "unsupported Landscape cohort filter(s): " + ", ".join(unknown)
        )

    out = {}
    if "minimum_rigorous_lower" in raw:
        value = int(raw["minimum_rigorous_lower"])
        if value < 0:
            raise ValueError("minimum_rigorous_lower must be nonnegative")
        out["minimum_rigorous_lower"] = value

    for key in ("candidate_status", "curve_status"):
        if key not in raw:
            continue
        value = raw[key]
        values = [value] if isinstance(value, str) else list(value or [])
        normalized = sorted({str(item).strip() for item in values if str(item).strip()})
        if not normalized:
            raise ValueError(f"{key} must contain at least one status")
        out[key] = normalized

    if "only_with_curve" in raw:
        out["only_with_curve"] = bool(raw["only_with_curve"])
    return out


def _curve_member(row):
    row = dict(row)
    return {
        "kind": "curve",
        "curve_id": int(row["id"]),
        "family": str(row["family"]),
        "parameter": str(row["parameter"]),
        "status": row.get("status"),
        "plugin_id": row.get("plugin_id"),
        "plugin_version": row.get("plugin_version"),
        "family_spec": row.get("family_spec"),
        "family_sha256": row.get("family_sha256"),
        "native_family_key": row.get("native_family_key"),
        "native_family_spec": row.get("native_family_spec"),
        "native_parameter": row.get("native_parameter"),
        "chart_id": row.get("chart_id"),
        "chart_parameter": row.get("chart_parameter"),
        "chart_map_fingerprint": row.get("chart_map_fingerprint"),
    }


def _pool_candidate_member(row):
    row = dict(row)
    ranking = pool_candidate_ranking_state(row)
    return {
        "kind": "candidate_pool",
        "candidate_id": int(row["id"]),
        "pool_id": int(row["pool_id"]),
        "curve_id": None if row.get("curve_id") is None else int(row["curve_id"]),
        "parameter": str(row["parameter"]),
        "status": str(row["status"]),
        "native_family_key": row.get("native_family_key"),
        "native_family_spec": row.get("native_family_spec"),
        "native_parameter": row.get("native_parameter"),
        "chart_id": row.get("chart_id"),
        "chart_parameter": row.get("chart_parameter"),
        "chart_map_fingerprint": row.get("chart_map_fingerprint"),
        "ranking": _jsonable(ranking),
    }


def _pipeline_candidate_member(row, *, curve_identity=None):
    row = dict(row)
    ranking = pipeline_candidate_ranking_state(row)
    return {
        "kind": "pipeline_candidate",
        "candidate_id": int(row["id"]),
        "run_id": int(row["run_id"]),
        "curve_id": None if row.get("curve_id") is None else int(row["curve_id"]),
        "parameter": str(row["parameter"]),
        "provider_key": str(row.get("provider_key") or ""),
        "status": str(row["status"]),
        "stage_id": row.get("current_stage_id"),
        "stage_index": row.get("current_stage_index"),
        "ranking": _jsonable(ranking),
        "curve_identity": None if curve_identity is None else _jsonable(curve_identity),
    }


def _curve_rows_by_ids(db, curve_ids):
    ids = sorted({int(value) for value in curve_ids})
    if not ids:
        return []
    rows = []
    for offset in range(0, len(ids), 800):
        chunk = ids[offset:offset + 800]
        marks = ",".join("?" for _ in chunk)
        rows.extend(
            db.execute(
                f"SELECT * FROM curves WHERE id IN ({marks}) ORDER BY id",
                chunk,
            ).fetchall()
        )
    rows.sort(key=lambda row: int(row["id"]))
    return rows


def _family_source(db, source):
    source = dict(source or {})
    clauses = []
    values = []

    if source.get("native_family_key"):
        clauses.append("native_family_key=?")
        values.append(str(source["native_family_key"]))
    elif source.get("family_spec"):
        clauses.append("family_spec=?")
        values.append(str(source["family_spec"]))
    elif source.get("family"):
        clauses.append("family=?")
        values.append(str(source["family"]))
    else:
        raise ValueError(
            "family cohort requires native_family_key, family_spec, or family"
        )

    for key in ("plugin_id", "family_sha256", "chart_id"):
        if source.get(key) is not None:
            clauses.append(f"{key}=?")
            values.append(str(source[key]))

    rows = db.execute(
        "SELECT * FROM curves WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id",
        values,
    ).fetchall()
    normalized = {
        key: source[key]
        for key in (
            "native_family_key",
            "family_spec",
            "family",
            "plugin_id",
            "family_sha256",
            "chart_id",
        )
        if source.get(key) is not None
    }
    normalized["selection_policy"] = "frozen_retained_curve_membership"
    return normalized, [_curve_member(row) for row in rows]


def _explicit_source(db, source):
    source = dict(source or {})
    raw_ids = source.get("curve_ids")
    if not isinstance(raw_ids, (list, tuple, set)) or not raw_ids:
        raise ValueError("explicit_curves cohort requires non-empty curve_ids")
    ids = sorted({int(value) for value in raw_ids})
    rows = _curve_rows_by_ids(db, ids)
    found = {int(row["id"]) for row in rows}
    missing = sorted(set(ids) - found)
    if missing:
        raise ValueError(
            "Landscape explicit cohort references missing curve id(s): "
            + ", ".join(str(value) for value in missing)
        )
    return {
        "curve_ids": ids,
        "selection_policy": "explicit_frozen_curve_ids",
    }, [_curve_member(row) for row in rows]


def _campaign_source(db, source):
    source = dict(source or {})
    if source.get("campaign_id") is None:
        raise ValueError("campaign cohort requires campaign_id")
    campaign_id = int(source["campaign_id"])
    campaign = db.execute(
        "SELECT * FROM research_campaigns WHERE id=?",
        (campaign_id,),
    ).fetchone()
    if campaign is None:
        raise ValueError(f"campaign #{campaign_id} not found")
    curve_ids = campaign_curve_ids_readonly(db, campaign_id)
    rows = _curve_rows_by_ids(db, curve_ids)
    return {
        "campaign_id": campaign_id,
        "campaign_name": str(campaign["name"]),
        "campaign_created_at": str(campaign["created_at"]),
        "campaign_updated_at_at_freeze": str(campaign["updated_at"]),
        "selection_policy": "campaign_traceable_curves_frozen_at_creation",
    }, [_curve_member(row) for row in rows]


def _candidate_pool_source(db, source):
    source = dict(source or {})
    if source.get("pool_id") is None:
        raise ValueError("candidate_pool cohort requires pool_id")
    pool_id = int(source["pool_id"])
    pool = db.execute(
        "SELECT * FROM candidate_pools WHERE id=?",
        (pool_id,),
    ).fetchone()
    if pool is None:
        raise ValueError(f"candidate pool #{pool_id} not found")
    rows = db.execute(
        """SELECT c.*, p.plugin_id, p.plugin_version, p.family_spec,
                  p.family_sha256, p.adapter_sha256, p.plugin_manifest_sha256
           FROM candidates c
           JOIN candidate_pools p ON p.id=c.pool_id
           WHERE c.pool_id=?
           ORDER BY c.rank_order,c.id""",
        (pool_id,),
    ).fetchall()
    snapshot = {
        "pool_id": pool_id,
        "pool_name": str(pool["name"]),
        "plugin_id": pool["plugin_id"],
        "plugin_version": pool["plugin_version"],
        "family_spec": pool["family_spec"],
        "family_sha256": pool["family_sha256"],
        "adapter_sha256": pool["adapter_sha256"],
        "plugin_manifest_sha256": pool["plugin_manifest_sha256"],
        "generation": _json(pool["generation_json"], {}),
        "candidate_count_at_freeze": int(pool["candidate_count"] or 0),
        "pool_created_at": pool["created_at"],
        "pool_updated_at_at_freeze": pool["updated_at"],
        "selection_policy": "candidate_pool_rows_frozen_at_creation",
    }
    return _jsonable(snapshot), [_pool_candidate_member(row) for row in rows]


def _pipeline_run_source(db, source):
    source = dict(source or {})
    if source.get("run_id") is None:
        raise ValueError("pipeline_run cohort requires run_id")
    run_id = int(source["run_id"])
    run = db.execute(
        "SELECT * FROM search_pipeline_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValueError(f"pipeline run #{run_id} not found")
    rows = db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE run_id=? ORDER BY id""",
        (run_id,),
    ).fetchall()
    curve_rows = _curve_rows_by_ids(
        db,
        [
            int(row["curve_id"])
            for row in rows
            if row["curve_id"] is not None
        ],
    )
    curve_identity = {
        int(row["id"]): _curve_member(row)
        for row in curve_rows
    }
    snapshot = {
        "run_id": run_id,
        "pipeline_id": run["pipeline_id"],
        "pipeline_name": str(run["pipeline_name"]),
        "campaign_id": run["campaign_id"],
        "target_mode": str(run["target_mode"]),
        "target": _json(run["target_json"], {}),
        "stages": _json(run["stages_json"], []),
        "run_config": _json(run["run_config_json"], {}),
        "manifest": _json(run["manifest_json"], {}),
        "status_at_freeze": str(run["status"]),
        "run_created_at": run["created_at"],
        "run_updated_at_at_freeze": run["updated_at"],
        "selection_policy": "pipeline_candidate_rows_frozen_at_creation",
    }
    return _jsonable(snapshot), [
        _pipeline_candidate_member(
            row,
            curve_identity=(
                curve_identity.get(int(row["curve_id"]))
                if row["curve_id"] is not None
                else None
            ),
        )
        for row in rows
    ]


def _apply_filters(db, members, filters):
    members = list(members)
    if filters.get("candidate_status"):
        allowed = set(filters["candidate_status"])
        members = [
            member
            for member in members
            if member["kind"] == "curve" or str(member.get("status")) in allowed
        ]

    if filters.get("curve_status"):
        allowed = set(filters["curve_status"])
        curve_ids = {
            int(member["curve_id"])
            for member in members
            if member.get("curve_id") is not None
        }
        status_by_curve = {}
        if curve_ids:
            for row in _curve_rows_by_ids(db, curve_ids):
                status_by_curve[int(row["id"])] = str(row["status"])
        members = [
            member
            for member in members
            if member.get("curve_id") is not None
            and status_by_curve.get(int(member["curve_id"])) in allowed
        ]

    if filters.get("only_with_curve"):
        members = [
            member for member in members
            if member.get("curve_id") is not None
        ]

    minimum = filters.get("minimum_rigorous_lower")
    if minimum is not None:
        curve_ids = {
            int(member["curve_id"])
            for member in members
            if member.get("curve_id") is not None
        }
        states = (
            curve_research_state_map(db, curve_ids=curve_ids)
            if curve_ids
            else {}
        )
        members = [
            member
            for member in members
            if member.get("curve_id") is not None
            and int(
                states.get(int(member["curve_id"]), {}).get("rigorous_lower", -1)
            ) >= int(minimum)
        ]
    return members


def _cohort_hash_payload(cohort):
    return {
        "cohort_version": int(cohort["cohort_version"]),
        "source_kind": str(cohort["source_kind"]),
        "source": cohort["source"],
        "filters": cohort["filters"],
        "members": cohort["members"],
        "curve_ids": cohort.get("curve_ids", []),
        "candidate_refs": cohort.get("candidate_refs", []),
        "member_count": int(cohort.get("member_count", len(cohort["members"]))),
        "dimension_registry_version": int(cohort["dimension_registry_version"]),
        "dimension_registry_hash": str(cohort["dimension_registry_hash"]),
        "core_version": str(cohort.get("core_version") or ""),
    }


def _cohort_hash(cohort):
    return hashlib.sha256(
        _canonical_json(_cohort_hash_payload(cohort)).encode("utf-8")
    ).hexdigest()


def build_landscape_cohort(db, *, source_kind, source, filters=None):
    """Freeze one reproducible Landscape population without mutating source state."""
    kind = str(source_kind or "").strip()
    if kind not in LANDSCAPE_SOURCE_KINDS:
        raise ValueError(f"unsupported Landscape cohort source {kind!r}")
    normalized_filters = _normalize_filters(filters)
    if (
        "candidate_status" in normalized_filters
        and kind not in {"candidate_pool", "pipeline_run"}
    ):
        raise ValueError(
            "candidate_status filter requires candidate_pool or pipeline_run source"
        )

    if kind == "campaign":
        source_snapshot, members = _campaign_source(db, source)
    elif kind == "family":
        source_snapshot, members = _family_source(db, source)
    elif kind == "candidate_pool":
        source_snapshot, members = _candidate_pool_source(db, source)
    elif kind == "pipeline_run":
        source_snapshot, members = _pipeline_run_source(db, source)
    else:
        source_snapshot, members = _explicit_source(db, source)

    members = _apply_filters(db, members, normalized_filters)
    curve_ids = sorted({
        int(member["curve_id"])
        for member in members
        if member.get("curve_id") is not None
    })
    candidate_refs = [
        {
            "kind": member["kind"],
            "candidate_id": int(member["candidate_id"]),
            **(
                {"pool_id": int(member["pool_id"])}
                if member.get("pool_id") is not None
                else {}
            ),
            **(
                {"run_id": int(member["run_id"])}
                if member.get("run_id") is not None
                else {}
            ),
        }
        for member in members
        if member.get("candidate_id") is not None
    ]

    cohort = {
        "cohort_version": LANDSCAPE_COHORT_VERSION,
        "source_kind": kind,
        "source": _jsonable(source_snapshot),
        "filters": normalized_filters,
        "members": _jsonable(members),
        "curve_ids": curve_ids,
        "candidate_refs": candidate_refs,
        "member_count": len(members),
        "dimension_registry_version": LANDSCAPE_DIMENSION_REGISTRY_VERSION,
        "dimension_registry_hash": LANDSCAPE_DIMENSION_REGISTRY_HASH,
        "core_version": str(CORE_VERSION),
        "created_at": now(),
    }
    cohort["member_hash"] = _cohort_hash(cohort)
    return cohort


def validate_landscape_cohort(cohort):
    if not isinstance(cohort, dict):
        raise ValueError("Landscape cohort must be an object")
    required = {
        "cohort_version",
        "source_kind",
        "source",
        "filters",
        "members",
        "member_hash",
        "dimension_registry_version",
        "dimension_registry_hash",
    }
    missing = sorted(required - set(cohort))
    if missing:
        raise ValueError(
            "Landscape cohort missing field(s): " + ", ".join(missing)
        )
    if int(cohort["cohort_version"]) != LANDSCAPE_COHORT_VERSION:
        raise ValueError(
            f"unsupported Landscape cohort version {cohort['cohort_version']!r}"
        )
    if str(cohort["source_kind"]) not in LANDSCAPE_SOURCE_KINDS:
        raise ValueError(f"unsupported Landscape cohort source {cohort['source_kind']!r}")
    expected = _cohort_hash(cohort)
    if str(cohort["member_hash"]) != expected:
        raise ValueError("Landscape cohort membership hash mismatch")
    return _jsonable(dict(cohort))


def save_landscape_analysis(
    db,
    *,
    name,
    primary_cohort,
    dimension_ids,
    comparison_cohort=None,
    feature_definitions=None,
):
    """Persist immutable analysis configuration and frozen cohort membership.

    This writes only research-analysis configuration. It does not update curves,
    points, rank evidence, arithmetic, candidates, or Pipeline scientific state.
    """
    label = str(name or "").strip()
    if not label:
        raise ValueError("Landscape analysis name cannot be blank")

    primary = validate_landscape_cohort(primary_cohort)
    comparison = (
        None
        if comparison_cohort is None
        else validate_landscape_cohort(comparison_cohort)
    )

    ids = [str(value) for value in (dimension_ids or [])]
    if not ids:
        raise ValueError("Landscape analysis requires at least one dimension")
    if len(ids) != len(set(ids)):
        raise ValueError("Landscape analysis dimension ids must be unique")
    dimensions = [get_landscape_dimension(value) for value in ids]
    features = [
        normalize_landscape_feature(spec)
        for spec in (feature_definitions or [])
    ]

    ensure_landscape_schema(db, commit=False)
    ts = now()
    cur = db.execute(
        """INSERT INTO research_landscape_analyses(
               name,analysis_schema_version,primary_cohort_json,
               comparison_cohort_json,dimensions_json,feature_definitions_json,
               dimension_registry_version,dimension_registry_hash,
               core_version,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            label,
            LANDSCAPE_ANALYSIS_SCHEMA_VERSION,
            _canonical_json(primary),
            None if comparison is None else _canonical_json(comparison),
            _canonical_json(dimensions),
            _canonical_json(features),
            LANDSCAPE_DIMENSION_REGISTRY_VERSION,
            LANDSCAPE_DIMENSION_REGISTRY_HASH,
            str(CORE_VERSION),
            ts,
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def get_landscape_analysis(db, analysis_id):
    if not _table_exists(db, "research_landscape_analyses"):
        return None
    row = db.execute(
        "SELECT * FROM research_landscape_analyses WHERE id=?",
        (int(analysis_id),),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "analysis_schema_version": int(row["analysis_schema_version"]),
        "primary_cohort": _json(row["primary_cohort_json"], {}),
        "comparison_cohort": _json(row["comparison_cohort_json"], None),
        "dimensions": _json(row["dimensions_json"], []),
        "feature_definitions": _json(row["feature_definitions_json"], []),
        "dimension_registry_version": int(row["dimension_registry_version"]),
        "dimension_registry_hash": str(row["dimension_registry_hash"]),
        "core_version": str(row["core_version"]),
        "created_at": str(row["created_at"]),
    }


def list_landscape_analyses(db, *, limit=100):
    if not _table_exists(db, "research_landscape_analyses"):
        return []
    rows = db.execute(
        """SELECT id,name,analysis_schema_version,dimension_registry_version,
                  dimension_registry_hash,core_version,created_at
           FROM research_landscape_analyses
           ORDER BY id DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [_jsonable(dict(row)) for row in rows]
