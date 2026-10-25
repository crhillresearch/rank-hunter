"""Bounded PARI Heegner-point search; returned coordinates are checked in core."""
from __future__ import annotations

import json
import subprocess
import sys
import time


MARKER = "RANK42_HEEGNER_POINT_RESULT="


def run_heegner_point(E, *, timeout=120):
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "rank42.heegner_point_worker"],
            input=json.dumps({"a_invariants": [str(a) for a in E.a_invariants()]}),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "runtime_seconds": time.monotonic() - started}
    result_line = next(
        (line[len(MARKER):] for line in reversed(completed.stdout.splitlines())
         if line.startswith(MARKER)), None,
    )
    if completed.returncode or result_line is None:
        return {
            "status": "error", "runtime_seconds": time.monotonic() - started,
            "reason": "heegner_worker_failed",
            "detail": "\n".join((completed.stderr or "").splitlines()[-8:]),
        }
    try:
        result = json.loads(result_line)
        if result.get("status") == "point":
            from sage.all import QQ

            xy = result.get("point")
            if not isinstance(xy, list) or len(xy) != 2:
                raise ValueError("worker returned malformed point")
            point = E(QQ(xy[0]), QQ(xy[1]))
            if point.is_zero():
                raise ValueError("worker returned point at infinity")
            result["point"] = [str(point[0]), str(point[1])]
        elif result.get("status") not in {"not_applicable", "no_point", "error"}:
            raise ValueError("worker returned unknown status")
        result["runtime_seconds"] = time.monotonic() - started
        return result
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "error", "reason": "invalid_heegner_result",
                "detail": str(exc), "runtime_seconds": time.monotonic() - started}
