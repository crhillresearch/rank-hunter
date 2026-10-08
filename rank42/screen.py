"""Controller for bounded specialization + quick rank screening."""

from __future__ import annotations

import json
import subprocess
import sys
import time

AINVS_MARKER = "RANK42_AINVS="
RESULT_MARKER = "RANK42_SCREEN_JSON="


class ScreenTimeout(RuntimeError):
    def __init__(self, message, *, a_invariants=None, runtime=None):
        super().__init__(message)
        self.a_invariants = a_invariants
        self.runtime = runtime


class ScreenFailure(RuntimeError):
    def __init__(self, message, *, a_invariants=None, runtime=None):
        super().__init__(message)
        self.a_invariants = a_invariants
        self.runtime = runtime


def _decode_timeout_output(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _extract_ainvs(text):
    for line in text.splitlines():
        if line.startswith(AINVS_MARKER):
            try:
                return json.loads(line[len(AINVS_MARKER):])
            except Exception:
                return None
    return None


def run_fast_screen(*, family, parameter, strategy="pari", timeout=20):
    cmd = [sys.executable, "-m", "rank42.screen_worker"]
    payload = {
        "family": family,
        "parameter": str(parameter),
        "strategy": strategy,
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            cmd,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        stdout = _decode_timeout_output(exc.stdout)
        ainvs = _extract_ainvs(stdout)
        raise ScreenTimeout(
            f"specialization + {strategy} screen exceeded {timeout}s",
            a_invariants=ainvs,
            runtime=runtime,
        ) from exc

    runtime = time.monotonic() - started
    result = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            result = json.loads(line[len(RESULT_MARKER):])
            break

    if cp.returncode != 0 or result is None:
        tail = "\n".join(((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:])
        raise ScreenFailure(
            f"screen worker failed with exit={cp.returncode}; tail:\n{tail}",
            a_invariants=_extract_ainvs(cp.stdout or ""),
            runtime=runtime,
        )

    result["_runtime_seconds"] = runtime
    return result
