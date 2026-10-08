"""Explicit promotion boundary from researcher Library projections to Candidate Pools.

This module is intentionally narrow. It may create Candidate Pool orchestration
state, but it does not create Curves, Points, rank evidence, or proof records.
External rank values remain provenance claims in candidate metadata.
"""
from __future__ import annotations

from datetime import datetime

from rank42.candidates import create_pool, replace_pool_rows
from rank42.plugins import get_plugin, get_variant, plugin_fingerprints
from rank42.research_library import load_research_library
from rank42.research_library_projection import (
    count_research_projection_records,
    research_projection_status,
    search_research_projection,
)


DEFAULT_MAX_PROMOTION_ROWS = 100_000


def _candidate_from_projection_row(row, *, provenance, family_name):
    parameter = str(row.get("parameter") or "").strip()
    if not parameter:
        raise ValueError(
            f"projected record {row.get('record_index')} is missing mapped parameter"
        )

    claims = {
        key: row.get(key)
        for key in (
            "rank_claim",
            "rank_lower_claim",
            "rank_upper_claim",
            "exact_rank_claim",
        )
        if row.get(key) not in (None, "")
    }
    metadata = {
        "family_name": str(family_name or ""),
        "library_projection": {
            **dict(provenance),
            "record_index": int(row["record_index"]),
            "claims": claims,
            "source": row.get("source"),
            "notes": row.get("notes"),
            "external_family_label": row.get("family_label"),
            "external_curve_label": row.get("curve_label"),
        },
        "ranking_kind": "library_order",
        "ranking_value": int(row["record_index"]),
    }
    return {
        "parameter": parameter,
        "score": 0.0,
        **metadata,
    }


def create_candidate_pool_from_research_projection(
    db,
    project_root,
    *,
    library_id,
    artifact_id,
    plugin_id,
    variant_id,
    query=None,
    pool_name=None,
    max_rows=DEFAULT_MAX_PROMOTION_ROWS,
):
    """Create one explicit unsearched Candidate Pool from current projection rows."""

    manifest = load_research_library(project_root, library_id)
    associated_plugin = str(manifest.get("plugin_id") or "").strip()
    plugin_id = str(plugin_id or "").strip()
    if associated_plugin and associated_plugin != plugin_id:
        raise ValueError(
            f"Library is associated with plugin {associated_plugin!r}, not {plugin_id!r}"
        )

    plugin = get_plugin(project_root, plugin_id)
    if plugin.plugin_type != "family":
        raise ValueError(f"plugin {plugin.id!r} is not a family plugin")
    variant = get_variant(plugin, variant_id)

    status = research_projection_status(project_root, library_id, artifact_id)
    if not status.get("exists"):
        raise ValueError("external projection has not been built")
    if status.get("stale"):
        raise ValueError("external projection is stale; rebuild it before promotion")

    match_count = count_research_projection_records(
        project_root,
        library_id,
        artifact_id=artifact_id,
        query=query or None,
    )
    if match_count < 1:
        raise ValueError("no projected records match the current selection")
    if match_count > int(max_rows):
        raise ValueError(
            f"promotion selection has {match_count:,} rows; limit is {int(max_rows):,}"
        )

    rows = search_research_projection(
        project_root,
        library_id,
        artifact_id=artifact_id,
        query=query or None,
        limit=match_count,
    )
    if len(rows) != match_count:
        raise ValueError(
            f"projection selection changed during promotion ({len(rows)} of {match_count})"
        )

    provenance = {
        "library_id": str(library_id),
        "library_name": str(manifest.get("name") or ""),
        "artifact_id": str(artifact_id),
        "artifact_sha256": str(status.get("artifact_sha256") or ""),
        "mapping_sha256": str(status.get("mapping_sha256") or ""),
        "projection_built_at": str(status.get("built_at") or ""),
        "query": str(query or ""),
    }
    candidate_rows = [
        _candidate_from_projection_row(
            row,
            provenance=provenance,
            family_name=variant.curve_family_name,
        )
        for row in rows
    ]

    fp = plugin_fingerprints(plugin, variant)
    if not pool_name:
        pool_name = (
            f"library-{library_id}-{variant.id}-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        )
    generation = {
        "source": "research_library_projection",
        "library_id": str(library_id),
        "library_name": str(manifest.get("name") or ""),
        "artifact_id": str(artifact_id),
        "artifact_sha256": provenance["artifact_sha256"],
        "mapping_sha256": provenance["mapping_sha256"],
        "projection_built_at": provenance["projection_built_at"],
        "query": str(query or ""),
        "selection_count": len(candidate_rows),
        "variant_id": str(variant.id),
        "rank_claim_policy": "provenance_only",
    }
    pool = create_pool(
        db,
        name=str(pool_name),
        plugin_id=plugin.id,
        plugin_version=plugin.version,
        family_spec=variant.family_spec,
        generation=generation,
        status="importing",
        **fp,
    )
    replace_pool_rows(db, int(pool["id"]), candidate_rows)
    return db.execute(
        "SELECT * FROM candidate_pools WHERE id=?",
        (int(pool["id"]),),
    ).fetchone()
