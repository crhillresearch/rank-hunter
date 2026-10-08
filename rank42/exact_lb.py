"""Hard-timeout wrapper and CLI for exact Mordell--Weil lower-bound certificates."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

MARKER = "RANK42_EXACT_LB_JSON="


class ExactCertificateTimeout(RuntimeError):
    pass


class ExactCertificateFailure(RuntimeError):
    pass



def _heartbeat(label, stop_event, started, interval):
    interval = max(0.01, float(interval))
    while not stop_event.wait(interval):
        elapsed = int(time.monotonic() - started)
        print(f"[heartbeat] {label} still running · {elapsed}s elapsed", flush=True)


def run_exact_certificate(a_invariants, points, *, timeout=120, max_halvings=40, max_prime=1000000, max_columns=640, heartbeat_label=None, heartbeat_seconds=30):
    payload = {
        "a_invariants": [str(x) for x in a_invariants],
        "points": [[str(p[0]), str(p[1])] for p in points],
        "max_halvings": int(max_halvings),
        "max_prime": int(max_prime),
        "max_columns": int(max_columns),
    }
    cmd = [sys.executable, "-m", "rank42.exact_lb_worker"]
    env = os.environ.copy()
    project_root = str(Path(__file__).resolve().parents[1])
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        project_root
        if not existing_pythonpath
        else os.pathsep.join((project_root, existing_pythonpath))
    )
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
            cmd,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=float(timeout),
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExactCertificateTimeout(f"exact lower-bound certificate exceeded {timeout}s") from exc
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=0.2)
    runtime = time.monotonic() - started

    result_line = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            result_line = line[len(MARKER) :]
            break
    tail = "\n".join(((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:])
    if result_line is None:
        raise ExactCertificateFailure(
            f"exact certificate worker failed with exit={cp.returncode}; tail:\n{tail}"
        )
    try:
        result = json.loads(result_line)
    except json.JSONDecodeError as exc:
        raise ExactCertificateFailure(f"worker returned invalid JSON; tail:\n{tail}") from exc
    result["runtime_seconds"] = runtime
    if cp.returncode != 0 and result.get("status") == "error":
        raise ExactCertificateFailure(result.get("error") or tail)
    return result


def load_witness(path):
    data = json.loads(Path(path).read_text())
    ainvs = data.get("a_invariants") or data.get("ainvs")
    points = data.get("points")
    if not ainvs or points is None:
        raise ValueError("witness JSON must contain a_invariants/ainvs and points")
    if len(ainvs) == 2:
        ainvs = ["0", "0", "0", str(ainvs[0]), str(ainvs[1])]
    return [str(x) for x in ainvs], [[str(p[0]), str(p[1])] for p in points]


def parse_args():
    ap = argparse.ArgumentParser(
        description="Rigorous lower-bound certificate from exact quadratic-character 2-descent"
    )
    ap.add_argument("witness_json")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-halvings", type=int, default=40)
    ap.add_argument("--max-prime", type=int, default=1000000)
    ap.add_argument("--max-columns", type=int, default=640)
    return ap.parse_args()


def main():
    args = parse_args()
    ainvs, points = load_witness(args.witness_json)
    result = run_exact_certificate(
        ainvs,
        points,
        timeout=args.timeout,
        max_halvings=args.max_halvings,
        max_prime=args.max_prime,
        max_columns=args.max_columns,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("independent"):
        print(
            f"\nRIGOROUS LOWER BOUND: rank >= {result['rank_lower_bound']} "
            f"from {result['point_count']} supplied points"
        )
    elif result.get("status") == "dependent":
        print("\nRESULT: supplied points are provably dependent modulo torsion")
    else:
        print("\nRESULT: exact certificate inconclusive within current budget")


if __name__ == "__main__":
    main()
