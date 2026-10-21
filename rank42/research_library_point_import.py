"""Exact researcher-Library point validation against an existing Rank Hunter curve.

The only scientific-state write in this worker is an exact-verified candidate
point after the supplied coordinates reconstruct successfully on the stored
curve over QQ.  Invalid Library coordinates are retained only as append-only
point-discovery rejections.
"""
from __future__ import annotations

import argparse
import json

from rank42.db import connect, get_curve
from rank42.points import record_point_discovery, upsert_point
from rank42.research_library import (
    load_research_library,
    research_library_mapping_draft,
)
from rank42.research_library_projection import (
    count_research_projection_records,
    research_projection_status,
    search_research_projection,
)


MARKER = "RANK42_LIBRARY_POINT_IMPORT_RESULT="
DEFAULT_MAX_POINT_ROWS = 5000


def _point_import_provenance(
    manifest,
    projection_status,
    *,
    library_id,
    artifact_id,
    query,
    row,
):
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
    return {
        "library_id": str(library_id),
        "library_name": str(manifest.get("name") or ""),
        "artifact_id": str(artifact_id),
        "artifact_sha256": str(projection_status.get("artifact_sha256") or ""),
        "mapping_sha256": str(projection_status.get("mapping_sha256") or ""),
        "projection_built_at": str(projection_status.get("built_at") or ""),
        "query": str(query or ""),
        "record_index": int(row["record_index"]),
        "projected_x": row.get("x"),
        "projected_y": row.get("y"),
        "claims": claims,
        "source": row.get("source"),
        "notes": row.get("notes"),
        "external_family_label": row.get("family_label"),
        "external_curve_label": row.get("curve_label"),
    }


def validate_projection_point_rows(
    db,
    *,
    curve_id,
    rows,
    manifest,
    projection_status,
    library_id,
    artifact_id,
    query,
    point_factory,
):
    """Validate mapped x/y exactly and persist only successful curve points."""

    curve_id = int(curve_id)
    valid = 0
    rejected = 0
    point_ids = []
    discovery_ids = []

    for row in rows:
        record_index = int(row["record_index"])
        x = row.get("x")
        y = row.get("y")
        provenance = _point_import_provenance(
            manifest,
            projection_status,
            library_id=library_id,
            artifact_id=artifact_id,
            query=query,
            row=row,
        )
        search_ref = (
            f"library:{library_id}:artifact:{artifact_id}:record:{record_index}"
        )

        if x in (None, "") or y in (None, ""):
            discovery = record_point_discovery(
                db,
                curve_id=curve_id,
                source="research_library_projection",
                outcome="rejected_exact_point",
                search_ref=search_ref,
                exact_verified=False,
                error_class="missing_coordinates",
                error="mapped x/y coordinates are required",
                metadata={"library_point_import": provenance},
            )
            discovery_ids.append(int(discovery["id"]))
            rejected += 1
            continue

        try:
            point = point_factory(str(x), str(y))
            if bool(point.is_zero()):
                raise ValueError("identity point is not a finite x/y candidate")
            exact_x = str(point[0])
            exact_y = str(point[1])
        except Exception as exc:
            discovery = record_point_discovery(
                db,
                curve_id=curve_id,
                source="research_library_projection",
                outcome="rejected_exact_point",
                search_ref=search_ref,
                exact_verified=False,
                error_class=type(exc).__name__,
                error=str(exc),
                metadata={"library_point_import": provenance},
            )
            discovery_ids.append(int(discovery["id"]))
            rejected += 1
            continue

        point_row = upsert_point(
            db,
            curve_id=curve_id,
            x=exact_x,
            y=exact_y,
            source="research_library_projection",
            role="candidate",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref=search_ref,
            plugin_id=(
                str(manifest.get("plugin_id"))
                if manifest.get("plugin_id")
                else None
            ),
            metadata={
                "library_point_import": provenance,
                "validation": "exact_on_stored_curve_over_QQ",
            },
        )
        discovery = record_point_discovery(
            db,
            curve_id=curve_id,
            point_id=int(point_row["id"]),
            source="research_library_projection",
            outcome="exact_point_verified",
            search_ref=search_ref,
            stored_x=exact_x,
            stored_y=exact_y,
            exact_verified=True,
            metadata={"library_point_import": provenance},
        )
        point_ids.append(int(point_row["id"]))
        discovery_ids.append(int(discovery["id"]))
        valid += 1

    return {
        "curve_id": curve_id,
        "processed": len(rows),
        "exact_verified": valid,
        "rejected": rejected,
        "point_ids": sorted(set(point_ids)),
        "discovery_ids": discovery_ids,
    }


def import_projection_points(
    db,
    project_root,
    *,
    library_id,
    artifact_id,
    curve_id,
    query=None,
    max_rows=DEFAULT_MAX_POINT_ROWS,
    point_factory,
):
    """Validate the currently selected projection rows against one existing curve."""

    manifest = load_research_library(project_root, library_id)
    draft = research_library_mapping_draft(project_root, library_id, artifact_id)
    if not draft:
        raise ValueError("artifact has no saved field-mapping draft")
    mapped_roles = set(dict(draft.get("roles") or {}).values())
    if not {"x", "y"}.issubset(mapped_roles):
        raise ValueError("point handoff requires mapped x and y fields")

    status = research_projection_status(project_root, library_id, artifact_id)
    if not status.get("exists"):
        raise ValueError("external projection has not been built")
    if status.get("stale"):
        raise ValueError("external projection is stale; rebuild it before point validation")

    curve = get_curve(db, int(curve_id))
    if curve is None:
        raise ValueError(f"curve #{int(curve_id)} not found")
    if not curve["a_invariants_json"]:
        raise ValueError(f"curve #{int(curve_id)} has no stored model")

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
            f"point validation selection has {match_count:,} rows; "
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
            f"projection selection changed during point validation "
            f"({len(rows)} of {match_count})"
        )

    return validate_projection_point_rows(
        db,
        curve_id=int(curve_id),
        rows=rows,
        manifest=manifest,
        projection_status=status,
        library_id=library_id,
        artifact_id=artifact_id,
        query=query,
        point_factory=point_factory,
    )


def parse_args():
    ap = argparse.ArgumentParser(
        description="Exact-validate researcher Library points on an existing curve"
    )
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--library-id", required=True)
    ap.add_argument("--artifact-id", required=True)
    ap.add_argument("--curve-id", required=True, type=int)
    ap.add_argument("--query", default="")
    ap.add_argument("--max-rows", type=int, default=DEFAULT_MAX_POINT_ROWS)
    return ap.parse_args()


def main():
    args = parse_args()

    # Sage is deliberately imported only in the worker process; Streamlit and
    # lightweight test imports never acquire a Sage dependency.
    from sage.all import EllipticCurve, QQ

    db = connect(args.db)
    try:
        curve = get_curve(db, int(args.curve_id))
        if curve is None or not curve["a_invariants_json"]:
            raise SystemExit(
                f"curve #{int(args.curve_id)} unavailable or missing stored model"
            )
        ainvs = [QQ(str(value)) for value in json.loads(curve["a_invariants_json"])]
        E = EllipticCurve(QQ, ainvs)

        def point_factory(x, y):
            return E(QQ(str(x)), QQ(str(y)))

        result = import_projection_points(
            db,
            args.project_root,
            library_id=args.library_id,
            artifact_id=args.artifact_id,
            curve_id=int(args.curve_id),
            query=args.query or None,
            max_rows=max(1, int(args.max_rows)),
            point_factory=point_factory,
        )
    finally:
        db.close()

    print(MARKER + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
