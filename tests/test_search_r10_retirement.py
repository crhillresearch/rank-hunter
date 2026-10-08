import inspect

import pytest

from rank42 import auto_search, manage_store, torsion_auto_search
from rank42.db import connect
from rank42.ui_pages import auto_search as auto_page
from rank42.ui_pages import diagnostics_page, jobs_detail_panel


def test_auto_page_is_pipeline_only_and_history_is_in_diagnostics():
    page = inspect.getsource(auto_page.page)
    archive = inspect.getsource(diagnostics_page._render_legacy_auto_archive)

    assert 'tabs(' not in page
    assert '_render_history(' not in page
    assert '_render_auto(db, ctx)' in page
    assert 'Read-only pre-Pipeline Auto history' in archive
    assert 'campaign_trials(' in archive
    assert '_launch_campaign' not in archive


def test_auto_blocking_jobs_still_prevent_conflicting_fresh_launches(monkeypatch):
    native_rows = [
        {"id": 1, "status": "queued"},
        {"id": 2, "status": "completed"},
    ]
    pipeline_rows = [
        {"id": 3, "status": "running"},
        {"id": 4, "status": "stopping"},
        {"id": 5, "status": "failed"},
    ]
    monkeypatch.setattr(auto_page, "_auto_jobs", lambda db: native_rows)
    monkeypatch.setattr(auto_page, "_auto_pipeline_jobs", lambda db: pipeline_rows)

    selected = auto_page._blocking_auto_jobs(object())

    assert auto_page.BLOCKING_JOB_STATES == {"queued", "running", "stopping"}
    assert [row["id"] for row in selected] == [1, 3, 4]


def test_legacy_auto_entrypoints_are_resume_only():
    family_main = inspect.getsource(auto_search.main)
    torsion_main = inspect.getsource(torsion_auto_search.main)

    assert 'if args.campaign_id is None:' in family_main
    assert 'legacy Auto is resume-only' in family_main
    assert 'if args.campaign_id is None:' in torsion_main
    assert 'legacy torsion Auto is resume-only' in torsion_main


def test_legacy_search_campaign_identity_prefers_separated_key():
    assert manage_store.legacy_search_campaign_id(
        {'search_campaign_id': 7, 'campaign_id': 99}
    ) == 7
    assert manage_store.legacy_search_campaign_id({'campaign_id': 99}) == 99
    assert manage_store.legacy_search_campaign_id({}) is None


def test_new_legacy_auto_schedules_are_rejected(tmp_path):
    db = connect(tmp_path / 'rank42.db')
    manage_store.ensure_manage_schema(db)

    with pytest.raises(ValueError, match='cannot be scheduled'):
        manage_store.create_schedule(
            db,
            label='Retired Auto',
            kind='auto_search',
            command=['python', '-m', 'rank42.auto_search'],
            cwd=tmp_path,
            next_run_at='2099-01-01T00:00:00+00:00',
        )


def test_jobs_disables_fresh_retry_for_legacy_auto():
    detail = inspect.getsource(jobs_detail_panel.render_job_detail)
    controls = inspect.getsource(jobs_detail_panel._render_job_controls)

    assert 'retired_search = str(row["kind"]) in RETIRED_SEARCH_JOB_KINDS' in detail
    assert "retired_search=retired_search" in detail
    assert controls.count("disabled=retired_search") == 2
    assert '"Retry fresh"' in controls
    assert '"Retry"' in controls
