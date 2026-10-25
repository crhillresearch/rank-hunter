"""AI Hunt keeps planning local and sends its Pipeline rounds to workers."""

from pathlib import Path

from rank42 import ai_search_runner
from rank42 import node_compute_client


class _JobDB:
    def execute(self, statement, params):
        assert "FROM ui_jobs" in statement
        assert params == (17,)
        return self

    def fetchone(self):
        return {"log_path": "/controller/ai-hunt.log"}


def test_ai_hunt_pipeline_round_uses_full_job_route(monkeypatch):
    calls = []
    monkeypatch.setenv("RANK_HUNTER_JOB_ID", "17")
    monkeypatch.setattr(node_compute_client, "full_job_routing_enabled", lambda: True)
    monkeypatch.setattr(
        node_compute_client, "run_remote_pipeline_command",
        lambda **kwargs: calls.append(kwargs) or (0, False),
    )
    command = [
        "python", "-u", "-m", "rank42.pipeline_runner",
        "--project-root", "/controller", "--db", "/controller/rank42.db",
        "--run-id", "9",
    ]
    assert ai_search_runner._run_pipeline(
        command, cwd=Path("/controller"), seconds=600, db=_JobDB()
    ) == (0, False)
    assert calls == [{
        "job_id": 17, "command": command,
        "project_root": Path("/controller"),
        "db_path": "/controller/rank42.db",
        "log_path": "/controller/ai-hunt.log", "seconds": 600,
    }]
