"""Process-isolated Gusic-Tadic specialization injectivity certificates."""
from __future__ import annotations

import json
import subprocess
import sys
import time

RESULT_MARKER = "RANK42_SPECIALIZATION_INJECTIVITY_RESULT="


class SpecializationInjectivityTimeout(RuntimeError):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


class SpecializationInjectivityFailure(RuntimeError):
    def __init__(self, message, *, runtime=None, returncode=None, tail=None):
        super().__init__(message)
        self.runtime = runtime
        self.returncode = returncode
        self.tail = tail


def run_specialization_injectivity(
    family,
    specialization_parameter,
    *,
    timeout=60,
    max_divisors=4096,
):
    data = getattr(family, "data", None)
    parameter = getattr(family, "parameter", None)
    if not isinstance(data, dict) or not parameter:
        return {
            "status": "unsupported",
            "reason": "requires_formula_family_data",
        }
    payload = {
        "parameter_variable": str(parameter),
        "specialization_parameter": str(specialization_parameter),
        "a_invariants": list(data.get("a_invariants") or []),
        "max_divisors": max(1, int(max_divisors)),
    }
    timeout = max(1, int(timeout))
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.specialization_injectivity_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        raise SpecializationInjectivityTimeout(
            f"specialization injectivity criterion exceeded {timeout}s",
            runtime=runtime,
        ) from exc

    runtime = time.monotonic() - started
    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            marker = line[len(RESULT_MARKER):]
            break
    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-40:]
    )
    if cp.returncode != 0 or marker is None:
        raise SpecializationInjectivityFailure(
            f"specialization injectivity worker failed with exit={cp.returncode}"
            + (f"; tail:\n{tail}" if tail else ""),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as exc:
        raise SpecializationInjectivityFailure(
            f"specialization injectivity worker returned invalid JSON: {exc}",
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        ) from exc
    if str(result.get("status") or "") not in {
        "completed", "unsupported", "rejected", "inconclusive"
    }:
        raise SpecializationInjectivityFailure(
            str(result.get("error") or "injectivity worker returned invalid status"),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    result["_runtime_seconds"] = runtime
    return result
