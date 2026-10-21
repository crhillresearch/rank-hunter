"""Core-owned UI handoff contracts for research Workspaces.

Optional Workspaces may choose interesting stored objects, but execution pages
remain the owners of Target Search and Pipelines.  This module keeps those
cross-page session-state details out of plugins.

The handoff payload is intentionally scientific-context only.  Storing a
handoff never creates a job, curve, point, candidate, or rank-evidence row.
"""
from __future__ import annotations

import hashlib
import json

from rank42.ui_active_curve import set_active_curve_id


RESEARCH_HANDOFF_KEY = "_rh_research_handoff"
RESEARCH_HANDOFF_VERSION = 1


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _json_copy(value):
    """Require one JSON-serializable value and detach caller-owned containers."""
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def normalize_research_handoff(payload):
    """Normalize one inspectable, deterministic research-selection handoff."""
    if not isinstance(payload, dict):
        raise ValueError("research handoff must be a mapping")

    source = str(payload.get("source") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    family = str(payload.get("family") or "").strip()
    if not source:
        raise ValueError("research handoff source is required")
    if not kind:
        raise ValueError("research handoff kind is required")

    curve_ids = []
    seen = set()
    for raw in payload.get("curve_ids") or ():
        curve_id = _positive_int(raw)
        if curve_id is None or curve_id in seen:
            continue
        seen.add(curve_id)
        curve_ids.append(curve_id)

    parameters = []
    for rec in payload.get("parameters") or ():
        if isinstance(rec, dict):
            curve_id = _positive_int(rec.get("curve_id"))
            parameter = str(rec.get("parameter") or "").strip()
            if curve_id is None or not parameter:
                continue
            item = {
                "curve_id": curve_id,
                "parameter": parameter,
            }
            for key in (
                "rigorous_lower",
                "generic_lower",
                "family_baseline",
                "family_baseline_kind",
                "baseline_excess",
                "rank_jump",
                "score",
                "root_number",
            ):
                if rec.get(key) is not None:
                    item[key] = rec.get(key)
            parameters.append(item)

    selection = payload.get("selection")
    if selection is None:
        selection = {}
    if not isinstance(selection, dict):
        raise ValueError("research handoff selection must be a mapping")

    normalized = {
        "version": RESEARCH_HANDOFF_VERSION,
        "source": source,
        "kind": kind,
        "family": family,
        "curve_ids": curve_ids,
        "parameters": parameters,
        "selection": _json_copy(selection),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    normalized["selection_hash"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    return normalized


def set_research_handoff(state, payload):
    normalized = normalize_research_handoff(payload)
    state[RESEARCH_HANDOFF_KEY] = normalized
    return normalized


def get_research_handoff(state):
    payload = state.get(RESEARCH_HANDOFF_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        return normalize_research_handoff(payload)
    except ValueError:
        return None


def clear_research_handoff(state):
    """Remove only the optional research-selection context from UI state."""
    return state.pop(RESEARCH_HANDOFF_KEY, None)


def research_handoff_for_curve(state, curve_id):
    curve_id = _positive_int(curve_id)
    if curve_id is None:
        return None
    payload = get_research_handoff(state)
    if payload is None or curve_id not in payload["curve_ids"]:
        return None
    return payload


def route_curve_to_target(state, curve_id, *, handoff=None):
    """Route one stored curve to the ordinary Target page."""
    curve_id = set_active_curve_id(state, curve_id, "target_curve_id")
    if handoff is not None:
        set_research_handoff(state, handoff)
    state["rh_page"] = "Target"
    return curve_id


def route_selection_to_pipelines(state, handoff):
    """Route a research selection to Pipelines as inspectable context only.

    Arbitrary parameter lists are deliberately not stuffed into private Builder
    target controls.  The Pipeline page owns how (or whether) a future core
    population contract consumes this selection.
    """
    payload = set_research_handoff(state, handoff)
    state["builder_main_view"] = "Editor"
    state["builder_view_revision"] = int(
        state.get("builder_view_revision") or 0
    ) + 1
    state["rh_page"] = "Pipelines"
    return payload
