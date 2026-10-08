"""Read-only chronological research-attempt projection for one retained curve.

The timeline is deliberately a projection over existing durable artifacts.  It
does not own scientific truth and it does not create a parallel event ledger.
"""
from __future__ import annotations

import json


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _loads(value, default):
    try:
        result = json.loads(value or "")
    except Exception:
        return default
    return result if isinstance(result, type(default)) else default


def _timestamp(*values):
    for value in values:
        if value:
            return str(value)
    return ""


def _artifact_ref(kind, artifact_id):
    if artifact_id is None:
        return str(kind)
    return f"{kind}:{artifact_id}"


def _append(
    items,
    *,
    timestamp,
    source,
    kind,
    artifact_type,
    artifact_id,
    status,
    summary,
    detail=None,
    route=None,
):
    when = _timestamp(timestamp)
    if not when:
        return
    items.append({
        "timestamp": when,
        "source": str(source),
        "kind": str(kind),
        "artifact_type": str(artifact_type),
        "artifact_id": artifact_id,
        "artifact_ref": _artifact_ref(artifact_type, artifact_id),
        "status": None if status is None else str(status),
        "summary": str(summary),
        "detail": dict(detail or {}),
        "route": route,
    })


def _bounds_text(row):
    if row["exact_rank"] is not None:
        return f"rank = {int(row['exact_rank'])}"
    parts = []
    if row["rigorous_lower"] is not None:
        parts.append(f"rank ≥ {int(row['rigorous_lower'])}")
    if row["rigorous_upper"] is not None:
        parts.append(f"rank ≤ {int(row['rigorous_upper'])}")
    if row["conditional_analytic_upper"] is not None:
        parts.append(f"conditional analytic ≤ {int(row['conditional_analytic_upper'])}")
    if row["conditional_mw_upper"] is not None:
        parts.append(f"conditional MW ≤ {int(row['conditional_mw_upper'])}")
    if row["numerical_rank_signal"] is not None:
        parts.append(f"numerical signal {int(row['numerical_rank_signal'])}")
    return " · ".join(parts) or "no rank bound change"


def _point_status(row):
    if int(row["rigorous_independent"] or 0):
        return "rigorous independent"
    return str(row["independence_status"] or "unknown")


def _job_matches_curve(row, curve_id, point_ids):
    metadata = _loads(row["metadata_json"], {})
    direct = metadata.get("curve_id")
    try:
        if direct is not None and int(direct) == int(curve_id):
            return True
    except (TypeError, ValueError):
        pass

    curve_ids = metadata.get("curve_ids")
    if isinstance(curve_ids, (list, tuple)):
        for value in curve_ids:
            try:
                if int(value) == int(curve_id):
                    return True
            except (TypeError, ValueError):
                continue

    job_points = metadata.get("point_ids")
    if isinstance(job_points, (list, tuple)) and point_ids:
        for value in job_points:
            try:
                if int(value) in point_ids:
                    return True
            except (TypeError, ValueError):
                continue

    command = _loads(row["command_json"], [])
    if isinstance(command, list):
        for flag in ("--curve-id", "--curve_id"):
            try:
                index = [str(x) for x in command].index(flag)
            except ValueError:
                continue
            if index + 1 < len(command):
                try:
                    if int(command[index + 1]) == int(curve_id):
                        return True
                except (TypeError, ValueError):
                    pass
    return False


def research_timeline(db, curve_id, *, limit=500):
    """Merge durable curve-related artifacts into a deterministic newest-first view."""
    curve_id = int(curve_id)
    limit = max(1, int(limit))
    items = []

    point_ids = {
        int(row["id"])
        for row in db.execute(
            "SELECT id FROM points WHERE curve_id=?",
            (curve_id,),
        ).fetchall()
    }
    point_rows = db.execute(
        """SELECT * FROM points
           WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
        (curve_id, max(limit * 2, 1000)),
    ).fetchall()

    # Point Ledger creation/current state plus durable hard-case bookmarks.
    for row in point_rows:
        _append(
            items,
            timestamp=row["created_at"],
            source="Point Ledger",
            kind="point_discovered",
            artifact_type="point",
            artifact_id=int(row["id"]),
            status=_point_status(row),
            summary=(
                f"Point #{int(row['id'])} stored from {row['source']} "
                f"({ 'exact' if int(row['exact_verified'] or 0) else 'unverified' })"
            ),
            detail={
                "role": row["role"],
                "search_ref": row["search_ref"],
                "x": row["x"],
                "y": row["y"],
                "exact_verified": bool(int(row["exact_verified"] or 0)),
            },
            route="Independence",
        )
        if row["updated_at"] and str(row["updated_at"]) != str(row["created_at"]):
            _append(
                items,
                timestamp=row["updated_at"],
                source="Point Ledger",
                kind="point_record_updated",
                artifact_type="point",
                artifact_id=int(row["id"]),
                status=_point_status(row),
                summary=f"Point #{int(row['id'])} record updated · current state: {_point_status(row)}",
                detail={
                    "role": row["role"],
                    "exact_verified": bool(int(row["exact_verified"] or 0)),
                    "rigorous_independent": bool(int(row["rigorous_independent"] or 0)),
                    "search_ref": row["search_ref"],
                },
                route="Independence",
            )
        if int(row["hard_flag"] or 0) and row["hard_flagged_at"]:
            _append(
                items,
                timestamp=row["hard_flagged_at"],
                source="Independence",
                kind="hard_case_bookmark",
                artifact_type="point",
                artifact_id=int(row["id"]),
                status="bookmarked",
                summary=f"Point #{int(row['id'])} bookmarked as a hard independence case",
                detail={"reason": row["hard_reason"]},
                route="Independence",
            )

    if _table_exists(db, "point_discoveries"):
        for row in db.execute(
            """SELECT * FROM point_discoveries
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            band = None
            if row["effective_denominator_low"] is not None or row["effective_denominator_high"] is not None:
                band = (
                    f"{row['effective_denominator_low'] if row['effective_denominator_low'] is not None else '—'}"
                    f"–{row['effective_denominator_high'] if row['effective_denominator_high'] is not None else '—'}"
                )
            _append(
                items,
                timestamp=row["created_at"],
                source="Point Search",
                kind="point_search_observation",
                artifact_type="point_discovery",
                artifact_id=int(row["id"]),
                status=row["outcome"],
                summary=(
                    f"{row['source']} · {row['outcome']}"
                    + (f" · point #{int(row['point_id'])}" if row["point_id"] is not None else "")
                ),
                detail={
                    "point_id": row["point_id"],
                    "tier": row["tier"],
                    "search_ref": row["search_ref"],
                    "pipeline_run_id": row["pipeline_run_id"],
                    "pipeline_stage_id": row["pipeline_stage_id"],
                    "height": row["height"],
                    "denominator_band": band,
                    "exact_verified": bool(int(row["exact_verified"] or 0)),
                    "error_class": row["error_class"],
                    "error": row["error"],
                },
                route="Independence" if row["point_id"] is not None else "Target",
            )

    if _table_exists(db, "rank_evidence"):
        for row in db.execute(
            """SELECT * FROM rank_evidence
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            options = _loads(row["options_json"], {})
            _append(
                items,
                timestamp=_timestamp(row["finished_at"], row["updated_at"], row["created_at"], row["started_at"]),
                source="Rank Evidence",
                kind=str(row["evidence_type"] or "rank_evidence"),
                artifact_type="rank_evidence",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=f"{row['engine']} · {_bounds_text(row)}",
                detail={
                    "engine": row["engine"],
                    "evidence_type": row["evidence_type"],
                    "rigorous": bool(int(row["rigorous"] or 0)),
                    "timed_out": bool(int(row["timed_out"] or 0)),
                    "partial": bool(int(row["partial"] or 0)),
                    "elapsed_seconds": row["elapsed_seconds"],
                    "options": options,
                },
                route="Rank Proof",
            )

    if _table_exists(db, "mw_lattices"):
        for row in db.execute(
            """SELECT * FROM mw_lattices
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="MW Geometry",
                kind="lattice",
                artifact_type="mw_lattice",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=(
                    f"{row['source']} lattice · basis {int(row['basis_count'])} · "
                    f"{int(row['precision_bits'])}-bit heights"
                ),
                detail={
                    "basis_count": int(row["basis_count"]),
                    "precision_bits": int(row["precision_bits"]),
                    "determinant": row["determinant"],
                    "min_eigenvalue": row["min_eigenvalue"],
                    "positive_definite_screen": bool(int(row["positive_definite_screen"] or 0)),
                },
                route="MW Geometry",
            )

    if _table_exists(db, "quartic_searches"):
        for row in db.execute(
            """SELECT * FROM quartic_searches
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            metadata = _loads(row["metadata_json"], {})
            _append(
                items,
                timestamp=_timestamp(row["finished_at"], row["updated_at"], row["started_at"], row["created_at"]),
                source="Quartics",
                kind="quartic_search",
                artifact_type="quartic_search",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=(
                    f"Quartic search H={int(row['height_bound'])} · "
                    f"{int(row['point_count'] or 0)} quartic hit(s)"
                ),
                detail={
                    "hole_label": row["hole_label"],
                    "denominator_low": row["denominator_low"],
                    "denominator_high": row["denominator_high"],
                    "runtime": row["runtime"],
                    "schema": metadata.get("schema"),
                    "anchor_source": metadata.get("anchor_source"),
                    "map_back_failure_count": metadata.get("map_back_failure_count"),
                    "error": row["error"],
                },
                route="Quartics",
            )

    if _table_exists(db, "coverings"):
        for row in db.execute(
            """SELECT * FROM coverings
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Quartics",
                kind="covering",
                artifact_type="covering",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=f"Exact covering #{int(row['id'])} · {row['status']}",
                detail={
                    "lattice_id": row["lattice_id"],
                    "hole_id": row["hole_id"],
                    "quartic_search_id": row["quartic_search_id"],
                    "error": row["error"],
                },
                route="Quartics",
            )

    if _table_exists(db, "covering_search_attempts"):
        for row in db.execute(
            """SELECT * FROM covering_search_attempts
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=row["created_at"],
                source="Quartics",
                kind="covering_search_attempt",
                artifact_type="covering_attempt",
                artifact_id=int(row["id"]),
                status=row["outcome"],
                summary=(
                    f"Covering #{int(row['covering_id'])} H={int(row['height'])} · "
                    f"{int(row['mapped_points'] or 0)} mapped point(s)"
                ),
                detail={
                    "covering_id": int(row["covering_id"]),
                    "pipeline_run_id": row["pipeline_run_id"],
                    "pipeline_stage_index": row["pipeline_stage_index"],
                    "pipeline_stage_id": row["pipeline_stage_id"],
                    "timeout_seconds": int(row["timeout_seconds"]),
                    "ratpoints_hits": int(row["ratpoints_hits"] or 0),
                    "mapped_points": int(row["mapped_points"] or 0),
                    "runtime_seconds": row["runtime_seconds"],
                    "error": row["error"],
                },
                route="Quartics",
            )

    if _table_exists(db, "search_pipeline_candidates") and _table_exists(db, "search_pipeline_runs"):
        for row in db.execute(
            """SELECT c.*, r.pipeline_name, r.status AS run_status
               FROM search_pipeline_candidates c
               JOIN search_pipeline_runs r ON r.id=c.run_id
               WHERE c.curve_id=?
               ORDER BY c.id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Pipeline",
                kind="pipeline_candidate",
                artifact_type="pipeline_candidate",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=(
                    f"{row['pipeline_name']} run #{int(row['run_id'])} · "
                    f"{row['current_stage_id'] or 'candidate'}"
                ),
                detail={
                    "run_id": int(row["run_id"]),
                    "run_status": row["run_status"],
                    "parameter": row["parameter"],
                    "rigorous_lower": int(row["rigorous_lower"] or 0),
                    "rigorous_upper": row["rigorous_upper"],
                    "exact_rank": row["exact_rank"],
                    "error": row["error"],
                },
                route="Pipelines",
            )

    if _table_exists(db, "search_pipeline_point_attempts"):
        for row in db.execute(
            """SELECT * FROM search_pipeline_point_attempts
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Pipeline",
                kind="pipeline_point_attempt",
                artifact_type="pipeline_point_attempt",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=(
                    f"{row['stage_id']} · {row['tier']} · H={int(row['height'])} · "
                    f"{int(row['exact_points'] or 0)} exact point(s)"
                ),
                detail={
                    "run_id": int(row["run_id"]),
                    "candidate_id": int(row["candidate_id"]),
                    "stage_index": int(row["stage_index"]),
                    "center": row["center"],
                    "scale": row["scale"],
                    "denominator_low": row["effective_denominator_low"],
                    "denominator_high": row["effective_denominator_high"],
                    "runtime": row["runtime"],
                    "timeout_seconds": row["timeout_seconds"],
                    "attempt_count": int(row["attempt_count"] or 0),
                    "error": row["error"],
                },
                route="Pipelines",
            )

    if _table_exists(db, "search_pipeline_strategy_steps"):
        for row in db.execute(
            """SELECT * FROM search_pipeline_strategy_steps
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Pipeline",
                kind="pipeline_strategy_step",
                artifact_type="pipeline_strategy_step",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=f"{row['strategy_id']} · {row['nested_stage_id']}",
                detail={
                    "run_id": int(row["run_id"]),
                    "candidate_id": int(row["candidate_id"]),
                    "stage_index": int(row["stage_index"]),
                    "step_index": int(row["step_index"]),
                    "attempt_count": int(row["attempt_count"] or 0),
                    "elapsed_seconds": row["elapsed_seconds"],
                    "wall_budget_seconds": row["wall_budget_seconds"],
                    "error": row["error"],
                },
                route="Pipelines",
            )

    if _table_exists(db, "auto_search_trials") and _table_exists(db, "auto_search_campaigns"):
        for row in db.execute(
            """SELECT t.*, a.family_name, a.plugin_id, a.variant_id,
                      a.search_mode, a.target_rank, a.status AS campaign_status
               FROM auto_search_trials t
               JOIN auto_search_campaigns a ON a.id=t.campaign_id
               WHERE t.curve_id=?
               ORDER BY t.id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Auto Search",
                kind="auto_search_trial",
                artifact_type="auto_search_trial",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=(
                    f"Campaign #{int(row['campaign_id'])} · "
                    f"{row['family_name'] or row['plugin_id']} · rank ≥{int(row['rigorous_lower'] or 0)}"
                ),
                detail={
                    "campaign_id": int(row["campaign_id"]),
                    "campaign_status": row["campaign_status"],
                    "variant_id": row["variant_id"],
                    "search_mode": row["search_mode"],
                    "target_rank": int(row["target_rank"]),
                    "tier": row["tier"],
                    "exact_points": int(row["exact_points"] or 0),
                    "rank_growth": int(row["rank_growth"] or 0),
                    "error": row["error"],
                },
                route="Auto",
            )

    if _table_exists(db, "candidates") and _table_exists(db, "candidate_pools"):
        for row in db.execute(
            """SELECT c.*, p.name AS pool_name, p.plugin_id AS pool_plugin_id
               FROM candidates c
               JOIN candidate_pools p ON p.id=c.pool_id
               WHERE c.curve_id=?
               ORDER BY c.id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Search",
                kind="candidate",
                artifact_type="candidate",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=f"Candidate pool {row['pool_name']} · parameter {row['parameter']}",
                detail={
                    "pool_id": int(row["pool_id"]),
                    "plugin_id": row["pool_plugin_id"],
                    "score": row["score"],
                    "rank_order": row["rank_order"],
                },
                route="Search",
            )

    if _table_exists(db, "general_hunt_trials"):
        for row in db.execute(
            """SELECT * FROM general_hunt_trials
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=_timestamp(row["updated_at"], row["created_at"]),
                source="Search",
                kind="general_hunt_trial",
                artifact_type="general_hunt_trial",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=(
                    f"General hunt · rank ≥{int(row['rigorous_lower'] or 0)} · "
                    f"{int(row['point_candidates'] or 0)} point candidate(s)"
                ),
                detail={
                    "mode": row["mode"],
                    "target_lower": int(row["target_lower"]),
                    "screened_rank": int(row["screened_rank"] or 0),
                    "error": row["error"],
                },
                route="Search",
            )

    if _table_exists(db, "ui_jobs"):
        for row in db.execute(
            "SELECT * FROM ui_jobs ORDER BY id DESC LIMIT ?",
            (max(limit * 4, 200),),
        ).fetchall():
            if not _job_matches_curve(row, curve_id, point_ids):
                continue
            metadata = _loads(row["metadata_json"], {})
            _append(
                items,
                timestamp=_timestamp(row["finished_at"], row["updated_at"], row["started_at"], row["created_at"]),
                source="Jobs",
                kind="ui_job",
                artifact_type="ui_job",
                artifact_id=int(row["id"]),
                status=row["status"],
                summary=str(row["label"]),
                detail={
                    "kind": row["kind"],
                    "exit_code": row["exit_code"],
                    "campaign_id": row["campaign_id"],
                    "metadata": metadata,
                },
                route="Jobs",
            )

    if _table_exists(db, "events"):
        for row in db.execute(
            """SELECT * FROM events
               WHERE curve_id=? ORDER BY id DESC LIMIT ?""",
            (curve_id, limit),
        ).fetchall():
            _append(
                items,
                timestamp=row["created_at"],
                source="Event Log",
                kind="curve_event",
                artifact_type="event",
                artifact_id=int(row["id"]),
                status=row["level"],
                summary=row["message"],
                detail={},
                route=None,
            )

    items.sort(
        key=lambda rec: (
            str(rec["timestamp"]),
            str(rec["source"]),
            str(rec["artifact_type"]),
            str(rec["artifact_id"]),
        ),
        reverse=True,
    )
    return items[:limit]
