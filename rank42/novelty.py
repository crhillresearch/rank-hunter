"""Catalog cross-checks for locally discovered elliptic curves.

A catalog hit establishes that a curve is already present in that source.
Catalog absence is deliberately *not* promoted to a mathematical novelty
claim.  LMFDB lookup is SQL-first against the public read-only PostgreSQL
mirror, with the documented HTTP API as a bounded fallback; successful rows
are cached by conductor.  ICARM is checked from a locally synchronized public
snapshot.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from rank42.catalog import ensure_icarm_synced
from rank42.lmfdb_sql import (
    LMFDBSQLFailure,
    LMFDBSQLUnavailable,
    fetch_lmfdb_sql_many,
    probe_lmfdb_sql,
)

LMFDB_API = "https://www.lmfdb.org/api/ec_curvedata/"
LMFDB_CURVE_BASE = "https://www.lmfdb.org/EllipticCurve/Q/data/"
LMFDB_COMPLETE_CONDUCTOR = 500_000
LMFDB_FIELDS = ("lmfdb_label", "Clabel", "ainvs", "conductor", "rank")
USER_AGENT = "Rank-Hunter/0.7.14 catalog-check"
LMFDB_API_BATCH_DELAY = 0.20


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fresh_enough(value, max_age_hours: float) -> bool:
    dt = _parse_time(value)
    if dt is None:
        return False
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    return age <= max(0.0, float(max_age_hours)) * 3600.0


def _json_get(url: str, timeout: int, *, attempts: int = 3) -> dict:
    """Fetch JSON with bounded retry/backoff, including malformed 200 bodies."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_exc = None
    for attempt in range(max(1, int(attempts))):
        try:
            with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
                raw = resp.read()
                content_type = str(resp.headers.get("Content-Type") or "")
            if not raw.strip():
                raise ValueError("LMFDB API returned an empty 200 response")
            if content_type and "json" not in content_type.lower():
                preview = raw[:160].decode("utf-8", errors="replace")
                raise ValueError(
                    f"LMFDB API returned non-JSON content-type {content_type!r}: {preview!r}"
                )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                preview = raw[:160].decode("utf-8", errors="replace")
                raise ValueError(f"LMFDB API returned malformed JSON: {preview!r}") from exc
            if not isinstance(payload, dict):
                raise ValueError("LMFDB API response is not a JSON object")
            return payload
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(5.0, max(0.25, float(retry_after))) if retry_after else 0.5 * (2 ** attempt)
            except (TypeError, ValueError):
                delay = 0.5 * (2 ** attempt)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.5 * (2 ** attempt))
    raise last_exc  # pragma: no cover

def lmfdb_query_url(conductor: int, *, offset: int = 0) -> str:
    """Build a documented LMFDB API query for one integer conductor.

    LMFDB requires a type prefix on query values (``i`` for integers).  Limit
    the response to the exact fields needed for Q-isomorphism matching.
    """
    query = urllib.parse.urlencode(
        {
            "_format": "json",
            "_fields": ",".join(LMFDB_FIELDS),
            "conductor": f"i{int(conductor)}",
            "_offset": int(offset),
        }
    )
    return f"{LMFDB_API}?{query}"


def fetch_lmfdb_by_conductor(conductor: int, *, timeout: int = 8, max_pages: int = 50) -> list[dict]:
    """Return all LMFDB ``ec_curvedata`` rows at one conductor."""
    url = lmfdb_query_url(int(conductor))
    rows: list[dict] = []
    seen = set()
    for _ in range(int(max_pages)):
        if url in seen:
            break
        seen.add(url)
        payload = _json_get(url, timeout)
        rows.extend(payload.get("data") or [])
        nxt = payload.get("next")
        if not nxt:
            break
        url = urllib.parse.urljoin("https://www.lmfdb.org", str(nxt))
    return rows


def _query_cache_row(db, source: str, query_key: str):
    return db.execute(
        "SELECT * FROM catalog_query_cache WHERE source=? AND query_key=?",
        (str(source), str(query_key)),
    ).fetchone()


def _cached_rows(cached):
    if cached is None or not cached["payload_json"]:
        return None
    try:
        value = json.loads(cached["payload_json"])
    except Exception:
        return None
    return value if isinstance(value, list) else None


def _store_query_cache(db, *, source: str, query_key: str, payload, metadata=None):
    db.execute(
        """
        INSERT INTO catalog_query_cache(source,query_key,status,payload_json,metadata_json,fetched_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(source,query_key) DO UPDATE SET
            status=excluded.status,
            payload_json=excluded.payload_json,
            metadata_json=excluded.metadata_json,
            fetched_at=excluded.fetched_at
        """,
        (
            str(source), str(query_key), "ok",
            json.dumps(payload, sort_keys=True),
            json.dumps(metadata or {}, sort_keys=True),
            _utcnow(),
        ),
    )
    db.commit()


def _cache_lmfdb_rows(db, conductor: int, rows: list[dict], metadata: dict):
    _store_query_cache(
        db, source="lmfdb", query_key=f"conductor:{int(conductor)}",
        payload=rows, metadata={"conductor": int(conductor), **dict(metadata or {})},
    )


def _fresh_cached_lmfdb(db, conductor: int, cache_hours: float, *, force: bool = False):
    key = f"conductor:{int(conductor)}"
    cached = _query_cache_row(db, "lmfdb", key)
    rows = _cached_rows(cached)
    if (
        not force and cached is not None and cached["status"] == "ok"
        and rows is not None and _fresh_enough(cached["fetched_at"], cache_hours)
    ):
        return list(rows), {
            "cache": "hit", "fetched_at": cached["fetched_at"], "fresh": True,
        }
    return None


def _stale_cached_lmfdb(db, conductor: int):
    cached = _query_cache_row(db, "lmfdb", f"conductor:{int(conductor)}")
    rows = _cached_rows(cached)
    if cached is None or rows is None:
        return None
    return list(rows), cached["fetched_at"]


def prefetch_lmfdb_cached(
    db,
    conductors,
    *,
    timeout: int = 8,
    cache_hours: float = 24,
    force: bool = False,
    transport: str = "auto",
    api_fallback: bool = True,
) -> dict[int, dict]:
    """Populate/fetch conductor rows, using one SQL query for all cache misses.

    Returns ``{conductor: {rows, meta}}``.  Failed uncached conductors carry an
    ``error`` key.  In ``auto`` mode the public SQL mirror is primary and the
    HTTP API is a bounded fallback.
    """
    mode = str(transport or "auto").lower()
    if mode not in {"auto", "sql", "api"}:
        raise ValueError(f"unsupported LMFDB transport {transport!r}")
    values = sorted({int(c) for c in conductors})
    out: dict[int, dict] = {}
    missing = []
    for conductor in values:
        fresh = _fresh_cached_lmfdb(db, conductor, cache_hours, force=force)
        if fresh is not None:
            rows, meta = fresh
            out[conductor] = {"rows": rows, "meta": meta}
        else:
            missing.append(conductor)

    sql_error = None
    if missing and mode in {"auto", "sql"}:
        try:
            grouped, sql_meta = fetch_lmfdb_sql_many(missing, timeout=float(timeout))
            for conductor in missing:
                rows = list(grouped.get(conductor, []))
                meta = {
                    **sql_meta,
                    "cache": "refreshed" if _query_cache_row(db, "lmfdb", f"conductor:{conductor}") else "miss",
                    "fresh": True,
                    "batch": True,
                }
                _cache_lmfdb_rows(db, conductor, rows, meta)
                out[conductor] = {"rows": rows, "meta": meta}
            missing = []
        except (LMFDBSQLUnavailable, LMFDBSQLFailure) as exc:
            sql_error = exc
            if not api_fallback:
                for conductor in missing:
                    stale = _stale_cached_lmfdb(db, conductor)
                    if stale is not None:
                        rows, fetched_at = stale
                        out[conductor] = {"rows": rows, "meta": {
                            "cache": "stale_fallback", "fetched_at": fetched_at, "fresh": False,
                            "network_error": repr(exc), "transport": "sql",
                        }}
                    else:
                        out[conductor] = {"error": exc, "meta": {"transport": "sql"}}
                missing = []

    if missing and (mode == "api" or api_fallback):
        for index, conductor in enumerate(missing):
            try:
                rows = fetch_lmfdb_by_conductor(conductor, timeout=int(timeout))
                meta = {
                    "transport": "api",
                    "api": LMFDB_API,
                    "typed_query": f"conductor=i{conductor}",
                    "fields": list(LMFDB_FIELDS),
                    "cache": "refreshed" if _query_cache_row(db, "lmfdb", f"conductor:{conductor}") else "miss",
                    "fresh": True,
                }
                if sql_error is not None:
                    meta["sql_fallback_reason"] = repr(sql_error)
                _cache_lmfdb_rows(db, conductor, rows, meta)
                out[conductor] = {"rows": rows, "meta": meta}
            except Exception as exc:
                stale = _stale_cached_lmfdb(db, conductor)
                if stale is not None:
                    rows, fetched_at = stale
                    out[conductor] = {"rows": rows, "meta": {
                        "cache": "stale_fallback", "fetched_at": fetched_at, "fresh": False,
                        "network_error": repr(exc), "transport": "api",
                        **({"sql_fallback_reason": repr(sql_error)} if sql_error is not None else {}),
                    }}
                else:
                    out[conductor] = {"error": exc, "meta": {
                        "transport": "api",
                        **({"sql_fallback_reason": repr(sql_error)} if sql_error is not None else {}),
                    }}
            if index + 1 < len(missing):
                time.sleep(LMFDB_API_BATCH_DELAY)

    return out


def fetch_lmfdb_cached(
    db,
    conductor: int,
    *,
    timeout: int = 8,
    cache_hours: float = 24,
    force: bool = False,
    transport: str = "auto",
    api_fallback: bool = True,
) -> tuple[list[dict], dict]:
    """Fetch one conductor through the SQL-first persistent cache layer."""
    item = prefetch_lmfdb_cached(
        db, [int(conductor)], timeout=timeout, cache_hours=cache_hours,
        force=force, transport=transport, api_fallback=api_fallback,
    )[int(conductor)]
    if "error" in item:
        raise item["error"]
    return list(item.get("rows") or []), dict(item.get("meta") or {})

def _upsert_check(db, *, curve_id: int, source: str, status: str, source_label=None,
                  source_url=None, source_rank=None, metadata=None):
    ts = _utcnow()
    db.execute(
        """
        INSERT INTO curve_catalog_checks(
            curve_id,source,status,source_label,source_url,source_rank,metadata_json,checked_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(curve_id,source) DO UPDATE SET
            status=excluded.status,
            source_label=excluded.source_label,
            source_url=excluded.source_url,
            source_rank=excluded.source_rank,
            metadata_json=excluded.metadata_json,
            checked_at=excluded.checked_at
        """,
        (
            int(curve_id), str(source), str(status), source_label, source_url,
            int(source_rank) if source_rank is not None else None,
            json.dumps(metadata or {}, sort_keys=True), ts,
        ),
    )
    db.commit()


def _icarm_snapshot_rows(db):
    """Return the finite active ICARM snapshot in worker-safe JSON form."""
    rows = db.execute(
        "SELECT source_id,source_url,rank_lower_bound,proof_method,conductor,a_invariants_json "
        "FROM external_curves WHERE source='icarm' AND active=1 ORDER BY source_id"
    ).fetchall()
    out = []
    for row in rows:
        try:
            ainvs = json.loads(row["a_invariants_json"] or "[]")
        except Exception:
            continue
        if len(ainvs) != 5:
            continue
        out.append({
            "source_id": str(row["source_id"]),
            "source_url": row["source_url"],
            "rank_lower_bound": int(row["rank_lower_bound"] or 0),
            "proof_method": row["proof_method"],
            "conductor": str(row["conductor"]) if row["conductor"] is not None else None,
            "a_invariants": [str(x) for x in ainvs],
        })
    return out


def _run_icarm_exact_worker(local_ainvs, rows, *, timeout: int = 30):
    """Run exact ICARM matching out-of-process with a hard wall-clock timeout."""
    payload = {
        "local_ainvs": [str(x) for x in local_ainvs],
        "rows": list(rows),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "rank42.icarm_math_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
            cwd=str(Path(__file__).resolve().parents[1]),
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"ICARM exact match exceeded {int(timeout)}s") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "ICARM exact worker failed").strip()
        raise RuntimeError(err[-2000:])
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("ICARM exact worker returned no result")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ICARM exact worker returned malformed result: {lines[-1][-500:]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("ICARM exact worker result is not an object")
    return result


def _check_icarm_local(db, curve_id, E, *, timeout: int = 30):
    """Bounded exact lookup against the synchronized ICARM snapshot.

    We intentionally avoid ``E.conductor()`` here.  Conductor is only a filter
    and can require expensive factorization for large specializations.  Exact
    equality of j-invariants is a cheap necessary prefilter; Sage
    ``is_isomorphic`` is then the final exact Q-isomorphism test.  Both happen
    in a killable child process, so a manual catalog check cannot wedge forever.
    """
    rows = _icarm_snapshot_rows(db)
    total = len(rows)
    if not total:
        _upsert_check(
            db, curve_id=curve_id, source="icarm", status="not_synced",
            metadata={"meaning": "ICARM public database snapshot is not available locally"},
        )
        return None

    result = _run_icarm_exact_worker(E.a_invariants(), rows, timeout=int(timeout))
    source_id = result.get("source_id") if result.get("status") == "known" else None
    if source_id is not None:
        hit = next((row for row in rows if str(row["source_id"]) == str(source_id)), None)
        if hit is None:
            raise RuntimeError(f"ICARM exact worker returned unknown source id {source_id!r}")
        _upsert_check(
            db, curve_id=curve_id, source="icarm", status="known",
            source_label=str(hit["source_id"]), source_url=hit["source_url"],
            source_rank=int(hit["rank_lower_bound"] or 0),
            metadata={
                "match": "Q-isomorphic",
                "proof_method": hit["proof_method"],
                "checked_active_rows": int(total),
                "prefilter": "exact_j_invariant",
                "j_candidates": int(result.get("j_candidates") or 0),
                "hard_timeout_seconds": int(timeout),
            },
        )
        return hit

    _upsert_check(
        db, curve_id=curve_id, source="icarm", status="not_found_synced",
        metadata={
            "checked_active_rows": int(total),
            "prefilter": "exact_j_invariant",
            "j_candidates": int(result.get("j_candidates") or 0),
            "hard_timeout_seconds": int(timeout),
            "warning": "ICARM is a high-rank leaderboard, not a complete catalog.",
        },
    )
    return None


def _record_lmfdb_failure(db, *, curve_id: int, conductor: int, exc) -> str:
    """Record a failed retry without erasing an earlier catalog conclusion."""
    previous = db.execute(
        "SELECT * FROM curve_catalog_checks WHERE curve_id=? AND source='lmfdb'",
        (int(curve_id),),
    ).fetchone()
    definitive = {"known", "not_found_complete_range", "not_found"}
    if previous is not None and previous["status"] in definitive:
        try:
            metadata = json.loads(previous["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        metadata.update({
            "last_attempt_status": "check_failed",
            "last_attempt_error": repr(exc),
            "last_attempt_at": _utcnow(),
            "preserved_status": previous["status"],
        })
        db.execute(
            "UPDATE curve_catalog_checks SET metadata_json=? WHERE curve_id=? AND source='lmfdb'",
            (json.dumps(metadata, sort_keys=True), int(curve_id)),
        )
        db.commit()
        return str(previous["status"])
    _upsert_check(db, curve_id=curve_id, source="lmfdb", status="check_failed",
                  metadata={"error": repr(exc), "queried_conductor": conductor})
    return "check_failed"



def check_icarm_on_write(
    db, curve_id: int, E, *, cache_dir: str = ".rank42-cache/catalogs/icarm",
    timeout: int = 30, max_age_hours: float = 24.0,
) -> dict:
    """Low-level ICARM lookup used by explicit/manual catalog commands.

    Search and import runners intentionally do not call this helper automatically.
    The public ICARM snapshot is refreshed at most once per freshness window, then
    exact Sage Q-isomorphism is checked against the local snapshot.  This records
    a source-specific catalog fact only; absence from ICARM is not a novelty proof.

    The historical function name is retained for API compatibility.
    """
    sync=ensure_icarm_synced(
        db, cache_dir=cache_dir, timeout=int(timeout),
        max_age_hours=float(max_age_hours),
    )
    snapshot=db.execute(
        "SELECT fetched_at FROM external_catalogs WHERE source='icarm'"
    ).fetchone()
    check=db.execute(
        "SELECT * FROM curve_catalog_checks WHERE curve_id=? AND source='icarm'",
        (int(curve_id),),
    ).fetchone()
    if check is not None and snapshot is not None and snapshot['fetched_at']:
        checked=_parse_time(check['checked_at'])
        fetched=_parse_time(snapshot['fetched_at'])
        if (
            check['status'] in {'known','not_found_synced'} and checked and fetched
            and checked >= fetched
        ):
            return {
                'status':str(check['status']),
                'source_id':str(check['source_label']) if check['source_label'] else None,
                'snapshot':sync,
                'cached':True,
            }
    try:
        hit=_check_icarm_local(db,int(curve_id),E,timeout=int(timeout))
    except TimeoutError as exc:
        _upsert_check(
            db, curve_id=int(curve_id), source='icarm', status='check_timeout',
            metadata={
                'error':repr(exc), 'snapshot':sync, 'trigger':'manual_catalog_check',
                'hard_timeout_seconds':int(timeout),
                'meaning':'Exact ICARM comparison was inconclusive because the bounded worker timed out.',
            },
        )
        return {'status':'check_timeout','error':repr(exc),'snapshot':sync}
    except Exception as exc:
        _upsert_check(
            db, curve_id=int(curve_id), source='icarm', status='check_failed',
            metadata={'error':repr(exc),'snapshot':sync,'trigger':'manual_catalog_check'},
        )
        return {'status':'check_failed','error':repr(exc),'snapshot':sync}
    row=db.execute(
        "SELECT * FROM curve_catalog_checks WHERE curve_id=? AND source='icarm'",
        (int(curve_id),),
    ).fetchone()
    return {
        'status':str(row['status']) if row is not None else 'unknown',
        'source_id':str(hit['source_id']) if hit is not None else None,
        'snapshot':sync,
        'cached':False,
    }

def check_curve_catalogs(
    db,
    curve_id: int,
    E,
    *,
    lmfdb_timeout: int = 8,
    lmfdb_cache_hours: float = 24,
    force_lmfdb: bool = False,
    lmfdb_transport: str = "auto",
    lmfdb_api_fallback: bool = True,
    lmfdb_prefetched: dict | None = None,
    auto_sync_icarm: bool = True,
    icarm_cache_dir: str = ".rank42-cache/catalogs/icarm",
    icarm_max_age_hours: float = 24,
    icarm_timeout: int = 30,
) -> dict:
    """Check one Sage curve against LMFDB and ICARM.

    The LMFDB match is verified by exact Sage Q-isomorphism after SQL-first
    conductor retrieval (HTTP API fallback).  ICARM lookup is performed against its locally stored public
    snapshot; when requested, a stale/missing snapshot is refreshed first.
    """
    conductor = int(E.conductor())
    result = {"curve_id": int(curve_id), "conductor": conductor}

    if auto_sync_icarm:
        sync_info = ensure_icarm_synced(
            db,
            cache_dir=icarm_cache_dir,
            timeout=int(icarm_timeout),
            max_age_hours=float(icarm_max_age_hours),
        )
        result["icarm_snapshot"] = sync_info

    try:
        if lmfdb_prefetched is not None:
            if "error" in lmfdb_prefetched:
                raise lmfdb_prefetched["error"]
            rows = list(lmfdb_prefetched.get("rows") or [])
            fetch_meta = dict(lmfdb_prefetched.get("meta") or {})
        else:
            rows, fetch_meta = fetch_lmfdb_cached(
                db,
                conductor,
                timeout=lmfdb_timeout,
                cache_hours=lmfdb_cache_hours,
                force=force_lmfdb,
                transport=lmfdb_transport,
                api_fallback=lmfdb_api_fallback,
            )
        from sage.all import QQ, EllipticCurve
        hit = None
        exact_ainvs = [str(a) for a in E.a_invariants()]
        for row in rows:
            try:
                row_ainvs = row.get("ainvs") or []
                F = EllipticCurve(QQ, [QQ(x) for x in row_ainvs])
                if E.is_isomorphic(F):
                    hit = row
                    break
            except Exception:
                continue
        if hit:
            label = str(hit.get("lmfdb_label") or hit.get("Clabel") or "")
            _upsert_check(
                db, curve_id=curve_id, source="lmfdb", status="known",
                source_label=label,
                source_url=(LMFDB_CURVE_BASE + label if label else None),
                source_rank=(int(hit["rank"]) if hit.get("rank") is not None else None),
                metadata={
                    "match": "Q-isomorphic",
                    "queried_conductor": conductor,
                    "transport": fetch_meta.get("transport", "cache"),
                    "typed_query": f"conductor=i{conductor}" if fetch_meta.get("transport") == "api" else None,
                    "rows_at_conductor": len(rows),
                    "local_ainvs": exact_ainvs,
                    "catalog_ainvs": [str(x) for x in (hit.get("ainvs") or [])],
                    "fetch": fetch_meta,
                },
            )
            result["lmfdb"] = {
                "status": "known", "label": label,
                "rank": (int(hit["rank"]) if hit.get("rank") is not None else None),
                "fetch": fetch_meta,
            }
        elif fetch_meta.get("cache") == "stale_fallback":
            # A stale positive match would have been accepted above.  A stale
            # negative is not enough to claim the current catalog lacks it.
            exc = RuntimeError(fetch_meta.get("network_error") or "LMFDB unavailable; stale cache only")
            preserved = _record_lmfdb_failure(db, curve_id=curve_id, conductor=conductor, exc=exc)
            result["lmfdb"] = {
                "status": preserved,
                "attempt_status": "check_failed",
                "fetch": fetch_meta,
                "preserved_previous_result": preserved != "check_failed",
            }
        else:
            complete = conductor <= LMFDB_COMPLETE_CONDUCTOR
            status = "not_found_complete_range" if complete else "not_found"
            _upsert_check(
                db, curve_id=curve_id, source="lmfdb", status=status,
                metadata={
                    "queried_conductor": conductor,
                    "transport": fetch_meta.get("transport", "cache"),
                    "typed_query": f"conductor=i{conductor}" if fetch_meta.get("transport") == "api" else None,
                    "rows_at_conductor": len(rows),
                    "complete_conductor_range": bool(complete),
                    "fetch": fetch_meta,
                    "warning": "Catalog absence is not proof of a new mathematical curve.",
                },
            )
            result["lmfdb"] = {"status": status, "fetch": fetch_meta}
    except (
        urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, RuntimeError,
        LMFDBSQLUnavailable, LMFDBSQLFailure,
    ) as exc:
        preserved = _record_lmfdb_failure(db, curve_id=curve_id, conductor=conductor, exc=exc)
        result["lmfdb"] = {
            "status": preserved,
            "attempt_status": "check_failed",
            "error": repr(exc),
            "preserved_previous_result": preserved != "check_failed",
        }

    icarm = _check_icarm_local(db, curve_id, E, timeout=int(icarm_timeout))
    result["icarm"] = {"status": "known", "source_id": str(icarm["source_id"])} if icarm else {
        "status": (db.execute(
            "SELECT status FROM curve_catalog_checks WHERE curve_id=? AND source='icarm'", (curve_id,)
        ).fetchone() or {"status": "unknown"})["status"]
    }
    return result


def checks_for_curve(db, curve_id: int) -> dict[str, dict]:
    rows = db.execute(
        "SELECT * FROM curve_catalog_checks WHERE curve_id=? ORDER BY source", (int(curve_id),)
    ).fetchall()
    return {str(r["source"]): dict(r) for r in rows}


def summarize_source_status(checks: dict[str, dict], source: str) -> tuple[str, str]:
    row = checks.get(str(source))
    if not row:
        return "Not checked", ""
    status = row.get("status")
    if source == "lmfdb":
        if status == "known":
            return "Known", str(row.get("source_label") or "match")
        if status == "not_found_complete_range":
            return "Not in LMFDB", "complete conductor range"
        if status == "not_found":
            return "Not found", "outside complete range"
        if status == "check_failed":
            return "Lookup failed", "retry"
    if source == "icarm":
        if status == "known":
            return "Known", f"#{row.get('source_label') or '?'}"
        if status == "not_found_synced":
            return "No match", "synced leaderboard"
        if status == "not_synced":
            return "Not synced", "refresh snapshot"
    return str(status or "Unknown"), ""


def summarize_known_status(checks: dict[str, dict]) -> tuple[str, str]:
    """Return operator-facing combined status without claiming global novelty."""
    lmfdb = checks.get("lmfdb")
    icarm = checks.get("icarm")
    if lmfdb and lmfdb.get("status") == "known":
        label = lmfdb.get("source_label") or "match"
        rank = lmfdb.get("source_rank")
        return "Known", f"LMFDB {label}" + (f" · rank {rank}" if rank is not None else "")
    if icarm and icarm.get("status") == "known":
        return "Known", f"ICARM #{icarm.get('source_label') or '?'}"
    if lmfdb and lmfdb.get("status") == "not_found_complete_range":
        return "Not in LMFDB", "conductor is in LMFDB's declared complete range; still not proof of global novelty"
    if lmfdb and lmfdb.get("status") == "not_found":
        return "Not found", "not found in checked LMFDB data; conductor outside declared complete range"
    if lmfdb and lmfdb.get("status") == "check_failed":
        return "Check failed", "LMFDB SQL/API lookup failed; ICARM may still have been checked locally"
    return "Not checked", "run the catalog check"


def curve_needs_catalog_check(db, curve_id: int) -> bool:
    checks = checks_for_curve(db, int(curve_id))
    lmfdb = checks.get("lmfdb")
    if not lmfdb or lmfdb.get("status") == "check_failed":
        return True
    icarm = checks.get("icarm")
    if not icarm or icarm.get("status") == "not_synced":
        return True
    snapshot = db.execute("SELECT fetched_at FROM external_catalogs WHERE source='icarm'").fetchone()
    if snapshot and snapshot["fetched_at"]:
        checked_at = _parse_time(icarm.get("checked_at"))
        fetched_at = _parse_time(snapshot["fetched_at"])
        if fetched_at and (checked_at is None or fetched_at > checked_at):
            return True
    return False


def parse_args():
    ap = argparse.ArgumentParser(description="Check local Rank Hunter curves against known catalogs")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int)
    ap.add_argument("--family")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=8)
    ap.add_argument("--unchecked-only", action="store_true", help="skip curves with current LMFDB+ICARM results")
    ap.add_argument("--force-lmfdb", action="store_true", help="bypass the fresh LMFDB conductor cache")
    ap.add_argument("--lmfdb-cache-hours", type=float, default=24.0)
    ap.add_argument("--lmfdb-transport", choices=("auto", "sql", "api"), default="auto",
                    help="LMFDB transport: auto=SQL mirror first with HTTP API fallback")
    ap.add_argument("--no-lmfdb-api-fallback", action="store_true",
                    help="with auto/sql transport, do not fall back to the HTTP API")
    ap.add_argument("--probe-lmfdb-sql", action="store_true",
                    help="probe the public SQL mirror with conductor 88024 and exit")
    ap.add_argument("--icarm-cache-dir", default=".rank42-cache/catalogs/icarm")
    ap.add_argument("--icarm-max-age-hours", type=float, default=24.0)
    ap.add_argument("--icarm-timeout", type=int, default=30)
    ap.add_argument("--force-icarm-refresh", action="store_true")
    ap.add_argument("--no-icarm-refresh", action="store_true")
    return ap.parse_args()


def _prefetch_summary(prefetched: dict[int, dict]) -> dict:
    counts = {"cache": 0, "sql": 0, "api": 0, "stale": 0, "error": 0}
    for item in prefetched.values():
        if "error" in item:
            counts["error"] += 1
            continue
        meta = item.get("meta") or {}
        if meta.get("cache") == "stale_fallback":
            counts["stale"] += 1
        elif meta.get("cache") == "hit":
            counts["cache"] += 1
        elif meta.get("transport") == "sql":
            counts["sql"] += 1
        elif meta.get("transport") == "api":
            counts["api"] += 1
    return {"conductors": len(prefetched), **counts}


def main():
    args = parse_args()

    if args.probe_lmfdb_sql:
        info = probe_lmfdb_sql(conductor=88024, timeout=float(args.timeout))
        print(json.dumps(info, sort_keys=True), flush=True)
        return

    from sage.all import QQ, EllipticCurve
    from rank42.db import connect

    db = connect(args.db)
    try:
        if not args.no_icarm_refresh:
            sync_info = ensure_icarm_synced(
                db,
                cache_dir=args.icarm_cache_dir,
                timeout=int(args.icarm_timeout),
                max_age_hours=float(args.icarm_max_age_hours),
                force=bool(args.force_icarm_refresh),
            )
            print(json.dumps({"icarm_snapshot": sync_info}, sort_keys=True), flush=True)

        if args.curve_id:
            rows = db.execute("SELECT * FROM curves WHERE id=?", (int(args.curve_id),)).fetchall()
        elif args.family:
            rows = db.execute(
                "SELECT * FROM curves WHERE family=? AND a_invariants_json IS NOT NULL ORDER BY id DESC LIMIT ?",
                (args.family, int(args.limit)),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM curves WHERE a_invariants_json IS NOT NULL ORDER BY id DESC LIMIT ?", (int(args.limit),)
            ).fetchall()

        if args.unchecked_only:
            rows = [row for row in rows if curve_needs_catalog_check(db, int(row["id"]))]
            print(json.dumps({"unchecked_curves": len(rows)}, sort_keys=True), flush=True)

        work = []
        for row in rows:
            ainvs = json.loads(row["a_invariants_json"] or "[]")
            if len(ainvs) != 5:
                continue
            E = EllipticCurve(QQ, [QQ(x) for x in ainvs])
            work.append((row, E, int(E.conductor())))

        prefetched = prefetch_lmfdb_cached(
            db,
            [conductor for _, _, conductor in work],
            timeout=int(args.timeout),
            cache_hours=float(args.lmfdb_cache_hours),
            force=bool(args.force_lmfdb),
            transport=str(args.lmfdb_transport),
            api_fallback=not bool(args.no_lmfdb_api_fallback),
        )
        if work:
            print(json.dumps({"lmfdb_prefetch": _prefetch_summary(prefetched)}, sort_keys=True), flush=True)

        for row, E, conductor in work:
            info = check_curve_catalogs(
                db,
                int(row["id"]),
                E,
                lmfdb_timeout=int(args.timeout),
                lmfdb_cache_hours=float(args.lmfdb_cache_hours),
                force_lmfdb=bool(args.force_lmfdb),
                lmfdb_transport=str(args.lmfdb_transport),
                lmfdb_api_fallback=not bool(args.no_lmfdb_api_fallback),
                lmfdb_prefetched=prefetched.get(conductor),
                auto_sync_icarm=False,
                icarm_cache_dir=args.icarm_cache_dir,
                icarm_max_age_hours=float(args.icarm_max_age_hours),
                icarm_timeout=int(args.icarm_timeout),
            )
            print(json.dumps(info, sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
