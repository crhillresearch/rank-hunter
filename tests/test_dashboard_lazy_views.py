import inspect

from rank42.ui_pages import dashboard


def test_dashboard_is_one_continuous_page_without_view_dispatch():
    page = inspect.getsource(dashboard.page)
    render = inspect.getsource(dashboard._render_dashboard)

    assert '["Overview", "Research", "Operations"]' not in page
    assert "dashboard_view" not in page
    assert "_render_dashboard(db, ctx)" in page
    assert "dashboard_research_snapshot(db)" in render
    assert "dashboard_operations_snapshot(" in render


def test_dashboard_one_page_reuses_loaded_attention_authorities():
    source = inspect.getsource(dashboard._render_dashboard)

    assert 'rank_conflicts=research["rank_conflicts"]' in source
    assert "operations=operations" in source
    assert "campaign=None" in source
    assert "_summary_metrics(research)" in source
    assert "_local_frontier_panel(" in source
    assert '_evidence_summary_row(db, research["certification"])' in source
    assert "_operations_state_panel(operations)" in source
    assert 'render_plugins_panel(db, ctx, health=operations["plugins"])' in source
    assert "render_research_charts(db)" in source


def test_dashboard_page_dispatches_without_direct_scientific_queries():
    source = inspect.getsource(dashboard.page)

    assert "rank_summary(" not in source
    assert "list_jobs(" not in source
    assert "render_research_charts(" not in source
    assert "render_chart_followups(" not in source
    assert "_operations_state_panel(" not in source
