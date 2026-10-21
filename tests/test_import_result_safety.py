import json

from sage.all import EllipticCurve, QQ

import rank42.import_result as import_result
from rank42.db import connect, get_curve, update_curve, upsert_curve


MODEL = ["0", "0", "0", "-1", "1"]


def test_import_result_claim_and_height_screen_do_not_promote_rank(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(
            import_result,
            "height_screen",
            lambda *_args, **_kwargs: {
                "positive_definite_screen": True,
                "determinant": "9.25",
            },
        )
        result = import_result.import_verified_result(
            db,
            {
                "a_invariants": MODEL,
                "points": [["0", "1"], ["1", "1"]],
                "known_exact_rank": 2,
                "regulator_mwrank": "7.5",
            },
            family="legacy-import",
            parameter="claim",
            source="test:legacy-import",
        )

        curve = get_curve(db, result["curve_id"])
        assert curve["descent_lower"] is None
        assert curve["descent_upper"] is None
        assert curve["exact_rank"] is None
        assert curve["generators_json"] is None
        assert curve["certain"] in (None, 0)
        assert curve["status"] == "imported"
        assert curve["regulator"] == "7.5"

        points = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (result["curve_id"],),
        ).fetchall()
        assert len(points) == 2
        assert all(int(row["exact_verified"]) == 1 for row in points)
        assert all(int(row["rigorous_independent"]) == 0 for row in points)
        assert all(row["independence_status"] == "unknown" for row in points)
        assert all(row["role"] == "candidate_extra" for row in points)

        event = db.execute(
            "SELECT message FROM events WHERE curve_id=? ORDER BY id DESC LIMIT 1",
            (result["curve_id"],),
        ).fetchone()["message"]
        assert "external_exact_rank_claim=2" in event
        assert "source=test:legacy-import" in event
    finally:
        db.close()


def test_import_result_maps_points_exactly_to_stored_minimal_model(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(
            import_result,
            "height_screen",
            lambda *_args, **_kwargs: {
                "positive_definite_screen": False,
                "determinant": None,
            },
        )
        source = EllipticCurve(QQ, [0, 0, 0, -16, 0])
        source_point = source(4, 0)
        result = import_result.import_verified_result(
            db,
            {
                "a_invariants": [str(x) for x in source.a_invariants()],
                "points": [[str(source_point[0]), str(source_point[1])]],
            },
            family="legacy-import",
            parameter="minimal-map",
            source="test:minimal-map",
        )

        curve = get_curve(db, result["curve_id"])
        stored_model = [QQ(str(x)) for x in json.loads(curve["a_invariants_json"])]
        Em = EllipticCurve(QQ, stored_model)
        assert [str(x) for x in Em.a_invariants()] == result["stored_a_invariants"]

        point = db.execute(
            "SELECT * FROM points WHERE curve_id=?",
            (result["curve_id"],),
        ).fetchone()
        mapped = Em(QQ(point["x"]), QQ(point["y"]))
        assert Em(mapped[0], mapped[1]) == mapped
        assert int(point["exact_verified"]) == 1
    finally:
        db.close()


def test_import_result_does_not_downgrade_existing_exact_curve(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(
            import_result,
            "height_screen",
            lambda *_args, **_kwargs: {
                "positive_definite_screen": True,
                "determinant": "1",
            },
        )
        curve_id = upsert_curve(
            db,
            family="legacy-import",
            parameter="existing-exact",
            a_invariants_json=json.dumps(MODEL),
        )
        update_curve(
            db,
            curve_id,
            descent_lower=1,
            descent_upper=1,
            exact_rank=1,
            certain=1,
            status="exact",
        )

        result = import_result.import_verified_result(
            db,
            {
                "a_invariants": MODEL,
                "points": [["0", "1"]],
                "known_exact_rank": 99,
            },
            family="legacy-import",
            parameter="existing-exact",
            source="test:reimport",
        )

        assert result["curve_id"] == curve_id
        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 1
        assert int(curve["descent_upper"]) == 1
        assert int(curve["exact_rank"]) == 1
        assert int(curve["certain"]) == 1
        assert curve["status"] == "exact"
        assert curve["generators_json"] is None
    finally:
        db.close()


def test_import_result_accepts_rank_claim_without_matching_point_count(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(
            import_result,
            "height_screen",
            lambda *_args, **_kwargs: {
                "positive_definite_screen": False,
                "determinant": None,
            },
        )
        result = import_result.import_verified_result(
            db,
            {
                "a_invariants": MODEL,
                "points": [],
                "known_exact_rank": 31,
            },
            family="legacy-import",
            parameter="claim-only",
            source="test:claim-only",
        )

        assert result["claimed_exact_rank"] == 31
        curve = get_curve(db, result["curve_id"])
        assert curve["descent_lower"] is None
        assert curve["exact_rank"] is None
        assert curve["status"] == "imported"
    finally:
        db.close()


def test_import_result_rejects_model_collision(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(
            import_result,
            "height_screen",
            lambda *_args, **_kwargs: {
                "positive_definite_screen": False,
                "determinant": None,
            },
        )
        curve_id = upsert_curve(
            db,
            family="legacy-import",
            parameter="collision",
            a_invariants_json=json.dumps(MODEL),
        )

        try:
            import_result.import_verified_result(
                db,
                {
                    "a_invariants": ["0", "0", "0", "-2", "1"],
                    "points": [],
                },
                family="legacy-import",
                parameter="collision",
                source="test:collision",
            )
        except ValueError as exc:
            assert "different stored minimal model" in str(exc)
        else:
            raise AssertionError("model collision was accepted")

        curve = get_curve(db, curve_id)
        assert json.loads(curve["a_invariants_json"]) == MODEL
    finally:
        db.close()
