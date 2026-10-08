from rank42.candidates import create_pool, replace_pool_rows
from rank42.dashboard_rank_state import dashboard_exact_rank_distribution, dashboard_frontier_rows
from rank42.db import connect, upsert_curve, update_curve
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.dashboard_charts import _exceptional_curve_rows, _parameter_search_rows
from rank42.ui_pages.dashboard_complexity import _rank_evidence_rows, _record_complexity_rows


MODEL = ["0", "0", "0", "-1", "0"]


def _evidence(db, curve_id, *, lower=None, upper=None, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "dashboard-frontier-test",
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


def test_dashboard_frontier_uses_specialization_state_and_excludes_generic_only(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        generic_only = upsert_curve(db, family="dash-frontier", parameter="generic")
        update_curve(db, generic_only, generic_lower=12)
        evidence_lower = upsert_curve(db, family="dash-frontier", parameter="lower")
        evidence_exact = upsert_curve(db, family="dash-frontier", parameter="exact")
        _evidence(db, evidence_lower, lower=7, key="frontier-lower")
        _evidence(db, evidence_exact, lower=8, upper=8, key="frontier-exact")
        upsert_point(
            db,
            curve_id=evidence_lower,
            x="1",
            y="1",
            source="test",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
        )

        rows = dashboard_frontier_rows(db)
        by_id = {int(row["id"]): row for row in rows}

        assert generic_only not in by_id
        assert int(by_id[evidence_lower]["rigorous_lower"]) == 7
        assert by_id[evidence_lower]["exact_rank"] is None
        assert int(by_id[evidence_lower]["independent_points"]) == 1
        assert int(by_id[evidence_exact]["exact_rank"]) == 8
    finally:
        db.close()


def test_dashboard_parameter_yield_uses_evidence_only_specialization_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        evidence_curve = upsert_curve(db, family="dash-candidate", parameter="evidence")
        generic_curve = upsert_curve(db, family="dash-candidate", parameter="generic")
        _evidence(db, evidence_curve, lower=6, key="candidate-evidence")
        update_curve(db, generic_curve, generic_lower=9)

        pool = create_pool(
            db,
            name="dashboard-test-pool",
            plugin_id="test",
            family_spec="test",
        )
        replace_pool_rows(
            db,
            int(pool["id"]),
            [
                {"parameter": "1", "a": 1, "b": 2, "score": 2.0},
                {"parameter": "2", "a": 2, "b": 3, "score": 1.0},
            ],
        )
        candidates = db.execute(
            "SELECT id, parameter FROM candidates WHERE pool_id=? ORDER BY rank_order",
            (int(pool["id"]),),
        ).fetchall()
        by_parameter = {str(row["parameter"]): int(row["id"]) for row in candidates}
        db.execute(
            "UPDATE candidates SET status='searched', curve_id=? WHERE id=?",
            (evidence_curve, by_parameter["1"]),
        )
        db.execute(
            "UPDATE candidates SET status='searched', curve_id=? WHERE id=?",
            (generic_curve, by_parameter["2"]),
        )
        db.commit()

        rows = _parameter_search_rows(db, pool_id=int(pool["id"]))
        by_candidate = {int(row["Candidate"]): row for row in rows}

        assert by_candidate[by_parameter["1"]]["Yield"] == 1
        assert int(by_candidate[by_parameter["1"]]["Rigorous lower"]) == 6
        assert by_candidate[by_parameter["1"]]["Rank evidence"] == "≥ 6"
        assert by_candidate[by_parameter["2"]]["Yield"] == 0
        assert int(by_candidate[by_parameter["2"]]["Rigorous lower"]) == 0
    finally:
        db.close()


def test_dashboard_chart_and_complexity_rows_include_evidence_only_exact_and_lower(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        lower = upsert_curve(db, family="dash-chart", parameter="lower")
        exact = upsert_curve(db, family="dash-chart", parameter="exact")
        generic_only = upsert_curve(db, family="dash-chart", parameter="generic")
        update_curve(db, lower, discriminant="1000000")
        update_curve(
            db,
            exact,
            discriminant="100000000",
            conductor="101",
            a_invariants_json='["0","0","0","-1","0"]',
        )
        update_curve(db, generic_only, generic_lower=11, discriminant="10000")
        _evidence(db, lower, lower=5, key="chart-lower")
        _evidence(db, exact, lower=7, upper=7, key="chart-exact")

        exceptional = _exceptional_curve_rows(db)
        certificates = {row["Curve"]: row["Rank certificate"] for row in exceptional}
        assert certificates[f"#{lower}"] == "≥ 5"
        assert certificates[f"#{exact}"] == "= 7"
        assert f"#{generic_only}" not in certificates

        evidence_rows = _rank_evidence_rows(db)
        by_rank = {int(row["rank"]): row for row in evidence_rows}
        assert by_rank[5]["rigorous"]["count"] == 1
        assert by_rank[7]["exact"]["count"] == 1
        assert 11 not in by_rank

        distribution = dashboard_exact_rank_distribution(db)
        assert distribution == [{"rank_value": 7, "curve_count": 1}]

        records = _record_complexity_rows(db)
        rank7 = [row for row in records if int(row["rank"]) == 7]
        assert any(row["record"] == "Smallest conductor" and int(row["value"]) == 101 for row in rank7)
    finally:
        db.close()
