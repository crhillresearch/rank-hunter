from rank42.dashboard_rank_state import dashboard_rank_certification, dashboard_rank_summary
from rank42.db import connect, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence


MODEL = ["0", "0", "0", "-1", "0"]


def _evidence(db, curve_id, *, lower=None, upper=None, key=None):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "dashboard-test",
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


def test_dashboard_rank_summary_uses_authoritative_state_without_generic_hit_inflation(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        generic_only = upsert_curve(db, family="dashboard", parameter="generic")
        update_curve(db, generic_only, generic_lower=10)

        exact_from_evidence = upsert_curve(db, family="dashboard", parameter="exact")
        _evidence(db, exact_from_evidence, lower=9, upper=9, key="dashboard-exact")

        summary = dashboard_rank_summary(db)

        assert summary["best_rigorous"] == 10
        assert summary["best_exact"] == 9
        assert summary["curve_count"] == 2
        assert summary["exact_count"] == 1
        assert summary["high_rank_floor"] == 9
        assert summary["high_rank_hits"] == 1
        assert summary["high_rank_plus_2"] == 0
    finally:
        db.close()


def test_dashboard_certification_uses_reduced_exact_interval_lower_and_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        exact = upsert_curve(db, family="dashboard", parameter="exact")
        interval = upsert_curve(db, family="dashboard", parameter="interval")
        lower = upsert_curve(db, family="dashboard", parameter="lower")
        upsert_curve(db, family="dashboard", parameter="none")
        conflict = upsert_curve(db, family="dashboard", parameter="conflict")

        _evidence(db, exact, lower=6, upper=6, key="dashboard-cert-exact")
        _evidence(db, interval, lower=5, upper=7, key="dashboard-cert-interval")
        _evidence(db, lower, lower=4, key="dashboard-cert-lower")
        _evidence(db, conflict, lower=8, key="dashboard-cert-conflict-lower")
        _evidence(db, conflict, upper=7, key="dashboard-cert-conflict-upper")

        summary = dashboard_rank_certification(db)

        assert summary["total"] == 5
        assert summary["counts"] == {
            "exact": 1,
            "lower": 1,
            "interval": 1,
            "none": 1,
            "conflict": 1,
        }
    finally:
        db.close()
