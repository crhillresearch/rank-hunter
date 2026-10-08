import json

from rank42.db import connect, get_curve, upsert_curve
from rank42.point_promotion import promote_mwrank_strong_result
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter):
    return upsert_curve(
        db,
        family="mwrank-strong-promotion",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )


def _lower_evidence(db, curve_id, lower, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "mwrank-strong-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": int(lower),
            "rigorous_upper": None,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def test_mwrank_certain_exact_promotes_interval_and_witnesses(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "certain")
        result = {
            "lower": 2,
            "upper": 2,
            "certain": True,
            "points": [["0", "1"], ["1", "1"]],
            "a_invariants": MODEL,
            "engine_version": "test-eclib",
            "sage_version": "test-sage",
            "regulator": "1.5",
        }

        promoted = promote_mwrank_strong_result(
            db,
            curve_id=curve_id,
            model=MODEL,
            result=result,
            rigorous_lower=2,
            point_certificate=None,
            source="test-mwrank",
            search_ref="test:mwrank:certain",
            options={"timeout": 10},
            metadata={"certain": True},
        )

        assert promoted["rank_inconsistent"] is False
        assert promoted["rigorous_lower"] == 2
        assert promoted["rigorous_upper"] == 2
        assert promoted["exact_rank"] == 2
        assert promoted["subgroup_promoted"] is True

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert int(curve["descent_upper"]) == 2
        assert int(curve["exact_rank"]) == 2
        assert int(curve["certain"]) == 1
        assert curve["status"] == "exact"
        assert json.loads(curve["generators_json"]) == [["0", "1"], ["1", "1"]]

        rows = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(rows) == 2
        assert all(row["role"] == "rigorous_witness" for row in rows)
        assert all(int(row["rigorous_independent"]) == 1 for row in rows)

        evidence = list_rank_evidence(db, curve_id)
        assert {row["evidence_type"] for row in evidence} == {
            "descent",
            "certified_subgroup",
        }
    finally:
        db.close()


def test_mwrank_exact_point_certificate_promotes_open_interval(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "open")
        result = {
            "lower": 2,
            "upper": 4,
            "certain": False,
            "points": [["0", "1"], ["1", "1"]],
            "a_invariants": MODEL,
            "engine_version": "test-eclib",
            "sage_version": "test-sage",
        }
        point_certificate = {
            "independent": True,
            "rank_lower_bound": 2,
            "certificate": {"method": "exact-point-test"},
        }

        promoted = promote_mwrank_strong_result(
            db,
            curve_id=curve_id,
            model=MODEL,
            result=result,
            rigorous_lower=2,
            point_certificate=point_certificate,
            source="test-mwrank",
            search_ref="test:mwrank:open",
        )

        assert promoted["rigorous_lower"] == 2
        assert promoted["rigorous_upper"] == 4
        assert promoted["exact_rank"] is None
        assert promoted["subgroup_promoted"] is True

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert int(curve["descent_upper"]) == 4
        assert curve["exact_rank"] is None
        assert json.loads(curve["generators_json"]) == [["0", "1"], ["1", "1"]]
    finally:
        db.close()


def test_mwrank_upper_only_projects_no_generators(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "upper-only")
        result = {
            "lower": 0,
            "upper": 5,
            "certain": False,
            "points": [],
            "a_invariants": MODEL,
        }

        promoted = promote_mwrank_strong_result(
            db,
            curve_id=curve_id,
            model=MODEL,
            result=result,
            rigorous_lower=None,
            point_certificate=None,
            source="test-mwrank",
            search_ref="test:mwrank:upper",
        )

        assert promoted["rigorous_lower"] == 0
        assert promoted["rigorous_upper"] == 5
        assert promoted["exact_rank"] is None
        assert promoted["subgroup_promoted"] is False

        curve = get_curve(db, curve_id)
        assert curve["descent_lower"] is None
        assert int(curve["descent_upper"]) == 5
        assert curve["generators_json"] is None
    finally:
        db.close()


def test_mwrank_conflict_preserves_evidence_and_blocks_compatibility_projection(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "conflict")
        _lower_evidence(db, curve_id, 3, "existing-lower-3")
        result = {
            "lower": 2,
            "upper": 2,
            "certain": True,
            "points": [["0", "1"], ["1", "1"]],
            "a_invariants": MODEL,
        }

        promoted = promote_mwrank_strong_result(
            db,
            curve_id=curve_id,
            model=MODEL,
            result=result,
            rigorous_lower=2,
            point_certificate=None,
            source="test-mwrank",
            search_ref="test:mwrank:conflict",
        )

        assert promoted["rank_inconsistent"] is True
        assert promoted["rigorous_lower"] == 3
        assert promoted["rigorous_upper"] == 2
        assert promoted["exact_rank"] is None

        curve = get_curve(db, curve_id)
        assert curve["descent_lower"] is None
        assert curve["descent_upper"] is None
        assert curve["exact_rank"] is None
        assert curve["generators_json"] is None

        evidence = list_rank_evidence(db, curve_id)
        assert {row["evidence_type"] for row in evidence} == {
            "rank_bounds",
            "descent",
            "certified_subgroup",
        }
    finally:
        db.close()
