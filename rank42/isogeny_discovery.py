"""Core-owned hard-timeout execution for Sage isogeny discovery."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

MARKER = "RANK42_ISOGENY_RESULT="


def _terminate_process_tree(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    proc.wait()


def run_isogeny_degree_discovery(a_invariants, degree, points, *, timeout):
    """Discover one prime-degree isogeny neighborhood outside Pipeline."""
    timeout = max(1, int(timeout))
    payload = {
        "a_invariants": [str(x) for x in a_invariants],
        "degree": int(degree),
        "points": [[str(P[0]), str(P[1])] for P in (points or [])],
    }
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-m", "rank42.isogeny_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(
            json.dumps(payload),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        return {
            "status": "timeout",
            "reason": "isogeny discovery exceeded core timeout",
            "degree": int(degree),
            "timeout_seconds": timeout,
            "runtime_seconds": time.monotonic() - started,
            "children": [],
        }

    runtime = time.monotonic() - started
    marker = None
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(MARKER):
            marker = line[len(MARKER):]
            break
    tail = "\n".join(
        ((stdout or "") + "\n" + (stderr or "")).splitlines()[-40:]
    )
    if marker is None:
        return {
            "status": "error",
            "reason": "isogeny discovery worker returned no result marker",
            "error": f"worker exit={proc.returncode}",
            "degree": int(degree),
            "runtime_seconds": runtime,
            "output_tail": tail,
            "children": [],
        }
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "reason": "isogeny discovery worker returned invalid JSON",
            "error": repr(exc),
            "degree": int(degree),
            "runtime_seconds": runtime,
            "output_tail": tail,
            "children": [],
        }
    result = dict(result)
    result["degree"] = int(degree)
    result["runtime_seconds"] = float(
        result.get("runtime_seconds") or runtime
    )
    result["timeout_seconds"] = timeout
    result["worker_exit_code"] = int(proc.returncode)
    if proc.returncode != 0 and str(result.get("status")) != "error":
        result["status"] = "error"
        result.setdefault("reason", "isogeny discovery worker exited nonzero")
        result.setdefault("error", f"worker exit={proc.returncode}")
        result.setdefault("output_tail", tail)
    result.setdefault("children", [])
    return result
