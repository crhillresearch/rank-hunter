"""Core-owned hard-timeout execution for family research hooks."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WORKER = Path(__file__).with_name("plugin_hook_worker.py")
MARKER = "RANK42_PLUGIN_HOOK_RESULT="


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


def run_isolated_plugin_hook(plugin, hook_name, payload, *, timeout):
    """Execute one family adapter hook outside the Pipeline process.

    The plugin receives only structured JSON.  The configured timeout is a hard
    wall-clock deadline owned by core; timeout kills the worker process group.
    """
    adapter_path = getattr(plugin, "adapter_path", None) if plugin is not None else None
    plugin_id = str(getattr(plugin, "id", "") or "")
    if not adapter_path:
        return {
            "status": "unsupported",
            "reason": f"no plugin adapter available for {hook_name}",
            "plugin_id": plugin_id or None,
            "hook_name": str(hook_name),
        }

    request = {
        "plugin_id": plugin_id,
        "adapter_path": str(Path(adapter_path).resolve()),
        "hook_name": str(hook_name),
        "payload": dict(payload or {}),
    }
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(
            json.dumps(request),
            timeout=max(1, int(timeout)),
        )
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        return {
            "status": "timeout",
            "reason": f"{hook_name} exceeded core timeout",
            "timeout_seconds": max(1, int(timeout)),
            "runtime_seconds": time.monotonic() - started,
            "plugin_id": plugin_id or None,
            "hook_name": str(hook_name),
        }

    runtime = time.monotonic() - started
    marker = None
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(MARKER):
            marker = line[len(MARKER):]
            break
    tail = "\n".join(((stdout or "") + "\n" + (stderr or "")).splitlines()[-40:])
    if marker is None:
        return {
            "status": "error",
            "reason": "isolated plugin hook returned no result marker",
            "error": f"worker exit={proc.returncode}",
            "output_tail": tail,
            "runtime_seconds": runtime,
            "plugin_id": plugin_id or None,
            "hook_name": str(hook_name),
        }
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as exc:
        return {
            "status": "error",
            "reason": "isolated plugin hook returned invalid JSON",
            "error": repr(exc),
            "output_tail": tail,
            "runtime_seconds": runtime,
            "plugin_id": plugin_id or None,
            "hook_name": str(hook_name),
        }

    result = dict(result)
    isolated = dict(result.get("_isolated_hook") or {})
    isolated.update({
        "plugin_id": plugin_id or isolated.get("plugin_id"),
        "hook_name": str(hook_name),
        "runtime_seconds": runtime,
        "worker_exit_code": int(proc.returncode),
    })
    result["_isolated_hook"] = isolated
    if proc.returncode != 0 and str(result.get("status")) != "error":
        result["status"] = "error"
        result.setdefault("reason", "isolated plugin hook worker exited nonzero")
        result.setdefault("error", f"worker exit={proc.returncode}")
        result.setdefault("output_tail", tail)
    return result
