"""A full controller/worker round trip for the shared Search point engine."""

import os
import socket
import subprocess
import sys
import time

import pytest

from rank42.node_cluster import (
    connect_registry, controller_info, create_pair_code, set_remote_search_enabled,
)
from rank42.ratpoints import run_ratpoints, vendored_executable


def test_ratpoints_search_executes_on_paired_worker(tmp_path, monkeypatch):
    pytest.importorskip("sage.all")
    if not os.path.isfile(vendored_executable("CPU")):
        pytest.skip("vendored ratpoints executable is unavailable")

    root = tmp_path / "controller"
    root.mkdir()
    worker_home = tmp_path / "worker-home"
    worker_home.mkdir()
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
        try:
            code = create_pair_code(registry)
            set_remote_search_enabled(registry, True)
        finally:
            registry.close()
        worker = subprocess.Popen(
            [sys.executable, "-m", "rank42.node_worker",
             "--controller", url, "--poll-seconds", "0.5"],
            env={**os.environ, "HOME": str(worker_home), "RH_NODE_PAIR_CODE": code},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        monkeypatch.setenv("RANK_HUNTER_PROJECT_ROOT", str(root))
        result = run_ratpoints([0, -1, 0, 1], 10, timeout=10)
        assert result["engine_provenance"]["execution_location"] == "remote_worker"
        assert len(result["points"]) == 3
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        server.terminate()
        server.wait(timeout=5)
