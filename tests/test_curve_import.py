import json

import pytest

from rank42.curve_import import (
    DEFAULT_FAMILY,
    import_curve,
    import_curve_records,
    model_discriminant,
    parse_curve_import_json,
    parse_a_invariants,
    parse_points,
    point_on_curve,
)
from rank42.db import connect


def test_parse_a_invariants_accepts_json_and_csv():
    assert parse_a_invariants('[0, 0, 0, "-1/2", 3]') == ["0", "0", "0", "-1/2", "3"]
    assert parse_a_invariants("0,0,0,-1/2,3") == ["0", "0", "0", "-1/2", "3"]


def test_import_curve_keeps_external_rank_as_provenance(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id, created = import_curve(
            db,
            a_invariants="[0,0,0,-1,0]",
            family="Mystery hunter family",
            parameter="alpha-7",
            source="comparison-hunter",
            claimed_rank=">= 9",
            notes="interesting score",
        )
        assert created is True
        row = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()
        assert row["family"] == "Mystery hunter family"
        assert row["parameter"] == "alpha-7"
        assert row["status"] == "imported"
        assert json.loads(row["a_invariants_json"]) == ["0", "0", "0", "-1", "0"]
        assert row["generic_lower"] is None
        assert row["descent_lower"] is None
        assert row["descent_upper"] is None
        assert row["exact_rank"] is None
        event = db.execute(
            "SELECT message FROM events WHERE curve_id=? ORDER BY id DESC LIMIT 1",
            (curve_id,),
        ).fetchone()["message"]
        assert "external_rank_claim=>= 9" in event
        assert "source=comparison-hunter" in event
    finally:
        db.close()


def test_import_curve_allows_unclassified_family_and_stable_model_key(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        first, created = import_curve(db, a_invariants=[0, 0, 0, -1, 0])
        second, created_again = import_curve(db, a_invariants=[0, 0, 0, -1, 0])
        assert created is True
        assert created_again is False
        assert first == second
        row = db.execute("SELECT * FROM curves WHERE id=?", (first,)).fetchone()
        assert row["family"] == DEFAULT_FAMILY
        assert row["parameter"].startswith("model:")
    finally:
        db.close()


def test_import_curve_rejects_singular_model(tmp_path):
    assert model_discriminant([0, 0, 0, 0, 0]) == 0
    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="singular"):
            import_curve(db, a_invariants=[0, 0, 0, 0, 0])
    finally:
        db.close()


def test_import_curve_refuses_key_collision_with_different_model(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        import_curve(db, a_invariants=[0, 0, 0, -1, 0], family="X", parameter="t0")
        with pytest.raises(ValueError, match="different stored model"):
            import_curve(db, a_invariants=[0, 0, 0, -2, 0], family="X", parameter="t0")
    finally:
        db.close()


def test_parse_points_accepts_json_and_lines():
    assert parse_points("[[0,0],[1/2,3/4]]") == [("0", "0"), ("1/2", "3/4")]
    assert parse_points("0,0\n1,0") == [("0", "0"), ("1", "0")]


def test_point_membership_is_exact():
    ainvs = [0, 0, 0, -1, 0]
    assert point_on_curve(ainvs, ("0", "0"))
    assert point_on_curve(ainvs, ("1", "0"))
    assert point_on_curve(ainvs, ("-1", "0"))
    assert not point_on_curve(ainvs, ("2", "0"))


def test_import_curve_ingests_exact_external_points_without_independence_claim(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id, created = import_curve(
            db,
            a_invariants=[0, 0, 0, -1, 0],
            family="Pointed family",
            parameter="p0",
            source="other-hunter",
            points="[[-1,0],[0,0],[1,0]]",
        )
        assert created is True
        rows = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(rows) == 3
        assert all(int(r["exact_verified"]) == 1 for r in rows)
        assert all(r["independence_status"] == "unknown" for r in rows)
        assert all(int(r["rigorous_independent"]) == 0 for r in rows)
        assert all(r["source"] == "other-hunter" for r in rows)
        event = db.execute(
            "SELECT message FROM events WHERE curve_id=? ORDER BY id DESC LIMIT 1",
            (curve_id,),
        ).fetchone()["message"]
        assert "exact_points_imported=3" in event
    finally:
        db.close()


def test_bad_imported_point_aborts_before_curve_creation(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="is not on the supplied curve"):
            import_curve(
                db,
                a_invariants=[0, 0, 0, -1, 0],
                family="Bad points",
                parameter="bad",
                points="2,0",
            )
        assert db.execute(
            "SELECT COUNT(*) n FROM curves WHERE family='Bad points'"
        ).fetchone()["n"] == 0
    finally:
        db.close()


def test_manual_import_provenance_survives_workflow_status_change(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id, _ = import_curve(
            db,
            a_invariants=[0, 0, 0, -1, 0],
            family="Imported visibility",
            parameter="v0",
        )
        db.execute(
            "UPDATE curves SET status='rank_bounds_inconclusive' WHERE id=?",
            (curve_id,),
        )
        db.commit()
        row = db.execute(
            """
            SELECT curves.id
            FROM curves
            WHERE curves.id=?
              AND EXISTS (
                  SELECT 1 FROM events e
                  WHERE e.curve_id=curves.id
                    AND e.message LIKE 'Manual external curve import%'
              )
            """,
            (curve_id,),
        ).fetchone()
        assert row is not None
        assert int(row["id"]) == curve_id
    finally:
        db.close()



def test_parse_curve_import_json_accepts_single_object_and_aliases():
    records = parse_curve_import_json(json.dumps({
        "ainvs": [0, 0, 0, -1, 0],
        "family": "JSON family",
        "identifier": "j1",
        "rank": ">=3",
        "rational_points": [[0, 0], [1, 0]],
    }))
    assert records == [{
        "a_invariants": ["0", "0", "0", "-1", "0"],
        "family": "JSON family",
        "parameter": "j1",
        "source": None,
        "claimed_rank": ">=3",
        "notes": None,
        "points": [("0", "0"), ("1", "0")],
    }]


def test_parse_curve_import_json_accepts_bare_model_and_curves_wrapper():
    bare = parse_curve_import_json("[0,0,0,-1,0]")
    assert len(bare) == 1
    assert bare[0]["a_invariants"] == ["0", "0", "0", "-1", "0"]

    batch = parse_curve_import_json(json.dumps({
        "curves": [
            {"a_invariants": [0, 0, 0, -1, 0], "parameter": "a"},
            {"model": [0, 0, 0, -2, 0], "parameter": "b"},
        ]
    }))
    assert [rec["parameter"] for rec in batch] == ["a", "b"]
    assert batch[1]["a_invariants"] == ["0", "0", "0", "-2", "0"]


def test_json_batch_import_uses_same_scientific_safety_rules(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        records = parse_curve_import_json(json.dumps({
            "curves": [{
                "a_invariants": [0, 0, 0, -1, 0],
                "family": "JSON import",
                "parameter": "curve-a",
                "claimed_rank": 31,
                "points": [[-1, 0], [0, 0], [1, 0]],
            }]
        }))
        results = import_curve_records(db, records, default_source="json:curves.json")
        assert results == [{"curve_id": results[0]["curve_id"], "created": True}]
        curve_id = results[0]["curve_id"]

        row = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()
        assert row["generic_lower"] is None
        assert row["descent_lower"] is None
        assert row["exact_rank"] is None

        points = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id", (curve_id,)
        ).fetchall()
        assert len(points) == 3
        assert all(int(point["exact_verified"]) == 1 for point in points)
        assert all(int(point["rigorous_independent"]) == 0 for point in points)
        assert all(point["source"] == "json:curves.json" for point in points)

        event = db.execute(
            "SELECT message FROM events WHERE curve_id=? ORDER BY id DESC LIMIT 1",
            (curve_id,),
        ).fetchone()["message"]
        assert "external_rank_claim=31" in event
        assert "source=json:curves.json" in event
    finally:
        db.close()


def test_bad_json_record_is_rejected_before_any_batch_write(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        payload = {
            "curves": [
                {
                    "a_invariants": [0, 0, 0, -1, 0],
                    "family": "Batch safety",
                    "parameter": "good",
                },
                {
                    "a_invariants": [0, 0, 0, -1, 0],
                    "family": "Batch safety",
                    "parameter": "bad",
                    "points": [[2, 0]],
                },
            ]
        }
        with pytest.raises(ValueError, match="curve record 2"):
            records = parse_curve_import_json(json.dumps(payload))
            import_curve_records(db, records, default_source="json:batch.json")
        assert db.execute(
            "SELECT COUNT(*) n FROM curves WHERE family='Batch safety'"
        ).fetchone()["n"] == 0
    finally:
        db.close()
