"""Adapter-driven Euler-parameter candidate generation for Builder pipelines.

Family adapters provide the Euler-factor mathematics. Core enforces bounded
execution, candidate shape, score provenance, and durable candidate-pool
storage. These scores are scheduling metadata only.
"""
from __future__ import annotations

import math

from rank42.candidates import candidate_rows, create_pool, replace_pool_rows
from rank42.plugin_hook_runner import run_isolated_plugin_hook
from rank42.plugins import load_adapter, plugin_fingerprints


def _hook_supported(plugin, name):
    try:
        adapter = load_adapter(plugin)
    except Exception:
        return False
    return adapter is not None and adapter.optional_hook(name) is not None


def _candidate_rows(raw, *, stage_name, prime_bound=None, algorithm=None):
    if not isinstance(raw, list):
        raise ValueError(f"{stage_name} did not return a candidates list")
    out = []
    seen = set()
    for index, rec in enumerate(raw, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"{stage_name} candidate {index} is not an object")
        raw_parameter = rec.get("parameter", rec.get("t"))
        if raw_parameter is None and rec.get("a") is not None and rec.get("b") is not None:
            raw_parameter = f"{rec['a']}/{rec['b']}"
        parameter = str(raw_parameter or "").strip()
        if not parameter or parameter in seen:
            continue
        try:
            score = float(rec["score"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{stage_name} candidate {index} has no numeric score") from exc
        if not math.isfinite(score):
            raise ValueError(f"{stage_name} candidate {index} has a nonfinite score")
        seen.add(parameter)
        provenance = rec.get("score_provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        prior_provenance = rec.get("prior_score_provenance")
        if not isinstance(prior_provenance, list):
            prior_provenance = []
        if isinstance(rec.get("score_provenance"), dict) and rec.get("score_provenance"):
            prior_provenance = [*prior_provenance, dict(rec["score_provenance"])]
        row = dict(rec)
        row.pop("t", None)
        row["parameter"] = parameter
        row["score"] = score
        row["prime_bound"] = int(rec.get("prime_bound") or prime_bound or 0) or None
        row["ranking_kind"] = "family_euler"
        row["score_provenance"] = {
            **provenance,
            "algorithm": provenance.get("algorithm") or algorithm or "family_adapter_euler_factor_v1",
            "source_mode": "family_euler_factor_adapter",
            "prime_bound": provenance.get("prime_bound") or row["prime_bound"],
            "ranking_kind": "family_euler",
            "heuristic_only": True,
            "prior_stages": prior_provenance,
        }
        out.append(row)
    out.sort(key=lambda row: (-row["score"], row["parameter"]))
    return out


def euler_parameter_candidates(
    db,
    *,
    plugin,
    variant,
    stages,
    run_id,
    target,
    candidate_defaults,
    corpus_policy="off",
    project_root=None,
):
    """Run a family's Euler scan/rescore hooks and persist a reusable pool."""
    scan_records = [rec for rec in stages if rec["id"] == "euler_parameter_scan"]
    rescore_records = [rec for rec in stages if rec["id"] == "euler_parameter_rescore"]
    if len(scan_records) != 1:
        raise ValueError("Euler Parameter Scan must appear exactly once")

    scan = scan_records[0]["config"]
    scan_hook = "scan_pipeline_euler_parameters"
    if not _hook_supported(plugin, scan_hook):
        raise RuntimeError(f"family adapter does not implement {scan_hook}")
    timeout = max(1, int(scan.get("timeout") or 300))
    payload = {
        "schema": "rankhunter.euler-parameter-scan-request/v1",
        "stage_id": "euler_parameter_scan",
        "plugin_id": str(plugin.id),
        "variant_id": str(variant.id),
        "family_spec": str(variant.family_spec),
        "candidate_generation": dict(candidate_defaults or {}),
        "target": dict(target or {}),
        "config": dict(scan),
    }
    result = dict(run_isolated_plugin_hook(plugin, scan_hook, payload, timeout=timeout))
    if result.get("status") not in {"completed", "partial"}:
        raise RuntimeError(
            f"Euler Parameter Scan {result.get('status')}: "
            f"{result.get('reason') or result.get('error') or 'adapter failed'}"
        )
    complete = bool(result.get("coverage_complete"))
    try:
        examined = int(result.get("parameters_examined"))
    except (TypeError, ValueError):
        examined = 0
    max_parameters = max(1, int(scan.get("max_parameters") or 1000000))
    if examined > max_parameters:
        raise RuntimeError(
            f"Euler Parameter Scan exceeded its cap ({examined}>{max_parameters})"
        )
    if complete:
        try:
            space_size = int(result.get("parameter_space_size"))
        except (TypeError, ValueError):
            space_size = 0
        if examined <= 0 or space_size <= 0 or examined != space_size:
            raise RuntimeError(
                "complete Euler scan must report equal positive "
                "parameters_examined and parameter_space_size"
            )
    if not complete and not bool(scan.get("allow_partial_coverage", False)):
        raise RuntimeError(
            "Euler Parameter Scan did not report complete parameter coverage; "
            "set allow_partial_coverage only when a partial shortlist is intended"
        )
    rows = _candidate_rows(
        result.get("candidates"),
        stage_name="Euler Parameter Scan",
        prime_bound=scan.get("prime_bound"),
        algorithm=result.get("score_algorithm"),
    )
    scan_keep = max(1, int(scan.get("keep") or candidate_defaults.get("top") or 1000))
    rows = rows[:scan_keep]
    reports = [{
        "stage_id": "euler_parameter_scan",
        "status": result.get("status"),
        "coverage_complete": complete,
        "parameters_examined": result.get("parameters_examined"),
        "parameter_space_size": result.get("parameter_space_size"),
        "candidate_count": len(rows),
        "score_algorithm": result.get("score_algorithm"),
        "runtime_seconds": result.get("runtime_seconds"),
    }]

    for index, rec in enumerate(rescore_records, 1):
        cfg = rec["config"]
        hook = "rescore_pipeline_euler_parameters"
        if not _hook_supported(plugin, hook):
            raise RuntimeError(f"family adapter does not implement {hook}")
        rescore_payload = {
            "schema": "rankhunter.euler-parameter-rescore-request/v1",
            "stage_id": "euler_parameter_rescore",
            "plugin_id": str(plugin.id),
            "variant_id": str(variant.id),
            "family_spec": str(variant.family_spec),
            "candidates": rows,
            "candidate_generation": dict(candidate_defaults or {}),
            "config": dict(cfg),
        }
        prior_by_parameter = {row["parameter"]: row for row in rows}
        rescored = dict(run_isolated_plugin_hook(
            plugin, hook, rescore_payload,
            timeout=max(1, int(cfg.get("timeout") or 300)),
        ))
        if rescored.get("status") not in {"completed", "partial"}:
            raise RuntimeError(
                f"Euler Parameter Rescore {rescored.get('status')}: "
                f"{rescored.get('reason') or rescored.get('error') or 'adapter failed'}"
            )
        if not bool(rescored.get("coverage_complete")) and not bool(
            cfg.get("allow_partial_coverage", False)
        ):
            raise RuntimeError(
                "Euler Parameter Rescore did not report complete shortlist coverage; "
                "set allow_partial_coverage only when an incomplete rescore is intended"
            )
        raw_rescored = rescored.get("candidates")
        if not isinstance(raw_rescored, list):
            raise ValueError("Euler Parameter Rescore did not return a candidates list")
        for candidate in raw_rescored:
            if not isinstance(candidate, dict):
                continue
            key = str(candidate.get("parameter", candidate.get("t", ""))).strip()
            prior = prior_by_parameter.get(key)
            if prior is not None:
                inherited = list(prior.get("score_provenance", {}).get("prior_stages") or [])
                if prior.get("score_provenance"):
                    inherited.append(dict(prior["score_provenance"]))
                candidate.setdefault("prior_score_provenance", inherited)
        try:
            rescored_count = int(rescored.get("parameters_rescored", len(rows)))
        except (TypeError, ValueError):
            rescored_count = -1
        if bool(rescored.get("coverage_complete")) and rescored_count != len(rows):
            raise RuntimeError(
                "complete Euler rescore must report that every input candidate was rescored"
            )
        rows = _candidate_rows(
            raw_rescored,
            stage_name="Euler Parameter Rescore",
            prime_bound=cfg.get("prime_bound"),
            algorithm=rescored.get("score_algorithm"),
        )
        rows = rows[:max(1, int(cfg.get("keep") or len(rows) or 1))]
        reports.append({
            "stage_id": "euler_parameter_rescore",
            "stage_number": index,
            "status": rescored.get("status"),
            "coverage_complete": rescored.get("coverage_complete"),
            "parameters_rescored": rescored_count,
            "candidate_count": len(rows),
            "score_algorithm": rescored.get("score_algorithm"),
            "prime_bound": cfg.get("prime_bound"),
            "runtime_seconds": rescored.get("runtime_seconds"),
        })

    if corpus_policy != "off":
        from rank42.corpus import apply_candidate_corpus_policy
        rows, corpus_summary = apply_candidate_corpus_policy(
            project_root, plugin, variant, rows, corpus_policy
        )
    else:
        corpus_summary = {"policy": "off", "declared": False}

    output_name = str(
        target.get("output_pool_name")
        or f"pipeline-run-{int(run_id)}-{plugin.id}-{variant.id}"
    ).strip()
    fingerprints = plugin_fingerprints(plugin, variant)
    generation = {
        "pipeline_run_id": int(run_id),
        "generator": "pipeline_euler_parameter_hooks",
        "ranking_kind": "family_euler",
        "family_spec": variant.family_spec,
        "variant_id": variant.id,
        "stage_reports": reports,
        "corpus_summary": corpus_summary,
        "candidate_generation": dict(candidate_defaults or {}),
    }
    pool = create_pool(
        db,
        name=output_name,
        plugin_id=plugin.id,
        plugin_version=plugin.version,
        family_spec=variant.family_spec,
        generation=generation,
        status="generating",
        **fingerprints,
    )
    replace_pool_rows(db, int(pool["id"]), rows)
    pool_rows = candidate_rows(db, int(pool["id"]), limit=None)
    from rank42.candidate_ranking_state import pool_candidate_ranking_state
    return [
        {
            "parameter": str(row["parameter"]),
            "score": float(row["score"] or 0.0),
            "rank_order": int(row["rank_order"] or index),
            "candidate_id": int(row["id"]),
            "score_provenance": pool_candidate_ranking_state(row)["score_provenance"],
            "corpus_summary": corpus_summary,
            "corpus_matches": [],
        }
        for index, row in enumerate(pool_rows, 1)
    ]
