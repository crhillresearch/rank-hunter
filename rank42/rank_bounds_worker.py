"""Isolated Sage worker for rigorous algebraic rank bounds."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sage.all import QQ, EllipticCurve, proof

MODEL_MARKER = "RANK42_RANK_MODEL="
RESULT_MARKER = "RANK42_RANK_BOUNDS="
PARI_RETRY_MARKER = "RANK42_PARI_STACK_RETRY="
PARI_GIB = 1024 * 1024 * 1024


def _runtime_versions(engine):
    sage_version = None
    engine_version = None
    try:
        from sage.version import version as sage_version_value
        sage_version = str(sage_version_value)
    except Exception:
        pass
    if engine == "pari":
        try:
            from sage.all import pari
            value = pari("default(parisize)")  # force-load the PARI interface
            _ = value
            try:
                engine_version = str(pari("version()"))
            except Exception:
                engine_version = sage_version
        except Exception:
            engine_version = sage_version
    elif engine == "mwrank":
        # eclib is distributed with Sage; retain the Sage build version when a
        # separate eclib version string is not exposed by the Python interface.
        engine_version = sage_version
    return sage_version, engine_version


def _is_pari_stack_overflow(exc):
    message = str(exc)
    return "PARI stack overflows" in message or "PARI stack overflow" in message


def _pari_rank_bound_with_stack_retry(Em, max_bytes):
    """Retry PARI rank bounds with progressively larger stacks after overflow."""
    max_bytes = max(0, int(max_bytes or 0))
    try:
        return int(Em.rank_bound(algorithm="pari"))
    except Exception as exc:
        if not _is_pari_stack_overflow(exc):
            raise

    from sage.all import pari
    size = 2 * PARI_GIB
    while size <= max_bytes:
        print(
            PARI_RETRY_MARKER
            + json.dumps({"reason": "stack_overflow", "bytes": size, "max_bytes": max_bytes}, sort_keys=True),
            flush=True,
        )
        pari.allocatemem(size)
        try:
            return int(Em.rank_bound(algorithm="pari"))
        except Exception as exc:
            if not _is_pari_stack_overflow(exc):
                raise
            size *= 2
    raise RuntimeError(f"PARI rank bound exhausted configured stack cap of {max_bytes} bytes")


def main():
    payload = json.loads(sys.stdin.read())
    engine = str(payload.get("engine") or "pari").lower()
    if engine not in {"pari", "mwrank"}:
        raise SystemExit(f"unsupported rank-bounds engine: {engine}")

    proof.all(True)
    E = EllipticCurve(QQ, [QQ(str(x)) for x in payload["a_invariants"]])
    Em = E.global_minimal_model()
    minimal = [str(x) for x in Em.a_invariants()]
    print(MODEL_MARKER + json.dumps(minimal), flush=True)

    sage_version, engine_version = _runtime_versions(engine)
    started = datetime.now(timezone.utc).isoformat()
    if engine == "pari":
        upper = _pari_rank_bound_with_stack_retry(
            Em,
            int(payload.get("pari_stack_max_bytes") or (4 * PARI_GIB)),
        )
        strategy = "sage_rank_bound_pari_stack_retry"
    else:
        upper = int(Em.rank_bound(algorithm="mwrank"))
        strategy = "sage_rank_bound_mwrank"
    finished = datetime.now(timezone.utc).isoformat()

    result = {
        "engine": engine,
        "strategy": strategy,
        "engine_version": engine_version,
        "sage_version": sage_version,
        "evidence_type": "rank_bounds",
        "status": "completed",
        "rigorous": True,
        "rigorous_lower": None,
        "rigorous_upper": upper,
        "exact_rank": None,
        "conditional_analytic_upper": None,
        "numerical_rank_signal": None,
        "assumptions": [],
        "points_found": [],
        "minimal_model_a_invariants": minimal,
        "started_at": started,
        "finished_at": finished,
    }
    print(RESULT_MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
