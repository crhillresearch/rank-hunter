"""Read-only plugin corpus databases.

A corpus is external/reference scientific state. It is deliberately separate
from rank42.db: Rank Hunter may identify, filter, benchmark, and seed searches
from a corpus, but does not silently import corpus curves into its authoritative
research database.

Plugins declare corpora in plugin.json; core owns the cache location and this
standard query contract. Corpus builders are plugin-owned executables.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CORPUS_SCHEMA_VERSION = 1
_REQUIRED_TABLES = {"corpus_meta", "curves", "aliases", "observations", "artifacts", "files"}


def corpus_cache_root(project_root):
    return Path(project_root).resolve() / ".rank42-corpora"


def corpus_cache_dir(project_root, plugin, corpus):
    return corpus_cache_root(project_root) / str(plugin.id) / str(corpus.id)


def corpus_path(project_root, plugin, corpus):
    return corpus_cache_dir(project_root, plugin, corpus) / str(corpus.cache_file)


def corpus_artifact_cache(project_root, plugin, corpus):
    return corpus_cache_dir(project_root, plugin, corpus) / "artifacts"


def _connect_path(path, *, readonly=True):
    path = Path(path).resolve()
    if readonly:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def corpus_meta(db):
    return {
        str(row["key"]): str(row["value"])
        for row in db.execute("SELECT key,value FROM corpus_meta").fetchall()
    }


def validate_corpus_db(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    db = _connect_path(path, readonly=True)
    try:
        tables = {
            str(row["name"])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            raise ValueError("corpus database missing table(s): " + ", ".join(missing))
        meta = corpus_meta(db)
        version = int(meta.get("schema_version") or 0)
        if version != CORPUS_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported corpus schema_version {version}; expected {CORPUS_SCHEMA_VERSION}"
            )
        return meta
    finally:
        db.close()


def connect_corpus(project_root, plugin, corpus):
    path = corpus_path(project_root, plugin, corpus)
    validate_corpus_db(path)
    return _connect_path(path, readonly=True)


def corpus_status(project_root, plugin, corpus):
    path = corpus_path(project_root, plugin, corpus)
    rec = {
        "plugin_id": plugin.id,
        "corpus_id": corpus.id,
        "name": corpus.name,
        "path": str(path),
        "exists": path.is_file(),
        "ready": False,
        "error": None,
        "meta": {},
    }
    if not path.is_file():
        return rec
    try:
        rec["meta"] = validate_corpus_db(path)
        rec["ready"] = True
    except Exception as exc:
        rec["error"] = str(exc)
    return rec


def corpus_stats(db):
    result = {}
    for table, key in (
        ("curves", "curves"),
        ("observations", "observations"),
        ("artifacts", "artifacts"),
        ("files", "files"),
    ):
        result[key] = int(db.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"] or 0)
    result["exact_curves"] = int(
        db.execute("SELECT COUNT(*) n FROM curves WHERE exact_rank IS NOT NULL").fetchone()["n"] or 0
    )
    result["conflicts"] = int(
        db.execute("SELECT COUNT(*) n FROM curves WHERE conflict<>0").fetchone()["n"] or 0
    )
    row = db.execute("SELECT MAX(exact_rank) AS n FROM curves").fetchone()
    result["max_exact_rank"] = int(row["n"]) if row and row["n"] is not None else None
    return result


def corpus_families(db):
    return [
        str(row["family_key"])
        for row in db.execute(
            "SELECT family_key FROM curves GROUP BY family_key ORDER BY family_key"
        ).fetchall()
    ]


def _corpus_curve_where(
    *,
    family_key=None,
    exact_rank=None,
    min_rank=None,
    exact_only=False,
    query=None,
):
    where, vals = [], []
    if family_key:
        where.append("c.family_key=?")
        vals.append(str(family_key))
    if exact_rank is not None:
        where.append("c.exact_rank=?")
        vals.append(int(exact_rank))
    elif exact_only:
        where.append("c.exact_rank IS NOT NULL")
    if min_rank is not None:
        where.append("COALESCE(c.exact_rank,c.rank_lower,-1)>=?")
        vals.append(int(min_rank))
    if query:
        where.append(
            "(c.parameter LIKE ? OR c.family_key LIKE ? OR c.canonical_key LIKE ?)"
        )
        q = f"%{str(query)}%"
        vals.extend([q, q, q])
    return where, vals


def count_corpus_curves(
    db,
    *,
    family_key=None,
    exact_rank=None,
    min_rank=None,
    exact_only=False,
    query=None,
):
    where, vals = _corpus_curve_where(
        family_key=family_key,
        exact_rank=exact_rank,
        min_rank=min_rank,
        exact_only=exact_only,
        query=query,
    )
    sql = "SELECT COUNT(*) AS n FROM curves c"
    if where:
        sql += " WHERE " + " AND ".join(where)
    row = db.execute(sql, vals).fetchone()
    return int(row["n"] or 0)


def list_corpus_curves(
    db,
    *,
    family_key=None,
    exact_rank=None,
    min_rank=None,
    exact_only=False,
    query=None,
    limit=1000,
    offset=0,
):
    where, vals = _corpus_curve_where(
        family_key=family_key,
        exact_rank=exact_rank,
        min_rank=min_rank,
        exact_only=exact_only,
        query=query,
    )
    sql = """
        SELECT c.*
        FROM curves c
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
        ORDER BY COALESCE(c.exact_rank,c.rank_lower,-1) DESC,
                 c.observation_count DESC,
                 c.family_key ASC,
                 c.parameter ASC
    """
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        vals.extend([int(limit), int(offset)])
    elif int(offset):
        sql += " LIMIT -1 OFFSET ?"
        vals.append(int(offset))
    return db.execute(sql, vals).fetchall()


def observations_for_curve(db, curve_id, *, limit=500):
    return db.execute(
        """
        SELECT o.*, a.name AS artifact_name, a.run_id, f.path AS source_file
        FROM observations o
        LEFT JOIN artifacts a ON a.id=o.artifact_id
        LEFT JOIN files f ON f.id=o.file_id
        WHERE o.curve_id=?
        ORDER BY COALESCE(a.created_at,'') DESC, o.id DESC
        LIMIT ?
        """,
        (int(curve_id), int(limit)),
    ).fetchall()


def lookup_corpus_curve(db, family_key, parameter):
    return db.execute(
        """
        SELECT c.*
        FROM aliases a
        JOIN curves c ON c.id=a.curve_id
        WHERE a.family_key=? AND a.parameter=?
        LIMIT 1
        """,
        (str(family_key), str(parameter)),
    ).fetchone()


def _row_dict(row):
    return {key: row[key] for key in row.keys()}


def lookup_plugin_corpora(project_root, plugin, variant, parameter):
    """Return corpus matches for one exact native family specialization.

    This compatibility API treats parameter as belonging to variant's native
    coordinate system. Candidate records with chart/native lineage use the
    candidate-specific lookup below.
    """
    from rank42.plugins import corpora_for_variant

    matches = []
    for corpus, family_key in corpora_for_variant(plugin, variant):
        status = corpus_status(project_root, plugin, corpus)
        if not status["ready"]:
            continue
        db = connect_corpus(project_root, plugin, corpus)
        try:
            row = lookup_corpus_curve(db, family_key, parameter)
            if row is None:
                continue
            rec = _row_dict(row)
            rec.update({
                "plugin_id": plugin.id,
                "corpus_id": corpus.id,
                "corpus_name": corpus.name,
                "corpus_family_key": family_key,
                "corpus_match_identity": {
                    "parameter_space": "native",
                    "variant_id": str(variant.id),
                    "family_key": str(family_key),
                    "parameter": str(parameter),
                },
            })
            matches.append(rec)
        finally:
            db.close()
    return matches


def _candidate_metadata(record):
    raw = record.get("metadata_json") if isinstance(record, dict) else None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _candidate_value(record, key):
    if isinstance(record, dict) and record.get(key) is not None:
        return record.get(key)
    return _candidate_metadata(record).get(key)


def _normalized_parameter(value):
    from rank42.family_charts import qstr

    try:
        return qstr(value)
    except Exception:
        return str(value)


def candidate_corpus_identity(plugin, variant, corpus, record):
    """Resolve the exact Research Library identity for one candidate.

    Library aliases are expressed in the native Family variant coordinate
    system. Chart candidates therefore match with their exact native parameter,
    never with the chart parameter shown to the search UI.
    """
    from rank42.plugins import get_chart

    chart_id = _candidate_value(record, "chart_id")
    native_parameter = _candidate_value(record, "native_parameter")
    native_variant_id = _candidate_value(record, "native_variant_id")

    if chart_id:
        chart = get_chart(plugin, chart_id)
        if chart is None:
            raise ValueError(f"candidate references unavailable chart {chart_id!r}")
        if native_variant_id is not None and str(native_variant_id) != str(chart.native_variant_id):
            raise ValueError(
                f"candidate native variant {native_variant_id!r} disagrees with "
                f"chart {chart.id!r} native variant {chart.native_variant_id!r}"
            )
        native_variant_id = chart.native_variant_id
        chart_parameter = (
            _candidate_value(record, "chart_parameter")
            or _candidate_value(record, "t")
            or _candidate_value(record, "parameter")
        )
        if chart_parameter in (None, ""):
            raise ValueError("charted candidate is missing chart parameter")
        expected_native = _normalized_parameter(chart.forward(chart_parameter))
        if native_parameter in (None, ""):
            native_parameter = expected_native
        elif _normalized_parameter(native_parameter) != expected_native:
            raise ValueError(
                f"candidate chart/native parameter mismatch for chart {chart.id!r}: "
                f"{chart_parameter!r} -> {expected_native!r}, stored native "
                f"{native_parameter!r}"
            )
        if _normalized_parameter(chart.inverse(native_parameter)) != _normalized_parameter(chart_parameter):
            raise ValueError(
                f"candidate chart {chart.id!r} failed exact native round trip"
            )
    else:
        native_variant_id = native_variant_id or variant.id
        native_parameter = (
            native_parameter
            if native_parameter not in (None, "")
            else _candidate_value(record, "t") or _candidate_value(record, "parameter")
        )

    if native_parameter in (None, ""):
        raise ValueError("candidate is missing native parameter identity")

    family_key = corpus.family_key(native_variant_id)
    return {
        "parameter_space": "native",
        "variant_id": str(native_variant_id),
        "family_key": str(family_key),
        "parameter": _normalized_parameter(native_parameter),
        "chart_id": None if not chart_id else str(chart_id),
    }


def lookup_candidate_corpora(project_root, plugin, variant, record):
    """Return Research Library matches using one explicit native identity."""
    matches = []
    for corpus in tuple(getattr(plugin, "corpora", ()) or ()):
        status = corpus_status(project_root, plugin, corpus)
        if not status["ready"]:
            continue
        identity = candidate_corpus_identity(plugin, variant, corpus, record)
        db = connect_corpus(project_root, plugin, corpus)
        try:
            row = lookup_corpus_curve(
                db,
                identity["family_key"],
                identity["parameter"],
            )
            if row is None:
                continue
            rec = _row_dict(row)
            rec.update({
                "plugin_id": plugin.id,
                "corpus_id": corpus.id,
                "corpus_name": corpus.name,
                "corpus_family_key": identity["family_key"],
                "corpus_match_identity": identity,
            })
            matches.append(rec)
        finally:
            db.close()
    return matches


def corpus_match_summary(matches):
    matches = list(matches or [])
    if not matches:
        return {
            "known": False,
            "exact_rank": None,
            "rank_lower": None,
            "rank_upper": None,
            "conflict": False,
            "conflict_reasons": [],
            "corpora": [],
        }

    exacts = [int(m["exact_rank"]) for m in matches if m.get("exact_rank") is not None]
    lowers = [int(m["rank_lower"]) for m in matches if m.get("rank_lower") is not None]
    uppers = [int(m["rank_upper"]) for m in matches if m.get("rank_upper") is not None]
    exact_values = sorted(set(exacts))
    lower_values = [*lowers, *exacts]
    upper_values = [*uppers, *exacts]
    lower = max(lower_values) if lower_values else None
    upper = min(upper_values) if upper_values else None

    reasons = []
    if any(bool(m.get("conflict")) for m in matches):
        reasons.append("source_conflict")
    if len(exact_values) > 1:
        reasons.append("exact_rank_disagreement")
    if lower is not None and upper is not None and lower > upper:
        reasons.append("bounds_disagreement")

    conflict = bool(reasons)
    exact = exact_values[0] if len(exact_values) == 1 and not conflict else None
    return {
        "known": True,
        "exact_rank": exact,
        "rank_lower": lower,
        "rank_upper": upper,
        "conflict": conflict,
        "conflict_reasons": reasons,
        "corpora": [str(m.get("corpus_id")) for m in matches],
    }


def corpus_seed_candidate(row, corpus):
    """Build one typed candidate record from a Research Library curve."""
    exact = row["exact_rank"]
    lower = row["rank_lower"]
    if exact is not None:
        ranking_value = int(exact)
        rank_field = "exact_rank"
        ranking_kind = "known_rank"
    elif lower is not None:
        ranking_value = int(lower)
        rank_field = "rank_lower"
        ranking_kind = "known_rank"
    else:
        ranking_value = 0
        rank_field = None
        ranking_kind = "imported_priority"

    provenance = {
        "ranking_kind": ranking_kind,
        "source": "research_library",
        "corpus_id": str(corpus.id),
        "corpus_curve_id": int(row["id"]),
        "rank_field": rank_field,
    }
    return {
        "t": str(row["parameter"]),
        "score": float(ranking_value),
        "ranking_value": float(ranking_value),
        "ranking_kind": ranking_kind,
        "ranking_provenance": provenance,
        "score_provenance": dict(provenance),
        "corpus_seed": True,
        "corpus_id": corpus.id,
        "corpus_curve_id": int(row["id"]),
        "corpus_summary": {
            "known": True,
            "exact_rank": row["exact_rank"],
            "rank_lower": row["rank_lower"],
            "rank_upper": row["rank_upper"],
            "conflict": bool(row["conflict"]),
            "corpora": [corpus.id],
        },
    }


def annotate_candidate_from_corpora(project_root, plugin, variant, record):
    parameter = str(record.get("t") or record.get("parameter") or "")
    native_parameter = _candidate_value(record, "native_parameter")
    if not parameter and native_parameter in (None, ""):
        return record, []
    matches = lookup_candidate_corpora(project_root, plugin, variant, record)
    if matches:
        record["corpus_matches"] = matches
        record["corpus_summary"] = corpus_match_summary(matches)
    else:
        record["corpus_summary"] = corpus_match_summary(())
    return record, matches


def apply_candidate_corpus_policy(project_root, plugin, variant, records, policy):
    """Annotate/filter candidate records using the shared corpus policy contract."""
    policy = str(policy or "annotate")
    if policy not in {"annotate", "exclude-known", "known-only", "off"}:
        raise ValueError(f"unsupported corpus policy {policy!r}")

    rows = [dict(record) for record in (records or [])]
    declared = list(getattr(plugin, "corpora", ()) or ())
    summary = {
        "policy": policy,
        "declared": len(declared),
        "ready": 0,
        "known": 0,
        "retained": len(rows),
        "classified": False,
    }
    if policy == "off" or not declared:
        return rows, summary

    ready = [
        corpus for corpus in declared
        if corpus_status(project_root, plugin, corpus).get("ready")
    ]
    summary["ready"] = len(ready)
    if not ready:
        if policy in {"exclude-known", "known-only"}:
            raise ValueError(
                "corpus filtering requested, but no declared corpus is built/ready; "
                "build it from the Corpora page or use --corpus-policy annotate/off"
            )
        return rows, summary

    out = []
    known = 0
    for record in rows:
        record, matches = annotate_candidate_from_corpora(
            project_root, plugin, variant, record
        )
        is_known = bool(matches)
        known += int(is_known)
        if policy == "exclude-known" and is_known:
            continue
        if policy == "known-only" and not is_known:
            continue
        out.append(record)
    summary.update({
        "known": int(known),
        "retained": len(out),
        "classified": True,
    })
    return out, summary


def build_corpus_command(*, python, project_root, plugin, corpus):
    if corpus.builder_path is None:
        raise ValueError(f"corpus {plugin.id}:{corpus.id} has no builder")
    out = corpus_path(project_root, plugin, corpus)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache = corpus_artifact_cache(project_root, plugin, corpus)
    cache.mkdir(parents=True, exist_ok=True)
    return [
        str(python),
        str(corpus.builder_path),
        "--output",
        str(out),
        "--artifact-cache",
        str(cache),
        "--project-root",
        str(Path(project_root).resolve()),
        "--plugin-id",
        str(plugin.id),
        "--corpus-id",
        str(corpus.id),
    ]


def corpus_provenance_json(row):
    try:
        return json.loads(row["metadata_json"] or "{}")
    except Exception:
        return {}
