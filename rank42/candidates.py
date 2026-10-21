"""First-class candidate-pool persistence for Rank Hunter v0.8.

SQLite is authoritative.  JSONL remains a reproducible interchange/export
format and an adapter bridge for older scientific CLIs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from rank42.curve_research_state import curve_research_state_map
from rank42.db import ensure_native_fiber, now


def create_pool(db, *, name, plugin_id, family_spec, plugin_version=None, generation=None, status="ready", source_path=None,
                family_sha256=None, adapter_sha256=None, plugin_manifest_sha256=None):
    ts = now()
    db.execute(
        """
        INSERT INTO candidate_pools(name,plugin_id,plugin_version,family_spec,family_sha256,adapter_sha256,plugin_manifest_sha256,generation_json,status,candidate_count,source_path,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            plugin_id=excluded.plugin_id,
            plugin_version=excluded.plugin_version,
            family_spec=excluded.family_spec,
            family_sha256=excluded.family_sha256,
            adapter_sha256=excluded.adapter_sha256,
            plugin_manifest_sha256=excluded.plugin_manifest_sha256,
            generation_json=excluded.generation_json,
            status=excluded.status,
            source_path=COALESCE(excluded.source_path,candidate_pools.source_path),
            updated_at=excluded.updated_at
        """,
        (str(name), str(plugin_id), plugin_version, str(family_spec), family_sha256, adapter_sha256,
         plugin_manifest_sha256, json.dumps(generation or {}, sort_keys=True), str(status), source_path, ts, ts),
    )
    db.commit()
    return db.execute("SELECT * FROM candidate_pools WHERE name=?", (str(name),)).fetchone()


def get_pool(db, pool_id):
    return db.execute("SELECT * FROM candidate_pools WHERE id=?", (int(pool_id),)).fetchone()


def delete_pool(db, pool_id):
    """Delete one candidate pool and its queue rows.

    Candidate rows are owned by the pool and cascade on delete. Scientific
    curve rows are deliberately *not* deleted: a pool is an orchestration
    object, while curves/points/lattices are independent scientific state.
    """
    pool = get_pool(db, pool_id)
    if pool is None:
        return False
    db.execute("DELETE FROM candidate_pools WHERE id=?", (int(pool_id),))
    db.commit()
    return True


def list_pools(db, *, plugin_id=None, limit=100):
    if plugin_id:
        return db.execute(
            "SELECT * FROM candidate_pools WHERE plugin_id=? ORDER BY id DESC LIMIT ?",
            (str(plugin_id), int(limit)),
        ).fetchall()
    return db.execute("SELECT * FROM candidate_pools ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()


def _parameter_from_row(row):
    if row.get("t") is not None:
        return str(row["t"])
    if row.get("parameter") is not None:
        return str(row["parameter"])
    if row.get("a") is not None and row.get("b") is not None:
        return f"{row['a']}/{row['b']}"
    raise ValueError("candidate row requires t/parameter or a,b")


def replace_pool_rows(db, pool_id, rows: Iterable[dict]):
    pool_id = int(pool_id)
    db.execute("DELETE FROM candidates WHERE pool_id=?", (pool_id,))
    ts = now()
    rows = list(rows)
    ordered = sorted(rows, key=lambda r: float(r.get("score", 0.0)), reverse=True)
    for rank_order, row in enumerate(ordered, 1):
        parameter = _parameter_from_row(row)
        metadata = dict(row)
        for key in ("t", "parameter", "a", "b", "score", "prime_bound", "prime_terms"):
            metadata.pop(key, None)
        native_family_key = row.get("native_family_key")
        native_parameter = row.get("native_parameter")
        native_fiber_id = ensure_native_fiber(
            db, native_family_key=native_family_key, native_parameter=native_parameter,
            native_family_spec=row.get("native_family_spec"),
        ) if native_family_key and native_parameter is not None else None
        db.execute(
            """
            INSERT INTO candidates(pool_id,rank_order,parameter,a,b,score,prime_bound,prime_terms,status,curve_id,
                                   native_fiber_id,native_family_key,native_family_spec,native_parameter,chart_id,chart_parameter,chart_map_fingerprint,
                                   metadata_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pool_id, rank_order, parameter,
                None if row.get("a") is None else str(row.get("a")),
                None if row.get("b") is None else str(row.get("b")),
                float(row.get("score", 0.0)),
                None if row.get("prime_bound") is None else int(row.get("prime_bound")),
                None if row.get("prime_terms") is None else int(row.get("prime_terms")),
                "unsearched", None, native_fiber_id,
                None if native_family_key is None else str(native_family_key),
                row.get("native_family_spec"),
                None if native_parameter is None else str(native_parameter),
                row.get("chart_id"), row.get("chart_parameter"), row.get("chart_map_fingerprint"),
                json.dumps(metadata, sort_keys=True), ts, ts,
            ),
        )
    db.execute(
        "UPDATE candidate_pools SET candidate_count=?, status='ready', updated_at=? WHERE id=?",
        (len(ordered), ts, pool_id),
    )
    db.commit()
    return len(ordered)


def _enrich_candidate_rows_with_curve_state(db, rows):
    """Attach authoritative live curve rank state to candidate queue rows."""
    rows = list(rows)
    curve_ids = {
        int(row["curve_id"])
        for row in rows
        if row["curve_id"] is not None
    }
    states = curve_research_state_map(db, curve_ids=curve_ids) if curve_ids else {}
    enriched = []
    for record in rows:
        row = dict(record)
        curve_id = row.get("curve_id")
        state = states.get(int(curve_id)) if curve_id is not None else None
        if state is None:
            row["rigorous_lower"] = None
            row["rigorous_upper"] = None
            row["exact_rank"] = None
            row["rank_inconsistent"] = False
        else:
            lower = int(state["rigorous_lower"] or 0)
            row["rigorous_lower"] = (
                lower if lower > 0 or state["exact_rank"] is not None else None
            )
            row["rigorous_upper"] = state["rigorous_upper"]
            row["exact_rank"] = state["exact_rank"]
            row["rank_inconsistent"] = bool(state["rank_inconsistent"])
        enriched.append(row)
    return enriched


def candidate_rows(db, pool_id, *, limit=None, offset=0, only_unsearched=False):
    sql = """
        SELECT c.*, p.plugin_id, p.plugin_version, p.family_spec, p.family_sha256, p.adapter_sha256,
               p.plugin_manifest_sha256, p.name AS pool_name,
               cv.status AS curve_status, cv.quick_upper, cv.updated_at AS curve_updated_at
        FROM candidates c
        JOIN candidate_pools p ON p.id=c.pool_id
        LEFT JOIN curves cv ON cv.id=c.curve_id
        WHERE c.pool_id=?
    """
    vals = [int(pool_id)]
    if only_unsearched:
        sql += " AND c.status='unsearched'"
    sql += " ORDER BY c.rank_order ASC, c.score DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        vals.extend([int(limit), int(offset)])
    return _enrich_candidate_rows_with_curve_state(
        db,
        db.execute(sql, vals).fetchall(),
    )


def stamp_curve_provenance_from_pool(db, curve_id, pool, candidate=None):
    """Copy pool identity onto a surviving curve without overwriting prior provenance."""
    if curve_id is None or pool is None:
        return
    fields = {
        "plugin_id": pool["plugin_id"],
        "plugin_version": pool["plugin_version"],
        "family_spec": pool["family_spec"],
        "family_sha256": pool["family_sha256"] if "family_sha256" in pool.keys() else None,
        "adapter_sha256": pool["adapter_sha256"] if "adapter_sha256" in pool.keys() else None,
        "plugin_manifest_sha256": pool["plugin_manifest_sha256"] if "plugin_manifest_sha256" in pool.keys() else None,
    }
    if candidate is not None:
        for key in ("native_fiber_id", "native_family_key", "native_family_spec", "native_parameter", "chart_id", "chart_parameter", "chart_map_fingerprint"):
            try: value = candidate[key]
            except (KeyError, IndexError): value = None
            if value is not None:
                fields[key] = value
    current = db.execute("SELECT * FROM curves WHERE id=?", (int(curve_id),)).fetchone()
    if current is None:
        return
    updates = {k: v for k, v in fields.items() if v is not None and (k not in current.keys() or current[k] in (None, ""))}
    if updates:
        from rank42.db import update_curve
        update_curve(db, int(curve_id), **updates)


def candidate_outcome(row):
    """Human-readable scientific outcome for one candidate queue record."""
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}
    last = meta.get("last_search") if isinstance(meta.get("last_search"), dict) else {}
    curve_id = (row["curve_id"] if "curve_id" in row.keys() else None) or last.get("curve_id")
    status = str((row["curve_status"] if "curve_status" in row.keys() else None) or last.get("curve_status") or "")
    if str(row["status"]) == "unsearched":
        return "unsearched"

    if curve_id and "rank_inconsistent" in row.keys() and bool(row["rank_inconsistent"]):
        suffix = f" · curve #{curve_id}"
        lower = int(row["rigorous_lower"] or 0)
        upper = row["rigorous_upper"] if "rigorous_upper" in row.keys() else None
        bound = f"≥{lower}" + (f" / ≤{int(upper)}" if upper is not None else "")
        return f"rank evidence conflict {bound}{suffix}"

    if curve_id and "exact_rank" in row.keys() and row["exact_rank"] is not None:
        return f"rank ={int(row['exact_rank'])} retained · curve #{curve_id}"

    if curve_id and "rigorous_lower" in row.keys() and row["rigorous_lower"] is not None:
        return f"rank ≥{int(row['rigorous_lower'])} retained · curve #{curve_id}"

    historical_lower = int(last.get("rigorous_lower") or 0)
    if historical_lower > 0:
        suffix = f" · curve #{curve_id}" if curve_id else ""
        return f"historical search rank ≥{historical_lower}{suffix}"
    if "timeout" in status.lower() or bool(last.get("timeout")):
        return "timeout → no retained rank evidence"
    if last.get("zero_evidence_curve_discarded") or not curve_id:
        return "searched → no retained evidence"
    return f"searched · curve #{curve_id}"


def _reconcile_candidate_rows(db, pool, rows, *, infer_searched):
    ts = now()
    for rec in rows:
        meta = json.loads(rec["metadata_json"] or "{}")
        family_name = meta.get("family") or meta.get("family_name")
        curve_row = None
        if family_name:
            curve_row = db.execute(
                "SELECT id,status FROM curves WHERE family=? AND parameter=?",
                (str(family_name), str(rec["parameter"])),
            ).fetchone()
        if curve_row is None and rec["native_family_key"] and rec["native_parameter"] is not None:
            matches = db.execute(
                "SELECT id,status FROM curves WHERE native_family_key=? AND native_parameter=? ORDER BY id DESC LIMIT 2",
                (str(rec["native_family_key"]), str(rec["native_parameter"])),
            ).fetchall()
            if len(matches) == 1:
                curve_row = matches[0]
        if curve_row is None:
            matches = db.execute(
                "SELECT id,status FROM curves WHERE parameter=? ORDER BY id DESC LIMIT 2",
                (str(rec["parameter"]),),
            ).fetchall()
            if len(matches) == 1:
                curve_row = matches[0]
        if curve_row is None:
            continue

        terminal = {
            "exact", "proven_lower", "extra_done", "extra_review",
            "strong_done", "pruned", "promising", "quick_done",
        }
        current = str(rec["status"] or "unsearched")
        state = current
        if infer_searched and current == "unsearched" and curve_row["status"] in terminal:
            state = "searched"
        db.execute(
            "UPDATE candidates SET curve_id=?, status=?, updated_at=? WHERE id=?",
            (int(curve_row["id"]), state, ts, int(rec["id"])),
        )
        stamp_curve_provenance_from_pool(db, int(curve_row["id"]), pool, rec)
    db.commit()


def reconcile_candidates(db, pool_id, candidate_ids, *, infer_searched=True):
    """Reconcile only selected candidate rows from one pool."""
    pool = get_pool(db, pool_id)
    if pool is None:
        raise ValueError(f"candidate pool #{pool_id} not found")
    ids = [int(value) for value in candidate_ids or []]
    if not ids:
        return

    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        q = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT * FROM candidates WHERE pool_id=? AND id IN ({q})",
            [int(pool_id), *chunk],
        ).fetchall()
        _reconcile_candidate_rows(
            db,
            pool,
            rows,
            infer_searched=bool(infer_searched),
        )


def reconcile_pool(db, pool_id, *, infer_searched=True):
    """Attach every candidate in one pool to matching curve rows.

    This is intentionally a full-pool maintenance operation. UI render paths
    must not call it implicitly; stale-job finalization uses reconcile_candidates
    so cleanup stays bounded to that job's candidate slice.
    """
    pool = get_pool(db, pool_id)
    if pool is None:
        raise ValueError(f"candidate pool #{pool_id} not found")
    rows = db.execute(
        "SELECT * FROM candidates WHERE pool_id=?",
        (int(pool_id),),
    ).fetchall()
    _reconcile_candidate_rows(
        db,
        pool,
        rows,
        infer_searched=bool(infer_searched),
    )

def export_pool_jsonl(db, pool_id, path, *, limit=None, offset=0, only_unsearched=False):
    pool = get_pool(db, pool_id)
    if pool is None:
        raise ValueError(f"candidate pool #{pool_id} not found")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = candidate_rows(db, pool_id, limit=limit, offset=offset, only_unsearched=only_unsearched)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            rec = dict(meta)
            rec.update({
                "t": row["parameter"],
                "score": float(row["score"] or 0.0),
                "family_spec": pool["family_spec"],
                "plugin_id": pool["plugin_id"],
            })
            if row["a"] is not None:
                rec["a"] = row["a"]
            if row["b"] is not None:
                rec["b"] = row["b"]
            if row["prime_bound"] is not None:
                rec["prime_bound"] = int(row["prime_bound"])
            if row["prime_terms"] is not None:
                rec["prime_terms"] = int(row["prime_terms"])
            handle.write(json.dumps(rec, sort_keys=True) + "\n")
    return path



def pool_resume_offset(db, pool_id):
    """Absolute zero-based pool offset of the first unfinished candidate."""
    row = db.execute(
        "SELECT MIN(rank_order) AS r FROM candidates WHERE pool_id=? AND status='unsearched'",
        (int(pool_id),),
    ).fetchone()
    if row is not None and row["r"] is not None:
        return max(0, int(row["r"]) - 1)
    pool = get_pool(db, pool_id)
    return int(pool["candidate_count"] or 0) if pool is not None else 0


def candidate_rows_from_pool_offset(db, pool_id, *, limit, offset=0, only_unsearched=True):
    """Select from an absolute pool position, not OFFSET within a filtered subset."""
    sql = """
        SELECT c.*, p.plugin_id, p.plugin_version, p.family_spec, p.family_sha256, p.adapter_sha256,
               p.plugin_manifest_sha256, p.name AS pool_name, cv.status AS curve_status, cv.generic_lower, cv.descent_lower, cv.exact_rank,
               cv.quick_upper, cv.updated_at AS curve_updated_at
        FROM candidates c
        JOIN candidate_pools p ON p.id=c.pool_id
        LEFT JOIN curves cv ON cv.id=c.curve_id
        WHERE c.pool_id=? AND c.rank_order>?
    """
    vals = [int(pool_id), int(offset)]
    if only_unsearched:
        sql += " AND c.status='unsearched'"
    sql += " ORDER BY c.rank_order ASC, c.score DESC LIMIT ?"
    vals.append(int(limit))
    return db.execute(sql, vals).fetchall()


def export_rows_jsonl(db, pool_id, rows, path):
    """Export an already-selected deterministic candidate slice."""
    pool = get_pool(db, pool_id)
    if pool is None:
        raise ValueError(f"candidate pool #{pool_id} not found")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            rec = dict(meta)
            rec.update({
                "_candidate_id": int(row["id"]),
                "t": row["parameter"],
                "score": float(row["score"] or 0.0),
                "family_spec": pool["family_spec"],
                "plugin_id": pool["plugin_id"],
            })
            if row["a"] is not None: rec["a"] = row["a"]
            if row["b"] is not None: rec["b"] = row["b"]
            if row["prime_bound"] is not None: rec["prime_bound"] = int(row["prime_bound"])
            if row["prime_terms"] is not None: rec["prime_terms"] = int(row["prime_terms"])
            handle.write(json.dumps(rec, sort_keys=True) + "\n")
    return path


def import_jsonl(db, path, *, name, plugin_id, family_spec, plugin_version=None, generation=None,
                 family_sha256=None, adapter_sha256=None, plugin_manifest_sha256=None):
    path = Path(path)
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError(f"candidate line {line_no} is not a JSON object")
            rows.append(rec)
    pool = create_pool(
        db, name=name, plugin_id=plugin_id, plugin_version=plugin_version,
        family_spec=family_spec, generation=generation or {"imported_from": str(path)},
        source_path=str(path), status="importing", family_sha256=family_sha256,
        adapter_sha256=adapter_sha256, plugin_manifest_sha256=plugin_manifest_sha256,
    )
    replace_pool_rows(db, pool["id"], rows)
    return get_pool(db, pool["id"])
