import json
from pathlib import Path

from rank42.db import connect, upsert_curve, update_curve
from rank42.novelty import fetch_lmfdb_by_conductor, summarize_known_status


def test_curve_catalog_check_table_is_additive(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "curve_catalog_checks" in names
        cid = upsert_curve(db, family="demo", parameter="1")
        db.execute(
            "INSERT INTO curve_catalog_checks(curve_id,source,status,checked_at) VALUES(?,?,?,?)",
            (cid, "lmfdb", "known", "now"),
        )
        db.commit()
        assert db.execute("SELECT COUNT(*) AS n FROM curve_catalog_checks").fetchone()["n"] == 1
    finally:
        db.close()


def test_known_status_contract():
    status, detail = summarize_known_status({
        "lmfdb": {"status": "known", "source_label": "88024.x1", "source_rank": 3}
    })
    assert status == "Known"
    assert "88024.x1" in detail
    assert "rank 3" in detail

    status, detail = summarize_known_status({"lmfdb": {"status": "not_found_complete_range"}})
    assert status == "Not in LMFDB"
    assert "not proof" in detail.lower()


def test_lmfdb_fetch_uses_conductor_and_follows_pages(monkeypatch):
    seen = []
    def fake_get(url, timeout):
        seen.append(url)
        if len(seen) == 1:
            return {"data": [{"lmfdb_label": "x"}], "next": "/api/ec_curvedata/?_format=json&conductor=123&_offset=1"}
        return {"data": [{"lmfdb_label": "y"}], "next": None}
    monkeypatch.setattr("rank42.novelty._json_get", fake_get)
    rows = fetch_lmfdb_by_conductor(123, timeout=2)
    assert [r["lmfdb_label"] for r in rows] == ["x", "y"]
    assert "conductor=i123" in seen[0]


def test_failed_retry_does_not_erase_prior_not_found(tmp_path):
    from rank42.novelty import _record_lmfdb_failure, checks_for_curve
    db = connect(tmp_path / "rank42.db")
    try:
        cid = upsert_curve(db, family="demo", parameter="x")
        db.execute(
            "INSERT INTO curve_catalog_checks(curve_id,source,status,metadata_json,checked_at) VALUES(?,?,?,?,?)",
            (cid, "lmfdb", "not_found", json.dumps({"queried_conductor": 999999}), "now"),
        )
        db.commit()
        status = _record_lmfdb_failure(db, curve_id=cid, conductor=999999, exc=TimeoutError("temporary"))
        assert status == "not_found"
        row = checks_for_curve(db, cid)["lmfdb"]
        assert row["status"] == "not_found"
        metadata = json.loads(row["metadata_json"])
        assert metadata["last_attempt_status"] == "check_failed"
        assert metadata["preserved_status"] == "not_found"
    finally:
        db.close()


def test_failed_retry_does_not_erase_prior_known(tmp_path):
    from rank42.novelty import _record_lmfdb_failure, checks_for_curve
    db = connect(tmp_path / "rank42.db")
    try:
        cid = upsert_curve(db, family="demo", parameter="x")
        db.execute(
            "INSERT INTO curve_catalog_checks(curve_id,source,status,source_label,checked_at) VALUES(?,?,?,?,?)",
            (cid, "lmfdb", "known", "123.a1", "now"),
        )
        db.commit()
        status = _record_lmfdb_failure(db, curve_id=cid, conductor=123, exc=TimeoutError("temporary"))
        assert status == "known"
        row = checks_for_curve(db, cid)["lmfdb"]
        assert row["status"] == "known"
        assert row["source_label"] == "123.a1"
    finally:
        db.close()


def test_lmfdb_query_uses_documented_typed_integer_and_reduced_fields():
    from urllib.parse import parse_qs, urlparse
    from rank42.novelty import LMFDB_FIELDS, lmfdb_query_url

    url = lmfdb_query_url(123)
    query = parse_qs(urlparse(url).query)
    assert query["conductor"] == ["i123"]
    assert query["_format"] == ["json"]
    assert query["_fields"] == [",".join(LMFDB_FIELDS)]


def test_lmfdb_conductor_cache_avoids_duplicate_network_calls(tmp_path, monkeypatch):
    from rank42.novelty import fetch_lmfdb_cached

    db = connect(tmp_path / "rank42.db")
    calls = []
    try:
        def fake_fetch(conductor, *, timeout=8, max_pages=50):
            calls.append((conductor, timeout))
            return [{"lmfdb_label": "123.a1", "ainvs": [0, 0, 0, -1, 0], "conductor": 123}]

        monkeypatch.setattr("rank42.novelty.fetch_lmfdb_by_conductor", fake_fetch)
        first, meta1 = fetch_lmfdb_cached(db, 123, timeout=2, cache_hours=24, transport="api")
        second, meta2 = fetch_lmfdb_cached(db, 123, timeout=2, cache_hours=24, transport="api")
        assert first == second
        assert len(calls) == 1
        assert meta1["cache"] == "miss"
        assert meta2["cache"] == "hit"
        row = db.execute(
            "SELECT * FROM catalog_query_cache WHERE source='lmfdb' AND query_key='conductor:123'"
        ).fetchone()
        assert row is not None
    finally:
        db.close()


def test_lmfdb_stale_cache_is_positive_only_fallback(tmp_path, monkeypatch):
    from rank42.novelty import fetch_lmfdb_cached

    db = connect(tmp_path / "rank42.db")
    try:
        db.execute(
            """
            INSERT INTO catalog_query_cache(source,query_key,status,payload_json,metadata_json,fetched_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                "lmfdb", "conductor:999", "ok",
                json.dumps([{"lmfdb_label": "999.a1", "ainvs": [0, 0, 0, -1, 0]}]),
                "{}", "2000-01-01T00:00:00+00:00",
            ),
        )
        db.commit()

        def fail(*args, **kwargs):
            raise TimeoutError("offline")

        monkeypatch.setattr("rank42.novelty.fetch_lmfdb_by_conductor", fail)
        rows, meta = fetch_lmfdb_cached(db, 999, timeout=1, cache_hours=1, transport="api")
        assert rows[0]["lmfdb_label"] == "999.a1"
        assert meta["cache"] == "stale_fallback"
        assert meta["fresh"] is False
        assert "offline" in meta["network_error"]
    finally:
        db.close()


def test_curve_needs_catalog_check_tracks_new_icarm_snapshot(tmp_path):
    from rank42.novelty import curve_needs_catalog_check

    db = connect(tmp_path / "rank42.db")
    try:
        cid = upsert_curve(db, family="demo", parameter="x")
        db.execute(
            "INSERT INTO curve_catalog_checks(curve_id,source,status,checked_at) VALUES(?,?,?,?)",
            (cid, "lmfdb", "known", "2026-08-20T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO curve_catalog_checks(curve_id,source,status,checked_at) VALUES(?,?,?,?)",
            (cid, "icarm", "not_found_synced", "2026-08-20T00:00:00+00:00"),
        )
        db.execute(
            """
            INSERT INTO external_catalogs(source,source_url,curve_count,best_rank_lower,fetched_at,updated_at)
            VALUES(?,?,?,?,?,?)
            """,
            ("icarm", "x", 1, 1, "2026-08-21T00:00:00+00:00", "2026-08-21T00:00:00+00:00"),
        )
        db.commit()
        assert curve_needs_catalog_check(db, cid) is True
        db.execute(
            "UPDATE curve_catalog_checks SET checked_at=? WHERE curve_id=? AND source='icarm'",
            ("2026-08-22T00:00:00+00:00", cid),
        )
        db.commit()
        assert curve_needs_catalog_check(db, cid) is False
    finally:
        db.close()


def test_lmfdb_batch_prefetch_uses_one_sql_query_for_multiple_conductors(tmp_path, monkeypatch):
    from rank42.novelty import prefetch_lmfdb_cached

    db = connect(tmp_path / "rank42.db")
    calls = []
    try:
        def fake_sql(conductors, *, timeout=8):
            values = list(conductors)
            calls.append(values)
            return {
                c: [{"lmfdb_label": f"{c}.a1", "ainvs": [0, 0, 0, -1, 0], "conductor": c, "rank": 1}]
                for c in values
            }, {"transport": "sql", "driver": "fake", "requested_conductors": len(values)}

        monkeypatch.setattr("rank42.novelty.fetch_lmfdb_sql_many", fake_sql)
        out = prefetch_lmfdb_cached(db, [123, 456, 123], timeout=2)
        assert calls == [[123, 456]]
        assert out[123]["meta"]["transport"] == "sql"
        assert out[456]["rows"][0]["lmfdb_label"] == "456.a1"

        # Second pass is entirely SQLite cache; no second SQL call.
        again = prefetch_lmfdb_cached(db, [123, 456], timeout=2)
        assert calls == [[123, 456]]
        assert again[123]["meta"]["cache"] == "hit"
    finally:
        db.close()


def test_lmfdb_sql_failure_falls_back_to_api(tmp_path, monkeypatch):
    from rank42.lmfdb_sql import LMFDBSQLFailure
    from rank42.novelty import prefetch_lmfdb_cached

    db = connect(tmp_path / "rank42.db")
    api_calls = []
    try:
        def fail_sql(*args, **kwargs):
            raise LMFDBSQLFailure("mirror unavailable")

        def fake_api(conductor, *, timeout=8, max_pages=50):
            api_calls.append(conductor)
            return [{"lmfdb_label": f"{conductor}.a1", "ainvs": [0, 0, 0, -1, 0], "conductor": conductor, "rank": 2}]

        monkeypatch.setattr("rank42.novelty.fetch_lmfdb_sql_many", fail_sql)
        monkeypatch.setattr("rank42.novelty.fetch_lmfdb_by_conductor", fake_api)
        monkeypatch.setattr("rank42.novelty.time.sleep", lambda *_: None)
        out = prefetch_lmfdb_cached(db, [321, 654], timeout=2)
        assert api_calls == [321, 654]
        assert out[321]["meta"]["transport"] == "api"
        assert "mirror unavailable" in out[321]["meta"]["sql_fallback_reason"]
    finally:
        db.close()


def test_json_get_retries_empty_200_body(monkeypatch):
    from rank42.novelty import _json_get

    class Headers:
        def get(self, key, default=None):
            return "application/json" if key.lower() == "content-type" else default

    class Response:
        def __init__(self, body):
            self.body = body
            self.headers = Headers()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return self.body

    bodies = [b"", b'{"data": []}']
    monkeypatch.setattr("rank42.novelty.urllib.request.urlopen", lambda *args, **kwargs: Response(bodies.pop(0)))
    monkeypatch.setattr("rank42.novelty.time.sleep", lambda *_: None)
    assert _json_get("https://example.test", 1, attempts=2) == {"data": []}
