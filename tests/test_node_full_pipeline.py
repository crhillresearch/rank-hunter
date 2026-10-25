"""A whole Pipeline process runs on a worker while Node 1 owns SQLite."""

import json
import os
import socket
import subprocess
import sys
import time

import pytest

from rank42.db import connect, upsert_curve
from rank42.node_cluster import (
    connect_registry, controller_info, create_pair_code, full_jobs_enabled,
    list_nodes, list_tasks,
)
from rank42.pipeline_state import create_pipeline_run, get_pipeline_run
from rank42.ui_store import create_job, ensure_ui_schema, get_job


@pytest.mark.parametrize("target_mode", ["genus2_jacobian", "curve"])
def test_full_pipeline_uses_remote_worker_and_controller_database(tmp_path, target_mode):
    pytest.importorskip("sage.all")
    root = tmp_path / "controller"
    root.mkdir()
    path = root / "rank42.db"
    db = connect(path)
    ensure_ui_schema(db)
    if target_mode == "curve":
        curve_id = upsert_curve(
            db, family="node-test", parameter="0",
            a_invariants_json=json.dumps([0, 0, 0, -1, 0]),
        )
        target = {"curve_id": curve_id}
        stages = [{"id": "integral_seed", "config": {}}]
    else:
        target = {"coefficients_ascending": ["1", "0", "0", "0", "0", "1"]}
        stages = [{"id": "genus2_model_audit", "config": {}}]
    run_id = create_pipeline_run(
        db, pipeline_name="Remote model audit", target_mode=target_mode,
        target=target, stages=stages, run_config={}, project_root=root,
    )
    log = root / "pipeline.log"
    command = [
        sys.executable, "-m", "rank42.pipeline_runner",
        "--project-root", str(root), "--db", str(path),
        "--run-id", str(run_id),
    ]
    job_id = create_job(
        db, kind="pipeline_search", label="Remote model audit",
        command=command, cwd=str(root), log_path=str(log),
    )
    db.close()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "rank42.node_controller",
         "--project-root", str(root), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    worker = None
    try:
        for _ in range(60):
            if controller_info(root, port):
                break
            time.sleep(0.05)
        else:
            pytest.fail("node controller did not start")
        registry = connect_registry(root)
        code = create_pair_code(registry)
        registry.close()
        home = tmp_path / "worker-home"
        home.mkdir()
        worker = subprocess.Popen(
            [sys.executable, "-m", "rank42.node_worker", "--controller", url,
             "--poll-seconds", "0.5"],
            env={**os.environ, "HOME": str(home), "RH_NODE_PAIR_CODE": code},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            registry = connect_registry(root)
            online = bool(list_nodes(registry))
            routed = full_jobs_enabled(registry)
            registry.close()
            if online and routed:
                break
            time.sleep(0.05)
        else:
            pytest.fail("node worker did not pair")
        runner = subprocess.run(
            [sys.executable, "-m", "rank42.ui_job_runner",
             "--db", str(path), "--job-id", str(job_id)],
            env={**os.environ, "RANK_HUNTER_PROJECT_ROOT": str(root)},
            capture_output=True, text=True, timeout=30,
        )
        assert runner.returncode == 0, runner.stderr
        registry = connect_registry(root)
        tasks = list_tasks(registry)
        registry.close()
        assert tasks[0]["kind"] == "ui_job"
        assert tasks[0]["status"] == "succeeded", tasks[0]["error"]
        assert json.loads(tasks[0]["result_json"])["exit_code"] == 0
        assert "RANK42_PIPELINE_RESULT=" in log.read_text()
        db = connect(path)
        try:
            assert get_pipeline_run(db, run_id)["status"] == "completed"
            assert get_job(db, job_id)["status"] == "succeeded"
        finally:
            db.close()
    finally:
        if worker is not None:
            worker.terminate()
            worker.wait(timeout=5)
        server.terminate()
        server.wait(timeout=5)
