from pathlib import Path
import json

from rank42.analysis_planner import analysis_plan, planner_research_paths
from rank42.analyze_workspace import curve_analysis_snapshot
from rank42.db import connect, update_curve, upsert_curve
from rank42.family_evidence import (
    family_evidence_key,
    family_evidence_state_for_curve,
    record_family_evidence,
    reduce_family_evidence,
)


MODEL = ["0", "0", "0", "-1", "0"]

ROOT = Path(__file__).resolve().parents[1]


def _curve(db, *, parameter="1", lower=4, baseline=2, family_sha256=None):
    curve_id = upsert_curve(
        db,
        family="generic-evidence-family",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )
    update_curve(
        db,
        curve_id,
        family_spec="json:generic-evidence-family",
        family_sha256=family_sha256,
        generic_lower=baseline,
        descent_lower=lower,
    )
    return curve_id


def test_family_evidence_reducer_honors_explicit_exact_generic_rank(tmp_path):
    db = connect(tmp_path / "family-evidence.db")
    try:
        key = family_evidence_key("json:generic-evidence-family", None)
        evidence_id = record_family_evidence(
            db,
            family_spec="json:generic-evidence-family",
            criterion="specialization_injectivity",
            specialization_parameter="0",
            generic_lower=2,
            generic_upper=None,
            exact_generic_rank=2,
            certificate={
                "method": "fixture theorem",
                "reference": "fixture-ref",
                "assumptions": [],
            },
        )

        state = reduce_family_evidence(db, key)

        assert state["rigorous_generic_lower"] == 2
        assert state["rigorous_generic_upper"] == 2
        assert state["exact_generic_rank"] == 2
        assert state["rank_inconsistent"] is False
        assert state["exact_evidence_ids"] == [evidence_id]
    finally:
        db.close()


def test_conflicting_exact_generic_rank_evidence_is_quarantined(tmp_path):
    db = connect(tmp_path / "family-evidence-conflict.db")
    try:
        key = family_evidence_key("json:generic-evidence-family", None)
        record_family_evidence(
            db,
            family_spec="json:generic-evidence-family",
            criterion="theorem-a",
            specialization_parameter="0",
            exact_generic_rank=2,
            certificate={"method": "fixture-a"},
        )
        record_family_evidence(
            db,
            family_spec="json:generic-evidence-family",
            criterion="theorem-b",
            specialization_parameter="1",
            exact_generic_rank=3,
            certificate={"method": "fixture-b"},
        )

        state = reduce_family_evidence(db, key)

        assert state["rigorous_generic_lower"] == 3
        assert state["rigorous_generic_upper"] == 2
        assert state["exact_generic_rank"] is None
        assert state["rank_inconsistent"] is True
        assert state["exact_evidence_ids"] == []
    finally:
        db.close()


def test_rank_jump_snapshot_uses_fingerprint_bound_exact_generic_rank(tmp_path):
    db = connect(tmp_path / "rank-jump.db")
    try:
        curve_id = _curve(db, lower=4, baseline=2)
        evidence_id = record_family_evidence(
            db,
            family_spec="json:generic-evidence-family",
            criterion="generic_rank_theorem",
            specialization_parameter="0",
            exact_generic_rank=2,
            certificate={
                "method": "fixture exact generic rank",
                "reference": "paper/example",
                "assumptions": [],
            },
        )

        snapshot = curve_analysis_snapshot(db, curve_id)

        assert snapshot["generic_rank_exact"] == 2
        assert snapshot["rank_jump_lower"] == 2
        assert snapshot["rank_jump_status"] == "certified_rank_jump_lower"
        family_state = snapshot["family_generic_evidence"]
        assert family_state["exact_evidence_ids"] == [evidence_id]
        assert family_state["evidence"][0]["certificate"]["reference"] == "paper/example"

        jump = next(
            path
            for path in planner_research_paths(analysis_plan(snapshot))
            if path["kind"] == "study_jump"
        )
        assert jump["title"] == "Study the certified rank jump"
        assert "generic rank is rigorously exact at 2" in jump["why"]
        assert "rank jump is rigorously at least 2" in jump["why"]
    finally:
        db.close()


def test_rank_jump_does_not_consume_stale_family_fingerprint(tmp_path):
    db = connect(tmp_path / "rank-jump-fingerprint.db")
    try:
        curve_id = _curve(
            db,
            lower=4,
            baseline=2,
            family_sha256="current-family-sha",
        )
        record_family_evidence(
            db,
            family_spec="json:generic-evidence-family",
            family_sha256="old-family-sha",
            criterion="generic_rank_theorem",
            specialization_parameter="0",
            exact_generic_rank=2,
            certificate={"method": "stale fixture theorem"},
        )

        snapshot = curve_analysis_snapshot(db, curve_id)

        assert snapshot["generic_rank_exact"] is None
        assert snapshot["rank_jump_lower"] is None
        assert snapshot["rank_jump_status"] == "no_completed_generic_rank_evidence"
        assert snapshot["family_generic_evidence"]["evidence"] == []
    finally:
        db.close()


def test_rank_jump_conflicting_family_evidence_blocks_formal_claim(tmp_path):
    db = connect(tmp_path / "rank-jump-conflict.db")
    try:
        curve_id = _curve(db, lower=5, baseline=2)
        for criterion, parameter, exact in (
            ("theorem-a", "0", 2),
            ("theorem-b", "1", 3),
        ):
            record_family_evidence(
                db,
                family_spec="json:generic-evidence-family",
                criterion=criterion,
                specialization_parameter=parameter,
                exact_generic_rank=exact,
                certificate={"method": criterion},
            )

        snapshot = curve_analysis_snapshot(db, curve_id)

        assert snapshot["generic_rank_exact"] is None
        assert snapshot["rank_jump_lower"] is None
        assert snapshot["rank_jump_status"] == "family_generic_rank_conflict"

        jump = next(
            path
            for path in planner_research_paths(analysis_plan(snapshot))
            if path["kind"] == "study_jump"
        )
        assert jump["title"] == "Resolve generic-rank evidence conflict"
        assert "No formal" not in jump["why"]
        assert "family-level conflict" in jump["why"]
    finally:
        db.close()


def test_family_evidence_state_exposes_only_matching_provenance(tmp_path):
    db = connect(tmp_path / "family-provenance.db")
    try:
        curve_id = _curve(db, family_sha256="bound-sha")
        record_family_evidence(
            db,
            family_spec="json:generic-evidence-family",
            family_sha256="bound-sha",
            plugin_id="fixture-plugin",
            plugin_version="1.2.3",
            criterion="published_theorem",
            specialization_parameter="7/11",
            exact_generic_rank=2,
            certificate={
                "reference": "Fixture 2026, Theorem 1",
                "assumptions": ["fixture assumption"],
            },
        )

        row = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()
        state = family_evidence_state_for_curve(db, row)

        assert state["available"] is True
        assert state["reason"] == "exact_generic_rank_known"
        assert state["exact_generic_rank"] == 2
        evidence = state["evidence"][0]
        assert evidence["plugin_id"] == "fixture-plugin"
        assert evidence["plugin_version"] == "1.2.3"
        assert evidence["family_sha256"] == "bound-sha"
        assert evidence["certificate"]["reference"] == "Fixture 2026, Theorem 1"
    finally:
        db.close()


def test_rank_jump_ui_surfaces_family_evidence_and_certified_jump_language():
    source = (ROOT / "rank42" / "ui_pages" / "analyze_page.py").read_text(
        encoding="utf-8"
    )

    assert '"Exact generic rank"' in source
    assert '"Certified rank jump"' in source
    assert '"Family generic-rank evidence"' in source
    assert "Historical plugin generic-rank metadata is not treated as proof." in source
    assert "No formal specialization rank-jump claim is made" in source
