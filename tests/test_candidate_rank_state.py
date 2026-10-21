import json

from rank42.candidates import (
    candidate_outcome,
    candidate_rows,
    create_pool,
    replace_pool_rows,
)
from rank42.db import connect, upsert_curve
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.candidate_pools_page import _candidate_rank_view


MODEL = ["0", "0", "0", "-1", "0"]


def _evidence(db, curve_id, *, lower=None, upper=None, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "candidate-rank-test",
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
        key=key,
    )


def _pool_with_candidate(db, *, name="candidate-rank"):
    pool = create_pool(
        db,
        name=name,
        plugin_id="test",
        family_spec="test.family",
    )
    replace_pool_rows(
        db,
        int(pool["id"]),
        [{"parameter": "1", "score": 1.0}],
    )
    candidate = db.execute(
        "SELECT * FROM candidates WHERE pool_id=?",
        (int(pool["id"]),),
    ).fetchone()
    return pool, candidate


def _link_candidate(db, candidate_id, curve_id, *, metadata=None):
    db.execute(
        """UPDATE candidates
           SET status='searched', curve_id=?, metadata_json=?
           WHERE id=?""",
        (int(curve_id), json.dumps(metadata or {}, sort_keys=True), int(candidate_id)),
    )
    db.commit()


def test_candidate_rows_use_evidence_only_live_lower_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        pool, candidate = _pool_with_candidate(db)
        curve_id = upsert_curve(db, family="candidate", parameter="1")
        _evidence(db, curve_id, lower=5, key="candidate-live-lower")
        _link_candidate(db, candidate["id"], curve_id)

        row = candidate_rows(db, pool["id"])[0]

        assert int(row["rigorous_lower"]) == 5
        assert row["rigorous_upper"] is None
        assert row["exact_rank"] is None
        assert row["rank_inconsistent"] is False
        assert _candidate_rank_view(row) == "≥ 5"
        assert candidate_outcome(row) == f"rank ≥5 retained · curve #{curve_id}"
    finally:
        db.close()


def test_candidate_rows_use_evidence_only_exact_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        pool, candidate = _pool_with_candidate(db, name="candidate-exact")
        curve_id = upsert_curve(db, family="candidate", parameter="1")
        _evidence(db, curve_id, lower=6, upper=6, key="candidate-live-exact")
        _link_candidate(db, candidate["id"], curve_id)

        row = candidate_rows(db, pool["id"])[0]

        assert int(row["rigorous_lower"]) == 6
        assert int(row["rigorous_upper"]) == 6
        assert int(row["exact_rank"]) == 6
        assert _candidate_rank_view(row) == "= 6"
        assert candidate_outcome(row) == f"rank =6 retained · curve #{curve_id}"
    finally:
        db.close()


def test_candidate_rows_surface_reduced_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        pool, candidate = _pool_with_candidate(db, name="candidate-conflict")
        curve_id = upsert_curve(db, family="candidate", parameter="1")
        _evidence(db, curve_id, lower=7, key="candidate-conflict-lower")
        _evidence(db, curve_id, upper=6, key="candidate-conflict-upper")
        _link_candidate(db, candidate["id"], curve_id)

        row = candidate_rows(db, pool["id"])[0]

        assert row["rank_inconsistent"] is True
        assert _candidate_rank_view(row) == "conflict ≥7 / ≤6"
        assert candidate_outcome(row) == f"rank evidence conflict ≥7 / ≤6 · curve #{curve_id}"
    finally:
        db.close()


def test_candidate_outcome_marks_last_search_rank_as_historical_without_live_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        pool, candidate = _pool_with_candidate(db, name="candidate-historical")
        metadata = {
            "last_search": {
                "rigorous_lower": 4,
                "curve_id": 99,
                "curve_status": "proven_lower",
            }
        }
        db.execute(
            """UPDATE candidates
               SET status='searched', metadata_json=?
               WHERE id=?""",
            (json.dumps(metadata, sort_keys=True), int(candidate["id"])),
        )
        db.commit()

        row = candidate_rows(db, pool["id"])[0]

        assert row["curve_id"] is None
        assert row["rigorous_lower"] is None
        assert candidate_outcome(row) == "historical search rank ≥4 · curve #99"
    finally:
        db.close()
