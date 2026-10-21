from types import SimpleNamespace

import pytest

import rank42.certify_curve as certify_curve
import rank42.hard_case_escalator as hard_case_escalator
from rank42.db import connect, get_curve, upsert_curve, update_curve
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter):
    return upsert_curve(
        db,
        family="exact-caller-promotion",
        parameter=str(parameter),
        a_invariants_json='["0","0","0","-1","1"]',
    )


def _lower_evidence(db, curve_id, lower, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "exact-caller-test",
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


def test_certify_curve_uses_central_exact_promotion(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "certify-success")
        update_curve(db, curve_id, descent_lower=2, status="proven_lower")
        monkeypatch.setattr(
            certify_curve,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 2,
                "proof_mode": True,
                "sage_version": "test-sage",
                "a_invariants": MODEL,
                "_runtime_seconds": 0.25,
            },
        )

        result = certify_curve.certify_stored_curve(db, curve_id, timeout=9)

        assert result["exact_rank"] == 2
        assert result["proven_lower"] == 2
        curve = get_curve(db, curve_id)
        assert int(curve["exact_rank"]) == 2
        assert int(curve["certain"]) == 1
        assert curve["status"] == "exact"

        evidence = list_rank_evidence(db, curve_id)
        exact = next(row for row in evidence if row["evidence_type"] == "exact_certificate")
        assert exact["engine"] == "sage_proof_rank"
        assert int(exact["rigorous_lower"]) == 2
        assert int(exact["rigorous_upper"]) == 2
    finally:
        db.close()


def test_certify_curve_conflicting_reduced_evidence_blocks_exact_projection(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "certify-conflict")
        _lower_evidence(db, curve_id, 3, "existing-lower-3")
        monkeypatch.setattr(
            certify_curve,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 2,
                "proof_mode": True,
                "sage_version": "test-sage",
                "a_invariants": MODEL,
            },
        )

        with pytest.raises(RuntimeError, match="reducer did not accept exact rank 2"):
            certify_curve.certify_stored_curve(db, curve_id, timeout=9)

        curve = get_curve(db, curve_id)
        assert curve["exact_rank"] is None
        assert curve["certain"] in (None, 0)
        evidence = list_rank_evidence(db, curve_id)
        assert {row["evidence_type"] for row in evidence} == {
            "rank_bounds",
            "exact_certificate",
        }
    finally:
        db.close()


def test_hard_case_proof_rank_uses_central_exact_promotion(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "hard-success")
        update_curve(db, curve_id, descent_lower=1, status="proven_lower")
        monkeypatch.setattr(
            hard_case_escalator,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 1,
                "proof_mode": True,
                "sage_version": "test-sage",
                "a_invariants": MODEL,
                "_runtime_seconds": 0.1,
            },
        )
        monkeypatch.setattr(
            hard_case_escalator,
            "_close_from_exact_ceiling",
            lambda *_args, **_kwargs: "dependent",
        )
        stages = []
        args = SimpleNamespace(curve_id=curve_id, rank_cert_timeout=12)

        status = hard_case_escalator._run_proof_rank(
            db,
            args,
            point_id=77,
            stages=stages,
            basis_fingerprint="basis-1",
        )

        assert status == "dependent"
        assert stages[-1]["status"] == "exact"
        assert stages[-1]["exact_rank"] == 1
        curve = get_curve(db, curve_id)
        assert int(curve["exact_rank"]) == 1
        assert curve["status"] == "exact"

        evidence = list_rank_evidence(db, curve_id)
        exact = next(row for row in evidence if row["evidence_type"] == "exact_certificate")
        assert exact["engine"] == "sage_proof_rank"
        assert int(stages[-1]["evidence_id"]) == int(exact["id"])
    finally:
        db.close()


def test_hard_case_proof_rank_conflict_records_failed_stage_without_projection(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "hard-conflict")
        _lower_evidence(db, curve_id, 3, "hard-existing-lower-3")
        monkeypatch.setattr(
            hard_case_escalator,
            "run_rank_certification",
            lambda *_args, **_kwargs: {
                "exact_rank": 2,
                "proof_mode": True,
                "sage_version": "test-sage",
                "a_invariants": MODEL,
            },
        )
        stages = []
        args = SimpleNamespace(curve_id=curve_id, rank_cert_timeout=12)

        status = hard_case_escalator._run_proof_rank(
            db,
            args,
            point_id=77,
            stages=stages,
            basis_fingerprint="basis-conflict",
        )

        assert status is None
        assert stages[-1]["status"] == "error"
        assert stages[-1]["exact_rank"] == 2
        assert "conflicts with existing rigorous rank evidence" in stages[-1]["error"]

        curve = get_curve(db, curve_id)
        assert curve["exact_rank"] is None
        assert curve["certain"] in (None, 0)

        evidence = list_rank_evidence(db, curve_id)
        types = [row["evidence_type"] for row in evidence]
        assert "rank_bounds" in types
        assert "exact_certificate" in types
        assert "hard_case_stage" in types
    finally:
        db.close()
