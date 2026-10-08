import inspect

from rank42.ui_pages import dashboard


def test_operations_panel_uses_distinct_authoritative_concepts():
    source = inspect.getsource(dashboard._operations_state_panel)

    assert "Candidate backlog" in source
    assert "Pipeline Runs active" in source
    assert "Jobs active" in source
    assert "Queue waiting" in source
    assert "Schedules enabled" in source
    assert "Dispatcher" in source

    assert 'candidates["backlog"]' in source
    assert 'runs["active"]' in source
    assert 'jobs["active"]' in source
    assert 'snapshot["queue_waiting"]' in source
    assert 'scheduler["enabled"]' in source
    assert 'dispatcher.get("healthy")' in source
    assert 'dispatcher.get("label")' in source
    assert "dispatcher_detail" in source
    assert "RECENT PIPELINE RUNS" not in source
    assert "recent_pipeline_runs" not in source


def test_operations_panel_never_labels_candidate_backlog_as_queue():
    source = inspect.getsource(dashboard._operations_state_panel)

    assert 'candidates["backlog"]' in source
    assert "<span>Queued</span>" not in source
    assert "Candidate coverage" not in source
    assert "Per Hour" not in source
    assert "PIPELINE</span>" not in source
    assert "Candidate backlog is unsearched scientific work" in source
    assert "Queue waiting is durable" in source


def test_operations_panel_is_navigation_only():
    source = inspect.getsource(dashboard._operations_state_panel)

    assert 'args=("Pipelines",)' in source
    assert 'args=("Candidate Pools",)' in source
    assert 'args=("Queue",)' in source
    assert 'args=("Scheduled",)' in source

    for lifecycle in (
        "pause_pipeline",
        "resume_pipeline",
        "retry_job",
        "stop_job",
        "hold_queue_job",
        "release_queue_job",
        "cancel_queue_job",
        "update_schedule",
    ):
        assert lifecycle not in source


def test_jobs_owner_navigation_sets_section_without_running_work():
    source = inspect.getsource(dashboard._go_to_jobs_section)

    assert 'st.session_state["jobs_section_pending"]' in source
    assert 'st.session_state["rh_page"] = "Jobs"' in source
