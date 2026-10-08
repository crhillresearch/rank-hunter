import json
from pathlib import Path

import rank42.torsion as torsion

from rank42.curve_research_state import get_curve_research_state
from rank42.db import connect, get_curve, upsert_curve
from rank42.points import upsert_point
from rank42.rank_evidence import (
    apply_reduced_rank_state,
    list_rank_evidence,
    record_rank_evidence,
    reduce_rank_state,
)


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter="1"):
    return upsert_curve(db, family="promotion-characterization", parameter=str(parameter))


def _evidence(db, curve_id, *, lower=None, upper=None, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "promotion-characterization",
            "evidence_type": "certified_subgroup" if lower is not None else "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def test_point_proof_flags_are_monotone_under_weaker_upsert(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)

        upsert_point(
            db,
            curve_id=curve_id,
            x="1",
            y="2",
            source="certificate",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
            search_ref="characterization:strong",
            metadata={"certificate": "strong"},
        )
        row = upsert_point(
            db,
            curve_id=curve_id,
            x="1",
            y="2",
            source="later-weaker-write",
            role="candidate_extra",
            exact_verified=False,
            independence_status="dependent",
            rigorous_independent=False,
            search_ref="characterization:weak",
            metadata={"note": "weaker"},
        )

        assert int(row["exact_verified"]) == 1
        assert int(row["rigorous_independent"]) == 1
        assert row["independence_status"] == "rigorous_independent"
        assert row["role"] == "rigorous_witness"
        assert row["search_ref"] == "characterization:strong"
        assert json.loads(row["metadata_json"]) == {"certificate": "strong"}
    finally:
        db.close()


def test_exact_point_persistence_alone_does_not_promote_rank(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)

        upsert_point(
            db,
            curve_id=curve_id,
            x="1",
            y="2",
            source="exact-point-only",
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
        )

        curve = get_curve(db, curve_id)
        state = get_curve_research_state(db, curve_id)
        assert curve["descent_lower"] is None
        assert curve["exact_rank"] is None
        assert state["rigorous_lower"] == 0
        assert list_rank_evidence(db, curve_id) == []
    finally:
        db.close()


def test_rigorous_point_row_alone_is_not_a_rank_evidence_record(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)

        upsert_point(
            db,
            curve_id=curve_id,
            x="3",
            y="4",
            source="rigorous-point-only",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
        )

        curve = get_curve(db, curve_id)
        state = reduce_rank_state(db, curve_id)
        assert curve["descent_lower"] is None
        assert state["rigorous_lower"] == 0
        assert list_rank_evidence(db, curve_id) == []
    finally:
        db.close()


def test_certified_subgroup_evidence_projects_through_reducer(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        _evidence(db, curve_id, lower=3, key="certified-subgroup-3")

        before = get_curve(db, curve_id)
        reduced = reduce_rank_state(db, curve_id)
        assert before["descent_lower"] is None
        assert reduced["rigorous_lower"] == 3
        assert reduced["exact_rank"] is None

        applied = apply_reduced_rank_state(db, curve_id)
        after = get_curve(db, curve_id)
        assert applied["rigorous_lower"] == 3
        assert int(after["descent_lower"]) == 3
        assert after["exact_rank"] is None
    finally:
        db.close()


def test_matching_rigorous_bounds_project_exact_rank(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        _evidence(db, curve_id, lower=4, key="exact-lower-4")
        _evidence(db, curve_id, upper=4, key="exact-upper-4")

        state = apply_reduced_rank_state(db, curve_id)
        curve = get_curve(db, curve_id)

        assert state["rigorous_lower"] == 4
        assert state["rigorous_upper"] == 4
        assert state["exact_rank"] == 4
        assert int(curve["descent_lower"]) == 4
        assert int(curve["descent_upper"]) == 4
        assert int(curve["exact_rank"]) == 4
        assert int(curve["certain"]) == 1
        assert curve["status"] == "exact"
    finally:
        db.close()


def test_inconsistent_evidence_blocks_compatibility_mutation(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        _evidence(db, curve_id, lower=5, key="conflict-lower-5")
        _evidence(db, curve_id, upper=4, key="conflict-upper-4")

        state = apply_reduced_rank_state(db, curve_id)
        curve = get_curve(db, curve_id)

        assert state["inconsistent"] is True
        assert state["rigorous_lower"] == 5
        assert state["rigorous_upper"] == 4
        assert curve["descent_lower"] is None
        assert curve["descent_upper"] is None
        assert curve["exact_rank"] is None
    finally:
        db.close()


def test_known_direct_promotion_writers_are_explicitly_inventoried():
    root = Path(__file__).resolve().parents[1]
    expected = {
        "rank42/auto_point_search.py": (
            "def certify_ledger_growth(",
            "promote_certified_subgroup(",
            'engine="auto_search_exact_certificate"',
        ),
        "rank42/search_family_baseline.py": (
            "def attach_family_specialization_seed(",
            "promote_certified_subgroup(",
            'engine="auto_family_section_bundle"',
        ),
        "rank42/fixed_curve_search.py": (
            "def _certify_exact_candidates(",
            "promote_certified_subgroup(",
            "engine='fixed_curve_exact_certificate'",
        ),
        "rank42/hunt_store.py": (
            "promote_certified_subgroup(",
            "promote_exact_rank_certificate(",
            'engine="general_hunt_sage_rank"',
        ),
        "rank42/independence_check.py": (
            "def _promote_working_basis(",
            "promote_certified_subgroup(",
            'engine=str(promotion_engine or "independence_workbench_chain")',
        ),
        "rank42/certify_curve.py": (
            "def certify_stored_curve(",
            "promote_exact_rank_certificate(",
            'source="certify_curve"',
        ),
        "rank42/hard_case_escalator.py": (
            "def _run_proof_rank(",
            "promote_exact_rank_certificate(",
            'source="hard_case_escalator"',
        ),
        "rank42/attack.py": (
            "promote_mwrank_strong_result(",
            'source="manual_attack"',
            'search_ref="analysis:manual-attack:mwrank"',
        ),
        "rank42/auto_analyze.py": (
            "promote_mwrank_strong_result(",
            'source="auto_analyze_strong"',
            'search_ref="auto:strong:mwrank"',
        ),
    }

    for relative_path, markers in expected.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, f"{relative_path} no longer contains characterized marker {marker!r}"

    auto_source = (root / "rank42/auto_point_search.py").read_text(encoding="utf-8")
    assert "descent_lower=len(basis)" not in auto_source
    assert "generators_json=json.dumps([[str(Q[0]), str(Q[1])] for Q in basis])" not in auto_source

    family_auto_source = (root / "rank42/search_family_baseline.py").read_text(encoding="utf-8")
    assert '"descent_lower": max(int(row["descent_lower"] or 0), lower)' not in family_auto_source
    assert 'role="rigorous_witness"' not in family_auto_source
    assert 'engine="auto_family_specialization_certificate"' in family_auto_source

    fixed_source = (root / "rank42/fixed_curve_search.py").read_text(encoding="utf-8")
    assert "descent_lower=len(rigorous_basis)" not in fixed_source
    assert "generators_json=json.dumps([[str(Q[0]),str(Q[1])] for Q in rigorous_basis])" not in fixed_source


    hunt_source = (root / "rank42/hunt_store.py").read_text(encoding="utf-8")
    assert '"descent_lower": int(rigorous_lower)' not in hunt_source
    assert '"generators_json": json.dumps([[str(P[0]), str(P[1])] for P in basis])' not in hunt_source
    assert "update_curve(db, curve_id, exact_rank=exact" not in hunt_source


    independence_source = (root / "rank42/independence_check.py").read_text(encoding="utf-8")
    assert "new_descent_lower =" not in independence_source
    assert "descent_lower=new_descent_lower" not in independence_source
    assert 'engine=str(promotion_engine or "independence_workbench_chain")' in independence_source
    # generators_json remains intentionally for equivalent-basis saturation
    # replacement; that path changes the active basis representation, not rank.


    certify_source = (root / "rank42/certify_curve.py").read_text(encoding="utf-8")
    assert "update_curve(db, int(curve_id), exact_rank=exact" not in certify_source

    escalator_source = (root / "rank42/hard_case_escalator.py").read_text(encoding="utf-8")
    proof_rank_source = escalator_source.split("def _run_proof_rank(", 1)[1].split("def _record_escalator_summary", 1)[0]
    assert "update_curve(" not in proof_rank_source


    attack_source = (root / "rank42/attack.py").read_text(encoding="utf-8")
    assert '"descent_upper": upper' not in attack_source
    assert 'fields["descent_lower"]' not in attack_source
    assert 'fields["generators_json"]' not in attack_source
    assert 'fields["exact_rank"]' not in attack_source

    auto_analyze_source = (root / "rank42/auto_analyze.py").read_text(encoding="utf-8")
    strong_source = auto_analyze_source.split("raw_lower = int(strong[\"lower\"])", 1)[1]
    assert '"descent_upper": upper' not in strong_source
    assert 'fields["descent_lower"]' not in strong_source
    assert 'fields["generators_json"]' not in strong_source
    assert 'fields["exact_rank"]' not in strong_source


    import_source = (root / "rank42/import_result.py").read_text(encoding="utf-8")
    assert "descent_lower=" not in import_source
    assert "descent_upper=" not in import_source
    assert "exact_rank=claimed" not in import_source
    assert "generators_json=" not in import_source
    assert 'rigorous_independent=False' in import_source
    assert 'independence_status="unknown"' in import_source
