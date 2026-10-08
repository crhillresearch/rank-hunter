from rank42.db import connect, log_event, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.common import curve_label, curve_options


MODEL = ["0", "0", "0", "-1", "0"]


def _evidence(db, curve_id, *, lower=None, upper=None, rigorous=True, key=None):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "curve-options-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": rigorous,
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


def test_curve_options_includes_curve_certified_only_in_rank_evidence(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="C2xC8", parameter="7/5", score=12.5)
        _evidence(db, curve_id, lower=2, key="curve-options-lower")

        rows = curve_options(db)

        assert [int(row["id"]) for row in rows] == [curve_id]
        assert int(rows[0]["rigorous_lower"]) == 2
        assert rows[0]["exact_rank"] is None
    finally:
        db.close()


def test_curve_options_ignores_nonrigorous_evidence_and_respects_filters(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        other = upsert_curve(db, family="other", parameter="1", score=2.0)
        update_curve(db, other, descent_lower=1)
        nonrigorous = upsert_curve(db, family="C2xC8", parameter="2", score=3.0)
        rigorous = upsert_curve(db, family="C2xC8", parameter="3", score=4.0)
        _evidence(db, nonrigorous, lower=9, rigorous=False, key="curve-options-nonrigorous")
        _evidence(db, rigorous, lower=2, key="curve-options-rigorous")

        rows = curve_options(db, minimum_lower=2, family="C2xC8")

        assert [int(row["id"]) for row in rows] == [rigorous]
        assert int(rows[0]["rigorous_lower"]) == 2
    finally:
        db.close()


def test_curve_options_projects_evidence_only_exact_rank_and_label(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="exact-family", parameter="11/7", score=1.0)
        _evidence(db, curve_id, lower=6, upper=6, key="curve-options-exact")

        row = curve_options(db)[0]

        assert int(row["rigorous_lower"]) == 6
        assert int(row["rigorous_upper"]) == 6
        assert int(row["exact_rank"]) == 6
        assert curve_label(row) == f"#{curve_id} · rank = 6 · exact-family · t=11/7"
    finally:
        db.close()


def test_curve_options_preserves_manual_import_visibility_at_rank_zero(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        manual = upsert_curve(db, family="manual", parameter="external")
        hidden = upsert_curve(db, family="manual", parameter="hidden")
        log_event(db, manual, "info", "Manual external curve import from test")

        rows = curve_options(db)

        assert [int(row["id"]) for row in rows] == [manual]
        assert rows[0]["rigorous_lower"] is None
        assert curve_label(rows[0]) == f"#{manual} · rank unknown · manual · t=external"
        assert curve_options(db, minimum_lower=1) == []
        assert hidden not in {int(row["id"]) for row in rows}
    finally:
        db.close()


def test_curve_options_exposes_reduced_conflict_in_label(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="conflict-family", parameter="5")
        _evidence(db, curve_id, lower=7, key="curve-options-conflict-lower")
        _evidence(db, curve_id, upper=6, key="curve-options-conflict-upper")

        row = curve_options(db)[0]

        assert row["rank_inconsistent"] is True
        assert int(row["rigorous_lower"]) == 7
        assert int(row["rigorous_upper"]) == 6
        assert curve_label(row) == f"#{curve_id} · rank conflict ≥ 7 / ≤ 6 · conflict-family · t=5"
    finally:
        db.close()
