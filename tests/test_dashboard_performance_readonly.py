import inspect

from rank42.dashboard_snapshot import (
    dashboard_operations_snapshot,
    dashboard_research_snapshot,
)
from rank42.db import connect, upsert_curve
from rank42.rank_evidence import record_rank_evidence
from rank42.manage_store import ensure_manage_schema
from rank42.ui_store import ensure_ui_schema
from rank42.ui_pages import dashboard


MODEL = ["0", "0", "0", "-1", "0"]


def _writes(statements):
    prefixes = ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "DROP ", "REPLACE ")
    return [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(prefixes)
    ]


def test_dashboard_research_snapshot_reduces_curve_state_once(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="dashboard-r8", parameter="1")
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "dashboard-r8",
                "evidence_type": "rank_bounds",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 5,
                "rigorous_upper": 5,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
            key="dashboard-r8-exact",
        )
        traced = []
        db.set_trace_callback(traced.append)

        result = dashboard_research_snapshot(db)

        curve_scans = [
            statement
            for statement in traced
            if statement.strip().upper().startswith("SELECT * FROM CURVES")
        ]
        assert len(curve_scans) == 1
        assert result["rank"]["best_exact"] == 5
        assert result["certification"]["counts"]["exact"] == 1
        assert result["frontier"][0]["rigorous_lower"] == 5
    finally:
        db.close()


def test_dashboard_frontier_point_count_is_scoped_to_frontier_ids():
    from rank42 import dashboard_rank_state

    source = inspect.getsource(dashboard_rank_state.dashboard_frontier_rows)

    assert "rigorous_independent=1 AND curve_id IN (" in source
    assert "for chunk in _chunks(ids)" in source


def test_dashboard_research_and_operations_snapshots_are_sql_read_only(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        ensure_ui_schema(db)
        ensure_manage_schema(db)
        upsert_curve(db, family="dashboard-r8", parameter="read-only")
        traced = []
        db.set_trace_callback(traced.append)

        dashboard_research_snapshot(db)
        dashboard_operations_snapshot(db, tmp_path)

        assert _writes(traced) == []
    finally:
        db.close()


def test_dashboard_operations_uses_bounded_rows_and_aggregate_counts():
    import rank42.dashboard_snapshot as snapshot

    source = inspect.getsource(snapshot.dashboard_operations_snapshot)

    assert "list_jobs(db, limit=100)" in source
    assert "job_status_counts(db)" in source
    assert "list_pipeline_runs_readonly(db, limit=100)" in source
    assert "pipeline_run_status_counts_readonly(db)" in source
    assert "list_schedule_errors(db, limit=100)" in source
    assert "list_schedule_occurrences(db, limit=100)" in source
    assert "schedule_status_summary(db)" in source
    assert 'SELECT COUNT(*) AS n FROM candidate_pools' in source
    assert "limit=1000" not in source
    assert "limit=10000" not in source


def test_dashboard_parameter_all_scope_is_explicit_and_pool_discovery_is_bounded():
    from rank42.ui_pages import dashboard_charts

    pools = inspect.getsource(dashboard_charts._parameter_pool_rows)
    rows = inspect.getsource(dashboard_charts._parameter_search_rows)

    assert "COUNT(c.id) AS searched_numeric" in pools
    assert "GROUP BY" in pools
    assert "LIMIT ?" in pools
    assert "if pool_id is not None:" in rows
    assert "AND c.pool_id=?" in rows
    assert "LIMIT ?" not in rows
    assert "ROW_NUMBER() OVER" not in rows



def test_dashboard_page_has_no_recurring_polling_or_forced_rerun():
    source = inspect.getsource(dashboard)

    assert "st.autorefresh" not in source
    assert "st.rerun(" not in source
    assert "time.sleep(" not in source
    assert "while True" not in source


def test_dashboard_attention_reuses_loaded_state_without_campaign_projection():
    source = inspect.getsource(dashboard._render_dashboard)

    assert "dashboard_research_snapshot(db)" in source
    assert "campaign=None" in source
    assert 'rank_conflicts=research["rank_conflicts"]' in source
    assert "operations=operations" in source
    assert "dashboard_overview_snapshot" not in source


def test_dashboard_feature_hook_has_explicit_bounded_host_contract():
    from rank42 import feature_hooks, plugin_hooks

    contract = plugin_hooks.FEATURE_HOOK_HOST_CONTRACTS["dashboard.after_header"]
    assert contract["layout"] == "compact_status_v1"
    assert int(contract["max_height"]) == 180
    assert int(contract["max_contributions"]) == 2

    source = inspect.getsource(feature_hooks.render_feature_hook)
    assert 'hook_payload["host_contract"]' in source
    assert "height=max_height" in source
    assert "contributions_attempted >= max_contributions" in source
