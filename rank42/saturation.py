"""Standalone bounded saturation for retained rigorous Mordell-Weil witnesses.

This module deliberately decouples subgroup saturation from full mwrank
2-descent. Large curves may exceed eclib/mwrank's internal coefficient limits
while still having an exact stored point basis that Sage can saturate directly.
A successful bounded saturation does not increase rank.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time

from rank42.db import get_curve, log_event
from rank42.rank_evidence import record_rank_evidence

MARKER = "RANK42_SATURATION_RESULT="


class SaturationTimeout(RuntimeError):
    pass


class SaturationFailure(RuntimeError):
    pass



def primes_in_range(min_prime, max_prime):
    """Return the rational primes in one inclusive integer interval."""
    low = max(2, int(min_prime))
    high = max(2, int(max_prime))
    if low > high:
        return []
    sieve = bytearray(b"\x01") * (high + 1)
    sieve[:2] = b"\x00\x00"
    limit = int(high ** 0.5)
    for prime in range(2, limit + 1):
        if not sieve[prime]:
            continue
        start = prime * prime
        sieve[start : high + 1 : prime] = b"\x00" * (
            ((high - start) // prime) + 1
        )
    return [prime for prime in range(low, high + 1) if sieve[prime]]


def basis_fingerprint(points):
    """Stable fingerprint for an ordered exact point basis."""
    payload = [
        [str(point[0]), str(point[1])]
        for point in list(points or ())
    ]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _point_payloads(points):
    return [[str(point[0]), str(point[1])] for point in list(points or ())]


def _heartbeat(label, stop_event, started, interval):
    interval = max(0.01, float(interval))
    while not stop_event.wait(interval):
        elapsed = int(time.monotonic() - started)
        print(f"[heartbeat] {label} still running · {elapsed}s elapsed", flush=True)


def run_saturation(a_invariants, points, *, max_prime=100, min_prime=2, timeout=900, heartbeat_label=None, heartbeat_seconds=30):
    payload = {
        "a_invariants": [str(x) for x in a_invariants],
        "points": [[str(P[0]), str(P[1])] for P in points],
        "max_prime": int(max_prime),
        "min_prime": int(min_prime),
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
            [sys.executable, "-m", "rank42.saturation_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
        )
    except subprocess.TimeoutExpired as exc:
        raise SaturationTimeout(
            f"saturation exceeded {int(timeout)}s"
        ) from exc
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=0.2)

    elapsed = time.monotonic() - started
    marker = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            try:
                marker = json.loads(line[len(MARKER):])
            except json.JSONDecodeError:
                marker = None
            break

    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:]
    )
    if marker is None:
        raise SaturationFailure(
            f"saturation worker returned no result (exit={cp.returncode}); tail:\n{tail}"
        )
    if marker.get("status") != "completed":
        raise SaturationFailure(str(marker.get("error") or tail))
    if cp.returncode != 0:
        raise SaturationFailure(
            f"saturation worker exited {cp.returncode}; tail:\n{tail}"
        )
    marker["elapsed_seconds"] = elapsed
    return marker


def run_saturation_ladder(
    a_invariants,
    points,
    *,
    min_prime=2,
    max_prime=100,
    timeout=900,
    heartbeat_label=None,
):
    """Saturate sequentially at each prime, carrying the exact basis forward."""
    primes = primes_in_range(min_prime, max_prime)
    if not primes:
        return {
            "status": "inconclusive",
            "attempted": False,
            "reason": "no_primes_in_range",
            "mode": "ladder",
            "min_prime": int(min_prime),
            "max_prime": int(max_prime),
            "prime_results": [],
        }

    work_points = _point_payloads(points)
    before_fp = basis_fingerprint(work_points)
    combined_index = 1
    total_elapsed = 0.0
    prime_results = []
    last = None

    for prime in primes:
        label = (
            f"{heartbeat_label} · p={prime}"
            if heartbeat_label
            else None
        )
        try:
            step = run_saturation(
                a_invariants,
                work_points,
                min_prime=int(prime),
                max_prime=int(prime),
                timeout=int(timeout),
                heartbeat_label=label,
            )
        except SaturationTimeout as exc:
            return {
                "status": "timeout",
                "attempted": True,
                "error": str(exc),
                "partial": bool(prime_results),
                "mode": "ladder",
                "min_prime": int(min_prime),
                "max_prime": int(max_prime),
                "saturated_through_prime": (
                    int(prime_results[-1]["prime"]) if prime_results else None
                ),
                "index": str(combined_index),
                "regulator": None if last is None else last.get("regulator"),
                "saturated_points": work_points,
                "basis_fingerprint_before": before_fp,
                "basis_fingerprint_after": basis_fingerprint(work_points),
                "prime_results": prime_results + [{
                    "prime": int(prime),
                    "status": "timeout",
                    "error": str(exc),
                }],
                "elapsed_seconds": total_elapsed,
            }
        except SaturationFailure as exc:
            return {
                "status": "error",
                "attempted": True,
                "error": str(exc),
                "partial": bool(prime_results),
                "mode": "ladder",
                "min_prime": int(min_prime),
                "max_prime": int(max_prime),
                "saturated_through_prime": (
                    int(prime_results[-1]["prime"]) if prime_results else None
                ),
                "index": str(combined_index),
                "regulator": None if last is None else last.get("regulator"),
                "saturated_points": work_points,
                "basis_fingerprint_before": before_fp,
                "basis_fingerprint_after": basis_fingerprint(work_points),
                "prime_results": prime_results + [{
                    "prime": int(prime),
                    "status": "error",
                    "error": str(exc),
                }],
                "elapsed_seconds": total_elapsed,
            }

        raw_points = list(step.get("saturated_points") or ())
        if len(raw_points) != len(work_points):
            message = (
                f"p={prime} saturation returned {len(raw_points)} point(s) "
                f"for a {len(work_points)}-point rigorous basis"
            )
            return {
                "status": "error",
                "attempted": True,
                "error": message,
                "partial": bool(prime_results),
                "mode": "ladder",
                "min_prime": int(min_prime),
                "max_prime": int(max_prime),
                "saturated_through_prime": (
                    int(prime_results[-1]["prime"]) if prime_results else None
                ),
                "index": str(combined_index),
                "regulator": None if last is None else last.get("regulator"),
                "saturated_points": work_points,
                "basis_fingerprint_before": before_fp,
                "basis_fingerprint_after": basis_fingerprint(work_points),
                "prime_results": prime_results + [{
                    "prime": int(prime),
                    "status": "error",
                    "error": message,
                }],
                "elapsed_seconds": total_elapsed,
            }

        try:
            factor = abs(int(str(step.get("index") or "1")))
        except (TypeError, ValueError) as exc:
            raise SaturationFailure(
                f"p={prime} saturation returned a non-integral index "
                f"{step.get('index')!r}"
            ) from exc

        combined_index *= factor
        work_points = _point_payloads(raw_points)
        total_elapsed += float(step.get("elapsed_seconds") or 0.0)
        prime_results.append({
            "prime": int(prime),
            "status": "completed",
            "index_factor": str(factor),
            "regulator": step.get("regulator"),
            "elapsed_seconds": step.get("elapsed_seconds"),
            "model_used": step.get("model_used"),
            "basis_changed": bool(factor != 1),
        })
        last = step

    return {
        "status": "completed",
        "attempted": True,
        "mode": "ladder",
        "min_prime": int(min_prime),
        "max_prime": int(max_prime),
        "saturated_through_prime": int(max_prime),
        "index": str(combined_index),
        "regulator": None if last is None else last.get("regulator"),
        "saturated_points": work_points,
        "model_used": None if last is None else last.get("model_used"),
        "model_warning": None if last is None else last.get("model_warning"),
        "minimal_model_a_invariants": (
            None if last is None else last.get("minimal_model_a_invariants")
        ),
        "sage_version": None if last is None else last.get("sage_version"),
        "engine_version": None if last is None else last.get("engine_version"),
        "basis_fingerprint_before": before_fp,
        "basis_fingerprint_after": basis_fingerprint(work_points),
        "prime_results": prime_results,
        "elapsed_seconds": total_elapsed,
    }


def persist_saturation_evidence(db, *, curve_id, model, result, source, extra_options=None):
    """Persist one bounded saturation attempt without changing rank claims."""
    attempted = bool(result and result.get("attempted", True))
    if not attempted:
        return None

    status = str(result.get("status") or "completed")
    error = result.get("error")
    if error and status == "completed":
        status = "error"
    if status not in {"completed", "timeout", "error", "inconclusive"}:
        status = "error" if error else "completed"

    saturated_through = result.get("saturated_through_prime")
    if saturated_through is None and status == "completed":
        saturated_through = result.get("max_prime")

    options = {
        "source": str(source),
        "mode": str(result.get("mode") or "range"),
        "max_prime": int(result.get("max_prime") or 0),
        "min_prime": int(result.get("min_prime") or 2),
        "index": result.get("index"),
        "witness_subgroup_regulator": result.get("regulator"),
        "regulator_scope": "bounded_saturated_witness_subgroup",
        "saturated_through_prime": (
            None if saturated_through is None else int(saturated_through)
        ),
        "model_used": result.get("model_used"),
        "model_warning": result.get("model_warning"),
        "witness_count": result.get("witness_count"),
        "witness_required": result.get("witness_required"),
        "basis_fingerprint_before": result.get("basis_fingerprint_before"),
        "basis_fingerprint_after": result.get("basis_fingerprint_after"),
        "prime_results": list(result.get("prime_results") or []),
    }
    if extra_options:
        options.update(dict(extra_options))
    evidence_id = record_rank_evidence(
        db,
        curve_id=int(curve_id),
        model=model,
        data={
            "engine": "sage_saturation",
            "engine_version": result.get("engine_version"),
            "sage_version": result.get("sage_version"),
            "evidence_type": "saturation",
            "status": status,
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": list(result.get("saturated_points") or []),
            "options": options,
            "minimal_model_a_invariants": result.get("minimal_model_a_invariants"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "stderr_summary": str(error) if error else None,
            "timed_out": status == "timeout",
            "partial": bool(result.get("partial", False)),
        },
    )
    if status != "completed":
        log_event(
            db,
            int(curve_id),
            "warn",
            f"bounded saturation {status}: {error or result.get('reason') or status}",
        )
    else:
        log_event(
            db,
            int(curve_id),
            "info",
            (
                f"bounded saturation completed through prime "
                f"{options['saturated_through_prime']} "
                f"(index {options['index']})"
            ),
        )
    return evidence_id

def _rigorous_curve_basis(db, curve_id):
    from sage.all import QQ, EllipticCurve
    from rank42.points import rigorous_witness_basis

    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    try:
        ainvs = json.loads(row["a_invariants_json"] or "[]")
    except Exception as exc:
        raise ValueError("curve has malformed stored a-invariants") from exc
    if len(ainvs) != 5:
        raise ValueError("curve has no stored five-term Weierstrass model")

    E = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
    basis, required, complete = rigorous_witness_basis(db, int(curve_id), E)
    return row, ainvs, E, basis, int(required), bool(complete)


def _incomplete_basis_result(*, basis, required, min_prime, max_prime, mode):
    return {
        "status": "inconclusive",
        "attempted": False,
        "reason": "incomplete_rigorous_witness_basis",
        "mode": str(mode),
        "witness_count": len(basis),
        "witness_required": int(required),
        "min_prime": int(min_prime),
        "max_prime": int(max_prime),
    }


def saturate_curve(
    db,
    curve_id,
    *,
    min_prime=2,
    max_prime=100,
    timeout=900,
    source="standalone",
):
    """Saturate the complete rigorous point-ledger witness basis for one curve."""
    _row, ainvs, E, basis, required, complete = _rigorous_curve_basis(
        db, int(curve_id)
    )
    if not complete or not basis:
        result = _incomplete_basis_result(
            basis=basis,
            required=required,
            min_prime=min_prime,
            max_prime=max_prime,
            mode="range",
        )
        log_event(
            db,
            int(curve_id),
            "warn",
            (
                "standalone saturation skipped: rigorous point-ledger basis "
                f"{len(basis)}/{int(required)}"
            ),
        )
        return result

    before_fp = basis_fingerprint(basis)
    try:
        result = run_saturation(
            E.a_invariants(),
            basis,
            min_prime=int(min_prime),
            max_prime=int(max_prime),
            timeout=int(timeout),
            heartbeat_label=f"saturation curve #{int(curve_id)}",
        )
    except SaturationTimeout as exc:
        result = {
            "status": "timeout",
            "attempted": True,
            "error": str(exc),
            "mode": "range",
            "min_prime": int(min_prime),
            "max_prime": int(max_prime),
            "witness_count": len(basis),
            "witness_required": int(required),
            "basis_fingerprint_before": before_fp,
        }
        persist_saturation_evidence(
            db,
            curve_id=int(curve_id),
            model=ainvs,
            result=result,
            source=source,
        )
        return result
    except SaturationFailure as exc:
        result = {
            "status": "error",
            "attempted": True,
            "error": str(exc),
            "mode": "range",
            "min_prime": int(min_prime),
            "max_prime": int(max_prime),
            "witness_count": len(basis),
            "witness_required": int(required),
            "basis_fingerprint_before": before_fp,
        }
        persist_saturation_evidence(
            db,
            curve_id=int(curve_id),
            model=ainvs,
            result=result,
            source=source,
        )
        return result

    result["mode"] = "range"
    result["witness_count"] = len(basis)
    result["witness_required"] = int(required)
    result["basis_fingerprint_before"] = before_fp
    result["basis_fingerprint_after"] = basis_fingerprint(
        result.get("saturated_points") or []
    )
    result["saturated_through_prime"] = int(max_prime)
    persist_saturation_evidence(
        db,
        curve_id=int(curve_id),
        model=ainvs,
        result=result,
        source=source,
    )
    return result


def saturate_curve_ladder(
    db,
    curve_id,
    *,
    min_prime=2,
    max_prime=100,
    timeout=900,
    source="standalone_ladder",
):
    """Prime-by-prime saturation of the complete rigorous witness basis."""
    _row, ainvs, E, basis, required, complete = _rigorous_curve_basis(
        db, int(curve_id)
    )
    if not complete or not basis:
        result = _incomplete_basis_result(
            basis=basis,
            required=required,
            min_prime=min_prime,
            max_prime=max_prime,
            mode="ladder",
        )
        log_event(
            db,
            int(curve_id),
            "warn",
            (
                "saturation ladder skipped: rigorous point-ledger basis "
                f"{len(basis)}/{int(required)}"
            ),
        )
        return result

    result = run_saturation_ladder(
        E.a_invariants(),
        basis,
        min_prime=int(min_prime),
        max_prime=int(max_prime),
        timeout=int(timeout),
        heartbeat_label=f"saturation ladder curve #{int(curve_id)}",
    )
    result["witness_count"] = len(basis)
    result["witness_required"] = int(required)
    result.setdefault("basis_fingerprint_before", basis_fingerprint(basis))
    if result.get("saturated_points"):
        result.setdefault(
            "basis_fingerprint_after",
            basis_fingerprint(result["saturated_points"]),
        )

    if result.get("attempted"):
        persist_saturation_evidence(
            db,
            curve_id=int(curve_id),
            model=ainvs,
            result=result,
            source=source,
        )
    return result

