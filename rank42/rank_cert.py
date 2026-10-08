"""Bounded exact-rank certification wrapper for small curves.

The caller never trusts a long-running in-process ``E.rank()`` call.  The
actual Sage computation runs in a child process with a hard timeout.  A result
is promoted to ``exact_rank`` only when Sage returns under proof mode.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

MARKER = "RANK42_RANK_CERT="


class RankCertificationTimeout(RuntimeError):
    pass


class RankCertificationFailure(RuntimeError):
    pass



def _heartbeat(label, stop_event, started, interval):
    interval = max(0.01, float(interval))
    while not stop_event.wait(interval):
        elapsed = int(time.monotonic() - started)
        print(f"[heartbeat] {label} still running · {elapsed}s elapsed", flush=True)


def run_rank_certification(a_invariants, *, timeout: int = 20, heartbeat_label=None, heartbeat_seconds=30) -> dict:
    payload = {"a_invariants": [str(x) for x in a_invariants]}
    started = time.monotonic()
    stop_event = threading.Event()
    heartbeat_thread = None
    if heartbeat_label:
        heartbeat_thread = threading.Thread(
            target=_heartbeat,
            args=(str(heartbeat_label), stop_event, started, heartbeat_seconds),
            daemon=True,
        )
        heartbeat_thread.start()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.rank_cert_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=float(timeout),
        )
    except subprocess.TimeoutExpired as exc:
        raise RankCertificationTimeout(f"exact-rank certification exceeded {int(timeout)}s") from exc
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=0.2)

    runtime = time.monotonic() - started
    result_line = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            result_line = line[len(MARKER):]
            break
    tail = "\n".join(((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:])
    if cp.returncode != 0 or result_line is None:
        raise RankCertificationFailure(
            f"exact-rank worker failed with exit={cp.returncode}; tail:\n{tail}"
        )
    try:
        result = json.loads(result_line)
    except json.JSONDecodeError as exc:
        raise RankCertificationFailure(f"invalid exact-rank worker JSON; tail:\n{tail}") from exc
    result["_runtime_seconds"] = runtime
    return result
