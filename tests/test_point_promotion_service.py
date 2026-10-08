import json

from sage.all import EllipticCurve, QQ

import rank42.auto_point_search as auto_point_search
import rank42.torsion as torsion
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.point_promotion import promote_certified_subgroup
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter="1"):
    curve_id = upsert_curve(db, family="promotion-service", parameter=str(parameter))
    update_curve(db, curve_id, a_invariants_json=json.dumps(MODEL))
    return curve_id


def _E():
    return EllipticCurve(QQ, [QQ(v) for v in MODEL])


def _upper_evidence(db, curve_id, upper, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "promotion-service-test-upper",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": int(upper),
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def test_promote_certified_subgroup_persists_witness_evidence_and_projection(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        points = [E(0, 1), E(1, 1)]

        result = promote_certified_subgroup(
            db,
            curve_id=curve_id,
            E=E,
            points=points,
            source="service-test",
            search_ref="service:test",
            certificate={"method": "exact-test"},
            metadata={"campaign": "unit"},
            engine="test_exact_certificate",
        )

        assert result["projected"] is True
        assert result["rank_inconsistent"] is False
        assert result["rigorous_lower"] == 2
        assert result["witness_count"] == 2

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert curve["status"] == "proven_lower"
        assert json.loads(curve["generators_json"]) == [["0", "1"], ["1", "1"]]

        point_rows = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(point_rows) == 2
        assert all(int(row["exact_verified"]) == 1 for row in point_rows)
        assert all(int(row["rigorous_independent"]) == 1 for row in point_rows)
        assert all(row["role"] == "rigorous_witness" for row in point_rows)

        evidence = list_rank_evidence(db, curve_id)
        assert len(evidence) == 1
        row = evidence[0]
        assert row["evidence_type"] == "certified_subgroup"
        assert int(row["rigorous_lower"]) == 2
        assert json.loads(row["points_json"]) == [["0", "1"], ["1", "1"]]
        summary = json.loads(row["stdout_summary"])
        assert summary["certificate"] == {"method": "exact-test"}
        assert summary["metadata"]["campaign"] == "unit"
    finally:
        db.close()


def test_promote_certified_subgroup_preserves_existing_rigorous_point_metadata(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P = E(0, 1)

        from rank42.points import upsert_point
        upsert_point(
            db,
            curve_id=curve_id,
            x=P[0],
            y=P[1],
            source="older-proof",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
            search_ref="older:proof",
            metadata={"certificate": "older", "keep": True},
        )

        promote_certified_subgroup(
            db,
            curve_id=curve_id,
            E=E,
            points=[P],
            source="new-proof",
            search_ref="new:proof",
            certificate={"method": "new"},
            metadata={"new": True},
        )

        point = db.execute(
            "SELECT * FROM points WHERE curve_id=? AND x=? AND y=?",
            (curve_id, "0", "1"),
        ).fetchone()
        assert point["source"] == "older-proof"
        assert point["search_ref"] == "older:proof"
        assert json.loads(point["metadata_json"]) == {"certificate": "older", "keep": True}

        evidence = list_rank_evidence(db, curve_id)
        assert len(evidence) == 1
        assert json.loads(evidence[0]["stdout_summary"])["certificate"] == {"method": "new"}
    finally:
        db.close()


def test_promote_certified_subgroup_conflict_preserves_evidence_but_blocks_projection(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        _upper_evidence(db, curve_id, 0, "upper-zero")

        result = promote_certified_subgroup(
            db,
            curve_id=curve_id,
            E=E,
            points=[E(0, 1)],
            source="conflict-test",
            search_ref="conflict:test",
            certificate={"method": "exact-test"},
        )

        assert result["rank_inconsistent"] is True
        assert result["projected"] is False
        assert result["rigorous_lower"] == 1
        assert result["rigorous_upper"] == 0

        curve = get_curve(db, curve_id)
        assert curve["descent_lower"] is None
        assert curve["generators_json"] is None
        assert curve["status"] == "new"

        evidence = list_rank_evidence(db, curve_id)
        assert {row["evidence_type"] for row in evidence} == {"rank_bounds", "certified_subgroup"}
        point = db.execute(
            "SELECT * FROM points WHERE curve_id=?",
            (curve_id,),
        ).fetchone()
        assert int(point["rigorous_independent"]) == 1
    finally:
        db.close()


def test_promote_certified_subgroup_requires_certificate_provenance(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        E = _E()
        try:
            promote_certified_subgroup(
                db,
                curve_id=curve_id,
                E=E,
                points=[E(0, 1)],
                source="missing-certificate",
                search_ref="missing:certificate",
                certificate=None,
            )
        except ValueError as exc:
            assert "requires certificate provenance" in str(exc)
        else:
            raise AssertionError("missing certificate provenance was accepted")
    finally:
        db.close()


def test_auto_ledger_growth_promotes_through_central_service(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P = E(0, 1)

        monkeypatch.setattr(
            auto_point_search,
            "rigorous_witness_basis",
            lambda *_args, **_kwargs: ([], 0, True),
        )
        monkeypatch.setattr(
            auto_point_search,
            "_ledger_candidates",
            lambda *_args, **_kwargs: [P],
        )
        monkeypatch.setattr(
            auto_point_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: {
                "independent": True,
                "status": "independent",
                "certificate": {"method": "auto-test"},
            },
        )

        result = auto_point_search.certify_ledger_growth(
            db,
            curve_id=curve_id,
            E=E,
            source="auto-test",
            search_ref="auto:test",
            certificate_timeout=5,
            max_candidates=1,
        )

        assert result == {
            "attempts": 1,
            "growth": 1,
            "rigorous_lower": 1,
            "basis_complete": True,
        }
        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 1
        assert curve["status"] == "proven_lower"

        evidence = list_rank_evidence(db, curve_id)
        subgroup = next(row for row in evidence if row["evidence_type"] == "certified_subgroup")
        assert subgroup["engine"] == "auto_search_exact_certificate"
        assert int(subgroup["rigorous_lower"]) == 1
    finally:
        db.close()
