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
    if mode not in {"sections", "base_change", "rank_jump_constructor"}:
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


def register_rank_jump_family(project_root, result, cover_index):
    """Register one certified rational pullback as a searchable JSON family."""
    if result.get("mode") != "rank_jump_constructor":
        raise ValueError("the experiment is not a rank-jump construction")
    covers = (result.get("result") or {}).get("covers") or []
    if not 0 <= int(cover_index) < len(covers):
        raise ValueError("cover index is out of range")
    cover = covers[int(cover_index)]
    family = cover.get("derived_family")
    seed_certificate = cover.get("seed_fiber_rank_certificate")
    if (cover.get("independence") != "certified_over_quadratic_cover"
            and not seed_certificate) or not isinstance(family, dict):
        raise ValueError("only a certified rational pullback can become a searchable family")
    if not family.get("rank_lower_certificate"):
        raise ValueError("the new section needs an exact rank lower-bound certificate")
    witness = cover.get("non_torsion_witness") or {}
    validation = family.get("validation_parameter")
    if validation is None and int(cover.get("cover_degree") or 0) == 1:
        # For a linear cover the pullback coordinate is exactly w.
        validation = witness["w"]
    if validation is None:
        raise ValueError("the derived family has no verified validation parameter")
    source_digest = str(result.get("source_sha256") or "")
    if not source_digest:
        raise ValueError("the source family fingerprint is missing")
    identity = {"source_sha256": source_digest, "x": cover["x"],
                "branch": cover["branch_polynomial"], "map": cover["parameter_map"]}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    plugin_id = f"rh_rank_jump_{digest}"
    root = Path(project_root).resolve() / "plugins" / "families" / plugin_id
    manifest = {
        "schema_version": 1, "plugin_type": "family", "id": plugin_id,
        "name": str(family.get("name") or f"Quadratic rank jump {digest}"),
        "version": "0.1.0", "icon": ":material/transform:",
        "description": "Exact quadratic-cover family constructed in Rank Hunter.",
        "enabled_by_default": True, "default_variant": "derived",
        "capabilities": ["candidate_generation"],
        "variants": [{
            "id": "derived", "name": "Certified pullback",
            "curve_family_name": str(family.get("name") or f"Quadratic rank jump {digest}"),
            "family": {"kind": "json", "file": "family.json"},
            "validation_parameter": str(validation),
            "generic_rank": int(family["generic_rank"]),
            "verified_generic_rank_lower": int(family["generic_rank"]),
            "generic_rank_claim_state": "generic_lower_bound_verified",
            "candidate_defaults": {
                "a_min": -200, "a_max": 200, "b_min": 1, "b_max": 50,
                "stage_bounds": "500,1000,2000", "stage_keeps": "8000,2000,500",
                "top": 500, "engine": "sieve",
            },
        }],
        "construction_record": "construction.json",
    }
    provenance = {
        "schema": "rank42.rank_jump_registration.v1", "identity": identity,
        "source_family": result.get("family_name"),
        "experiment": result.get("experiment"), "cover": cover,
    }
    existing = root / "family.json"
    if root.exists():
        if (not existing.is_file() or not (root / "plugin.json").is_file()
                or not (root / "construction.json").is_file()
                or json.loads(existing.read_text(encoding="utf-8")) != family):
            raise FileExistsError(f"registered family {plugin_id} has different contents")
        return plugin_id, root
    root.mkdir(parents=True, exist_ok=False)
    (root / "family.json").write_text(json.dumps(family, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "construction.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plugin_id, root
