"""Searchable external projections for researcher Libraries.

Projection databases live beside researcher Library manifests under
.rank42-libraries. They are derived external state only: building or querying
one never writes rank42.db or promotes claims into Curves, Points, Candidates,
or proof evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from rank42.research_library import (
    LIBRARY_MAPPING_ROLES,
    library_dir,
    load_research_library,
    research_library_mapping_draft,
)
from rank42.research_library_preview import structured_research_artifact_rows


PROJECTION_SCHEMA_VERSION = 1
DEFAULT_MAX_PROJECTION_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_PROJECTION_ROWS = 100_000

_SEARCHABLE_ROLES = tuple(LIBRARY_MAPPING_ROLES)


def _now():
    return datetime.now(timezone.utc).isoformat()


def research_projection_path(project_root, library_id):
    return library_dir(project_root, library_id) / "projection.sqlite3"


def _artifact_record(manifest, artifact_id):
    wanted = str(artifact_id)
    for artifact in manifest.get("artifacts") or []:
        if str(artifact.get("id") or "") == wanted:
            return artifact
    raise KeyError(f"research library artifact {artifact_id!r} not found")


def _mapping_fingerprint(draft):
    canonical = {
        "parser": str(draft.get("parser") or ""),
        "columns": [str(value) for value in draft.get("columns") or []],
        "roles": {
            str(key): str(value)
            for key, value in sorted(dict(draft.get("roles") or {}).items())
        },
    }
    raw = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), canonical


def _connect(path, *, readonly=False):
    path = Path(path)
    if readonly:
        db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def _ensure_schema(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS projection_meta(
            artifact_id TEXT PRIMARY KEY,
            artifact_sha256 TEXT NOT NULL,
            parser TEXT NOT NULL,
            mapping_sha256 TEXT NOT NULL,
            mapping_json TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            built_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS records(
            artifact_id TEXT NOT NULL,
            record_index INTEGER NOT NULL,
            parameter TEXT,
            family_label TEXT,
            curve_label TEXT,
            a1 TEXT,
            a2 TEXT,
            a3 TEXT,
            a4 TEXT,
            a6 TEXT,
            x TEXT,
            y TEXT,
            rank_claim TEXT,
            rank_lower_claim TEXT,
            rank_upper_claim TEXT,
            exact_rank_claim TEXT,
            source TEXT,
            notes TEXT,
            mapped_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            search_text TEXT NOT NULL,
            PRIMARY KEY(artifact_id, record_index)
        );

        CREATE INDEX IF NOT EXISTS idx_projection_parameter
            ON records(parameter);
        CREATE INDEX IF NOT EXISTS idx_projection_family
            ON records(family_label);
        CREATE INDEX IF NOT EXISTS idx_projection_curve
            ON records(curve_label);
        CREATE INDEX IF NOT EXISTS idx_projection_artifact
            ON records(artifact_id);
        """
    )


def _text_value(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _mapped_row(raw_row, roles):
    mapped = {}
    for source_column, role in roles.items():
        mapped[str(role)] = raw_row.get(source_column)
    return mapped


def build_research_projection(
    project_root,
    library_id,
    artifact_id,
    *,
    max_bytes=DEFAULT_MAX_PROJECTION_BYTES,
    max_rows=DEFAULT_MAX_PROJECTION_ROWS,
):
    """Rebuild one artifact's searchable external projection transactionally."""

    manifest = load_research_library(project_root, library_id)
    artifact = _artifact_record(manifest, artifact_id)
    draft = research_library_mapping_draft(project_root, library_id, artifact_id)
    if not draft:
        raise ValueError("artifact has no saved field-mapping draft")

    roles = dict(draft.get("roles") or {})
    if not roles:
        raise ValueError("mapping draft has no mapped fields")

    mapping_sha256, mapping_canonical = _mapping_fingerprint(draft)
    parsed = structured_research_artifact_rows(
        project_root,
        library_id,
        artifact_id,
        row_limit=int(max_rows) + 1,
        max_bytes=int(max_bytes),
    )
    parser = str(parsed.get("parser") or "")
    if parser != str(draft.get("parser") or ""):
        raise ValueError(
            f"mapping parser {draft.get('parser')!r} does not match artifact parser {parser!r}"
        )

    parse_errors = list((parsed.get("meta") or {}).get("parse_errors") or [])
    if parse_errors:
        raise ValueError(
            "projection refused malformed structured input: "
            + "; ".join(str(error) for error in parse_errors[:3])
        )

    rows = list(parsed.get("rows") or [])
    if len(rows) > int(max_rows):
        raise ValueError(
            f"artifact exceeds projection row limit {int(max_rows):,}"
        )

    parsed_columns = {str(column) for column in parsed.get("columns") or []}
    missing = [
        str(column)
        for column in draft.get("columns") or []
        if str(column) not in parsed_columns
    ]
    if missing:
        raise ValueError(
            "mapping draft references columns unavailable to the current parser: "
            + ", ".join(missing)
        )

    path = research_projection_path(project_root, library_id)
    db = _connect(path, readonly=False)
    try:
        _ensure_schema(db)
        db.execute("BEGIN")
        db.execute("DELETE FROM records WHERE artifact_id=?", (str(artifact_id),))
        db.execute("DELETE FROM projection_meta WHERE artifact_id=?", (str(artifact_id),))

        insert_sql = """
            INSERT INTO records(
                artifact_id,record_index,
                parameter,family_label,curve_label,
                a1,a2,a3,a4,a6,x,y,
                rank_claim,rank_lower_claim,rank_upper_claim,exact_rank_claim,
                source,notes,mapped_json,raw_json,search_text
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        for record_index, raw_row in enumerate(rows, 1):
            if not isinstance(raw_row, dict):
                raise ValueError(
                    f"structured parser emitted non-object row at record {record_index}"
                )
            mapped = _mapped_row(raw_row, roles)
            searchable = {
                role: _text_value(mapped.get(role))
                for role in _SEARCHABLE_ROLES
            }
            search_text = " ".join(
                value
                for value in searchable.values()
                if value not in (None, "")
            )
            db.execute(
                insert_sql,
                (
                    str(artifact_id),
                    int(record_index),
                    searchable.get("parameter"),
                    searchable.get("family_label"),
                    searchable.get("curve_label"),
                    searchable.get("a1"),
                    searchable.get("a2"),
                    searchable.get("a3"),
                    searchable.get("a4"),
                    searchable.get("a6"),
                    searchable.get("x"),
                    searchable.get("y"),
                    searchable.get("rank_claim"),
                    searchable.get("rank_lower_claim"),
                    searchable.get("rank_upper_claim"),
                    searchable.get("exact_rank_claim"),
                    searchable.get("source"),
                    searchable.get("notes"),
                    json.dumps(mapped, sort_keys=True, ensure_ascii=False),
                    json.dumps(raw_row, sort_keys=True, ensure_ascii=False),
                    search_text,
                ),
            )

        built_at = _now()
        db.execute(
            """
            INSERT INTO projection_meta(
                artifact_id,artifact_sha256,parser,mapping_sha256,mapping_json,
                record_count,built_at,schema_version
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                str(artifact_id),
                str(artifact.get("sha256") or ""),
                parser,
                mapping_sha256,
                json.dumps(
                    mapping_canonical,
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                len(rows),
                built_at,
                PROJECTION_SCHEMA_VERSION,
            ),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "library_id": str(library_id),
        "artifact_id": str(artifact_id),
        "path": str(path),
        "record_count": len(rows),
        "artifact_sha256": str(artifact.get("sha256") or ""),
        "mapping_sha256": mapping_sha256,
        "parser": parser,
        "built_at": built_at,
    }


def research_projection_status(project_root, library_id, artifact_id):
    """Return current/stale status for one artifact's derived projection."""

    path = research_projection_path(project_root, library_id)
    if not path.is_file():
        return {
            "exists": False,
            "stale": False,
            "path": str(path),
            "record_count": 0,
        }

    try:
        manifest = load_research_library(project_root, library_id)
        artifact = _artifact_record(manifest, artifact_id)
        draft = research_library_mapping_draft(
            project_root,
            library_id,
            artifact_id,
        )
        current_mapping_sha = (
            _mapping_fingerprint(draft)[0]
            if draft
            else None
        )

        db = _connect(path, readonly=True)
        try:
            row = db.execute(
                "SELECT * FROM projection_meta WHERE artifact_id=?",
                (str(artifact_id),),
            ).fetchone()
        finally:
            db.close()
    except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
        return {
            "exists": False,
            "stale": True,
            "path": str(path),
            "record_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if row is None:
        return {
            "exists": False,
            "stale": False,
            "path": str(path),
            "record_count": 0,
        }

    stale = (
        str(row["artifact_sha256"]) != str(artifact.get("sha256") or "")
        or current_mapping_sha is None
        or str(row["mapping_sha256"]) != str(current_mapping_sha)
        or int(row["schema_version"]) != PROJECTION_SCHEMA_VERSION
    )
    return {
        "exists": True,
        "stale": bool(stale),
        "path": str(path),
        "record_count": int(row["record_count"] or 0),
        "built_at": str(row["built_at"]),
        "parser": str(row["parser"]),
        "artifact_sha256": str(row["artifact_sha256"]),
        "mapping_sha256": str(row["mapping_sha256"]),
    }


def research_projection_inventory(project_root, library_id):
    """Describe every artifact's mapping/projection state for Library browsing."""

    manifest = load_research_library(project_root, library_id)
    drafts = dict(manifest.get("mapping_drafts") or {})
    rows = []
    for artifact in manifest.get("artifacts") or []:
        artifact_id = str(artifact.get("id") or "")
        draft = drafts.get(artifact_id)
        roles = sorted(
            {
                str(role)
                for role in dict((draft or {}).get("roles") or {}).values()
                if role
            }
        )
        status = research_projection_status(
            project_root,
            library_id,
            artifact_id,
        )
        if status.get("exists") and status.get("stale"):
            projection_state = "stale"
        elif status.get("exists"):
            projection_state = "current"
        elif draft and roles:
            projection_state = "mapped_not_built"
        elif draft:
            projection_state = "mapped_empty"
        else:
            projection_state = "unmapped"

        rows.append(
            {
                "artifact_id": artifact_id,
                "original_name": str(artifact.get("original_name") or ""),
                "category": str(artifact.get("category") or "unknown_raw"),
                "media_type": str(artifact.get("media_type") or ""),
                "size_bytes": int(artifact.get("size_bytes") or 0),
                "sha256": str(artifact.get("sha256") or ""),
                "mapping_roles": roles,
                "projection_state": projection_state,
                "record_count": int(status.get("record_count") or 0),
                "built_at": status.get("built_at"),
                "parser": status.get("parser") or (
                    str((draft or {}).get("parser") or "") or None
                ),
                "projection_error": status.get("error"),
            }
        )
    return rows


def _projection_filters(
    *,
    artifact_id=None,
    artifact_ids=None,
    query=None,
    roles=None,
):
    where = []
    params = []

    if artifact_id is not None and artifact_ids is not None:
        raise ValueError("use artifact_id or artifact_ids, not both")
    if artifact_id is not None:
        where.append("artifact_id=?")
        params.append(str(artifact_id))
    elif artifact_ids is not None:
        ids = [str(value) for value in artifact_ids]
        if not ids:
            return ["0=1"], []
        marks = ",".join("?" for _ in ids)
        where.append(f"artifact_id IN ({marks})")
        params.extend(ids)

    if query:
        where.append("search_text LIKE ?")
        params.append(f"%{str(query)}%")

    if roles:
        normalized = []
        for role in roles:
            role = str(role)
            if role not in _SEARCHABLE_ROLES:
                raise ValueError(f"unsupported projection role filter {role!r}")
            if role not in normalized:
                normalized.append(role)
        if normalized:
            where.append(
                "("
                + " OR ".join(
                    f"({role} IS NOT NULL AND {role}!='')"
                    for role in normalized
                )
                + ")"
            )

    return where, params


def count_research_projection_records(
    project_root,
    library_id,
    *,
    artifact_id=None,
    artifact_ids=None,
    query=None,
    roles=None,
):
    path = research_projection_path(project_root, library_id)
    if not path.is_file():
        return 0
    where, params = _projection_filters(
        artifact_id=artifact_id,
        artifact_ids=artifact_ids,
        query=query,
        roles=roles,
    )
    sql = "SELECT COUNT(*) AS n FROM records"
    if where:
        sql += " WHERE " + " AND ".join(where)

    db = _connect(path, readonly=True)
    try:
        row = db.execute(sql, params).fetchone()
        return int(row["n"] or 0)
    finally:
        db.close()


def search_research_projection(
    project_root,
    library_id,
    *,
    artifact_id=None,
    artifact_ids=None,
    query=None,
    roles=None,
    limit=250,
    offset=0,
):
    """Search derived Library rows without touching Rank Hunter core state."""

    path = research_projection_path(project_root, library_id)
    if not path.is_file():
        return []
    where, params = _projection_filters(
        artifact_id=artifact_id,
        artifact_ids=artifact_ids,
        query=query,
        roles=roles,
    )

    sql = """
        SELECT artifact_id,record_index,
               parameter,family_label,curve_label,
               a1,a2,a3,a4,a6,x,y,
               rank_claim,rank_lower_claim,rank_upper_claim,exact_rank_claim,
               source,notes,mapped_json,raw_json
        FROM records
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY artifact_id,record_index LIMIT ? OFFSET ?"
    params.extend([max(0, int(limit)), max(0, int(offset))])

    db = _connect(path, readonly=True)
    try:
        return [dict(row) for row in db.execute(sql, params).fetchall()]
    finally:
        db.close()
