"""Hard-timeout wrappers for classical 2-descent / covering searches.

This module is deliberately family-agnostic. It does not use eclib-rh. The
isolated worker exposes Sage's classical Denis Simon known-points two-descent
and standard eclib/mwrank two-descent, returning only exact points reconstructed
on the supplied curve plus any rigorous upper bound produced by the engine.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

WORKER = Path(__file__).with_name("classical_descent_worker.py")
MARKER = "RANK42_CLASSICAL_DESCENT_RESULT="


class ClassicalDescentTimeout(RuntimeError):
    pass


class ClassicalDescentFailure(RuntimeError):
    pass



def _heartbeat(label, stop_event, started, interval):
    interval = max(0.01, float(interval))
    while not stop_event.wait(interval):
        elapsed = int(time.monotonic() - started)
        print(f"[heartbeat] {label} still running · {elapsed}s elapsed", flush=True)


def run_classical_descent(
    a_invariants,
    known_points,
    *,
    mode="simon_known",
    timeout=300,
    first_limit=20,
    second_limit=10,
    n_aux=-1,
    lim1=5,
    lim3=80,
    limtriv=3,
    maxprob=20,
    limbigprime=0,
    heartbeat_label=None,
    heartbeat_seconds=30,
):
    """Run one isolated classical descent/covering computation.

    Mathematical lower bounds returned by the underlying routine are not
    promoted here. Callers must exact-certify any newly returned points before
    increasing Rank Hunter's rigorous lower bound.
    """
    payload = {
        "mode": str(mode),
        "a_invariants": [str(x) for x in a_invariants],
        "known_points": [[str(P[0]), str(P[1])] for P in known_points],
        "first_limit": int(first_limit),
        "second_limit": int(second_limit),
        "n_aux": int(n_aux),
        "lim1": int(lim1),
        "lim3": int(lim3),
        "limtriv": int(limtriv),
        "maxprob": int(maxprob),
        "limbigprime": int(limbigprime),
    }
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
            [sys.executable, str(WORKER)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClassicalDescentTimeout(
            f"{mode} classical 2-descent exceeded {int(timeout)}s"
        ) from exc
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=0.2)

    runtime = time.monotonic() - started
    marker_line = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            marker_line = line[len(MARKER):]
            break
    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-40:]
    )
    if marker_line is None:
        raise ClassicalDescentFailure(
            f"{mode} worker returned no result marker (exit={cp.returncode}); tail:\n{tail}"
        )
    try:
        result = json.loads(marker_line)
    except json.JSONDecodeError as exc:
        raise ClassicalDescentFailure(
            f"{mode} worker returned invalid JSON; tail:\n{tail}"
        ) from exc

    result["runtime_seconds"] = runtime
    result["worker_exit_code"] = int(cp.returncode)
    if result.get("status") == "error":
        raise ClassicalDescentFailure(result.get("error") or tail)
    if cp.returncode != 0:
        raise ClassicalDescentFailure(
            f"{mode} worker exited {cp.returncode} after emitting result; tail:\n{tail}"
        )
    return result
