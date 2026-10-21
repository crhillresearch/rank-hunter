"""Universal pipeline stages for lattice-derived charts.

The core owns artifact selection, lineage checks, hook isolation, exact point
replay, and rank certification. Domain adapters own the mathematics that turns
lattice data into charts and searches those charts.
"""
from __future__ import annotations

import json

from rank42.lattice_store import get_lattice, list_holes, list_lattices
from rank42.plugin_hook_runner import run_isolated_plugin_hook
from rank42.plugins import load_adapter
from rank42.research_modules import _persist_hook_points, _rigorous_lower


CHART_SCHEMA = "rankhunter.lattice-chart/v1"


def _adapter(context, hook):
    plugin = context.get("plugin")
    if plugin is None:
        return None, {
            "status": "unsupported",
            "reason": f"no domain adapter available for {hook}",
        }
    try:
        adapter = load_adapter(plugin)
    except Exception as exc:
        return None, {"status": "error", "reason": "adapter_load_failed", "error": str(exc)}
    if adapter.optional_hook(hook) is None:
        return None, {
            "status": "unsupported",
            "reason": f"adapter does not implement {hook}",
            "plugin_id": str(getattr(plugin, "id", "") or ""),
        }
    return plugin, None


def _selected_lattice(db, *, curve_id, context, config):
    raw_id = config.get("lattice_id")
    if raw_id not in (None, "", 0, "0"):
        lattice = get_lattice(db, int(raw_id))
    else:
        rows = list_lattices(db, curve_id=int(curve_id), limit=250)
        lattice = rows[0] if rows else None
    if lattice is None:
        return None, "no_stored_lattice"
    if int(lattice["curve_id"]) != int(curve_id):
        return None, "lattice_curve_mismatch"
    required = _rigorous_lower(db, int(curve_id))
    try:
        metadata = json.loads(lattice["metadata_json"] or "{}")
    except (TypeError, ValueError):
        metadata = {}
    ledger = metadata.get("point_ledger")
    snapshot = metadata.get(
        "rigorous_lower_at_build",
        ledger.get("rigorous_basis_required") if isinstance(ledger, dict) else None,
    )
    try:
        snapshot = int(snapshot) if snapshot is not None else None
    except (TypeError, ValueError, OverflowError):
        snapshot = None
    if int(lattice["basis_count"] or 0) < required or (
        snapshot is not None and snapshot < required
    ):
        return None, "stale_lattice_basis_after_rank_growth"
    if not bool(lattice["positive_definite_screen"]):
        return None, "lattice_not_positive_definite"
    return lattice, None


def lattice_chart_construction(db, *, context, config, run_id, stage_index):
    """Ask a domain adapter to construct charts from selected lattice holes."""
    hook = "derive_pipeline_lattice_charts"
    plugin, error = _adapter(context, hook)
    if error:
        return error
    lattice, reason = _selected_lattice(
        db, curve_id=context["curve_id"], context=context, config=config
    )
    if lattice is None:
        return {"status": "skipped", "reason": reason, "charts": []}

    holes = list_holes(
        db,
        int(lattice["id"]),
        limit=max(1, int(config.get("max_holes") or 64)),
        status=config.get("hole_status") or None,
    )
    if not holes:
        return {
            "status": "skipped",
            "reason": "no_lattice_holes",
            "lattice_id": int(lattice["id"]),
            "charts": [],
        }
    timeout = max(1, int(config.get("timeout") or 120))
    payload = {
        "schema": "rankhunter.lattice-chart-request/v1",
        "stage_id": "lattice_chart_construction",
        "curve_id": int(context["curve_id"]),
        "a_invariants": [str(x) for x in context["E"].a_invariants()],
        "parameter": str(context.get("parameter")),
        "lattice": {
            "id": int(lattice["id"]),
            "basis_count": int(lattice["basis_count"] or 0),
            "gram": json.loads(lattice["gram_json"]),
            "basis": json.loads(lattice["basis_json"]),
            "metadata": json.loads(lattice["metadata_json"] or "{}"),
        },
        "holes": [
            {
                "id": int(row["id"]),
                "lattice_id": int(row["lattice_id"]),
                "coset_key": str(row["hole_key"]),
                "bits": str(row["bits"]),
                "representative": json.loads(row["representative_json"]),
                "norm2": row["norm2"],
                "method": str(row["method"]),
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            for row in holes
        ],
        "config": dict(config or {}),
    }
    result = dict(run_isolated_plugin_hook(plugin, hook, payload, timeout=timeout))
    if result.get("status") != "completed":
        return {**result, "lattice_id": int(lattice["id"]), "charts": []}

    charts = result.get("charts")
    if not isinstance(charts, list):
        return {"status": "error", "reason": "adapter_chart_list_missing", "lattice_id": int(lattice["id"]), "charts": []}
    normalized = []
    seen = set()
    hole_ids = {int(row["id"]) for row in holes}
    for index, raw in enumerate(charts, 1):
        if not isinstance(raw, dict) or raw.get("schema") != CHART_SCHEMA:
            return {"status": "error", "reason": "invalid_chart_schema", "chart_index": index, "lattice_id": int(lattice["id"]), "charts": []}
        chart_id = str(raw.get("chart_id") or "").strip()
        try:
            hole_id = int(raw.get("source_hole_id"))
        except (TypeError, ValueError):
            hole_id = -1
        if not chart_id or chart_id in seen or hole_id not in hole_ids:
            return {"status": "error", "reason": "invalid_chart_lineage", "chart_index": index, "lattice_id": int(lattice["id"]), "charts": []}
        if raw.get("source_lattice_id") not in (None, int(lattice["id"])):
            return {"status": "error", "reason": "invalid_chart_lineage", "chart_index": index, "lattice_id": int(lattice["id"]), "charts": []}
        if not isinstance(raw.get("chart"), dict):
            return {"status": "error", "reason": "chart_payload_missing", "chart_index": index, "lattice_id": int(lattice["id"]), "charts": []}
        seen.add(chart_id)
        normalized.append({
            "schema": CHART_SCHEMA,
            "chart_id": chart_id,
            "source_lattice_id": int(lattice["id"]),
            "source_hole_id": hole_id,
            "chart": raw["chart"],
            "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
        })
    return {
        "status": "completed" if normalized else "inconclusive",
        "reason": None if normalized else "adapter_returned_no_charts",
        "lattice_id": int(lattice["id"]),
        "charts": normalized,
        "charts_created": len(normalized),
        "runtime_seconds": result.get("runtime_seconds"),
        "domain_math_owned_by_adapter": True,
    }


def lattice_chart_search(db, *, context, config, run_id, stage_index, prior_charts):
    """Search chart artifacts and send returned points through exact core checks."""
    hook = "search_pipeline_lattice_charts"
    plugin, error = _adapter(context, hook)
    if error:
        return error
    charts = prior_charts.get("charts") if isinstance(prior_charts, dict) else None
    if not isinstance(charts, list) or not charts:
        return {"status": "skipped", "reason": "no_prior_lattice_charts", "charts_searched": 0}
    lattice_ids = {int(c.get("source_lattice_id", -1)) for c in charts if isinstance(c, dict)}
    if len(lattice_ids) != 1:
        return {"status": "error", "reason": "chart_lattice_lineage_mismatch", "charts_searched": 0}
    lattice_id = next(iter(lattice_ids))
    lattice, reason = _selected_lattice(
        db,
        curve_id=context["curve_id"],
        context=context,
        config={"lattice_id": lattice_id},
    )
    if lattice is None:
        return {"status": "skipped", "reason": reason or "stale_or_foreign_lattice", "lattice_id": lattice_id, "charts_searched": 0}
    timeout = max(1, int(config.get("timeout") or 300))
    payload = {
        "schema": "rankhunter.lattice-chart-search-request/v1",
        "stage_id": "lattice_chart_search",
        "curve_id": int(context["curve_id"]),
        "a_invariants": [str(x) for x in context["E"].a_invariants()],
        "parameter": str(context.get("parameter")),
        "charts": charts,
        "config": dict(config or {}),
    }
    result = dict(run_isolated_plugin_hook(plugin, hook, payload, timeout=timeout))
    if result.get("status") not in {"completed", "partial"}:
        return {**result, "lattice_id": lattice_id, "charts_searched": 0, "rank_growth": 0}
    points = result.get("points")
    if not isinstance(points, list):
        return {"status": "error", "reason": "adapter_point_list_missing", "lattice_id": lattice_id, "charts_searched": len(charts), "rank_growth": 0}
    accepted = _persist_hook_points(
        db,
        curve_id=int(context["curve_id"]),
        E=context["E"],
        points=points,
        source="pipeline_lattice_chart_search",
        search_ref=f"pipeline:{int(run_id)}:lattice-charts:{int(stage_index)}",
        metadata={"lattice_id": lattice_id, "chart_ids": [c["chart_id"] for c in charts]},
        certificate_timeout=max(1, int(config.get("certificate_timeout") or 120)),
        exact_candidates=max(1, int(config.get("exact_candidates") or 96)),
    )
    return {
        "status": result.get("status"),
        "lattice_id": lattice_id,
        "charts_searched": len(charts),
        "search_coverage": result.get("search_coverage"),
        "coverage_complete": result.get("coverage_complete"),
        "adapter_summary": result.get("summary"),
        "adapter_points_returned": len(points),
        **accepted,
        "runtime_seconds": result.get("runtime_seconds"),
        "rank_growth": int(accepted.get("rank_growth") or 0),
        "heuristic_search_does_not_promote_without_exact_certificate": True,
    }
