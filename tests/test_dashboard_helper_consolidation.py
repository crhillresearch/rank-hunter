import inspect

import rank42.dashboard_snapshot as dashboard_snapshot
from rank42.ui_pages import dashboard, dashboard_charts, dashboard_research, dashboard_sections


def test_dashboard_rank_authority_has_no_legacy_alias_or_duplicate_renderers():
    research = inspect.getsource(dashboard_research)
    charts = inspect.getsource(dashboard_charts)

    assert "def rank_summary(" not in research
    assert "def _render_rank_certification(" not in research
    assert "def _render_rank_certification_summary(" not in charts
    assert "dashboard_rank_certification(" not in research
    assert "dashboard_rank_certification(" not in charts


def test_dashboard_certification_presentation_consumes_snapshot_summary_only():
    source = inspect.getsource(dashboard._frontier_certification_html)
    evidence = inspect.getsource(dashboard._evidence_summary_row)

    assert "db.execute(" not in source
    assert "dashboard_rank_certification(" not in source
    assert "_frontier_certification_html(certification)" in evidence


def test_dashboard_job_summary_has_one_snapshot_implementation():
    research = inspect.getsource(dashboard_research)
    sections = inspect.getsource(dashboard_sections)
    snapshot = inspect.getsource(dashboard_snapshot.dashboard_operations_snapshot)

    assert "def pipeline_job_summary(" not in research
    assert "def pipeline_job_summary(" not in sections
    assert '"job_counts": {' in snapshot
    assert '"active": sum(' in snapshot
    assert '"completed": int(job_statuses.get("succeeded") or 0)' in snapshot
    assert '"failed": sum(' in snapshot


def test_dashboard_operations_panel_consumes_snapshot_job_counts_only():
    source = inspect.getsource(dashboard._operations_state_panel)

    assert 'jobs = snapshot["job_counts"]' in source
    assert "list_jobs(" not in source
    assert "pipeline_job_summary(" not in source
