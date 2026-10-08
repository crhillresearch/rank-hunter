"""Explicit researcher-Library projection handoff to retained Curve models.

This adapter reuses Rank Hunter's existing manual external curve importer. It
may create or match durable Curve/model rows, but it never promotes Library
rank claims into rigorous rank evidence and never imports mapped points.
"""
from __future__ import annotations

import argparse
import json

from rank42.curve_import import import_curve_records
from rank42.db import connect
from rank42.research_library import (
    load_research_library,
    research_library_mapping_draft,
)
from rank42.research_library_projection import (
    count_research_projection_records,
    research_projection_status,
    search_research_projection,
)


MARKER = "RANK42_LIBRARY_CURVE_IMPORT_RESULT="
DEFAULT_MAX_CURVE_ROWS = 5000
_REQUIRED_MODEL_ROLES = ("a1", "a2", "a3", "a4", "a6")


def _claim_text(row):
    labels = (
        ("exact_rank_claim", "exact"),
        ("rank_claim", "rank"),
        ("rank_lower_claim", "lower"),
        ("rank_upper_claim", "upper"),
    )
    parts = []
    for key, label in labels:
        value = row.get(key)
        if value not in (None, ""):
            parts.append(f"{label}={value}")
    return "; ".join(parts) or None


def _source_provenance(
    manifest,
    projection_status,
    *,
    library_id,
    artifact_id,
    query,
    row,
):
    return {
        "library_id": str(library_id),
        "library_name": str(manifest.get("name") or ""),
        "artifact_id": str(artifact_id),
        "artifact_sha256": str(projection_status.get("artifact_sha256") or ""),
        "mapping_sha256": str(projection_status.get("mapping_sha256") or ""),
        "projection_built_at": str(projection_status.get("built_at") or ""),
        "query": str(query or ""),
        "record_index": int(row["record_index"]),
        "external_family_label": row.get("family_label"),
        "external_curve_label": row.get("curve_label"),
        "source": row.get("source"),
        "notes": row.get("notes"),
        "rank_claims": {
            key: row.get(key)
            for key in (
                "rank_claim",
                "rank_lower_claim",
                "rank_upper_claim",
                "exact_rank_claim",
            )
            if row.get(key) not in (None, "")
        },
    }


def import_projection_curves(
    db,
    project_root,
    *,
    library_id,
    artifact_id,
    query=None,
    max_rows=DEFAULT_MAX_CURVE_ROWS,
):
    """Import selected projected models via the existing external Curve importer."""

    manifest = load_research_library(project_root, library_id)
    draft = research_library_mapping_draft(project_root, library_id, artifact_id)
    if not draft:
        raise ValueError("artifact has no saved field-mapping draft")

    mapped_roles = set(dict(draft.get("roles") or {}).values())
    missing_roles = [
        role for role in _REQUIRED_MODEL_ROLES
        if role not in mapped_roles
    ]
    if missing_roles:
        raise ValueError(
            "curve-model handoff requires mapped "
            + ", ".join(_REQUIRED_MODEL_ROLES)
            + "; missing "
            + ", ".join(missing_roles)
        )

    status = research_projection_status(project_root, library_id, artifact_id)
    if not status.get("exists"):
        raise ValueError("external projection has not been built")
    if status.get("stale"):
        raise ValueError("external projection is stale; rebuild it before Curve import")

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
            f"Curve import selection has {match_count:,} rows; "
            f"limit is {int(max_rows):,}"
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
            f"projection selection changed during Curve import "
            f"({len(rows)} of {match_count})"
        )

    records = []
    for row in rows:
        provenance = _source_provenance(
            manifest,
            status,
            library_id=library_id,
            artifact_id=artifact_id,
            query=query,
            row=row,
        )
        record_index = int(row["record_index"])
        records.append(
            {
                "a_invariants": [row.get(role) for role in _REQUIRED_MODEL_ROLES],
                "family": row.get("family_label"),
                "parameter": row.get("parameter"),
                "source": (
                    f"research_library:{library_id}:"
                    f"artifact:{artifact_id}:record:{record_index}"
                ),
                "claimed_rank": _claim_text(row),
                "notes": json.dumps(
                    {"research_library_curve_import": provenance},
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                # Point coordinates, if mapped, deliberately do not cross this
                # boundary. LIBRARIES-2F owns exact Point Ledger validation.
                "points": [],
            }
        )

    results = import_curve_records(db, records)
    curve_ids = [int(result["curve_id"]) for result in results]
    created = sum(1 for result in results if bool(result["created"]))
    return {
        "library_id": str(library_id),
        "artifact_id": str(artifact_id),
        "processed": len(results),
        "created": int(created),
        "matched_existing": int(len(results) - created),
        "curve_ids": curve_ids,
        "query": str(query or ""),
        "artifact_sha256": str(status.get("artifact_sha256") or ""),
        "mapping_sha256": str(status.get("mapping_sha256") or ""),
    }


def parse_args():
    ap = argparse.ArgumentParser(
        description="Import mapped researcher-Library curve models"
    )
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--library-id", required=True)
    ap.add_argument("--artifact-id", required=True)
    ap.add_argument("--query", default="")
    ap.add_argument("--max-rows", type=int, default=DEFAULT_MAX_CURVE_ROWS)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        result = import_projection_curves(
            db,
            args.project_root,
            library_id=args.library_id,
            artifact_id=args.artifact_id,
            query=args.query or None,
            max_rows=max(1, int(args.max_rows)),
        )
    finally:
        db.close()

    print(MARKER + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
