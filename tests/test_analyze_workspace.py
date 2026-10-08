import json

from rank42.analyze_workspace import curve_analysis_snapshot, next_useful_actions, researcher_paths
from rank42.db import connect, upsert_curve, update_curve
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.analyze_page import _analysis_curve_options


def _curve(db, *, lower=2, baseline=None, parameter="1"):
    baseline = lower if baseline is None else baseline
    cid = upsert_curve(db, family="analysis-test", parameter=parameter, score=12.5)
    update_curve(
        db,
        cid,
        a_invariants_json=json.dumps(["0", "0", "0", "-1", "0"]),
        generic_lower=baseline,
        descent_lower=lower if lower > baseline else None,
    )
    return cid


def _point(db, cid, x, y, *, witness=False, status="unknown"):
    upsert_point(
        db,
        curve_id=cid,
        x=x,
        y=y,
        source="test",
        role="rigorous_witness" if witness else "candidate_extra",
        exact_verified=True,
        independence_status="rigorous_independent" if witness else status,
        rigorous_independent=witness,
    )



def _rank_evidence(db, cid, *, lower=None, upper=None):
    return record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "analysis-test",
            "evidence_type": "rank_bounds",
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
    )

def test_analyze_snapshot_prioritizes_unresolved_exact_points(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=2)
    _point(db, cid, "0", "0", witness=True)
    _point(db, cid, "1", "0", witness=True)
    _point(db, cid, "2", "1", status="numerical_novel")

    snapshot = curve_analysis_snapshot(db, cid)
    assert snapshot["state"]["rigorous_lower"] == 2
    assert len(snapshot["witnesses"]) == 2
    assert len(snapshot["actionable_points"]) == 1
    assert len(snapshot["numerical_novel_points"]) == 1
    assert snapshot["witness_gap"] == 0

    actions = next_useful_actions(snapshot)
    assert actions[0]["kind"] == "independence"
    assert "Certify 1 unresolved exact point" in actions[0]["title"]


def test_analyze_snapshot_flags_incomplete_replayable_basis(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=3)
    _point(db, cid, "0", "0", witness=True)

    snapshot = curve_analysis_snapshot(db, cid)
    assert snapshot["witness_gap"] == 2

    actions = next_useful_actions(snapshot)
    assert actions[0]["title"] == "Repair the replayable witness basis"
    assert actions[0]["page"] == "Independence"


def test_analyze_open_is_read_only_with_respect_to_ui_jobs(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=1)
    before = db.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='ui_jobs'"
    ).fetchone()["n"]

    snapshot = curve_analysis_snapshot(db, cid)
    actions = next_useful_actions(snapshot)

    after = db.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='ui_jobs'"
    ).fetchone()["n"]
    assert before == after
    assert snapshot["curve"]["id"] == cid
    assert any(action["page"] == "Target" for action in actions)


def test_analyze_exact_rank_does_not_recommend_rank_growth_search(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=2)
    update_curve(db, cid, exact_rank=2, descent_upper=2)
    _point(db, cid, "0", "0", witness=True)
    _point(db, cid, "1", "0", witness=True)

    snapshot = curve_analysis_snapshot(db, cid)
    assert snapshot["state"]["exact_rank"] == 2

    actions = next_useful_actions(snapshot)
    assert not any(action["page"] == "Target" for action in actions)


def test_analyze_quantifies_extra_directions_without_overclaiming_generic_jump(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=4, baseline=2)
    for i in range(4):
        _point(db, cid, str(i), str(i + 1), witness=True)

    snapshot = curve_analysis_snapshot(db, cid)
    assert snapshot["family_section_lower"] == 2
    assert snapshot["state"]["rigorous_lower"] == 4
    assert snapshot["extra_directions_lower"] == 2
    assert snapshot["generic_rank_exact"] is None
    assert snapshot["rank_jump_lower"] is None


def test_analyze_research_paths_match_post_specialization_workflow(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=4, baseline=2)
    for i in range(4):
        _point(db, cid, str(i), str(i + 1), witness=True)

    paths = researcher_paths(curve_analysis_snapshot(db, cid))
    kinds = {path["kind"] for path in paths}
    assert kinds == {
        "determine_rank",
        "hunt_generator",
        "study_jump",
        "search_related",
    }
    jump = next(path for path in paths if path["kind"] == "study_jump")
    assert "at least 2 extra independent direction(s)" in jump["why"]
    assert "generic rank is known exactly" in jump["why"]


def test_analyze_compares_related_retained_family_fibers(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=4, baseline=2, parameter="1")
    _curve(db, lower=5, baseline=2, parameter="2")
    _curve(db, lower=3, baseline=2, parameter="3")

    snapshot = curve_analysis_snapshot(db, cid)
    assert snapshot["family_best_lower"] == 5
    assert snapshot["family_at_or_above"] == 2
    assert len(snapshot["family_jumps"]) == 3
    assert snapshot["family_total"] == 3
    peer = next(rec for rec in snapshot["family_jumps"] if rec["parameter"] == "2")
    assert peer["extra_directions_lower"] == 3

def test_analyze_selector_orders_by_authoritative_evidence_state(tmp_path):
    db = connect(tmp_path / "analysis.db")
    legacy_high_score = _curve(db, lower=4, parameter="legacy")
    evidence_high = _curve(db, lower=2, parameter="evidence")
    update_curve(db, legacy_high_score, score=100.0)
    update_curve(db, evidence_high, score=1.0)
    _rank_evidence(db, evidence_high, lower=6)

    rows = _analysis_curve_options(db)

    assert [int(row["id"]) for row in rows[:2]] == [evidence_high, legacy_high_score]
    assert int(rows[0]["rigorous_lower"]) == 6


def test_analyze_family_comparison_uses_authoritative_evidence_state(tmp_path):
    db = connect(tmp_path / "analysis.db")
    cid = _curve(db, lower=4, baseline=2, parameter="1")
    peer = _curve(db, lower=3, baseline=2, parameter="2")
    _rank_evidence(db, peer, lower=6)

    snapshot = curve_analysis_snapshot(db, cid)

    assert snapshot["family_best_lower"] == 6
    assert snapshot["family_at_or_above"] == 2
    peer_state = next(rec for rec in snapshot["family_jumps"] if rec["curve_id"] == peer)
    assert peer_state["rigorous_lower"] == 6
    assert peer_state["extra_directions_lower"] == 4

