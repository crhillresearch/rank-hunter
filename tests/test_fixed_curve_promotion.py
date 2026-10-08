import json

from sage.all import EllipticCurve, QQ

import rank42.fixed_curve_search as fixed_curve_search
import rank42.torsion as torsion
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.exact_lb import ExactCertificateTimeout
from rank42.points import upsert_point
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter="1"):
    curve_id = upsert_curve(
        db,
        family="fixed-curve-promotion",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )
    return curve_id


def _E():
    return EllipticCurve(QQ, [QQ(v) for v in MODEL])


def _upper_evidence(db, curve_id, upper, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "fixed-curve-promotion-test-upper",
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


def test_fixed_curve_exact_candidate_promotes_through_central_service(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P0 = E(0, 1)
        P1 = E(1, 1)

        upsert_point(
            db,
            curve_id=curve_id,
            x=P0[0],
            y=P0[1],
            source="existing-proof",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
            search_ref="existing:proof",
            metadata={"certificate": "existing"},
        )

        monkeypatch.setattr(
            fixed_curve_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: {
                "independent": True,
                "status": "certified_independent",
                "certificate": {"method": "fixed-test"},
            },
        )

        basis, growth, attempts = fixed_curve_search._certify_exact_candidates(
            db,
            curve_id=curve_id,
            E=E,
            rigorous_basis=[P0],
            candidates=[P1],
            exact_candidates=8,
            certificate_timeout=30,
        )

        assert basis == [P0, P1]
        assert growth == 1
        assert attempts == 1

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert curve["status"] == "proven_lower"
        assert json.loads(curve["generators_json"]) == [["0", "1"], ["1", "1"]]

        point = db.execute(
            "SELECT * FROM points WHERE curve_id=? AND x=? AND y=?",
            (curve_id, "1", "1"),
        ).fetchone()
        assert point["role"] == "rigorous_witness"
        assert int(point["exact_verified"]) == 1
        assert int(point["rigorous_independent"]) == 1
        assert point["search_ref"] == "fixed_curve:exact"

        evidence = list_rank_evidence(db, curve_id)
        subgroup = next(row for row in evidence if row["evidence_type"] == "certified_subgroup")
        assert subgroup["engine"] == "fixed_curve_exact_certificate"
        assert int(subgroup["rigorous_lower"]) == 2
        assert json.loads(subgroup["points_json"]) == [["0", "1"], ["1", "1"]]
    finally:
        db.close()


def test_fixed_curve_exact_candidates_preserve_order_after_timeout(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        first = E(1, 1)
        second = E(-1, 1)
        calls = []

        def _certificate(_ainvs, trial, timeout):
            calls.append((str(trial[-1][0]), int(timeout)))
            if len(calls) == 1:
                raise ExactCertificateTimeout("fixed timeout")
            return {
                "independent": True,
                "status": "certified_independent",
                "certificate": {"method": "fixed-second"},
            }

        monkeypatch.setattr(fixed_curve_search, "run_exact_certificate", _certificate)

        basis, growth, attempts = fixed_curve_search._certify_exact_candidates(
            db,
            curve_id=curve_id,
            E=E,
            rigorous_basis=[],
            candidates=[first, second],
            exact_candidates=2,
            certificate_timeout=17,
        )

        assert calls == [("1", 17), ("-1", 17)]
        assert basis == [second]
        assert growth == 1
        assert attempts == 2
        assert int(get_curve(db, curve_id)["descent_lower"]) == 1
    finally:
        db.close()


def test_fixed_curve_conflict_keeps_certified_basis_but_blocks_compatibility_projection(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P = E(0, 1)
        _upper_evidence(db, curve_id, 0, "fixed-upper-zero")

        monkeypatch.setattr(
            fixed_curve_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: {
                "independent": True,
                "status": "certified_independent",
                "certificate": {"method": "fixed-conflict"},
            },
        )

        basis, growth, attempts = fixed_curve_search._certify_exact_candidates(
            db,
            curve_id=curve_id,
            E=E,
            rigorous_basis=[],
            candidates=[P],
            exact_candidates=1,
            certificate_timeout=20,
        )

        assert basis == [P]
        assert growth == 1
        assert attempts == 1

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


def test_fixed_curve_exact_candidate_limit_is_preserved(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        E = _E()
        candidates = [E(0, 1), E(1, 1), E(-1, 1)]
        calls = []

        def _certificate(*_args, **_kwargs):
            calls.append(1)
            return {
                "independent": False,
                "status": "dependent",
                "certificate": {"method": "limit-test"},
            }

        monkeypatch.setattr(fixed_curve_search, "run_exact_certificate", _certificate)

        basis, growth, attempts = fixed_curve_search._certify_exact_candidates(
            db,
            curve_id=curve_id,
            E=E,
            rigorous_basis=[],
            candidates=candidates,
            exact_candidates=2,
            certificate_timeout=10,
        )

        assert basis == []
        assert growth == 0
        assert attempts == 2
        assert len(calls) == 2
    finally:
        db.close()
