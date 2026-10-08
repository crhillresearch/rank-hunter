"""Shared semantic active-curve context for researcher workflows.

The semantic key is authoritative for current UI context. Legacy page-specific
keys remain accepted only as compatibility inputs when no semantic context is
present, and may still be mirrored by callers that need old session-state
bookmarks/widgets to survive the v0.9.1 migration.
"""
from __future__ import annotations

ACTIVE_CURVE_KEY = "analysis_active_curve_id"

LEGACY_CURVE_KEYS = (
    "analyze_curve_id",
    "analysis_curve_id",
    "independence_curve_id",
    "descent_curve_id",
    "target_curve_id",
    "points_curve_id",
)


def _curve_id(value):
    if value in (None, ""):
        return None
    try:
        curve_id = int(value)
    except (TypeError, ValueError):
        return None
    return curve_id if curve_id > 0 else None


def get_active_curve_id(state, *legacy_keys):
    """Return the semantic active curve, adopting legacy state only if needed."""
    active = _curve_id(state.get(ACTIVE_CURVE_KEY))
    if active is not None:
        return active

    keys = legacy_keys or LEGACY_CURVE_KEYS
    for key in keys:
        curve_id = _curve_id(state.get(str(key)))
        if curve_id is not None:
            state[ACTIVE_CURVE_KEY] = curve_id
            return curve_id
    return None


def set_active_curve_id(state, curve_id, *compatibility_keys):
    """Set semantic active curve and optionally mirror legacy compatibility keys."""
    curve_id = _curve_id(curve_id)
    if curve_id is None:
        raise ValueError("active curve id must be a positive integer")
    state[ACTIVE_CURVE_KEY] = curve_id
    for key in compatibility_keys:
        state[str(key)] = curve_id
    return curve_id
