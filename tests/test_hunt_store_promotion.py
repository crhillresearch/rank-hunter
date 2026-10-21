import json

from sage.all import EllipticCurve, QQ

import rank42.hunt_store as hunt_store
import rank42.torsion as torsion
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.rank_cert import RankCertificationFailure, RankCertificationTimeout
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _E():
    return EllipticCurve(QQ, [QQ(v) for v in MODEL])


def _curve(db, parameter="manual"):
    return upsert_curve(
        db,
        family="general_hunt_test",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )


def _lower_evidence(db, curve_id, lower, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "hunt-store-promotion-test-lower",
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


def test_general_hunt_hit_uses_central_subgroup_promotion(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        E = _E()
        basis = [E(0, 1), E(1, 1)]
        certificate = {
            "independent": True,
            "status": "certified_independent",
            "rank_lower_bound": 2,
            "certificate": {"method": "general-hunt-test"},
        }

        curve_id = hunt_store.store_short_curve_hit(
            db,
            E,
            -1,
            1,
            basis,
            2,
            certificate,
            score=12.5,
        )

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert curve["status"] == "proven_lower"
        assert json.loads(curve["generators_json"]) == [["0", "1"], ["1", "1"]]
        assert curve["a_invariants_json"] == json.dumps(MODEL)
        assert curve["discriminant"] is not None
        assert curve["bad_primes_json"] is not None

        points = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(points) == 2
        assert all(row["role"] == "rigorous_witness" for row in points)
        assert all(int(row["rigorous_independent"]) == 1 for row in points)
        assert all(row["search_ref"] == "general_hunt:exact-certificate" for row in points)

        evidence = list_rank_evidence(db, curve_id)
        subgroup = next(row for row in evidence if row["evidence_type"] == "certified_subgroup")
        assert subgroup["engine"] == "general_hunt_exact_certificate"
        assert int(subgroup["rigorous_lower"]) == 2
        assert json.loads(subgroup["points_json"]) == [["0", "1"], ["1", "1"]]
        summary = json.loads(subgroup["stdout_summary"])
        assert summary["certificate"] == certificate
        assert summary["metadata"]["short_A"] == -1
        assert summary["metadata"]["short_B"] == 1
    finally:
        db.close()


def test_general_hunt_rejects_lower_that_does_not_match_basis_before_persistence(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        E = _E()
        basis = [E(0, 1)]
        try:
            hunt_store.store_short_curve_hit(
                db,
                E,
                -1,
                1,
                basis,
                2,
                {"independent": True, "certificate": {"method": "bad-lower"}},
            )
        except ValueError as exc:
            assert "must equal the certified subgroup basis size" in str(exc)
        else:
            raise AssertionError("mismatched General Hunt lower was accepted")

        row = db.execute(
            "SELECT id FROM curves WHERE family=?",
            (hunt_store.GENERAL_FAMILY,),
        ).fetchone()
        assert row is None
    finally:
        db.close()


def test_general_hunt_exact_rank_records_exact_certificate_evidence(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        E = _E()
        curve_id = hunt_store.store_short_curve_hit(
            db,
            E,
            -1,
            1,
            [E(0, 1)],
            1,
            {"independent": True, "certificate": {"method": "lower-proof"}},
        )
        monkeypatch.setattr(
            hunt_store,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 1,
                "proof_mode": True,
                "a_invariants": MODEL,
                "sage_version": "test",
                "_runtime_seconds": 0.01,
            },
        )

        exact = hunt_store.try_exact_rank(db, curve_id, E, 1, 20)

        assert exact == 1
        curve = get_curve(db, curve_id)
        assert int(curve["exact_rank"]) == 1
        assert int(curve["certain"]) == 1
        assert curve["status"] == "exact"

        evidence = list_rank_evidence(db, curve_id)
        exact_row = next(row for row in evidence if row["evidence_type"] == "exact_certificate")
        assert exact_row["engine"] == "general_hunt_sage_rank"
        assert int(exact_row["rigorous_lower"]) == 1
        assert int(exact_row["rigorous_upper"]) == 1
        assert int(exact_row["exact_rank"]) == 1
        summary = json.loads(exact_row["stdout_summary"])
        assert summary["certificate"]["proof_mode"] is True
        assert summary["exact_rank"] == 1
    finally:
        db.close()


def test_general_hunt_exact_rank_below_known_lower_is_rejected_without_evidence(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        E = _E()
        curve_id = _curve(db)
        update_curve(db, curve_id, descent_lower=2, status="proven_lower")
        monkeypatch.setattr(
            hunt_store,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 1,
                "proof_mode": True,
                "a_invariants": MODEL,
            },
        )

        exact = hunt_store.try_exact_rank(db, curve_id, E, 2, 20)

        assert exact is None
        curve = get_curve(db, curve_id)
        assert curve["exact_rank"] is None
        assert not any(
            row["evidence_type"] == "exact_certificate"
            for row in list_rank_evidence(db, curve_id)
        )
    finally:
        db.close()


def test_general_hunt_exact_rank_conflict_is_recorded_but_not_projected(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        E = _E()
        curve_id = _curve(db)
        _lower_evidence(db, curve_id, 3, "hunt-existing-lower-3")
        monkeypatch.setattr(
            hunt_store,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 2,
                "proof_mode": True,
                "a_invariants": MODEL,
            },
        )

        exact = hunt_store.try_exact_rank(db, curve_id, E, 1, 20)

        assert exact is None
        curve = get_curve(db, curve_id)
        assert curve["exact_rank"] is None
        assert curve["status"] == "new"

        evidence = list_rank_evidence(db, curve_id)
        assert {row["evidence_type"] for row in evidence} == {"rank_bounds", "exact_certificate"}
        exact_row = next(row for row in evidence if row["evidence_type"] == "exact_certificate")
        assert int(exact_row["rigorous_lower"]) == 2
        assert int(exact_row["rigorous_upper"]) == 2
    finally:
        db.close()


def test_general_hunt_exact_rank_timeout_and_failure_remain_non_promoting(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        E = _E()
        curve_id = _curve(db)

        monkeypatch.setattr(
            hunt_store,
            "run_rank_certification",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RankCertificationTimeout("timeout")
            ),
        )
        assert hunt_store.try_exact_rank(db, curve_id, E, 0, 7) is None

        monkeypatch.setattr(
            hunt_store,
            "run_rank_certification",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RankCertificationFailure("failure")
            ),
        )
        assert hunt_store.try_exact_rank(db, curve_id, E, 0, 7) is None

        assert list_rank_evidence(db, curve_id) == []
        assert get_curve(db, curve_id)["exact_rank"] is None
    finally:
        db.close()


def test_general_hunt_delegates_global_arithmetic_to_shared_authority():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "rank42" / "hunt_store.py").read_text(encoding="utf-8")

    assert "compute_and_publish_curve_arithmetic" in source
    assert "E.conductor()" not in source
    assert "E.root_number()" not in source
    assert "global_minimal_model" not in source
    assert "prime_divisors" not in source
    assert '"conductor":' not in source
    assert '"bad_primes_json":' not in source
