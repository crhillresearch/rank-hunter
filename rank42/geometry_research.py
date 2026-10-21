"""Core orchestration and durable artifacts for exact geometry experiments."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone


SCHEMA = "rank42.geometry_research.v1"
WORKER = Path(__file__).with_name("geometry_research_worker.py")


def run_experiment(*, science_python, family, mode, config, timeout=90):
    if mode not in {"sections", "base_change"}:
        raise ValueError("unsupported geometry research mode")
    if not str(science_python or "").strip():
        raise ValueError("scientific Python is not configured")
    request = {"schema": SCHEMA, "family": family, "mode": mode, "config": dict(config)}
    budget = max(5, min(int(timeout), 300))
    try:
        proc = subprocess.run(
            [str(science_python), str(WORKER)],
            input=json.dumps(request), text=True, capture_output=True,
            timeout=budget, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Geometry experiment exceeded its {budget}s budget; the result is inconclusive") from exc
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "geometry worker failed").strip()[-1200:])
    try:
        result = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError("geometry worker returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("schema") != SCHEMA or result.get("mode") != mode:
        raise RuntimeError("geometry worker returned an unsupported result")
    result["experiment"] = {"config": dict(config), "timeout_seconds": budget,
                            "created_at": datetime.now(timezone.utc).isoformat()}
    return result


def save_experiment(project_root, result):
    payload = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    directory = Path(project_root) / "runs" / "geometry-research"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result['mode']}-{digest}.json"
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def recent_experiments(project_root, *, limit=8):
    directory = Path(project_root) / "runs" / "geometry-research"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]
