"""Typed candidate ranking/provenance read projection.

Candidate score metadata is persisted by Candidates and Pipeline owners.  This
module normalizes that persisted metadata for comparative/research consumers
without recomputing any heuristic score.
"""
from __future__ import annotations

import json


_RANKING_LABELS = {
    "nagao": "Nagao",
    "known_rank": "Known rank",
    "imported_priority": "Imported priority",
    "legacy_score": "Legacy score",
}


def _get(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _json_dict(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except Exception:
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def ranking_label(kind):
    kind = str(kind or "legacy_score")
    return _RANKING_LABELS.get(kind, kind.replace("_", " ").title())


def pool_candidate_ranking_state(row):
    """Normalize one persisted candidate-pool row.

    No score is recalculated.  ranking_value falls back to the stored score only
    when older rows predate explicit ranking_value metadata.
    """
    metadata = _json_dict(_get(row, "metadata_json", "{}"))
    provenance = metadata.get("score_provenance")
    provenance = dict(provenance) if isinstance(provenance, dict) else {}
    kind = str(
        metadata.get("ranking_kind")
        or provenance.get("ranking_kind")
        or "legacy_score"
    )
    value = metadata.get("ranking_value")
    if value is None:
        value = _get(row, "score")
    try:
        normalized_value = None if value is None else float(value)
    except (TypeError, ValueError):
        normalized_value = None

    return {
        "source_kind": "candidate_pool",
        "candidate_id": _get(row, "id"),
        "pool_id": _get(row, "pool_id"),
        "parameter": _get(row, "parameter"),
        "rank_order": _get(row, "rank_order"),
        "ranking_kind": kind,
        "ranking_label": ranking_label(kind),
        "ranking_value": normalized_value,
        "stored_score": _get(row, "score"),
        "score_provenance": provenance,
        "plugin_id": _get(row, "plugin_id"),
        "plugin_version": _get(row, "plugin_version"),
        "family_spec": _get(row, "family_spec"),
        "family_sha256": _get(row, "family_sha256"),
        "adapter_sha256": _get(row, "adapter_sha256"),
        "plugin_manifest_sha256": _get(row, "plugin_manifest_sha256"),
        "native_family_key": _get(row, "native_family_key"),
        "native_family_spec": _get(row, "native_family_spec"),
        "native_parameter": _get(row, "native_parameter"),
        "chart_id": _get(row, "chart_id"),
        "chart_parameter": _get(row, "chart_parameter"),
        "chart_map_fingerprint": _get(row, "chart_map_fingerprint"),
    }


def pipeline_candidate_ranking_state(row):
    """Normalize one persisted Pipeline candidate score/provenance row."""
    provenance = _json_dict(_get(row, "score_provenance_json", "{}"))
    kind = str(provenance.get("ranking_kind") or "legacy_score")
    value = _get(row, "score")
    try:
        normalized_value = None if value is None else float(value)
    except (TypeError, ValueError):
        normalized_value = None
    return {
        "source_kind": "pipeline_candidate",
        "candidate_id": _get(row, "id"),
        "pipeline_run_id": _get(row, "run_id"),
        "parameter": _get(row, "parameter"),
        "ranking_kind": kind,
        "ranking_label": ranking_label(kind),
        "ranking_value": normalized_value,
        "stored_score": value,
        "score_provenance": provenance,
    }
