"""Process-isolated Brumer-Kramer class-group / 2-Selmer bounds."""
from __future__ import annotations

import json
import subprocess
import sys
import time

RESULT_MARKER = "RANK42_BRUMER_KRAMER_RESULT="


class BrumerKramerTimeout(RuntimeError):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


class BrumerKramerFailure(RuntimeError):
    def __init__(self, message, *, runtime=None, returncode=None, tail=None):
        super().__init__(message)
        self.runtime = runtime
        self.returncode = returncode
        self.tail = tail


def run_brumer_kramer_bound(
    E,
    *,
    timeout=300,
    proof_mode="grh",
):
    proof_mode = str(proof_mode or "grh")
    if proof_mode not in {"grh", "unconditional"}:
        raise ValueError("proof_mode must be 'grh' or 'unconditional'")
    timeout = max(1, int(timeout))
    payload = {
        "a_invariants": [str(x) for x in E.a_invariants()],
        "class_group_proof": proof_mode == "unconditional",
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.brumer_kramer_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        raise BrumerKramerTimeout(
            f"Brumer-Kramer class-group bound exceeded {timeout}s",
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
        raise BrumerKramerFailure(
            f"Brumer-Kramer worker failed with exit={cp.returncode}"
            + (f"; tail:\n{tail}" if tail else ""),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    try:
        result = json.loads(marker)
    except json.JSONDecodeError as exc:
        raise BrumerKramerFailure(
            f"Brumer-Kramer worker returned invalid JSON: {exc}; tail:\n{tail}",
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        ) from exc

    status = str(result.get("status") or "")
    if status not in {"completed", "unsupported"}:
        raise BrumerKramerFailure(
            str(result.get("error") or f"unexpected worker status {status!r}"),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    result["_runtime_seconds"] = runtime
    result["proof_mode"] = proof_mode
    return result
