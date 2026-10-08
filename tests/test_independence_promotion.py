import json

from sage.all import EllipticCurve, QQ

import rank42.torsion as torsion
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.independence_check import _promote_working_basis
from rank42.points import upsert_point
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _E():
    return EllipticCurve(QQ, [QQ(v) for v in MODEL])


def _curve(db, parameter="1"):
    return upsert_curve(
        db,
        family="independence-promotion",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )


def _attempt_evidence(db, curve_id, *, lower, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "rank42.independence_check",
            "engine_version": "5",
            "evidence_type": "exact_certificate",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": int(lower),
            "rigorous_upper": None,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {"test": key},
            "stdout_summary": json.dumps({"certificate": {"method": key}}),
        },
        key=key,
    )


def test_independence_final_basis_promotes_through_central_service(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P0 = E(0, 1)
        P1 = E(1, 1)

        update_curve(db, curve_id, descent_lower=1, status="proven_lower")
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
        evidence_id = _attempt_evidence(
            db,
            curve_id,
            lower=2,
            key="independence-growth-2",
        )
        outcomes = [
            {
                "status": "independent",
                "evidence_id": evidence_id,
                "record": {"id": 17},
            }
        ]

        result = _promote_working_basis(
            db,
            curve_id=curve_id,
            E=E,
            baseline_basis=[P0],
            working=[P0, P1],
            outcomes=outcomes,
            strategy="standard",
        )

        assert result["promoted"] is True
        assert result["rank_inconsistent"] is False
        assert result["old_explicit"] == 1
        assert result["new_explicit"] == 2
        assert result["rigorous_lower"] == 2
        assert result["certificate_chain"]["independent_evidence_ids"] == [evidence_id]

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert curve["status"] == "proven_lower"
        assert json.loads(curve["generators_json"]) == [["0", "1"], ["1", "1"]]

        new_point = db.execute(
            "SELECT * FROM points WHERE curve_id=? AND x=? AND y=?",
            (curve_id, "1", "1"),
        ).fetchone()
        assert new_point["role"] == "rigorous_witness"
        assert int(new_point["rigorous_independent"]) == 1
        assert new_point["search_ref"] == "analysis:independence:promotion"

        evidence = list_rank_evidence(db, curve_id)
        subgroup = next(row for row in evidence if row["evidence_type"] == "certified_subgroup")
        assert subgroup["engine"] == "independence_workbench_chain"
        summary = json.loads(subgroup["stdout_summary"])
        chain = summary["certificate"]
        assert chain["kind"] == "independence_workbench_chain"
        assert chain["independent_evidence_ids"] == [evidence_id]
        assert chain["baseline_witness_count"] == 1
        assert chain["final_witness_count"] == 2
    finally:
        db.close()


def test_independence_exact_rank_conflict_records_chain_but_blocks_projection(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P0 = E(0, 1)
        P1 = E(1, 1)

        update_curve(
            db,
            curve_id,
            descent_lower=1,
            descent_upper=1,
            exact_rank=1,
            certain=1,
            status="exact",
        )
        evidence_id = _attempt_evidence(
            db,
            curve_id,
            lower=2,
            key="independence-conflict-2",
        )

        result = _promote_working_basis(
            db,
            curve_id=curve_id,
            E=E,
            baseline_basis=[P0],
            working=[P0, P1],
            outcomes=[{"status": "independent", "evidence_id": evidence_id}],
            strategy="standard",
        )

        assert result["promoted"] is True
        assert result["rank_inconsistent"] is True
        assert result["rigorous_lower"] == 2

        curve = get_curve(db, curve_id)
        assert int(curve["exact_rank"]) == 1
        assert int(curve["descent_lower"]) == 1
        assert curve["generators_json"] is None
        assert curve["status"] == "exact"

        evidence = list_rank_evidence(db, curve_id)
        assert {row["evidence_type"] for row in evidence} == {
            "exact_certificate",
            "certified_subgroup",
        }
    finally:
        db.close()


def test_independence_no_explicit_growth_does_not_create_final_subgroup_evidence(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        E = _E()
        P0 = E(0, 1)

        result = _promote_working_basis(
            db,
            curve_id=curve_id,
            E=E,
            baseline_basis=[P0],
            working=[P0],
            outcomes=[],
            strategy="standard",
        )

        assert result == {
            "promoted": False,
            "rank_inconsistent": False,
            "old_explicit": 1,
            "new_explicit": 1,
            "rigorous_lower": None,
        }
        assert list_rank_evidence(db, curve_id) == []
    finally:
        db.close()


def test_independence_hard_saturation_preserves_witness_source_and_chain_metadata(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        P0 = E(0, 1)
        P1 = E(1, 1)
        evidence_id = _attempt_evidence(
            db,
            curve_id,
            lower=2,
            key="independence-saturation-2",
        )
        basis_preconditioning = {
            "evidence_id": 901,
            "index": "4",
            "cache_hit": False,
        }

        result = _promote_working_basis(
            db,
            curve_id=curve_id,
            E=E,
            baseline_basis=[P0],
            working=[P0, P1],
            outcomes=[{"status": "independent", "evidence_id": evidence_id}],
            strategy="saturation",
            basis_preconditioning=basis_preconditioning,
            saturation_prime=11,
        )

        assert result["rank_inconsistent"] is False
        assert result["certificate_chain"]["basis_preconditioning"] == basis_preconditioning
        assert result["certificate_chain"]["saturation_prime"] == 2

        rows = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(rows) == 2
        for index, row in enumerate(rows):
            assert row["source"] == "hard_case_2_saturation"
            assert row["search_ref"] == "analysis:hard-saturation"
            metadata = json.loads(row["metadata_json"])
            assert metadata["active_basis_index"] == index
            assert metadata["strategy"] == "saturation"
            assert metadata["saturation_prime"] == 2
            assert metadata["promotion_chain_evidence_ids"] == [evidence_id]
    finally:
        db.close()
