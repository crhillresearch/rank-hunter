import inspect
import sqlite3
import types
from pathlib import Path

import pytest

from rank42 import auto_analyze, fixed_curve_search, general_hunt, manage_store
from rank42.ui_pages import jobs_detail_panel, jobs_schedule_panel
from rank42.ui_store import create_job, ensure_ui_schema, update_job


ROOT = Path(__file__).resolve().parents[1]


def _job_db(tmp_path):
    db = sqlite3.connect(tmp_path / "jobs.db")
    db.row_factory = sqlite3.Row
    ensure_ui_schema(db)
    return db


def _legacy_job(db, tmp_path, *, kind, command, status="stopped"):
    job_id = create_job(
        db,
        kind=kind,
        label=f"Legacy {kind}",
        command=command,
        cwd=tmp_path,
        log_path=tmp_path / f"{kind}.log",
        metadata={},
    )
    update_job(db, job_id, status=status)
    return job_id


def test_native_search_entrypoints_have_no_current_ui_callers():
    sources = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "rank42/ui_pages/family_search.py",
            "rank42/ui_pages/general_hunt.py",
            "rank42/ui_pages/target_curve.py",
        )
    )

    assert "rank42.general_hunt" not in sources
    assert "rank42.auto_analyze" not in sources
    assert "rank42.fixed_curve_search" not in sources
    assert "build_family_search_command(" not in sources
    assert "build_target_search_command(" not in sources
    assert sources.count("require_pipeline=True") == 5


def test_retired_search_kind_inventory_covers_native_surfaces():
    assert manage_store.LEGACY_NATIVE_SEARCH_JOB_KINDS == {
        "family_search",
        "free_family_search",
        "general_hunt",
        "target_free",
        "target_plugin",
    }
    assert manage_store.RETIRED_SEARCH_JOB_KINDS == (
        manage_store.LEGACY_AUTO_JOB_KINDS
        | manage_store.LEGACY_NATIVE_SEARCH_JOB_KINDS
    )


@pytest.mark.parametrize(
    ("kind", "module"),
    (
        ("general_hunt", "rank42.general_hunt"),
        ("free_family_search", "rank42.auto_analyze"),
        ("target_free", "rank42.fixed_curve_search"),
    ),
)
def test_jobs_resume_marks_retired_core_entrypoint(kind, module, tmp_path, monkeypatch):
    db = _job_db(tmp_path)
    job_id = _legacy_job(
        db,
        tmp_path,
        kind=kind,
        command=["python", "-m", module, "--db", "rank42.db"],
    )
    captured = {}
    monkeypatch.setattr(
        manage_store,
        "enqueue_job",
        lambda db_path, **kwargs: captured.update(kwargs) or 91,
    )

    assert manage_store.resume_job(db, tmp_path / "jobs.db", job_id) == 91
    assert captured["command"].count("--legacy-resume") == 1
    assert captured["metadata"]["resume_of_job_id"] == job_id


def test_fresh_retry_and_schedule_reject_retired_native_search(tmp_path):
    db = _job_db(tmp_path)
    job_id = _legacy_job(
        db,
        tmp_path,
        kind="general_hunt",
        command=["python", "-m", "rank42.general_hunt"],
        status="failed",
    )

    with pytest.raises(ValueError, match="may only resume"):
        manage_store.retry_job(db, tmp_path / "jobs.db", job_id)
    with pytest.raises(ValueError, match="cannot be scheduled"):
        manage_store.create_schedule(
            db,
            label="Retired General Hunt",
            kind="general_hunt",
            command=["python", "-m", "rank42.general_hunt"],
            cwd=tmp_path,
            next_run_at="2099-01-01T00:00:00+00:00",
        )


def test_schedule_templates_and_retry_ui_exclude_retired_search(tmp_path):
    db = _job_db(tmp_path)
    _legacy_job(
        db,
        tmp_path,
        kind="target_free",
        command=["python", "-m", "rank42.fixed_curve_search"],
        status="succeeded",
    )
    pipeline_id = _legacy_job(
        db,
        tmp_path,
        kind="pipeline_search",
        command=["python", "-m", "rank42.pipeline_runner"],
        status="succeeded",
    )

    assert [int(row["id"]) for row in jobs_schedule_panel.schedule_source_jobs(db)] == [
        pipeline_id
    ]
    source = inspect.getsource(jobs_detail_panel.render_job_detail)
    assert 'retired_search = str(row["kind"]) in RETIRED_SEARCH_JOB_KINDS' in source
    assert source.count("disabled=retired_search") == 2


@pytest.mark.parametrize(
    ("module", "label"),
    (
        (general_hunt, "standalone General Hunt is retired"),
        (auto_analyze, "standalone FREE Family Search is retired"),
        (fixed_curve_search, "standalone FREE Target Search is retired"),
    ),
)
def test_retired_core_entrypoints_reject_fresh_direct_runs(module, label, monkeypatch):
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: types.SimpleNamespace(legacy_resume=False),
    )

    with pytest.raises(SystemExit, match=label):
        module.main()
