import json
import sqlite3

from rank42.db import (
    connect,
    discard_transient_zero_evidence_curve,
    repair_orphan_quartic_curve_refs,
    upsert_curve,
)
from rank42.quartic_store import (
    create_or_get_search,
    finish_search,
    record_map_back_failures,
    store_points,
)
from rank42.migration_manager import open_database_with_migrations
from rank42.ratpoints import Ratpoint, normalize_polynomial


def test_quartic_schema_and_dedup(tmp_path):
    db = connect(tmp_path / "q.db")
    n = normalize_polynomial([1, 0, 0, 0, 1])
    kw = dict(
        curve_id=None,
        family="test",
        parameter="1",
        hole_label="coset-001",
        coefficients=n.rational_coefficients,
        integer_coefficients=n.integer_coefficients,
        y_scale=n.y_scale,
        degree=n.degree,
        height_bound=100,
        denominator_low=None,
        denominator_high=None,
        extra_args=[],
    )
    a = create_or_get_search(db, **kw)
    b = create_or_get_search(db, **kw)
    assert a["id"] == b["id"]

    store_points(db, a["id"], [Ratpoint(0, 1, 0, 1, 1)])
    store_points(db, a["id"], [Ratpoint(0, 1, 0, 1, 1)])
    count = db.execute(
        "SELECT COUNT(*) AS n FROM quartic_points WHERE search_id=?", (a["id"],)
    ).fetchone()["n"]
    assert count == 1

    finish_search(db, a["id"], status="done", runtime=0.25, point_count=1)
    row = db.execute("SELECT * FROM quartic_searches WHERE id=?", (a["id"],)).fetchone()
    assert row["status"] == "done"
    assert row["point_count"] == 1



def _quartic_search_for_curve(db, curve_id, key_suffix):
    n = normalize_polynomial([1, 0, 0, 0, 1])
    return create_or_get_search(
        db,
        curve_id=curve_id,
        family="legacy",
        parameter=str(key_suffix),
        hole_label=f"orphan-{key_suffix}",
        coefficients=n.rational_coefficients,
        integer_coefficients=n.integer_coefficients,
        y_scale=n.y_scale,
        degree=n.degree,
        height_bound=100,
        denominator_low=None,
        denominator_high=None,
        extra_args=[],
    )


def test_connect_does_not_repair_legacy_orphaned_quartic_curve_reference(tmp_path):
    path = tmp_path / "legacy-orphan.db"
    db = connect(path)
    cid = upsert_curve(db, family="legacy", parameter="1")
    search = _quartic_search_for_curve(db, cid, "1")
    db.commit()

    # Recreate the historical failure mode: curve deleted while FK actions were
    # disabled, leaving the useful quartic-search row behind.
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("DELETE FROM curves WHERE id=?", (cid,))
    db.commit()
    row = db.execute(
        "SELECT curve_id FROM quartic_searches WHERE id=?", (search["id"],)
    ).fetchone()
    assert row["curve_id"] == cid
    assert db.execute("PRAGMA foreign_key_check").fetchall()
    db.close()

    reopened = connect(path)
    try:
        row = reopened.execute(
            "SELECT curve_id FROM quartic_searches WHERE id=?", (search["id"],)
        ).fetchone()
        assert row is not None
        assert row["curve_id"] == cid
        assert reopened.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        reopened.close()

    managed = open_database_with_migrations(path)
    try:
        row = managed.execute(
            "SELECT curve_id FROM quartic_searches WHERE id=?", (search["id"],)
        ).fetchone()
        assert row is not None
        assert row["curve_id"] == cid
        assert managed.execute("PRAGMA foreign_key_check").fetchall()

        assert repair_orphan_quartic_curve_refs(managed) == 1
        row = managed.execute(
            "SELECT curve_id FROM quartic_searches WHERE id=?", (search["id"],)
        ).fetchone()
        assert row["curve_id"] is None
        assert managed.execute("PRAGMA foreign_key_check").fetchall() == []
        assert repair_orphan_quartic_curve_refs(managed) == 0
    finally:
        managed.close()


def test_transient_curve_cleanup_preserves_quartic_history_without_orphan(tmp_path):
    db = connect(tmp_path / "cleanup.db")
    try:
        cid = upsert_curve(db, family="cleanup", parameter="1")
        search = _quartic_search_for_curve(db, cid, "cleanup")
        assert discard_transient_zero_evidence_curve(db, cid) is True

        assert db.execute(
            "SELECT 1 FROM curves WHERE id=?", (cid,)
        ).fetchone() is None
        row = db.execute(
            "SELECT curve_id FROM quartic_searches WHERE id=?", (search["id"],)
        ).fetchone()
        assert row is not None
        assert row["curve_id"] is None
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()



def test_quartic_map_back_failures_persist_bounded_samples_and_occurrence_count(
    tmp_path,
):
    db = connect(tmp_path / "map-failures.db")
    n = normalize_polynomial([1, 0, 0, 0, 1])
    search = create_or_get_search(
        db,
        curve_id=None,
        family="map-failure-test",
        parameter="1",
        hole_label="anchor-a",
        coefficients=n.rational_coefficients,
        integer_coefficients=n.integer_coefficients,
        y_scale=n.y_scale,
        degree=n.degree,
        height_bound=100,
        denominator_low=None,
        denominator_high=None,
        extra_args=[],
        metadata={"existing": "kept"},
    )
    failure = {
        "quartic_search_id": int(search["id"]),
        "anchor_key": "anchor-a",
        "model_key": "model-a",
        "hit": {"x": "0", "y": "1", "square_y": "1"},
        "reduction_transform": {
            "e": "1",
            "matrix": ["1", "0", "1", "0"],
            "h": ["0"],
        },
        "error_class": "ZeroDivisionError",
        "error": "quartic reduction map denominator is zero",
    }
    first = record_map_back_failures(
        db, search["id"], [failure, failure], sample_limit=1
    )
    assert first["count"] == 2
    assert len(first["samples"]) == 1
    assert first["samples_truncated"] is True

    second = record_map_back_failures(
        db, search["id"], [failure], sample_limit=1
    )
    assert second["count"] == 3
    assert len(second["samples"]) == 1

    row = db.execute(
        "SELECT metadata_json FROM quartic_searches WHERE id=?",
        (int(search["id"]),),
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["existing"] == "kept"
    assert metadata["map_back_failure_count"] == 3
    assert metadata["map_back_failure_samples_truncated"] is True
    sample = metadata["map_back_failures"][0]
    assert sample["quartic_search_id"] == int(search["id"])
    assert sample["anchor_key"] == "anchor-a"
    assert sample["model_key"] == "model-a"
    assert sample["hit"]["square_y"] == "1"
    assert sample["reduction_transform"]["matrix"] == ["1", "0", "1", "0"]
    assert sample["error_class"] == "ZeroDivisionError"
    assert sample["failure_key"]
