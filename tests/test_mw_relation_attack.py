import json

from rank42.db import connect, update_curve, upsert_curve
from rank42.mw_relation_attack import (
    ENGINE,
    _close_dependent,
    _prior_attempt,
)
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence


def test_relation_history_is_basis_point_and_budget_scoped(tmp_path):
    db = connect(tmp_path / "relation-history.db")
    cid = upsert_curve(db, family="test", parameter="relation")
    record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": ENGINE,
            "engine_version": "1",
            "evidence_type": "point_relation",
            "status": "inconclusive",
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "options": {
                "point_id": 7,
                "basis_fingerprint": "basis-a",
                "timeout": 180,
                "precision_bits": 256,
                "max_denominator": 64,
            },
        },
    )

    assert _prior_attempt(
        db,
        curve_id=cid,
        point_id=7,
        basis_fingerprint="basis-a",
        timeout=120,
        precision_bits=192,
        max_denominator=32,
    ) is not None
    assert _prior_attempt(
        db,
        curve_id=cid,
        point_id=7,
        basis_fingerprint="basis-b",
        timeout=120,
        precision_bits=192,
        max_denominator=32,
    ) is None
    assert _prior_attempt(
        db,
        curve_id=cid,
        point_id=8,
        basis_fingerprint="basis-a",
        timeout=120,
        precision_bits=192,
        max_denominator=32,
    ) is None
    assert _prior_attempt(
        db,
        curve_id=cid,
        point_id=7,
        basis_fingerprint="basis-a",
        timeout=120,
        precision_bits=512,
        max_denominator=32,
    ) is None
    assert _prior_attempt(
        db,
        curve_id=cid,
        point_id=7,
        basis_fingerprint="basis-a",
        timeout=120,
        precision_bits=192,
        max_denominator=128,
    ) is None


def test_verified_relation_closes_point_and_clears_hard_flag(tmp_path):
    db = connect(tmp_path / "relation-close.db")
    cid = upsert_curve(db, family="test", parameter="close")
    update_curve(
        db,
        cid,
        a_invariants_json='["0","0","0","-1","1"]',
        descent_lower=1,
        generators_json='[["0","1"]]',
    )
    rec = upsert_point(
        db,
        curve_id=cid,
        x="2",
        y="3",
        source="test",
        exact_verified=True,
        independence_status="inconclusive",
        rigorous_independent=False,
        metadata={"old": "keep"},
    )
    db.execute(
        "UPDATE points SET hard_flag=1,hard_reason='hard' WHERE id=?",
        (int(rec["id"]),),
    )
    db.commit()

    _close_dependent(
        db,
        rec,
        {
            "coefficients": ["1/2"],
            "integer_coefficients": [1],
            "common_denominator": 2,
            "torsion_exponent": 1,
            "torsion_residual": None,
        },
        99,
    )

    row = db.execute("SELECT * FROM points WHERE id=?", (int(rec["id"]),)).fetchone()
    assert row["independence_status"] == "dependent"
    assert row["rigorous_independent"] == 0
    assert row["hard_flag"] == 0
    meta = json.loads(row["metadata_json"])
    assert meta["old"] == "keep"
    assert meta["last_mw_relation"]["evidence_id"] == 99
    assert meta["last_mw_relation"]["common_denominator"] == 2


def test_relation_worker_requires_exact_group_verification():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "rank42" / "mw_relation_worker.py"
    ).read_text(encoding="utf-8")
    assert "torsion_exponent * residual" in source
    assert '"verified": verified' in source
    assert '"status": "dependent" if verified else "inconclusive"' in source


def test_advanced_ui_keeps_research_attacks_separate():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "independence_page.py"
    ).read_text(encoding="utf-8")
    assert 'st.expander("Advanced Research Escalation"' in source
    assert '"rank42.mw_relation_attack"' in source
    assert '"rank42.advanced_upper_bound"' in source
    assert '"--deep-saturation"' in source
