import json

from rank42.catalog import (
    external_best_lower_bound,
    external_torsion_label,
    get_external_curve,
    icarm_torsion_groups,
    import_icarm_payload,
    list_external_curves,
    validate_icarm_payload,
    witness_payload,
)
from rank42.db import best_lower_bound, connect, upsert_curve


def sample_payload():
    return {
        "count": 2,
        "curves": [
            {
                "id": 273,
                "curve_key": "c4:c6-record",
                "ainvs": ["1", "0", "0", "-5", "7"],
                "rank_lower_bound": 30,
                "torsion": [4, 2],
                "points": [["1", "2"], ["3", "4"]],
                "discriminant": "-123",
                "naive_height": 12.5,
                "faltings_height": 1.25,
                "submitter": "tester",
                "commentary": "record",
                "created_at": "2026-08-20 00:00:00",
                "updated_at": "2026-08-20 00:00:00",
            },
            {
                "id": 12,
                "curve_key": "c4:c6-old",
                "ainvs": ["0", "0", "1", "-2", "3"],
                "rank_lower_bound": 29,
                "torsion": [],
                "points": [],
            },
        ],
    }


def test_external_catalog_does_not_change_local_incumbent(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        upsert_curve(db, family="local", parameter="1", generic_lower=15)
        assert best_lower_bound(db) == 15
        info = import_icarm_payload(db, sample_payload(), raw_sha256="abc")
        assert info["best_rank_lower"] == 30
        assert external_best_lower_bound(db) == 30
        # External reference data must not affect discovery/pruning semantics.
        assert best_lower_bound(db) == 15
    finally:
        db.close()


def test_catalog_round_trip_and_witness_export(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        validate_icarm_payload(sample_payload())
        import_icarm_payload(db, sample_payload())
        rows = list_external_curves(db, rank_at_least=30)
        assert len(rows) == 1
        row = get_external_curve(db, "icarm", "273")
        witness = witness_payload(row)
        assert witness["source_rank_lower_bound"] == 30
        assert witness["a_invariants"] == ["1", "0", "0", "-5", "7"]
        assert witness["points"][0] == ["1", "2"]
    finally:
        db.close()


def test_icarm_torsion_metadata_is_source_provided_and_normalized(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        import_icarm_payload(db, sample_payload())

        groups = icarm_torsion_groups(db)
        assert groups == [
            {"key": "trivial", "factors": [], "label": "trivial"},
            {"key": "2x4", "factors": [2, 4], "label": "ℤ/2ℤ × ℤ/4ℤ"},
        ]

        row = get_external_curve(db, "icarm", "273")
        assert external_torsion_label(row) == "ℤ/2ℤ × ℤ/4ℤ"
        filtered = list_external_curves(db, torsion="2x4", limit=20)
        assert [str(item["source_id"]) for item in filtered] == ["273"]
    finally:
        db.close()


def test_icarm_only_best_matches_metric_frontier_within_torsion(tmp_path):
    payload = {
        "count": 5,
        "curves": [
            {
                "id": 1,
                "ainvs": ["0", "0", "0", "-1", "0"],
                "rank_lower_bound": 10,
                "torsion": [4, 2],
                "points": [],
                "conductor": "1000",
                "discriminant": "-5000",
                "naive_height": 50.0,
                "faltings_height": 5.0,
            },
            {
                "id": 2,
                "ainvs": ["0", "0", "0", "-2", "0"],
                "rank_lower_bound": 9,
                "torsion": [2, 4],
                "points": [],
                "conductor": "900",
                "discriminant": "-4000",
                "naive_height": 60.0,
                "faltings_height": 6.0,
            },
            {
                "id": 3,
                "ainvs": ["0", "0", "0", "-3", "0"],
                "rank_lower_bound": 9,
                "torsion": [2, 4],
                "points": [],
                "conductor": "800",
                "discriminant": "-3000",
                "naive_height": 40.0,
                "faltings_height": 4.0,
            },
            {
                "id": 4,
                "ainvs": ["0", "0", "0", "-4", "0"],
                "rank_lower_bound": 8,
                "torsion": [2, 4],
                "points": [],
                "conductor": "700",
                "discriminant": "-2500",
                "naive_height": 40.0,
                "faltings_height": 4.0,
            },
            {
                "id": 5,
                "ainvs": ["0", "0", "0", "-5", "0"],
                "rank_lower_bound": 9,
                "torsion": [],
                "points": [],
                "conductor": "600",
                "discriminant": "-2000",
                "naive_height": 30.0,
                "faltings_height": 3.0,
            },
        ],
    }
    db = connect(tmp_path / "rank42.db")
    try:
        import_icarm_payload(db, payload)

        group_frontier = list_external_curves(
            db,
            torsion="2x4",
            only_best=True,
            best_metric="naive",
            limit=20,
        )
        assert [str(row["source_id"]) for row in group_frontier] == ["1", "3", "4"]

        overall_frontier = list_external_curves(
            db,
            only_best=True,
            best_metric="naive",
            limit=20,
        )
        assert [str(row["source_id"]) for row in overall_frontier] == ["1", "5"]
    finally:
        db.close()


def test_icarm_payload_rejects_malformed_torsion_metadata():
    payload = sample_payload()
    payload["curves"][0]["torsion"] = [2, 1]

    try:
        validate_icarm_payload(payload)
    except ValueError as exc:
        assert "invalid torsion invariant factors" in str(exc)
    else:
        raise AssertionError("malformed source torsion metadata must be rejected")


def test_catalog_sync_preserves_stale_rows_as_inactive(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        first = sample_payload()
        import_icarm_payload(db, first)
        second = {"count": 1, "curves": [first["curves"][0]]}
        import_icarm_payload(db, second)
        old = get_external_curve(db, "icarm", "12")
        assert old is not None
        assert int(old["active"]) == 0
        assert [r["source_id"] for r in list_external_curves(db)] == ["273"]
    finally:
        db.close()


def test_catalog_source_removal_does_not_delete_prior_search_provenance(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        first = sample_payload()
        import_icarm_payload(db, first)
        ts = "2026-08-21T00:00:00+00:00"
        db.execute(
            """
            INSERT INTO external_curve_searches(
                search_key,source,source_id,stages_json,timeout_seconds,status,
                points_found,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            ("demo-search", "icarm", "12", "[1000]", 10, "done", 0, ts, ts),
        )
        db.commit()
        import_icarm_payload(db, {"count": 1, "curves": [first["curves"][0]]})
        assert db.execute(
            "SELECT COUNT(*) AS n FROM external_curve_searches WHERE search_key='demo-search'"
        ).fetchone()["n"] == 1
        assert int(get_external_curve(db, "icarm", "12")["active"]) == 0
    finally:
        db.close()


def test_ensure_icarm_synced_refreshes_once_then_uses_fresh_snapshot(tmp_path, monkeypatch):
    from rank42.catalog import ensure_icarm_synced

    db = connect(tmp_path / "rank42.db")
    calls = []
    try:
        def fake_sync(db_arg, *, cache_dir, timeout, input_path=None):
            calls.append((cache_dir, timeout))
            info = import_icarm_payload(db_arg, sample_payload(), raw_sha256="fresh")
            return info

        monkeypatch.setattr("rank42.catalog.sync_icarm", fake_sync)
        first = ensure_icarm_synced(db, cache_dir=str(tmp_path / "cache"), timeout=3, max_age_hours=24)
        second = ensure_icarm_synced(db, cache_dir=str(tmp_path / "cache"), timeout=3, max_age_hours=24)
        assert first["status"] == "refreshed"
        assert second["status"] == "fresh"
        assert len(calls) == 1
    finally:
        db.close()


def test_ensure_icarm_synced_preserves_old_snapshot_on_refresh_failure(tmp_path, monkeypatch):
    from rank42.catalog import ensure_icarm_synced

    db = connect(tmp_path / "rank42.db")
    try:
        import_icarm_payload(db, sample_payload())
        db.execute(
            "UPDATE external_catalogs SET fetched_at='2000-01-01T00:00:00+00:00' WHERE source='icarm'"
        )
        db.commit()

        def fail_sync(*args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr("rank42.catalog.sync_icarm", fail_sync)
        info = ensure_icarm_synced(db, timeout=1, max_age_hours=1)
        assert info["status"] == "stale_preserved"
        assert info["curve_count"] == 2
        assert "network down" in info["error"]
        assert len(list_external_curves(db)) == 2
    finally:
        db.close()
