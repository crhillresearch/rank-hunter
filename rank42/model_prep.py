"""Hard-timeout wrapper for elliptic-curve search-model preparation."""
from __future__ import annotations

import json
import subprocess
import sys
import time

MARKER = "RANK42_MODEL_PREP="


class ModelPrepTimeout(RuntimeError):
    pass


class ModelPrepFailure(RuntimeError):
    pass


def run_global_minimal_model(a_invariants, *, timeout=10):
    payload = {"a_invariants": [str(x) for x in a_invariants]}
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.model_prep_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=float(timeout),
        )
    except subprocess.TimeoutExpired as exc:
        raise ModelPrepTimeout(
            f"global minimal model exceeded {timeout}s"
        ) from exc

    elapsed = time.monotonic() - started
    result = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            try:
                result = json.loads(line[len(MARKER):])
            except json.JSONDecodeError:
                result = None
            break
    if cp.returncode != 0 or not isinstance(result, dict):
        tail = "\n".join(
            ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:]
        )
        raise ModelPrepFailure(
            f"minimal-model worker failed with exit={cp.returncode}; tail:\n{tail}"
        )
    values = result.get("minimal_a_invariants")
    if not isinstance(values, list) or len(values) != 5:
        raise ModelPrepFailure("minimal-model worker returned no five-term model")
    return {"a_invariants": [str(x) for x in values], "runtime": elapsed}
