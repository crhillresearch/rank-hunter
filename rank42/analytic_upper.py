"""Process-isolated GRH-conditional analytic-rank upper bounds."""
from __future__ import annotations

import json
import subprocess
import sys
import time

RESULT_MARKER = "RANK42_ANALYTIC_UPPER_RESULT="


class AnalyticUpperTimeout(RuntimeError):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


class AnalyticUpperFailure(RuntimeError):
    def __init__(self, message, *, runtime=None, returncode=None, tail=None):
        super().__init__(message)
        self.runtime = runtime
        self.returncode = returncode
        self.tail = tail


def run_conditional_analytic_upper(
    E,
    *,
    timeout=60,
    max_delta=1.5,
    adaptive=True,
    ncpus=1,
):
    timeout = max(1, int(timeout))
    max_delta = float(max_delta)
    ncpus = max(1, int(ncpus))
    payload = {
        "a_invariants": [str(x) for x in E.a_invariants()],
        "max_delta": max_delta,
        "adaptive": bool(adaptive),
        "ncpus": ncpus,
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.analytic_upper_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        raise AnalyticUpperTimeout(
            f"conditional analytic-rank upper exceeded {timeout}s",
            runtime=runtime,
        ) from exc

    runtime = time.monotonic() - started
    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            marker = line[len(RESULT_MARKER):]
            break
    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:]
    )
    if cp.returncode != 0 or marker is None:
        raise AnalyticUpperFailure(
            f"analytic upper worker failed with exit={cp.returncode}"
            + (f"; tail:\n{tail}" if tail else ""),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as exc:
        raise AnalyticUpperFailure(
            f"analytic upper worker returned invalid JSON: {exc}; tail:\n{tail}",
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        ) from exc
    if str(result.get("status") or "") != "completed":
        raise AnalyticUpperFailure(
            str(result.get("error") or "analytic upper worker did not complete"),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    result["_runtime_seconds"] = runtime
    return result
