"""External high-rank elliptic-curve catalogs.

The catalog layer is deliberately separate from the local ``curves`` table.
External lower bounds are reference data: they must never raise Rank Hunter's local
incumbent, affect pruning, or be reported as project discoveries.

The first adapter targets the public ICARM Elliptic Curve Rank Leaderboard:
https://elliptic-rank.icarm.cloud/database.json

ICARM states that accepted witness sets are checked exactly on-curve and that
independence is certified by an exact Cremona/Brumer 2-descent
quadratic-character computation.  Rank Hunter records that source claim as
provenance; the synchronized JSON does not contain the underlying certificate
matrix itself.  Local re-certification is provided separately by
``rank42.exact_lb``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from rank42.db import connect, now

ICARM_SOURCE = "icarm"
ICARM_DATABASE_URL = "https://elliptic-rank.icarm.cloud/database.json"
ICARM_HOME_URL = "https://elliptic-rank.icarm.cloud/"
ICARM_PROOF_METHOD = (
    "source-certified exact 2-descent quadratic-character independence "
    "(Cremona/Brumer), modulo torsion"
)
USER_AGENT = "Rank-Hunter/0.7.14 (+https://elliptic-rank.icarm.cloud/)"


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_icarm_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("ICARM snapshot must be a JSON object")
    curves = payload.get("curves")
    if not isinstance(curves, list):
        raise ValueError("ICARM snapshot has no curves array")
    declared = payload.get("count")
    if declared is not None and int(declared) != len(curves):
        raise ValueError(
            f"ICARM count mismatch: declared {declared}, found {len(curves)}"
        )
    seen_ids = set()
    for i, row in enumerate(curves):
        if not isinstance(row, dict):
            raise ValueError(f"ICARM curve[{i}] is not an object")
        for key in ("id", "ainvs", "rank_lower_bound", "points"):
            if key not in row:
                raise ValueError(f"ICARM curve[{i}] missing {key}")
        source_id = int(row["id"])
        if source_id in seen_ids:
            raise ValueError(f"duplicate ICARM curve id {source_id}")
        seen_ids.add(source_id)
        ainvs = row["ainvs"]
        if not isinstance(ainvs, list) or len(ainvs) not in (2, 5):
            raise ValueError(f"ICARM curve {source_id}: invalid ainvs")
        points = row["points"]
        if not isinstance(points, list):
            raise ValueError(f"ICARM curve {source_id}: points is not a list")
        if int(row["rank_lower_bound"]) < 0:
            raise ValueError(f"ICARM curve {source_id}: negative rank lower bound")
        torsion = row.get("torsion")
        if torsion is not None:
            if (
                not isinstance(torsion, list)
                or not all(
                    isinstance(value, int) and not isinstance(value, bool) and value > 1
                    for value in torsion
                )
            ):
                raise ValueError(
                    f"ICARM curve {source_id}: invalid torsion invariant factors"
                )
    return payload


def load_snapshot(path: str | os.PathLike) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    payload = validate_icarm_payload(json.loads(raw.decode("utf-8")))
    return payload, raw


def fetch_icarm(*, timeout: int = 30, url: str = ICARM_DATABASE_URL) -> tuple[dict, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ICARM download failed: {exc}") from exc
    payload = validate_icarm_payload(json.loads(raw.decode("utf-8")))
    return payload, raw


def snapshot_filename(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    return f"database-{ts.strftime('%Y%m%dT%H%M%SZ')}.json"


def write_snapshot(raw: bytes, cache_dir: str | os.PathLike) -> Path:
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    out = root / snapshot_filename()
    fd, tmp_name = tempfile.mkstemp(prefix=".icarm-", suffix=".json", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, out)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    latest = root / "latest.json"
    latest_tmp = root / ".latest.json.tmp"
    latest_tmp.write_bytes(raw)
    os.replace(latest_tmp, latest)
    return out


def import_icarm_payload(db, payload: dict, *, snapshot_path=None, raw_sha256=None) -> dict:
    payload = validate_icarm_payload(payload)
    curves = payload["curves"]
    ts = now()
    best = max((int(r.get("rank_lower_bound") or 0) for r in curves), default=0)

    db.execute("BEGIN IMMEDIATE")
    try:
        # Parent row first so external_curves can keep a real foreign key while
        # the import remains one atomic transaction.
        db.execute(
            """
            INSERT INTO external_catalogs(
                source, source_url, curve_count, best_rank_lower,
                snapshot_path, raw_sha256, fetched_at, metadata_json, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
                source_url=excluded.source_url,
                curve_count=excluded.curve_count,
                best_rank_lower=excluded.best_rank_lower,
                snapshot_path=excluded.snapshot_path,
                raw_sha256=excluded.raw_sha256,
                fetched_at=excluded.fetched_at,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                ICARM_SOURCE, ICARM_DATABASE_URL, len(curves), best,
                str(snapshot_path) if snapshot_path else None, raw_sha256, ts,
                json.dumps(
                    {
                        "proof_method": ICARM_PROOF_METHOD,
                        "home_url": ICARM_HOME_URL,
                        "acknowledgement": (
                            "ICARM / NSF Institute for Computer-Aided Reasoning in "
                            "Mathematics; NSF Grant DMS 2425401"
                        ),
                    },
                    sort_keys=True,
                ),
                ts,
            ),
        )
        # Preserve historical rows and any Rank Hunter search results attached to
        # them.  A source deletion marks a row inactive instead of erasing
        # scientific provenance through ON DELETE CASCADE.
        db.execute(
            "UPDATE external_curves SET active=0, updated_at=? WHERE source=?",
            (ts, ICARM_SOURCE),
        )

        for row in curves:
            source_id = str(int(row["id"]))
            ainvs = [str(x) for x in row.get("ainvs") or []]
            points = [[str(p[0]), str(p[1])] for p in (row.get("points") or [])]
            bad_primes = [str(x) for x in (row.get("bad_primes") or [])]
            raw_row = _canonical_json(row)
            db.execute(
                """
                INSERT INTO external_curves(
                    source, source_id, curve_key, a_invariants_json,
                    rank_lower_bound, proof_method, points_json, conductor,
                    bad_primes_json, discriminant, naive_height,
                    faltings_height, regulator, submitter, commentary,
                    source_created_at, source_updated_at, source_url,
                    raw_json, active, last_seen_at, imported_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    curve_key=excluded.curve_key,
                    a_invariants_json=excluded.a_invariants_json,
                    rank_lower_bound=excluded.rank_lower_bound,
                    proof_method=excluded.proof_method,
                    points_json=excluded.points_json,
                    conductor=excluded.conductor,
                    bad_primes_json=excluded.bad_primes_json,
                    discriminant=excluded.discriminant,
                    naive_height=excluded.naive_height,
                    faltings_height=excluded.faltings_height,
                    regulator=excluded.regulator,
                    submitter=excluded.submitter,
                    commentary=excluded.commentary,
                    source_created_at=excluded.source_created_at,
                    source_updated_at=excluded.source_updated_at,
                    source_url=excluded.source_url,
                    raw_json=excluded.raw_json,
                    active=1,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
                """,
                (
                    ICARM_SOURCE,
                    source_id,
                    row.get("curve_key"),
                    json.dumps(ainvs),
                    int(row.get("rank_lower_bound") or 0),
                    ICARM_PROOF_METHOD,
                    json.dumps(points),
                    str(row["conductor"]) if row.get("conductor") is not None else None,
                    json.dumps(bad_primes),
                    str(row["discriminant"]) if row.get("discriminant") is not None else None,
                    str(row["naive_height"]) if row.get("naive_height") is not None else None,
                    str(row["faltings_height"]) if row.get("faltings_height") is not None else None,
                    str(row["regulator"]) if row.get("regulator") is not None else None,
                    row.get("submitter"),
                    row.get("commentary"),
                    row.get("created_at"),
                    row.get("updated_at"),
                    f"https://elliptic-rank.icarm.cloud/curve/{source_id}",
                    raw_row,
                    1,
                    ts,
                    ts,
                    ts,
                ),
            )

        db.commit()
    except BaseException:
        db.rollback()
        raise

    return {
        "source": ICARM_SOURCE,
        "curve_count": len(curves),
        "best_rank_lower": best,
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "sha256": raw_sha256,
    }


def sync_icarm(
    db,
    *,
    cache_dir=".rank42-cache/catalogs/icarm",
    input_path=None,
    timeout=30,
) -> dict:
    if input_path:
        payload, raw = load_snapshot(input_path)
    else:
        payload, raw = fetch_icarm(timeout=timeout)
    snap = write_snapshot(raw, cache_dir)
    return import_icarm_payload(
        db,
        payload,
        snapshot_path=snap,
        raw_sha256=sha256_bytes(raw),
    )


def catalog_status(db, source=ICARM_SOURCE):
    return db.execute(
        "SELECT * FROM external_catalogs WHERE source=?", (source,)
    ).fetchone()


def _parse_catalog_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ensure_icarm_synced(
    db,
    *,
    cache_dir=".rank42-cache/catalogs/icarm",
    timeout=30,
    max_age_hours=24.0,
    force=False,
) -> dict:
    """Refresh ICARM only when the local public snapshot is absent or stale.

    If a refresh fails but an older imported snapshot exists, keep using that
    snapshot and report ``stale_preserved``.  Lookup remains offline after the
    synchronization step.
    """
    row = catalog_status(db, ICARM_SOURCE)
    active = int(db.execute(
        "SELECT COUNT(*) AS n FROM external_curves WHERE source=? AND active=1",
        (ICARM_SOURCE,),
    ).fetchone()["n"] or 0)
    fetched = _parse_catalog_time(row["fetched_at"]) if row is not None else None
    age_hours = None
    if fetched is not None:
        age_hours = max(0.0, (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0)
    fresh = bool(row is not None and active and age_hours is not None and age_hours <= float(max_age_hours))
    if fresh and not force:
        return {
            "status": "fresh",
            "curve_count": active,
            "fetched_at": row["fetched_at"],
            "age_hours": age_hours,
        }
    try:
        info = sync_icarm(db, cache_dir=cache_dir, timeout=int(timeout))
    except Exception as exc:
        if row is not None and active:
            return {
                "status": "stale_preserved",
                "curve_count": active,
                "fetched_at": row["fetched_at"],
                "age_hours": age_hours,
                "error": repr(exc),
            }
        return {
            "status": "unavailable",
            "curve_count": 0,
            "error": repr(exc),
        }
    return {"status": "refreshed", **info}


def external_best_lower_bound(db, source=None) -> int:
    if source is None:
        row = db.execute(
            "SELECT MAX(rank_lower_bound) AS r FROM external_curves WHERE active=1"
        ).fetchone()
    else:
        row = db.execute(
            "SELECT MAX(rank_lower_bound) AS r FROM external_curves WHERE source=? AND active=1",
            (source,),
        ).fetchone()
    return int(row["r"] or 0)


ICARM_BEST_METRICS = ("conductor", "disc", "faltings", "naive")


def _external_raw_payload(row) -> dict:
    try:
        payload = json.loads(row["raw_json"] or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def external_torsion_factors(row):
    """Return source-provided rational torsion invariant factors, normalized.

    ICARM exports PARI elltors invariant factors. Its own UI sorts them smallest
    first for display / URL grouping. Missing or malformed source metadata stays
    unknown; Rank Hunter never recomputes torsion for external rows here.
    """

    torsion = _external_raw_payload(row).get("torsion")
    if torsion is None:
        return None
    if not isinstance(torsion, list):
        return None
    normalized = []
    for value in torsion:
        if isinstance(value, bool):
            return None
        try:
            factor = int(value)
        except (TypeError, ValueError):
            return None
        if factor <= 1:
            return None
        normalized.append(factor)
    return tuple(sorted(normalized))


def torsion_key_from_factors(factors):
    if factors is None:
        return None
    values = tuple(int(value) for value in factors)
    return "trivial" if not values else "x".join(str(value) for value in values)


def torsion_label_from_factors(factors):
    if factors is None:
        return None
    values = tuple(int(value) for value in factors)
    if not values:
        return "trivial"
    return " × ".join(f"ℤ/{value}ℤ" for value in values)


def external_torsion_key(row):
    return torsion_key_from_factors(external_torsion_factors(row))


def external_torsion_label(row):
    return torsion_label_from_factors(external_torsion_factors(row))


def icarm_torsion_groups(db, *, rank_at_least=0):
    """Return source-provided torsion groups in ICARM's displayed order."""

    rows = db.execute(
        """
        SELECT raw_json
        FROM external_curves
        WHERE source=? AND active=1 AND rank_lower_bound>=?
        """,
        (ICARM_SOURCE, int(rank_at_least)),
    ).fetchall()
    groups = {}
    for row in rows:
        factors = external_torsion_factors(row)
        key = torsion_key_from_factors(factors)
        if key is None:
            continue
        groups[key] = factors
    ordered = sorted(
        groups.items(),
        key=lambda item: (
            len(item[1]),
            item[1][-1] if item[1] else 0,
            item[1],
        ),
    )
    return [
        {
            "key": key,
            "factors": list(factors),
            "label": torsion_label_from_factors(factors),
        }
        for key, factors in ordered
    ]


def _best_metric_value(row, metric):
    metric = str(metric)
    try:
        if metric == "conductor":
            value = row["conductor"]
            return None if value in (None, "") else int(str(value))
        if metric == "disc":
            value = row["discriminant"]
            return None if value in (None, "") else abs(int(str(value)))
        if metric == "faltings":
            value = row["faltings_height"]
            return None if value in (None, "") else float(str(value))
        if metric == "naive":
            value = row["naive_height"]
            return None if value in (None, "") else float(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    raise ValueError(f"unsupported ICARM best metric {metric!r}")


def _icarm_best_frontier(rows, metric):
    """Mirror ICARM's best-only frontier for one selected metric.

    A row survives exactly when no curve of equal or higher rank in the current
    torsion selection has a strictly smaller metric value. Ties survive.
    """

    metric = str(metric)
    if metric not in ICARM_BEST_METRICS:
        raise ValueError(f"unsupported ICARM best metric {metric!r}")

    by_rank = {}
    for row in rows:
        value = _best_metric_value(row, metric)
        if value is None:
            continue
        by_rank.setdefault(int(row["rank_lower_bound"]), []).append((row, value))

    frontier = []
    running_min = None
    for rank in sorted(by_rank, reverse=True):
        group = by_rank[rank]
        group_min = min(value for _row, value in group)
        running_min = group_min if running_min is None else min(running_min, group_min)
        frontier.extend(row for row, value in group if value == running_min)

    def source_id_key(row):
        try:
            source_id = (0, int(row["source_id"]))
        except (TypeError, ValueError):
            source_id = (1, str(row["source_id"]))
        metric_value = _best_metric_value(row, metric)
        return (
            -int(row["rank_lower_bound"]),
            metric_value,
            source_id,
        )

    return sorted(frontier, key=source_id_key)


def list_external_curves(
    db,
    *,
    source=ICARM_SOURCE,
    rank_at_least=0,
    torsion=None,
    only_best=False,
    best_metric="conductor",
    limit=200,
):
    params = [source, int(rank_at_least)]
    sql = """
        SELECT * FROM external_curves
        WHERE source=? AND active=1 AND rank_lower_bound>=?
        ORDER BY rank_lower_bound DESC,
                 CAST(naive_height AS REAL) ASC,
                 CAST(source_id AS INTEGER) ASC
    """
    needs_full_projection = torsion is not None or bool(only_best)
    if not needs_full_projection:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = list(db.execute(sql, params).fetchall())

    if torsion is not None:
        wanted = str(torsion)
        rows = [row for row in rows if external_torsion_key(row) == wanted]

    if only_best:
        rows = _icarm_best_frontier(rows, best_metric)

    return rows[: max(0, int(limit))]


def get_external_curve(db, source, source_id):
    return db.execute(
        "SELECT * FROM external_curves WHERE source=? AND source_id=?",
        (str(source), str(source_id)),
    ).fetchone()


def witness_payload(row) -> dict:
    if row is None:
        raise ValueError("external curve not found")
    return {
        "source": row["source"],
        "source_id": row["source_id"],
        "source_url": row["source_url"],
        "source_rank_lower_bound": int(row["rank_lower_bound"]),
        "source_proof_method": row["proof_method"],
        "a_invariants": json.loads(row["a_invariants_json"]),
        "points": json.loads(row["points_json"]),
    }


def parse_args():
    ap = argparse.ArgumentParser(description="Synchronize and inspect external curve catalogs")
    ap.add_argument("--db", default="rank42.db")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="download/import the ICARM database")
    p.add_argument("--source", default=ICARM_SOURCE, choices=[ICARM_SOURCE])
    p.add_argument("--input", help="offline database.json snapshot")
    p.add_argument("--cache-dir", default=".rank42-cache/catalogs/icarm")
    p.add_argument("--timeout", type=int, default=30)

    p = sub.add_parser("status")
    p.add_argument("--source", default=ICARM_SOURCE)

    p = sub.add_parser("list")
    p.add_argument("--source", default=ICARM_SOURCE)
    p.add_argument("--rank-at-least", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("export-witness")
    p.add_argument("source_id")
    p.add_argument("--source", default=ICARM_SOURCE)
    p.add_argument("--out", required=True)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        if args.command == "sync":
            info = sync_icarm(
                db,
                cache_dir=args.cache_dir,
                input_path=args.input,
                timeout=args.timeout,
            )
            print(json.dumps(info, indent=2, sort_keys=True))
            return
        if args.command == "status":
            row = catalog_status(db, args.source)
            print(json.dumps(dict(row) if row else {"source": args.source, "status": "not synced"}, indent=2, sort_keys=True))
            return
        if args.command == "list":
            rows = list_external_curves(
                db,
                source=args.source,
                rank_at_least=args.rank_at_least,
                limit=args.limit,
            )
            print("SOURCE ID   RANK>=  POINTS  NAIVE HEIGHT  SUBMITTER")
            print("-" * 78)
            for r in rows:
                pts = len(json.loads(r["points_json"] or "[]"))
                print(
                    f"{r['source']:<6} {r['source_id']:>5}   {int(r['rank_lower_bound']):>5}  "
                    f"{pts:>6}  {str(r['naive_height'] or '-'):>12}  {r['submitter'] or '-'}"
                )
            return
        if args.command == "export-witness":
            row = get_external_curve(db, args.source, args.source_id)
            if row is None:
                raise SystemExit(f"external curve {args.source}:{args.source_id} not found")
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(witness_payload(row), indent=2, sort_keys=True) + "\n")
            print(out)
            return
    finally:
        db.close()


if __name__ == "__main__":
    main()
