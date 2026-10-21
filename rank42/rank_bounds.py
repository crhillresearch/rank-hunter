"""Hard-timeout wrappers for rigorous rank-bound engines."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MODEL_MARKER = "RANK42_RANK_MODEL="
RESULT_MARKER = "RANK42_RANK_BOUNDS="
ECLIB_RH_SCHEMA = "eclib-rh-upper-v1"
ECLIB_RH_ENV = "RANK42_ECLIB_RH_UPPER"


def _decode(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _extract_marker(text, marker):
    for line in reversed(str(text or "").splitlines()):
        if line.startswith(marker):
            try:
                return json.loads(line[len(marker):])
            except Exception:
                return None
    return None


def _extract_json_object(text):
    for line in reversed(str(text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def _resolve_eclib_rh_upper():
    candidates = []
    configured = os.environ.get(ECLIB_RH_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())
    found = shutil.which("rh_upper_bound")
    if found:
        candidates.append(Path(found))
    candidates.append(Path.home() / "eclib-rh-dev" / "progs" / "rh_upper_bound")

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return str(resolved)
    return None


def eclib_rh_available() -> bool:
    return _resolve_eclib_rh_upper() is not None


def _binary_fingerprint(path):
    if not path:
        return None
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()[:20]}"
    except Exception:
        return None


def runtime_versions(engine: str) -> dict:
    """Best-effort runtime fingerprint without making it a scientific claim."""
    engine = str(engine).lower()
    if engine == "eclib_rh":
        path = _resolve_eclib_rh_upper()
        return {"sage_version": None, "engine_version": _binary_fingerprint(path)}

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
            try:
                engine_version = str(pari("version()"))
            except Exception:
                engine_version = sage_version
        except Exception:
            pass
    elif engine == "mwrank":
        engine_version = sage_version
    return {"sage_version": sage_version, "engine_version": engine_version}


def _eclib_rh_options(*, timeout, known_lower, tight):
    return {
        "timeout": int(timeout),
        "known_lower": int(known_lower if known_lower is not None else 0),
        "tight": bool(tight),
    }


def _run_eclib_rh_bounds(a_invariants, *, timeout, known_lower=0, tight=True) -> dict:
    path = _resolve_eclib_rh_upper()
    wall_started = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    options = _eclib_rh_options(timeout=timeout, known_lower=known_lower, tight=tight)
    versions = runtime_versions("eclib_rh")

    if path is None:
        return {
            "engine": "eclib_rh", "evidence_type": "rank_bounds", "status": "error",
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "conditional_analytic_upper": None, "numerical_rank_signal": None, "assumptions": [],
            "points_found": [], "timed_out": False, "partial": False,
            "minimal_model_a_invariants": None,
            "started_at": wall_started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.monotonic() - started, "stdout_summary": "",
            "stderr_summary": f"rh_upper_bound not found; set {ECLIB_RH_ENV} or put it on PATH",
            "options": options, **versions,
        }

    cmd = [path]
    if tight:
        cmd.append("--tight")
    cmd.extend(["--known-lower", str(options["known_lower"])])
    curve_text = "[" + ",".join(str(x) for x in a_invariants) + "]\n"

    try:
        cp = subprocess.run(
            cmd,
            input=curve_text,
            text=True,
            capture_output=True,
            timeout=float(timeout),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        stdout = _decode(exc.stdout)
        return {
            "engine": "eclib_rh", "evidence_type": "rank_bounds", "status": "timeout",
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "conditional_analytic_upper": None, "numerical_rank_signal": None, "assumptions": [],
            "points_found": [], "timed_out": True, "partial": False,
            "minimal_model_a_invariants": None,
            "started_at": wall_started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed, "stdout_summary": "\n".join(stdout.splitlines()[-20:]),
            "stderr_summary": _decode(exc.stderr), "options": options, **versions,
        }

    elapsed = time.monotonic() - started
    stdout = cp.stdout or ""
    stderr = cp.stderr or ""
    raw = _extract_json_object(stdout)
    valid = (
        cp.returncode == 0
        and isinstance(raw, dict)
        and raw.get("schema") == ECLIB_RH_SCHEMA
        and raw.get("status") in {"ok", "inconsistent"}
        and raw.get("rigorous") is True
        and raw.get("upper_bound") is not None
    )
    if not valid:
        return {
            "engine": "eclib_rh", "evidence_type": "rank_bounds", "status": "error",
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "conditional_analytic_upper": None, "numerical_rank_signal": None, "assumptions": [],
            "points_found": [], "timed_out": False, "partial": False,
            "minimal_model_a_invariants": None,
            "started_at": wall_started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed, "stdout_summary": "\n".join(stdout.splitlines()[-20:]),
            "stderr_summary": "\n".join(stderr.splitlines()[-30:]), "options": options, **versions,
        }

    return {
        "engine": "eclib_rh",
        "strategy": "eclib_rh_upper_adaptive" if tight else "eclib_rh_upper_fast",
        "engine_version": versions.get("engine_version"),
        "sage_version": None,
        "evidence_type": "rank_bounds",
        "status": "completed",
        "rigorous": True,
        # The lower bound was supplied by Rank Hunter and remains separate evidence.
        "rigorous_lower": None,
        "rigorous_upper": int(raw["upper_bound"]),
        "exact_rank": None,
        "conditional_analytic_upper": None,
        "numerical_rank_signal": None,
        "assumptions": [],
        "points_found": [],
        "minimal_model_a_invariants": None,
        "started_at": wall_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "timed_out": False,
        "partial": False,
        "stdout_summary": "\n".join(stdout.splitlines()[-20:]),
        "stderr_summary": "\n".join(stderr.splitlines()[-30:]),
        "options": options,
        "eclib_rh": raw,
    }


def _worker_failure_class(engine, stderr):
    """Return a stable machine-readable failure class for bounded engines."""
    text = str(stderr or "")
    lower = text.lower()
    if str(engine).lower() == "mwrank":
        if "attempt to convert" in lower and "to long fails" in lower and "too large" in lower:
            return "MWRANK_SIZE_LIMIT"
        if "a 2-descent did not complete successfully" in lower:
            return "MWRANK_FAILURE"
    if "pari stack overflow" in lower:
        return "PARI_STACK"
    return "WORKER_FAILURE"


def _worker_failure_status(engine, stderr):
    """Classify known no-bound outcomes without upgrading them to evidence."""
    failure_class = _worker_failure_class(engine, stderr)
    if failure_class in {"MWRANK_SIZE_LIMIT", "MWRANK_FAILURE"}:
        return "inconclusive"
    return "error"


def run_engine_bounds(a_invariants, *, engine="pari", timeout=300, known_lower=None, tight=True, pari_stack_max_bytes=None) -> dict:
    engine = str(engine).lower()
    if engine not in {"pari", "mwrank", "eclib_rh"}:
        raise ValueError(f"unsupported rank-bounds engine: {engine}")
    if engine == "eclib_rh":
        return _run_eclib_rh_bounds(
            a_invariants,
            timeout=timeout,
            known_lower=0 if known_lower is None else int(known_lower),
            tight=bool(tight),
        )

    payload = {"engine": engine, "a_invariants": [str(x) for x in a_invariants]}
    if engine == "pari" and pari_stack_max_bytes is not None:
        payload["pari_stack_max_bytes"] = int(pari_stack_max_bytes)
    wall_started = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.rank_bounds_worker"],
            input=json.dumps(payload), text=True, capture_output=True, timeout=float(timeout),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        stdout = _decode(exc.stdout)
        versions = runtime_versions(engine)
        return {
            "engine": engine, "evidence_type": "rank_bounds", "status": "timeout",
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "conditional_analytic_upper": None, "numerical_rank_signal": None, "assumptions": [],
            "points_found": [], "timed_out": True, "partial": False,
            "minimal_model_a_invariants": _extract_marker(stdout, MODEL_MARKER),
            "started_at": wall_started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed, "stdout_summary": "\n".join(stdout.splitlines()[-20:]),
            "stderr_summary": _decode(exc.stderr), "failure_class": "TIMEOUT",
            "options": {"timeout": int(timeout), "failure_class": "TIMEOUT"}, **versions,
        }

    elapsed = time.monotonic() - started
    stdout = cp.stdout or ""
    stderr = cp.stderr or ""
    result = _extract_marker(stdout, RESULT_MARKER)
    if cp.returncode != 0 or result is None:
        versions = runtime_versions(engine)
        return {
            "engine": engine, "evidence_type": "rank_bounds", "status": _worker_failure_status(engine, stderr),
            "rigorous": True, "rigorous_lower": None, "rigorous_upper": None, "exact_rank": None,
            "conditional_analytic_upper": None, "numerical_rank_signal": None, "assumptions": [],
            "points_found": [], "timed_out": False, "partial": False,
            "minimal_model_a_invariants": _extract_marker(stdout, MODEL_MARKER),
            "started_at": wall_started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed, "stdout_summary": "\n".join(stdout.splitlines()[-20:]),
            "stderr_summary": "\n".join(stderr.splitlines()[-30:]),
            "failure_class": _worker_failure_class(engine, stderr),
            "options": {
                "timeout": int(timeout),
                "failure_class": _worker_failure_class(engine, stderr),
            }, **versions,
        }
    result = dict(result)
    result["elapsed_seconds"] = elapsed
    result["timed_out"] = False
    result["partial"] = False
    result["stdout_summary"] = "\n".join(stdout.splitlines()[-20:])
    result["stderr_summary"] = "\n".join(stderr.splitlines()[-30:])
    result["failure_class"] = None
    result["options"] = {"timeout": int(timeout)}
    return result
