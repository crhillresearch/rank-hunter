"""Core-owned hard-timeout execution for one Targeted Twist trial."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

MARKER = "RANK42_TARGETED_TWIST_RESULT="


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


def run_targeted_twist_trial(
    a_invariants,
    twist_d,
    *,
    prime_bound,
    root_sign,
    timeout,
):
    timeout = max(0.05, float(timeout))
    payload = {
        "a_invariants": [str(x) for x in a_invariants],
        "twist_d": int(twist_d),
        "prime_bound": max(3, int(prime_bound)),
        "root_sign": str(root_sign or "any"),
    }
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-m", "rank42.targeted_twist_worker"],
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
            "reason": "targeted twist trial exceeded core timeout",
            "twist_d": int(twist_d),
            "timeout_seconds": float(timeout),
            "runtime_seconds": time.monotonic() - started,
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
            "reason": "targeted twist worker returned no result marker",
            "error": f"worker exit={proc.returncode}",
            "twist_d": int(twist_d),
            "runtime_seconds": runtime,
            "output_tail": tail,
        }
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "reason": "targeted twist worker returned invalid JSON",
            "error": repr(exc),
            "twist_d": int(twist_d),
            "runtime_seconds": runtime,
            "output_tail": tail,
        }
    result = dict(result)
    result["twist_d"] = int(twist_d)
    result["timeout_seconds"] = timeout
    result["runtime_seconds"] = float(
        result.get("runtime_seconds") or runtime
    )
    result["worker_exit_code"] = int(proc.returncode)
    if proc.returncode != 0 and str(result.get("status")) != "error":
        result["status"] = "error"
        result.setdefault("reason", "targeted twist worker exited nonzero")
        result.setdefault("error", f"worker exit={proc.returncode}")
        result.setdefault("output_tail", tail)
    return result
