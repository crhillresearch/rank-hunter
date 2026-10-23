"""Bounded function-field section search for JSON FormulaFamily Pipelines."""

from __future__ import annotations

import hashlib
import json
import sys

from sage.all import QQ

from rank42.formula_family import FormulaFamily
from rank42.geometry_research import run_experiment, save_experiment
from rank42.points import upsert_point


_RUN_SEARCH_CACHE = {}


def bounded_section_search(
    db, *, run_id, curve_id, curve, family, parameter, project_root, config,
):
    """Find exact Q(t) identities once per family/run and test this specialization."""
    if not isinstance(family, FormulaFamily):
        return {"status": "unsupported", "reason": "requires_json_formula_family"}
    settings = {
        "degree": max(0, min(int(config.get("degree", 1)), 3)),
        "coefficient_bound": max(1, min(int(config.get("coefficient_bound", 2)), 6)),
        "max_trials": max(1, min(int(config.get("max_trials", 2000)), 20000)),
        "max_results": max(1, min(int(config.get("max_results", 12)), 64)),
        "rational_x": bool(config.get("rational_x", True)),
    }
    family_json = json.dumps(family.data, sort_keys=True, separators=(",", ":"))
    key = (
        int(run_id), hashlib.sha256(family_json.encode()).hexdigest(),
        json.dumps(settings, sort_keys=True),
    )
    if key not in _RUN_SEARCH_CACHE:
        try:
            experiment = run_experiment(
                science_python=sys.executable, family=family.data,
                mode="sections", config=settings,
                timeout=max(5, min(int(config.get("timeout", 90)), 300)),
            )
            artifact = save_experiment(project_root, experiment)
            _RUN_SEARCH_CACHE[key] = (experiment, str(artifact))
        except (RuntimeError, TimeoutError, ValueError) as exc:
            _RUN_SEARCH_CACHE[key] = ({"error": str(exc)[:500]}, None)
    experiment, artifact = _RUN_SEARCH_CACHE[key]
    if "error" in experiment:
        return {"status": "inconclusive", "error": experiment["error"],
                "source_sha256": key[1]}

    search = experiment.get("result") or {}
    sections = list(search.get("sections") or [])
    result = {
        "status": "completed", "evidence": "exact_function_field_identity",
        "independence": "not_certified", "source_sha256": experiment["source_sha256"],
        "experiment_artifact": artifact, "sections": sections,
        "sections_found": len(sections),
        "x_candidates_checked": int(search.get("x_candidates_checked") or 0),
        "coverage": search.get("coverage"), "specialized_points": 0,
        "specialization_status": "not_attempted",
    }
    try:
        parameter_q = QQ(str(parameter))
        source = family.curve(parameter_q)
        if source is None or not source.is_isomorphic(curve):
            result["specialization_status"] = "model_mismatch_or_singular_fiber"
            return result
        map_to_curve = source.isomorphism_to(curve)
        point_ids = []
        for index, section in enumerate(sections):
            x = family._eval(section["x"], parameter_q)
            y = family._eval(section["y"], parameter_q)
            point = map_to_curve(source(x, y))
            if point.is_zero():
                continue
            checked = curve(point[0], point[1])
            stored = upsert_point(
                db, curve_id=int(curve_id), x=checked[0], y=checked[1],
                source="pipeline_bounded_section_search", role="candidate_extra",
                exact_verified=True, independence_status="unknown",
                rigorous_independent=False,
                search_ref=f"pipeline:{int(run_id)}:section:{index}",
                metadata={"source_sha256": experiment["source_sha256"],
                          "section_index": index, "section": section,
                          "experiment_artifact": artifact,
                          "verification": "exact_function_field_identity_and_fiber_membership"},
            )
            point_ids.append(int(stored["id"]))
        result["specialized_points"] = len(set(point_ids))
        result["point_ids"] = sorted(set(point_ids))
        result["specialization_status"] = "exact_on_stored_curve"
    except (ArithmeticError, TypeError, ValueError, ZeroDivisionError) as exc:
        result["specialization_status"] = "inconclusive"
        result["specialization_error"] = str(exc)[:500]
    return result
