"""Process-isolated PARI Cassels--Tate Selmer refinement."""
from __future__ import annotations

import json
import subprocess
import sys
import time

RESULT_MARKER = "RANK42_CASSELS_TATE_RESULT="


class CasselsTateTimeout(RuntimeError):
    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime


class CasselsTateFailure(RuntimeError):
    def __init__(self, message, *, runtime=None, returncode=None, tail=None):
        super().__init__(message)
        self.runtime = runtime
        self.returncode = returncode
        self.tail = tail


def validate_cassels_tate_result(result):
    if str(result.get("status") or "") != "completed":
        raise ValueError("Cassels--Tate worker did not complete")
    if not bool(result.get("pairing_computed")):
        raise ValueError("Cassels--Tate worker did not attest pairing computation")
    if not bool(result.get("unconditional")) or result.get("assumptions"):
        raise ValueError("Cassels--Tate result is not unconditional")
    if str(result.get("engine") or "") != "pari.ellrank_cassels_pairing":
        raise ValueError("Cassels--Tate result has the wrong engine attestation")
    lower = int(result["mordell_weil_rank_lower_diagnostic"])
    upper = int(result["mordell_weil_rank_upper"])
    pre_pairing = int(result["pre_pairing_selmer_upper"])
    pairing_rank = int(result["cassels_tate_pairing_rank"])
    refinement = int(result["refinement_amount"])
    if lower < 0 or upper < lower or pairing_rank < 0 or pairing_rank % 2:
        raise ValueError("invalid Cassels--Tate rank data")
    if refinement != pairing_rank or pre_pairing != upper + pairing_rank:
        raise ValueError("inconsistent Cassels--Tate refinement identity")
    return result


def run_cassels_tate_refinement(E, *, timeout=300, effort=0):
    timeout = max(1, int(timeout))
    effort = int(effort)
    if effort < 0:
        raise ValueError("effort must be non-negative")
    payload = {
        "a_invariants": [str(x) for x in E.a_invariants()],
        "effort": effort,
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.cassels_tate_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        raise CasselsTateTimeout(
            f"Cassels--Tate refinement exceeded {timeout}s",
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
        raise CasselsTateFailure(
            f"Cassels--Tate worker failed with exit={cp.returncode}"
            + (f"; tail:\n{tail}" if tail else ""),
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )
    try:
        result = json.loads(marker)
        validate_cassels_tate_result(result)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise CasselsTateFailure(
            f"Cassels--Tate worker returned invalid evidence: {exc}",
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        ) from exc
    result["_runtime_seconds"] = runtime
    return result
