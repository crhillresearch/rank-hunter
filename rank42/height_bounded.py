"""Hard-timeout wrapper for numerical Neron--Tate height screens."""

from __future__ import annotations

import json
import subprocess
import sys
import time

MARKER = "RANK42_HEIGHT_JSON="


class HeightScreenTimeout(RuntimeError):
    pass


class HeightScreenFailure(RuntimeError):
    pass


def run_height_screen(E, points, *, precision=128, timeout=60):
    payload = {
        "a_invariants": [str(a) for a in E.a_invariants()],
        "points": [[str(P[0]), str(P[1])] for P in points],
        "precision": int(precision),
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.height_worker"],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=float(timeout), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HeightScreenTimeout(f"height screen exceeded {timeout}s") from exc
    runtime = time.monotonic() - started
    result_line = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            result_line = line[len(MARKER):]
            break
    tail = "\n".join(((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:])
    if result_line is None:
        raise HeightScreenFailure(f"height worker failed with exit={cp.returncode}; tail:\n{tail}")
    try:
        result = json.loads(result_line)
    except json.JSONDecodeError as exc:
        raise HeightScreenFailure(f"height worker returned invalid JSON; tail:\n{tail}") from exc
    result["runtime_seconds"] = runtime
    if result.get("status") == "error" or cp.returncode != 0:
        raise HeightScreenFailure(result.get("error") or tail)
    return result
