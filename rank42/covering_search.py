"""Shared exact stored-covering search service for Pipeline and Analysis Quartics."""
from __future__ import annotations

import json
import time

from rank42.coverings import map_quartic_point, validate_covering
from rank42.lattice_store import (
    completed_covering_search_attempt,
    record_covering_search_attempt,
    update_covering,
)
from rank42.points import record_point_discovery, upsert_point
from rank42.ratpoints import RatpointsTimeout, run_ratpoints


def covering_row_to_data(row, *, height):
    return validate_covering({
        "schema": str(row["schema_version"]),
        "curve_id": int(row["curve_id"]),
        "lattice_id": row["lattice_id"],
        "hole_id": row["hole_id"],
        "quartic": {
            **json.loads(row["quartic_json"] or "{}"),
            "height": int(height),
        },
        "map": {"x": str(row["map_x"]), "y": str(row["map_y"])},
    })


def search_stored_covering(
    db,
    *,
    row,
    E,
    height,
    timeout,
    ratpoints,
    one_point=False,
    run_id=None,
    stage_index=None,
    stage_id="analysis_quartics",
    applied_plan=None,
    resume_completed=True,
    source="analysis_quartics_covering",
    run_search=None,
):
    """Search one stored exact covering, map hits, persist points and attempt history."""
    covering_id = int(row["id"])
    curve_id = int(row["curve_id"])
    height = max(1, int(height))
    timeout = max(1, int(timeout))
    one_point = bool(one_point)
    search_runner = run_ratpoints if run_search is None else run_search

    if resume_completed and run_id is not None and stage_index is not None:
        prior = completed_covering_search_attempt(
            db,
            covering_id=covering_id,
            curve_id=curve_id,
            pipeline_run_id=int(run_id),
            pipeline_stage_index=int(stage_index),
            pipeline_stage_id=str(stage_id),
            height=height,
            backend="ratpoints",
            one_point=one_point,
        )
        if prior is not None:
            return {
                "covering_id": covering_id,
                "attempt_id": int(prior["id"]),
                "status": "completed",
                "ratpoints": int(prior["ratpoints_hits"] or 0),
                "mapped_points": int(prior["mapped_points"] or 0),
                "map_back_failures": 0,
                "map_back_errors": [],
                "runtime_seconds": prior["runtime_seconds"],
                "height": height,
                "timeout_seconds": timeout,
                "covering_height_plan": applied_plan,
                "resume_completed": True,
                "prior_timeout_seconds": int(prior["timeout_seconds"] or 0),
            }

    started = time.monotonic()
    try:
        data = covering_row_to_data(row, height=height)
        result = search_runner(
            data["quartic"]["coefficients"],
            height,
            executable=ratpoints,
            timeout=timeout,
            one_point=one_point,
        )
        mapped = 0
        map_back_failures = 0
        map_back_errors = []
        point_observations = []
        point_search_ref = (
            f"pipeline:{int(run_id)}:exact-covering:{int(stage_index)}:{covering_id}"
            if run_id is not None and stage_index is not None
            else f"covering:{covering_id}"
        )
        for point in result.get("points") or []:
            try:
                P = map_quartic_point(E, data, point.x, point.y)
            except Exception as exc:
                map_back_failures += 1
                if len(map_back_errors) < 5:
                    map_back_errors.append(repr(exc))
                continue
            if P.is_zero():
                continue
            existing = db.execute(
                "SELECT * FROM points WHERE curve_id=? AND x=? AND y=?",
                (curve_id, str(P[0]), str(P[1])),
            ).fetchone()
            point_row = upsert_point(
                db,
                curve_id=curve_id,
                x=P[0],
                y=P[1],
                source=str(source),
                role="candidate_extra",
                exact_verified=True,
                independence_status="unknown",
                rigorous_independent=False,
                search_ref=point_search_ref,
                metadata={"covering_id": covering_id},
            )
            point_observations.append(
                {
                    "point_id": None if point_row is None else int(point_row["id"]),
                    "stored_x": P[0],
                    "stored_y": P[1],
                    "quartic_x": point.x,
                    "quartic_y": point.y,
                    "preexisting_point": existing is not None,
                }
            )
            mapped += 1

        status = (
            "partial"
            if map_back_failures and mapped
            else ("error" if map_back_failures else "completed")
        )
        map_error = (
            "exact covering map-back failed for one or more ratpoints hits"
            if map_back_failures
            else None
        )
        update_covering(
            db,
            covering_id,
            status=("mapped" if mapped else ("ready" if map_back_failures else "searched")),
            error=map_error,
        )
        runtime = result.get("runtime")
        if runtime is None:
            runtime = time.monotonic() - started
        attempt_id = record_covering_search_attempt(
            db,
            covering_id=covering_id,
            curve_id=curve_id,
            pipeline_run_id=run_id,
            pipeline_stage_index=stage_index,
            pipeline_stage_id=str(stage_id),
            height=height,
            timeout_seconds=timeout,
            backend="ratpoints",
            one_point=one_point,
            outcome=status,
            ratpoints_hits=len(result.get("points") or []),
            mapped_points=mapped,
            runtime_seconds=runtime,
            error=map_error,
            metadata={
                "ratpoints_executable": None if ratpoints is None else str(ratpoints),
                "map_back_failures": map_back_failures,
                "map_back_errors": map_back_errors,
                "covering_height_plan": applied_plan,
            },
        )
        for observation in point_observations:
            record_point_discovery(
                db,
                curve_id=curve_id,
                source=str(source),
                outcome="mapped",
                point_id=observation["point_id"],
                search_ref=point_search_ref,
                pipeline_run_id=run_id,
                pipeline_stage_index=stage_index,
                pipeline_stage_id=str(stage_id),
                height=height,
                stored_x=observation["stored_x"],
                stored_y=observation["stored_y"],
                exact_verified=True,
                metadata={
                    "covering_id": covering_id,
                    "covering_attempt_id": int(attempt_id),
                    "preexisting_point": bool(observation["preexisting_point"]),
                    "one_point": one_point,
                    "quartic_x": str(observation["quartic_x"]),
                    "quartic_y": str(observation["quartic_y"]),
                },
            )
        return {
            "covering_id": covering_id,
            "attempt_id": attempt_id,
            "status": status,
            "ratpoints": len(result.get("points") or []),
            "mapped_points": mapped,
            "map_back_failures": map_back_failures,
            "map_back_errors": map_back_errors,
            "runtime_seconds": runtime,
            "height": height,
            "timeout_seconds": timeout,
            "covering_height_plan": applied_plan,
            "resume_completed": False,
        }
    except RatpointsTimeout as exc:
        runtime = time.monotonic() - started
        attempt_id = record_covering_search_attempt(
            db,
            covering_id=covering_id,
            curve_id=curve_id,
            pipeline_run_id=run_id,
            pipeline_stage_index=stage_index,
            pipeline_stage_id=str(stage_id),
            height=height,
            timeout_seconds=timeout,
            backend="ratpoints",
            one_point=one_point,
            outcome="timeout",
            runtime_seconds=runtime,
            error=str(exc),
            metadata={
                "ratpoints_executable": None if ratpoints is None else str(ratpoints),
                "covering_height_plan": applied_plan,
            },
        )
        return {
            "covering_id": covering_id,
            "attempt_id": attempt_id,
            "status": "timeout",
            "error": str(exc),
            "runtime_seconds": runtime,
            "height": height,
            "timeout_seconds": timeout,
            "covering_height_plan": applied_plan,
            "resume_completed": False,
        }
    except Exception as exc:
        runtime = time.monotonic() - started
        attempt_id = record_covering_search_attempt(
            db,
            covering_id=covering_id,
            curve_id=curve_id,
            pipeline_run_id=run_id,
            pipeline_stage_index=stage_index,
            pipeline_stage_id=str(stage_id),
            height=height,
            timeout_seconds=timeout,
            backend="ratpoints",
            one_point=one_point,
            outcome="error",
            runtime_seconds=runtime,
            error=repr(exc),
            metadata={
                "ratpoints_executable": None if ratpoints is None else str(ratpoints),
                "covering_height_plan": applied_plan,
            },
        )
        return {
            "covering_id": covering_id,
            "attempt_id": attempt_id,
            "status": "error",
            "error": repr(exc),
            "runtime_seconds": runtime,
            "height": height,
            "timeout_seconds": timeout,
            "covering_height_plan": applied_plan,
            "resume_completed": False,
        }
