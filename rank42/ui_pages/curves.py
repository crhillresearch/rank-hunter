from __future__ import annotations

import html
import json
import math

import streamlit as st

from rank42.analysis_planner import analysis_plan, planner_immediate_actions
from rank42.analyze_workspace import curve_analysis_snapshot
from rank42.curve_arithmetic_backfill import arithmetic_backfill_status
from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_hall_of_fame import hall_of_fame_groups, hall_of_fame_summary
from rank42.curve_import import import_curve, import_curve_records, parse_curve_import_json
from rank42.curve_portrait import curve_portrait_data
from rank42.curve_research_state import curve_rank_summary_map
from rank42.feature_hooks import render_feature_hook
from rank42.manage_store import campaign_curve_ids_readonly, list_campaigns
from rank42.ui_active_curve import set_active_curve_id
from rank42.torsion import MAZUR_TORSION_CHOICES, ensure_torsion_schema
from rank42.ui_components import selectable_dataframe, tabs
from rank42.ui_extensions import enabled_extension_pages
from .common import title, curve_label, section_title, launch, setting


def _safe_json(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _first_value(*values):
    return next((value for value in values if value is not None and str(value).strip() != ""), None)


def _log_abs_integer(value):
    """Natural logarithm of a stored integer invariant for compact table display."""
    try:
        n = abs(int(str(value)))
        return math.log(n) if n > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _real_value(value):
    try:
        return float(str(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _rigorous_lower(row):
    value = row["rigorous_lower"] if "rigorous_lower" in row.keys() else None
    return int(value) if value is not None else None


def _evidence_state(row):
    if "rank_inconsistent" in row.keys() and bool(row["rank_inconsistent"]):
        return "EVIDENCE CONFLICT"
    exact = row["exact_rank"]
    if exact is not None:
        return f"EXACT {int(exact)}"
    lower = _rigorous_lower(row)
    return f"PROVEN ≥ {lower}" if lower is not None else "UNRESOLVED"


def _rigorous_upper(row):
    value = row["rigorous_upper"] if "rigorous_upper" in row.keys() else row["descent_upper"]
    return int(value) if value is not None else None


def _rank_conclusion(row):
    if "rank_inconsistent" in row.keys() and bool(row["rank_inconsistent"]):
        return "conflict", "conflicting rigorous rank evidence"
    exact = row["exact_rank"]
    if exact is not None:
        return "exact", f"rank = {int(exact)} exactly"
    lower = _rigorous_lower(row)
    upper = _rigorous_upper(row)
    if lower is not None and upper is not None:
        return "bounded", f"{lower} ≤ rank ≤ {upper}"
    if lower is not None:
        return "lower", f"rank ≥ {lower}"
    return "unknown", "rigorous rank unresolved"


def _arithmetic_source_label(row, field):
    provenance = row.get("arithmetic_provenance") or {}
    info = provenance.get(field) or {}
    kind = str(info.get("kind") or "missing")
    source = str(info.get("source") or "").strip()
    if kind == "external_reference":
        label = f"{source.upper()} reference" if source else "external reference"
        detail = str(info.get("detail") or "").strip()
        if detail:
            label += f" · {detail}"
    elif kind == "external_reference_cached_local":
        label = f"cached {source.upper()} reference" if source else "cached external reference"
    elif kind == "local_computed":
        label = str(info.get("method") or "local computed")
    elif kind == "local_persisted":
        label = "local"
    elif kind == "derived":
        label = "derived"
    else:
        label = "—"
    if field in set(row.get("arithmetic_conflicts") or []):
        label += " · conflict"
    return label


def _arithmetic_table_source_label(row, field):
    """Compact provenance label for dense Curves inventory tables.

    Full method/provenance text remains available in Curve detail.  The
    inventory only needs to distinguish local/computed/reference authority
    without letting implementation strings dominate the research columns.
    """
    provenance = row.get("arithmetic_provenance") or {}
    info = provenance.get(field) or {}
    kind = str(info.get("kind") or "missing")
    source = str(info.get("source") or "").strip()
    method = str(info.get("method") or "").strip()

    if kind == "external_reference":
        label = f"{source.upper()} ref" if source else "External ref"
    elif kind == "external_reference_cached_local":
        label = f"{source.upper()} cache" if source else "External cache"
    elif kind == "local_computed":
        if "bounded_global_minimal_model" in method:
            label = "Bounded Sage"
        elif method.startswith("sage_global_minimal_model"):
            label = "Sage"
        elif method in {"stored_bad_primes", "factor_minimal_discriminant"}:
            label = "Local"
        else:
            label = "Computed"
    elif kind == "local_persisted":
        label = "Local"
    elif kind == "derived":
        label = "Derived"
    else:
        label = "—"

    if field in set(row.get("arithmetic_conflicts") or []):
        label += " · conflict"
    return label


def _torsion_display(row):
    torsion = row.get("arithmetic_torsion")
    if isinstance(torsion, dict) and torsion.get("label") is not None:
        return str(torsion["label"])
    if row.get("torsion_label") is not None:
        return str(row["torsion_label"])
    return "Unknown" if row.get("torsion_computed_at") else "—"


def _catalog_summary(row, prefix):
    status = row[f"{prefix}_status"]
    label = row[f"{prefix}_id"]
    rank = row[f"{prefix}_rank"]
    if status == "known":
        bits = [str(label or "known")]
        if rank is not None:
            bits.append(f"rank ≥ {int(rank)}")
        return " · ".join(bits)
    if prefix == "icarm" and status == "not_found_synced":
        return "no synchronized match"
    if prefix == "lmfdb" and status == "not_found_complete_range":
        return "not found · complete range"
    if status:
        return str(status).replace("_", " ")
    return "unchecked"


def _enabled_curve_explorer_route(db, ctx):
    for route, plugin, page in enabled_extension_pages(db, ctx.project_root):
        if plugin.id == "curve_explorer":
            return route
    return None


def _point_summary(db, curve_id):
    row = db.execute(
        """
        SELECT COUNT(*) total,
               SUM(exact_verified=1) exact_verified,
               SUM(rigorous_independent=1) rigorous_independent,
               SUM(independence_status='dependent') dependent,
               SUM(independence_status='numerical_novel') numerical_novel,
               SUM(independence_status='unknown') unknown
        FROM points
        WHERE curve_id=?
        """,
        (int(curve_id),),
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def _point_counts_for_curve_ids(db, curve_ids):
    ids = sorted({int(value) for value in curve_ids})
    counts = {
        curve_id: {"point_count": 0, "rigorous_point_count": 0}
        for curve_id in ids
    }
    for start in range(0, len(ids), 800):
        chunk = ids[start:start + 800]
        if not chunk:
            continue
        marks = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"""SELECT curve_id,
                       COUNT(*) AS point_count,
                       SUM(rigorous_independent=1) AS rigorous_point_count
                FROM points
                WHERE curve_id IN ({marks})
                GROUP BY curve_id""",
            chunk,
        ).fetchall()
        for row in rows:
            counts[int(row["curve_id"])] = {
                "point_count": int(row["point_count"] or 0),
                "rigorous_point_count": int(row["rigorous_point_count"] or 0),
            }
    return counts


def _max_rigorous_lower(db):
    """Return the strongest stored rigorous lower bound across Curves."""

    row = db.execute(
        """
        WITH evidence AS (
            SELECT curve_id,
                   MAX(
                       CASE
                           WHEN rigorous=1 THEN rigorous_lower
                           ELSE NULL
                       END
                   ) AS evidence_lower
            FROM rank_evidence
            GROUP BY curve_id
        )
        SELECT MAX(
                   MAX(
                       COALESCE(c.exact_rank, 0),
                       COALESCE(c.descent_lower, 0),
                       COALESCE(c.generic_lower, 0),
                       COALESCE(e.evidence_lower, 0)
                   )
               ) AS max_lower
        FROM curves c
        LEFT JOIN evidence e ON e.curve_id=c.id
        """
    ).fetchone()
    return max(1, int(row["max_lower"] or 0))


def _curve_inventory_rows(
    db,
    *,
    wanted=None,
    curve_id=None,
    minrank=0,
    maxrank=None,
    family="All",
    campaign_id=None,
    evidence="Any",
    torsion="Any",
    status="Any",
    query="",
    include_arithmetic=True,
    include_point_counts=True,
    lightweight_metadata=False,
):
    """Load Curves inventory rows with authoritative reduced rank state.

    Catalog/point metadata remains a presentation query. Rank inclusion,
    evidence classification, and ordering come from Curve Research State.
    """
    query_text = str(query).strip()
    select_columns = (
        "curves.id,curves.family,curves.parameter,curves.score,curves.status,"
        "curves.torsion_label,curves.torsion_computed_at"
        if lightweight_metadata
        else """curves.*,
               icarm.source_label AS icarm_id,
               icarm.status AS icarm_status,
               icarm.source_rank AS icarm_rank,
               icarm.source_url AS icarm_url,
               icarm.checked_at AS icarm_checked_at,
               lmfdb.source_label AS lmfdb_id,
               lmfdb.status AS lmfdb_status,
               lmfdb.source_rank AS lmfdb_rank,
               lmfdb.source_url AS lmfdb_url,
               lmfdb.checked_at AS lmfdb_checked_at"""
    )
    needs_catalog_join = (not lightweight_metadata) or bool(query_text)
    sql = f"SELECT {select_columns} FROM curves"
    if needs_catalog_join:
        sql += """
        LEFT JOIN curve_catalog_checks icarm
          ON icarm.curve_id=curves.id AND icarm.source='icarm'
        LEFT JOIN curve_catalog_checks lmfdb
          ON lmfdb.curve_id=curves.id AND lmfdb.source='lmfdb'
        """
    sql += " WHERE 1=1"
    vals = []
    if curve_id is not None:
        sql += " AND curves.id=?"
        vals.append(int(curve_id))
    if family != "All":
        sql += " AND curves.family=?"
        vals.append(str(family))
    if torsion == "Not stored":
        sql += " AND curves.torsion_label IS NULL"
    elif torsion != "Any":
        sql += " AND curves.torsion_label=?"
        vals.append(str(torsion))
    if status != "Any":
        sql += " AND curves.status=?"
        vals.append(str(status))
    if query_text:
        like = f"%{query_text}%"
        sql += """
            AND (
                curves.family LIKE ?
                OR curves.parameter LIKE ?
                OR CAST(curves.id AS TEXT) LIKE ?
                OR COALESCE(icarm.source_label,'') LIKE ?
                OR COALESCE(lmfdb.source_label,'') LIKE ?
            )
        """
        vals += [like, like, like, like, like]

    metadata_rows = db.execute(sql, vals).fetchall()
    if campaign_id is not None:
        campaign_curve_ids = set(
            campaign_curve_ids_readonly(db, int(campaign_id))
        )
        if not campaign_curve_ids:
            return []
        metadata_rows = [
            row
            for row in metadata_rows
            if int(row["id"]) in campaign_curve_ids
        ]
    if not metadata_rows:
        return []

    ids = [int(row["id"]) for row in metadata_rows]
    states = curve_rank_summary_map(
        db,
        ids,
    )
    arithmetic_states = (
        curve_arithmetic_state_map(db, ids)
        if include_arithmetic
        else {}
    )
    wanted_id = int(wanted) if wanted is not None else None
    threshold = int(minrank or 0)
    ceiling = None if maxrank is None else int(maxrank)
    manual_candidates = [
        cid
        for cid in ids
        if int(states[cid]["rigorous_lower"]) <= 0
        and cid != wanted_id
    ]
    manual_import_ids = set()
    for start in range(0, len(manual_candidates), 800):
        chunk = manual_candidates[start:start + 800]
        if not chunk:
            continue
        marks = ",".join("?" for _ in chunk)
        manual_import_ids.update(
            int(row["curve_id"])
            for row in db.execute(
                f"""SELECT DISTINCT curve_id
                    FROM events
                    WHERE curve_id IN ({marks})
                      AND message LIKE 'Manual external curve import%'""",
                chunk,
            ).fetchall()
        )
    out = []
    for raw in metadata_rows:
        cid = int(raw["id"])
        state = states[cid]
        lower = int(state["rigorous_lower"])
        exact = state["exact_rank"]
        upper = state["rigorous_upper"]

        retained = lower > 0 or cid == wanted_id or cid in manual_import_ids
        if (
            not retained
            or lower < threshold
            or (ceiling is not None and lower > ceiling)
        ):
            continue
        if evidence == "Exact" and exact is None:
            continue
        if evidence == "Rigorous lower only" and not (lower > 0 and exact is None and upper is None):
            continue
        if evidence == "Rigorous interval" and not (
            exact is None and upper is not None and not bool(state["rank_inconsistent"])
        ):
            continue

        row = dict(raw)
        row["manual_import"] = 1 if cid in manual_import_ids else 0
        row["rigorous_lower"] = lower if lower > 0 or exact is not None else None
        row["rigorous_upper"] = upper
        row["exact_rank"] = exact
        row["rank_inconsistent"] = bool(state["rank_inconsistent"])
        if include_arithmetic:
            arithmetic = arithmetic_states[cid]
            row["arithmetic_conductor"] = arithmetic["conductor"]
            row["arithmetic_log_conductor"] = arithmetic["log_conductor"]
            row["arithmetic_discriminant"] = arithmetic["discriminant"]
            row["arithmetic_bad_primes"] = arithmetic["bad_primes"]
            row["arithmetic_root_number"] = arithmetic["root_number"]
            row["arithmetic_torsion"] = arithmetic["torsion"]
            row["arithmetic_naive_height"] = arithmetic["naive_height"]
            row["arithmetic_faltings_height"] = arithmetic["faltings_height"]
            row["arithmetic_local"] = arithmetic["local"]
            row["arithmetic_display"] = arithmetic["display"]
            row["arithmetic_provenance"] = arithmetic["provenance"]
            row["arithmetic_local_provenance"] = arithmetic["local_provenance"]
            row["arithmetic_references"] = arithmetic["references"]
            row["arithmetic_conflicts"] = arithmetic["conflicts"]
            row["arithmetic_issues"] = arithmetic["issues"]
        out.append(row)

    if include_point_counts and out:
        point_counts = _point_counts_for_curve_ids(
            db,
            [row["id"] for row in out],
        )
        for row in out:
            counts = point_counts.get(
                int(row["id"]),
                {"point_count": 0, "rigorous_point_count": 0},
            )
            row.update(counts)
    else:
        for row in out:
            row["point_count"] = 0
            row["rigorous_point_count"] = 0

    out.sort(
        key=lambda row: (
            -int(row["rigorous_lower"] or 0),
            -(float(row["score"]) if row["score"] is not None else float("-inf")),
            -int(row["id"]),
        )
    )
    return out[:5000]


def _curve_detail_row(db, curve_id):
    rows = _curve_inventory_rows(
        db,
        wanted=int(curve_id),
        curve_id=int(curve_id),
        include_arithmetic=True,
        include_point_counts=False,
    )
    return rows[0] if rows else None


def _inventory_table(rows, view):
    table = []
    for r in rows:
        family_t = f"{r['family']} · t={r['parameter']}"
        if view == "Arithmetic":
            table.append(
                {
                    "id": r["id"],
                    "torsion": _torsion_display(r),
                    "family / t": family_t,
                    "log N": r["arithmetic_log_conductor"],
                    "N source": _arithmetic_table_source_label(r, "conductor"),
                    "naive height": _real_value(r["arithmetic_naive_height"]),
                    "naive source": _arithmetic_table_source_label(r, "naive_height"),
                    "Faltings height": _real_value(r["arithmetic_faltings_height"]),
                    "Faltings source": _arithmetic_table_source_label(r, "faltings_height"),
                    "log |Δ|": _log_abs_integer(r["arithmetic_discriminant"]),
                    "Δ source": _arithmetic_table_source_label(r, "discriminant"),
                    "evidence": _evidence_state(r),
                }
            )
        elif view == "Catalog":
            table.append(
                {
                    "id": r["id"],
                    "family / t": family_t,
                    "ICARM": _catalog_summary(r, "icarm"),
                    "LMFDB": _catalog_summary(r, "lmfdb"),
                    "checked": str(r["icarm_checked_at"] or r["lmfdb_checked_at"] or "")[:19] or "—",
                    "status": r["status"],
                    "evidence": _evidence_state(r),
                }
            )
        else:
            table.append(
                {
                    "id": r["id"],
                    "family / t": family_t,
                    "rank ≥": r["rigorous_lower"],
                    "exact": r["exact_rank"],
                    "points": int(r["point_count"] or 0),
                    "rigorous pts": int(r["rigorous_point_count"] or 0),
                    "Nagao": round(float(r["score"]), 3) if r["score"] is not None else None,
                    "rigorous upper": _rigorous_upper(r),
                    "evidence": _evidence_state(r),
                    "status": r["status"],
                }
            )
    return table


def _research_landscape_data(rows, *, family_limit=16):
    """Project already-filtered Inventory rows into chart-only research summaries."""

    rank_buckets = {}
    family_buckets = {}
    scatter = []

    for row in rows:
        lower = int(row.get("rigorous_lower") or 0)
        exact = row.get("exact_rank") is not None
        rank_bucket = rank_buckets.setdefault(
            lower,
            {"count": 0, "exact_count": 0},
        )
        rank_bucket["count"] += 1
        if exact:
            rank_bucket["exact_count"] += 1

        score = row.get("score")
        if score is not None:
            scatter.append(
                {
                    "curve_id": int(row["id"]),
                    "family": str(row["family"]),
                    "parameter": str(row["parameter"]),
                    "rigorous_lower": lower,
                    "Nagao": float(score),
                    "evidence_class": "Exact" if exact else "Lower bound only",
                    "evidence": "Exact" if exact else f"Proven ≥ {lower}",
                }
            )

        family = str(row["family"])
        family_state = family_buckets.setdefault(
            family,
            {
                "curve_count": 0,
                "best_rigorous": 0,
                "rank_counts": {},
            },
        )
        family_state["curve_count"] += 1
        family_state["best_rigorous"] = max(
            int(family_state["best_rigorous"]),
            lower,
        )
        rank_state = family_state["rank_counts"].setdefault(
            lower,
            {"count": 0, "exact_count": 0},
        )
        rank_state["count"] += 1
        if exact:
            rank_state["exact_count"] += 1

    rank_distribution = []
    for rank in sorted(rank_buckets):
        bucket = rank_buckets[rank]
        exact_count = int(bucket["exact_count"])
        lower_only = int(bucket["count"]) - exact_count
        if lower_only:
            rank_distribution.append(
                {
                    "rigorous_lower": rank,
                    "evidence": "Lower bound only",
                    "count": lower_only,
                }
            )
        if exact_count:
            rank_distribution.append(
                {
                    "rigorous_lower": rank,
                    "evidence": "Exact",
                    "count": exact_count,
                }
            )

    family_order = sorted(
        family_buckets,
        key=lambda family: (
            -int(family_buckets[family]["best_rigorous"]),
            -int(family_buckets[family]["curve_count"]),
            family.lower(),
        ),
    )[: max(1, int(family_limit))]

    family_heatmap = []
    for family in family_order:
        for rank in sorted(family_buckets[family]["rank_counts"]):
            bucket = family_buckets[family]["rank_counts"][rank]
            family_heatmap.append(
                {
                    "family": family,
                    "rigorous_lower": rank,
                    "count": int(bucket["count"]),
                    "exact_count": int(bucket["exact_count"]),
                }
            )

    return {
        "rank_distribution": rank_distribution,
        "scatter": scatter,
        "family_heatmap": family_heatmap,
        "family_order": family_order,
    }


def _appearance_vega_spec(spec):
    """Apply the resolved Rank Hunter appearance to Vega-Lite chart chrome."""
    themed = dict(spec or {})
    palette = st.session_state.get("_rh_theme_palette") or {}
    if not isinstance(palette, dict):
        palette = {}

    card = str(palette.get("rh-card") or "transparent")
    text = str(palette.get("rh-text") or "#31333f")
    secondary = str(palette.get("rh-text-secondary") or text)
    muted = str(palette.get("rh-muted") or secondary)
    border = str(palette.get("rh-border") or "rgba(127,127,127,.22)")

    themed["background"] = card
    config = dict(themed.get("config") or {})
    config["view"] = {
        **dict(config.get("view") or {}),
        "fill": card,
        "stroke": None,
    }
    config["axis"] = {
        **dict(config.get("axis") or {}),
        "labelColor": secondary,
        "titleColor": text,
        "gridColor": border,
        "domainColor": border,
        "tickColor": border,
    }
    config["legend"] = {
        **dict(config.get("legend") or {}),
        "labelColor": secondary,
        "titleColor": text,
        "strokeColor": border,
    }
    config["title"] = {
        **dict(config.get("title") or {}),
        "color": text,
        "subtitleColor": muted,
    }
    themed["config"] = config
    return themed


def _themed_vega_lite_chart(data, spec, **kwargs):
    return st.vega_lite_chart(
        data,
        _appearance_vega_spec(spec),
        theme=None,
        **kwargs,
    )


def _curve_chart_palette():
    """Use the same restrained semantic evidence palette as Dashboard."""
    palette = st.session_state.get("_rh_theme_palette") or {}
    if not isinstance(palette, dict):
        palette = {}

    primary = str(palette.get("rh-primary") or "#246BFD")
    primary_deep = str(palette.get("rh-primary-deep") or primary)
    muted = str(palette.get("rh-muted") or "#7D899B")
    muted_2 = str(palette.get("rh-muted-2") or muted)
    card = str(palette.get("rh-card") or "transparent")
    card_2 = str(palette.get("rh-card-2") or card)
    surface_active = str(palette.get("rh-surface-active") or card_2)

    return {
        "evidence_domain": ["Exact", "Lower bound only"],
        "evidence_range": [primary, muted_2],
        "heatmap_range": [card_2, surface_active, primary_deep],
        "portrait_curve": primary_deep,
        "portrait_domain": ["Rigorous witness", "Exact point"],
        "portrait_range": [primary, muted_2],
    }


def _render_research_landscape(rows):
    """Render research charts from the current filtered Inventory rows only."""

    landscape = _research_landscape_data(rows)
    chart_colors = _curve_chart_palette()
    st.html("<div style='height:1.25rem'></div>")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Rigorous rank distribution")
        st.caption("Counts by proven rigorous lower bound; exact ranks are separated.")
        _themed_vega_lite_chart(
            landscape["rank_distribution"],
            {
                "mark": {"type": "bar", "cornerRadiusTopLeft": 3, "cornerRadiusTopRight": 3},
                "encoding": {
                    "x": {
                        "field": "rigorous_lower",
                        "type": "ordinal",
                        "sort": "ascending",
                        "title": "Rigorous lower bound",
                        "axis": {"labelAngle": 0},
                    },
                    "y": {
                        "field": "count",
                        "type": "quantitative",
                        "title": "Curves",
                        "stack": "zero",
                    },
                    "color": {
                        "field": "evidence",
                        "type": "nominal",
                        "title": "Evidence",
                        "scale": {
                            "domain": chart_colors["evidence_domain"],
                            "range": chart_colors["evidence_range"],
                        },
                    },
                    "tooltip": [
                        {"field": "rigorous_lower", "type": "ordinal", "title": "Rank ≥"},
                        {"field": "evidence", "type": "nominal", "title": "Evidence"},
                        {"field": "count", "type": "quantitative", "title": "Curves"},
                    ],
                },
                "height": 285,
            },
            width="stretch",
            key="curves-rank-distribution",
        )

    with right:
        st.markdown("#### Nagao vs rigorous rank")
        st.caption("Nagao is a heuristic signal; the vertical axis is rigorous evidence.")
        _themed_vega_lite_chart(
            landscape["scatter"],
            {
                "mark": {
                    "type": "circle",
                    "size": 72,
                    "opacity": 0.62,
                    "filled": True,
                },
                "encoding": {
                    "x": {
                        "field": "Nagao",
                        "type": "quantitative",
                        "title": "Nagao score",
                        "scale": {"zero": False},
                    },
                    "y": {
                        "field": "rigorous_lower",
                        "type": "quantitative",
                        "title": "Rigorous lower bound",
                        "axis": {"tickMinStep": 1},
                    },
                    "color": {
                        "field": "evidence_class",
                        "type": "nominal",
                        "title": "Evidence",
                        "scale": {
                            "domain": chart_colors["evidence_domain"],
                            "range": chart_colors["evidence_range"],
                        },
                    },
                    "tooltip": [
                        {"field": "curve_id", "type": "quantitative", "title": "Curve"},
                        {"field": "family", "type": "nominal", "title": "Family"},
                        {"field": "parameter", "type": "nominal", "title": "Parameter"},
                        {"field": "Nagao", "type": "quantitative", "title": "Nagao", "format": ".3f"},
                        {"field": "rigorous_lower", "type": "quantitative", "title": "Rank ≥"},
                        {"field": "evidence", "type": "nominal", "title": "Evidence"},
                    ],
                },
                "height": 285,
            },
            width="stretch",
            key="curves-nagao-rank-scatter",
        )

    st.markdown("#### Family × rigorous-rank landscape")
    family_count = len(landscape["family_order"])
    if len({str(row["family"]) for row in rows}) > family_count:
        st.caption(
            f"Top {family_count} families in the current filtered rows, ordered by "
            "strongest rigorous lower bound and then retained curve count."
        )
    else:
        st.caption(
            "Families in the current filtered rows, ordered by strongest rigorous "
            "lower bound and then retained curve count."
        )
    _themed_vega_lite_chart(
        landscape["family_heatmap"],
        {
            "mark": {"type": "rect", "cornerRadius": 2},
            "encoding": {
                "x": {
                    "field": "rigorous_lower",
                    "type": "ordinal",
                    "sort": "ascending",
                    "title": "Rigorous lower bound",
                    "axis": {"labelAngle": 0},
                },
                "y": {
                    "field": "family",
                    "type": "nominal",
                    "sort": landscape["family_order"],
                    "title": None,
                    "axis": {
                        "labelLimit": 210,
                        "labelPadding": 6,
                    },
                },
                "color": {
                    "field": "count",
                    "type": "quantitative",
                    "title": "Curves",
                    "scale": {"range": chart_colors["heatmap_range"]},
                },
                "tooltip": [
                    {"field": "family", "type": "nominal", "title": "Family"},
                    {"field": "rigorous_lower", "type": "ordinal", "title": "Rank ≥"},
                    {"field": "count", "type": "quantitative", "title": "Curves"},
                    {"field": "exact_count", "type": "quantitative", "title": "Exact"},
                ],
            },
            "height": max(250, min(560, 34 * max(1, family_count) + 70)),
        },
        width="stretch",
        key="curves-family-rank-heatmap",
    )


def _render_curve_summary_cards(row, point_stats):
    evidence = _evidence_state(row)
    nagao = (
        f"{float(row['score']):.2f}"
        if row["score"] is not None
        else "—"
    )
    if row["icarm_status"] == "known" and row["icarm_id"]:
        icarm_value = f"#{row['icarm_id']}"
        icarm_detail = (
            f"rank ≥ {int(row['icarm_rank'])}"
            if row["icarm_rank"] is not None
            else "known"
        )
    elif row["icarm_status"] == "not_found_synced":
        icarm_value = "NO MATCH"
        icarm_detail = "synchronized catalog"
    else:
        icarm_value = "UNCHECKED"
        icarm_detail = "no stored check"

    cards = [
        (
            "RANK",
            evidence,
            (
                f"rigorous upper {_rigorous_upper(row)}"
                if _rigorous_upper(row) is not None
                else "rigorous evidence"
            ),
        ),
        (
            "POINTS",
            str(point_stats["total"]),
            f"{point_stats['rigorous_independent']} rigorous witness(es)",
        ),
        ("NAGAO", nagao, "heuristic signal"),
        ("ICARM", icarm_value, icarm_detail),
    ]
    card_html = []
    for label, value, detail in cards:
        card_html.append(
            "<div class='rh-curve-summary-card'>"
            f"<div class='rh-curve-summary-label'>{html.escape(str(label))}</div>"
            f"<div class='rh-curve-summary-value'>{html.escape(str(value))}</div>"
            f"<div class='rh-curve-summary-detail'>{html.escape(str(detail))}</div>"
            "</div>"
        )
    st.html(
        """
        <style>
        .rh-curve-summary-grid{
            display:grid;
            grid-template-columns:repeat(4,minmax(0,1fr));
            gap:.75rem;
            margin:.25rem 0 1rem 0;
        }
        .rh-curve-summary-card{
            background:var(--rh-card,#FFFFFF);
            border:1px solid var(--rh-border,rgba(49,51,63,.14));
            border-radius:.8rem;
            padding:.85rem 1rem .8rem 1rem;
            min-height:5.25rem;
        }
        .rh-curve-summary-label{
            color:var(--rh-muted,#6B7280);
            font-size:.72rem;
            font-weight:700;
            letter-spacing:.08em;
        }
        .rh-curve-summary-value{
            color:var(--rh-text,#111827);
            font-size:1.12rem;
            font-weight:700;
            line-height:1.25;
            margin-top:.2rem;
        }
        .rh-curve-summary-detail{
            color:var(--rh-muted,#6B7280);
            font-size:.76rem;
            margin-top:.25rem;
        }
        @media (max-width: 900px){
            .rh-curve-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
        }
        @media (max-width: 560px){
            .rh-curve-summary-grid{grid-template-columns:minmax(0,1fr);}
        }
        </style>
        <div class='rh-curve-summary-grid'>
        """
        + "".join(card_html)
        + "</div>"
    )


def _open_planner_destination(action, curve_id):
    """Open a planner-owned specialist page without creating workflow state."""
    curve_id = set_active_curve_id(st.session_state, int(curve_id))
    page = str(action.get("page") or "Curves")
    page = {
        "MW Geometry": "Lattices",
        "Lattices & Heights": "Lattices",
    }.get(page, page)

    state_key = {
        "Independence": "independence_curve_id",
        "Lattices": "analysis_curve_id",
        "Descent": "descent_curve_id",
        "Target": "target_curve_id",
        "Points": "points_curve_id",
    }.get(page)
    if state_key is not None:
        set_active_curve_id(st.session_state, curve_id, state_key)
    elif page == "Curves":
        st.session_state["curves_detail_id"] = curve_id
    elif page == "Search":
        st.session_state["search_tab"] = "Family"
        family_pool_id = action.get("family_pool_id")
        if family_pool_id is not None:
            st.session_state["family_search_pool_id"] = int(family_pool_id)

    st.session_state["rh_page"] = page


def _render_next_action(db, row):
    """Show one proof-safe computed recommendation, not an authored case record."""
    snapshot = curve_analysis_snapshot(db, int(row["id"]))
    actions = planner_immediate_actions(analysis_plan(snapshot))
    if not actions:
        return

    action = actions[0]
    with st.container(border=True, key=f"curve-next-action-{int(row['id'])}"):
        copy, launch_col = st.columns([3.35, 1], vertical_alignment="center")
        with copy:
            section_title("Next useful action", str(action["title"]))
            st.caption(str(action["why"]))
        with launch_col:
            page = str(action.get("page") or "Curves")
            if st.button(
                f"Open {page}",
                type="primary",
                width="stretch",
                key=f"curve-next-action-open-{int(row['id'])}",
            ):
                _open_planner_destination(action, int(row["id"]))
                st.rerun()


def _render_overview(db, row, point_stats):
    with st.container(border=True):
        section_title("At a glance", "The shortest useful summary of this specialization.")
        state, conclusion = _rank_conclusion(row)
        if state == "exact":
            st.success(f"Rigorous conclusion: **{conclusion}**")
        else:
            st.info(f"Rigorous conclusion: **{conclusion}**")

        a, b, c, d = st.columns(4)
        a.metric("Stored points", point_stats["total"])
        b.metric("Exact on curve", point_stats["exact_verified"])
        c.metric("Rigorous witnesses", point_stats["rigorous_independent"])
        d.metric("Rigorous upper", _rigorous_upper(row) if _rigorous_upper(row) is not None else "—")

    _render_curve_portrait(db, row)

    model_col, arithmetic_col = st.columns([1.1, 0.9])
    with model_col:
        with st.container(border=True):
            section_title("Stored model", f"{row['family']} · t={row['parameter']}")
            st.caption("a-invariants")
            st.code(row["a_invariants_json"] or "not stored", language="text")
            if row["plugin_id"]:
                st.caption(f"Family plugin: {row['plugin_id']} · v{row['plugin_version'] or '—'}")
    with arithmetic_col:
        with st.container(border=True):
            section_title("Arithmetic summary", "Compact values first; exact integers live under Arithmetic.")
            conductor = row["arithmetic_conductor"]
            discriminant = row["arithmetic_discriminant"]
            c1, c2 = st.columns(2)
            c1.metric("log N", f"{row['arithmetic_log_conductor']:.2f}" if row["arithmetic_log_conductor"] is not None else "—")
            c2.metric("log |Δ|", f"{_log_abs_integer(discriminant):.2f}" if _log_abs_integer(discriminant) is not None else "—")
            c3, c4 = st.columns(2)
            c3.metric(
                "Naive height",
                f"{_real_value(row['arithmetic_naive_height']):.2f}"
                if _real_value(row["arithmetic_naive_height"]) is not None
                else "—",
            )
            c4.metric(
                "Faltings height",
                f"{_real_value(row['arithmetic_faltings_height']):.2f}"
                if _real_value(row["arithmetic_faltings_height"]) is not None
                else "—",
            )
            c5, c6 = st.columns(2)
            c5.metric("Torsion", _torsion_display(row))
            c6.metric("Nagao", f"{float(row['score']):.3f}" if row["score"] is not None else "—")
            bad_primes = row["arithmetic_bad_primes"]
            if isinstance(bad_primes, list):
                st.caption(
                    "Bad primes: "
                    + (" · ".join(str(p) for p in bad_primes) if bad_primes else "none")
                    + f" · {_arithmetic_source_label(row, 'bad_primes')}"
                )
            else:
                st.caption("Bad primes: not computed")
            st.caption(
                f"Conductor source: {_arithmetic_source_label(row, 'conductor')} · "
                f"Discriminant source: {_arithmetic_source_label(row, 'discriminant')}"
            )


def _render_rank_evidence(db, row, point_stats):
    with st.container(border=True):
        section_title("Rigorous evidence", "Only exact or rigorous bounds contribute to the conclusion.")
        state, conclusion = _rank_conclusion(row)
        if state == "exact":
            st.success(f"**{conclusion}**")
        elif state == "bounded":
            st.info(f"Rigorous interval: **{conclusion}**")
        else:
            st.info(f"Rigorous conclusion: **{conclusion}**")

        a, b, c, d = st.columns(4)
        a.metric("Generic lower", row["generic_lower"] if row["generic_lower"] is not None else "—")
        b.metric("Descent lower", row["descent_lower"] if row["descent_lower"] is not None else "—")
        c.metric("Descent upper", row["descent_upper"] if row["descent_upper"] is not None else "—")
        d.metric("Exact rank", row["exact_rank"] if row["exact_rank"] is not None else "—")

    with st.container(border=True):
        section_title("Heuristic / non-rigorous signals", "Useful for prioritization; these do not upgrade proof status.")
        h1, h2 = st.columns(2)
        h1.metric("Nagao score", f"{float(row['score']):.3f}" if row["score"] is not None else "—")
        h2.metric("Quick upper", row["quick_upper"] if row["quick_upper"] is not None else "—")
        st.caption("Nagao scores and quick bounds are kept visually separate from rigorous rank evidence.")

    _render_point_ledger_summary(db, row, point_stats)

    with st.container(border=True):
        section_title("External catalog evidence", "Catalog records are provenance/context, not a replacement for local certificates.")
        i1, i2 = st.columns(2)
        i1.write(f"**ICARM**  \n{_catalog_summary(row, 'icarm')}")
        i2.write(f"**LMFDB**  \n{_catalog_summary(row, 'lmfdb')}")
        checked = row["icarm_checked_at"] or row["lmfdb_checked_at"]
        if checked:
            st.caption(f"Latest displayed catalog check: {str(checked)[:19]}")


def _render_point_ledger_summary(db, row, point_stats):
    with st.container(border=True):
        section_title("Point ledger summary", "Exact verification and independence are shown separately.")
        a, b, c, d = st.columns(4)
        a.metric("Total", point_stats["total"])
        b.metric("Exact", point_stats["exact_verified"])
        c.metric("Rigorous independent", point_stats["rigorous_independent"])
        d.metric("Dependent", point_stats["dependent"])

        if point_stats["numerical_novel"] or point_stats["unknown"]:
            st.caption(
                f"Other states: {point_stats['numerical_novel']} numerical novelty · "
                f"{point_stats['unknown']} unknown."
            )

        point_rows = db.execute(
            """
            SELECT role, independence_status, COUNT(*) n
            FROM points
            WHERE curve_id=?
            GROUP BY role, independence_status
            ORDER BY n DESC, role
            """,
            (int(row["id"]),),
        ).fetchall()
        if point_rows:
            st.dataframe(
                [
                    {
                        "role": p["role"],
                        "independence": p["independence_status"],
                        "points": p["n"],
                    }
                    for p in point_rows
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            gens = _safe_json(row["generators_json"], [])
            st.info(f"No unified point-ledger rows yet. Legacy generator field contains {len(gens)} point(s).")

        if st.button("Open full Point Ledger", width="stretch", key=f"curves-open-points-{row['id']}"):
            set_active_curve_id(
                st.session_state,
                int(row["id"]),
                "points_curve_id",
            )
            st.session_state["rh_page"] = "Points"
            st.rerun()


def _render_arithmetic(row):
    conductor = row["arithmetic_conductor"]
    discriminant = row["arithmetic_discriminant"]
    naive = row["arithmetic_naive_height"]
    faltings = row["arithmetic_faltings_height"]
    torsion = row.get("arithmetic_torsion") or {}

    with st.container(border=True):
        section_title("Arithmetic", "Authoritative local values first; external reference fallback is labeled explicitly.")
        a, b, c, d = st.columns(4)
        a.metric("log N", f"{row['arithmetic_log_conductor']:.2f}" if row["arithmetic_log_conductor"] is not None else "—")
        b.metric("log |Δ|", f"{_log_abs_integer(discriminant):.2f}" if _log_abs_integer(discriminant) is not None else "—")
        c.metric("Naive height", f"{_real_value(naive):.2f}" if _real_value(naive) is not None else "—")
        d.metric("Faltings height", f"{_real_value(faltings):.2f}" if _real_value(faltings) is not None else "—")
        st.caption(
            f"Conductor: {_arithmetic_source_label(row, 'conductor')} · "
            f"Discriminant: {_arithmetic_source_label(row, 'discriminant')} · "
            f"Naive height: {_arithmetic_source_label(row, 'naive_height')} · "
            f"Faltings height: {_arithmetic_source_label(row, 'faltings_height')}"
        )
        t1, t2, t3 = st.columns(3)
        t1.metric("Rational torsion", _torsion_display(row))
        torsion_order = torsion.get("order") if isinstance(torsion, dict) else None
        t2.metric("Torsion order", torsion_order if torsion_order is not None else "—")
        t3.metric(
            "Root number",
            row["arithmetic_root_number"]
            if row["arithmetic_root_number"] is not None
            else "—",
        )
        torsion_prov = (row.get("arithmetic_provenance") or {}).get("torsion") or {}
        if torsion_prov.get("detail"):
            st.caption(f"Torsion computation: {torsion_prov['detail']}")
        elif torsion_prov.get("computed_at"):
            st.caption(
                f"Computed {str(torsion_prov['computed_at'])[:19]} · "
                f"{torsion_prov.get('method') or 'local arithmetic'}"
            )

        st.write("**Bad primes**")
        bad_primes = row["arithmetic_bad_primes"]
        st.code(
            json.dumps(bad_primes) if isinstance(bad_primes, list) else "not computed",
            language="text",
        )
        st.caption(f"Source: {_arithmetic_source_label(row, 'bad_primes')}")
        if row.get("arithmetic_conflicts"):
            st.warning(
                "Arithmetic reference conflict: "
                + ", ".join(str(field) for field in row["arithmetic_conflicts"])
                + ". Local persisted values remain authoritative."
            )
        if row.get("arithmetic_issues"):
            st.caption("Arithmetic data issue(s): " + "; ".join(row["arithmetic_issues"]))

        with st.expander("Exact integers & stored model", expanded=False):
            st.write("**a-invariants**")
            st.code(row["a_invariants_json"] or "not stored", language="text")
            st.write("**Discriminant**")
            st.code(str(discriminant) if discriminant is not None else "not computed", language="text")
            st.caption(f"Source: {_arithmetic_source_label(row, 'discriminant')}")
            st.write("**Conductor**")
            st.code(str(conductor) if conductor is not None else "not computed", language="text")
            st.caption(f"Source: {_arithmetic_source_label(row, 'conductor')}")


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _curve_history_rows(db, row, *, limit=80):
    curve_id = int(row["id"])
    history = [
        {
            "time": str(row["created_at"] or ""),
            "type": "curve",
            "activity": "Stored in Rank Hunter",
            "detail": f"{row['family']} · t={row['parameter']}",
            "status": str(row["status"] or ""),
        }
    ]

    if _table_exists(db, "candidates") and _table_exists(db, "candidate_pools"):
        records = db.execute(
            """
            SELECT c.id,c.parameter,c.score,c.status,c.created_at,c.updated_at,
                   p.id AS pool_id,p.name AS pool_name
            FROM candidates c
            JOIN candidate_pools p ON p.id=c.pool_id
            WHERE c.curve_id=?
            ORDER BY c.updated_at DESC,c.id DESC
            LIMIT 25
            """,
            (curve_id,),
        ).fetchall()
        for rec in records:
            bits = [f"pool #{int(rec['pool_id'])} · {rec['pool_name']}"]
            if rec["score"] is not None:
                bits.append(f"Nagao {float(rec['score']):.3f}")
            bits.append(f"t={rec['parameter']}")
            history.append(
                {
                    "time": str(rec["updated_at"] or rec["created_at"] or ""),
                    "type": "candidate",
                    "activity": f"Candidate #{int(rec['id'])} linked to curve",
                    "detail": " · ".join(bits),
                    "status": str(rec["status"] or ""),
                }
            )

    if _table_exists(db, "search_pipeline_point_attempts"):
        records = db.execute(
            """
            SELECT a.id,a.run_id,a.stage_index,a.stage_id,a.tier,a.height,
                   a.denominator_low,a.denominator_high,a.status,a.exact_points,
                   a.runtime,a.created_at,a.updated_at,
                   r.pipeline_name
            FROM search_pipeline_point_attempts a
            LEFT JOIN search_pipeline_runs r ON r.id=a.run_id
            WHERE a.curve_id=?
            ORDER BY a.updated_at DESC,a.id DESC
            LIMIT 35
            """,
            (curve_id,),
        ).fetchall()
        for rec in records:
            den = ""
            if rec["denominator_low"] is not None or rec["denominator_high"] is not None:
                den = (
                    f" · denom {rec['denominator_low'] if rec['denominator_low'] is not None else '—'}"
                    f"–{rec['denominator_high'] if rec['denominator_high'] is not None else '—'}"
                )
            runtime = (
                f" · {float(rec['runtime']):.2f}s"
                if rec["runtime"] is not None
                else ""
            )
            history.append(
                {
                    "time": str(rec["updated_at"] or rec["created_at"] or ""),
                    "type": "search",
                    "activity": f"Pipeline run #{int(rec['run_id'])} · {rec['stage_id']}",
                    "detail": (
                        f"{rec['pipeline_name'] or 'Pipeline'} · {rec['tier']} · "
                        f"height {int(rec['height'])}{den} · "
                        f"{int(rec['exact_points'] or 0)} exact point(s){runtime}"
                    ),
                    "status": str(rec["status"] or ""),
                }
            )

    if _table_exists(db, "point_discoveries"):
        records = db.execute(
            """
            SELECT id,source,outcome,tier,search_ref,pipeline_run_id,
                   pipeline_stage_id,height,effective_denominator_low,
                   effective_denominator_high,exact_verified,created_at
            FROM point_discoveries
            WHERE curve_id=?
            ORDER BY created_at DESC,id DESC
            LIMIT 35
            """,
            (curve_id,),
        ).fetchall()
        for rec in records:
            context = []
            if rec["search_ref"]:
                context.append(str(rec["search_ref"]))
            if rec["pipeline_run_id"] is not None:
                context.append(f"pipeline #{int(rec['pipeline_run_id'])}")
            if rec["pipeline_stage_id"]:
                context.append(str(rec["pipeline_stage_id"]))
            if rec["tier"]:
                context.append(str(rec["tier"]))
            if rec["height"] is not None:
                context.append(f"height {int(rec['height'])}")
            if (
                rec["effective_denominator_low"] is not None
                or rec["effective_denominator_high"] is not None
            ):
                context.append(
                    "denom "
                    f"{rec['effective_denominator_low'] if rec['effective_denominator_low'] is not None else '—'}"
                    "–"
                    f"{rec['effective_denominator_high'] if rec['effective_denominator_high'] is not None else '—'}"
                )
            history.append(
                {
                    "time": str(rec["created_at"] or ""),
                    "type": "point search",
                    "activity": str(rec["source"] or "Point discovery"),
                    "detail": " · ".join(context) or "curve-linked discovery",
                    "status": (
                        f"{rec['outcome']} · exact"
                        if int(rec["exact_verified"] or 0)
                        else str(rec["outcome"] or "")
                    ),
                }
            )

    if _table_exists(db, "general_hunt_trials"):
        records = db.execute(
            """
            SELECT id,mode,x_bound,target_lower,status,rigorous_lower,
                   created_at,updated_at
            FROM general_hunt_trials
            WHERE curve_id=?
            ORDER BY updated_at DESC,id DESC
            LIMIT 20
            """,
            (curve_id,),
        ).fetchall()
        for rec in records:
            history.append(
                {
                    "time": str(rec["updated_at"] or rec["created_at"] or ""),
                    "type": "search",
                    "activity": f"General Hunt trial #{int(rec['id'])}",
                    "detail": (
                        f"{rec['mode']} · x bound {int(rec['x_bound'])} · "
                        f"target ≥ {int(rec['target_lower'])} · "
                        f"rigorous ≥ {int(rec['rigorous_lower'] or 0)}"
                    ),
                    "status": str(rec["status"] or ""),
                }
            )

    if _table_exists(db, "quartic_searches"):
        records = db.execute(
            """
            SELECT id,hole_label,height_bound,status,point_count,runtime,
                   started_at,finished_at,created_at
            FROM quartic_searches
            WHERE curve_id=?
            ORDER BY COALESCE(finished_at,started_at,created_at) DESC,id DESC
            LIMIT 20
            """,
            (curve_id,),
        ).fetchall()
        for rec in records:
            runtime = (
                f" · {float(rec['runtime']):.2f}s"
                if rec["runtime"] is not None
                else ""
            )
            history.append(
                {
                    "time": str(
                        rec["finished_at"]
                        or rec["started_at"]
                        or rec["created_at"]
                        or ""
                    ),
                    "type": "quartic",
                    "activity": f"Quartic search #{int(rec['id'])}",
                    "detail": (
                        f"{rec['hole_label'] or 'unlabeled hole'} · "
                        f"height {int(rec['height_bound'])} · "
                        f"{int(rec['point_count'] or 0)} point(s){runtime}"
                    ),
                    "status": str(rec["status"] or ""),
                }
            )

    if _table_exists(db, "rank_evidence"):
        records = db.execute(
            """
            SELECT id,engine,status,rigorous,rigorous_lower,rigorous_upper,
                   exact_rank,timed_out,partial,elapsed_seconds,
                   started_at,finished_at,created_at
            FROM rank_evidence
            WHERE curve_id=?
            ORDER BY COALESCE(finished_at,started_at,created_at) DESC,id DESC
            LIMIT 25
            """,
            (curve_id,),
        ).fetchall()
        for rec in records:
            bounds = []
            if rec["exact_rank"] is not None:
                bounds.append(f"exact {int(rec['exact_rank'])}")
            elif rec["rigorous_lower"] is not None:
                bounds.append(f"rank ≥ {int(rec['rigorous_lower'])}")
                if rec["rigorous_upper"] is not None:
                    bounds.append(f"upper {int(rec['rigorous_upper'])}")
            if int(rec["timed_out"] or 0):
                bounds.append("timed out")
            if int(rec["partial"] or 0):
                bounds.append("partial")
            if rec["elapsed_seconds"] is not None:
                bounds.append(f"{float(rec['elapsed_seconds']):.2f}s")
            history.append(
                {
                    "time": str(
                        rec["finished_at"]
                        or rec["started_at"]
                        or rec["created_at"]
                        or ""
                    ),
                    "type": "rank evidence",
                    "activity": f"{rec['engine']} · evidence #{int(rec['id'])}",
                    "detail": " · ".join(bounds) or "no stored rank bounds",
                    "status": str(rec["status"] or ""),
                }
            )

    history.sort(key=lambda item: (str(item["time"]), str(item["activity"])), reverse=True)
    return history[: max(1, int(limit))]


def _render_history(db, row):
    history = _curve_history_rows(db, row)
    with st.container(border=True):
        section_title(
            "Curve timeline",
            "Durable curve-linked search, discovery, and proof history.",
        )
        if history:
            st.dataframe(
                history,
                width="stretch",
                hide_index=True,
                height=min(620, max(220, 38 * len(history) + 40)),
                column_config={
                    "time": st.column_config.TextColumn("Time"),
                    "type": st.column_config.TextColumn("Type"),
                    "activity": st.column_config.TextColumn("Activity"),
                    "detail": st.column_config.TextColumn("Detail", width="large"),
                    "status": st.column_config.TextColumn("Status"),
                },
            )
        else:
            st.info("No durable curve-linked history is stored yet.")
        st.caption(
            f"Stored {str(row['created_at'] or '')[:19]} · "
            f"last curve-row update {str(row['updated_at'] or '')[:19]}. "
            "Legacy searches without a durable curve link are intentionally not inferred."
        )

    catalog_col, prov_col = st.columns([0.9, 1.1])
    with catalog_col:
        with st.container(border=True):
            section_title("Catalog history", "Latest stored source checks.")
            st.write(f"**ICARM:** {_catalog_summary(row, 'icarm')}")
            if row["icarm_checked_at"]:
                st.caption(f"Checked {str(row['icarm_checked_at'])[:19]}")
            st.write(f"**LMFDB:** {_catalog_summary(row, 'lmfdb')}")
            if row["lmfdb_checked_at"]:
                st.caption(f"Checked {str(row['lmfdb_checked_at'])[:19]}")

    with prov_col:
        with st.container(border=True):
            section_title("Recent provenance", "Latest backend events for this curve.")
            events = db.execute(
                "SELECT level,message,created_at FROM events WHERE curve_id=? ORDER BY id DESC LIMIT 12",
                (int(row["id"]),),
            ).fetchall()
            if events:
                for ev in events:
                    st.caption(f"{ev['created_at'][:19]} · {str(ev['level']).upper()}")
                    st.write(str(ev["message"]))
            else:
                st.info("No provenance events stored yet.")

    if row["plugin_id"]:
        with st.expander("Reproducibility fingerprints", expanded=False):
            st.write(f"**Family plugin:** `{row['plugin_id']}` · v{row['plugin_version'] or '—'}")
            st.caption(f"Family spec: {row['family_spec'] or '—'}")
            st.code(
                f"family   {row['family_sha256'] or '—'}\n"
                f"adapter  {row['adapter_sha256'] or '—'}\n"
                f"manifest {row['plugin_manifest_sha256'] or '—'}",
                language="text",
            )



def _reset_curve_filters():
    st.session_state["curves-filter"] = ""
    st.session_state.pop("curves-minrank", None)
    st.session_state.pop("curves-rank-range", None)
    st.session_state["curves-family"] = "All"
    st.session_state["curves-campaign"] = 0
    st.session_state["curves-evidence"] = "Any"
    st.session_state["curves-torsion"] = "Any"
    st.session_state["curves-status"] = "Any"


def _open_curve_detail(curve_id):
    st.session_state["curves_selected_id"] = int(curve_id)
    st.session_state["curves_detail_id"] = int(curve_id)


def _clear_curve_table_selection():
    for key in list(st.session_state):
        if (
            "curves-browse-table" in str(key)
            or "curves-hof-" in str(key)
        ):
            st.session_state.pop(key, None)


def _render_import_curve(db):
    json_col, manual_col = st.columns([0.9, 1.1], gap="large")

    with json_col:
        with st.container(border=True):
            st.markdown("#### JSON import")
            st.caption(
                "Accepts one curve object, an array of curve objects, "
                '{"curves":[...]}, or a bare [a1,a2,a3,a4,a6] array.'
            )
            uploaded = st.file_uploader(
                "JSON file",
                type=["json"],
                key="curves-import-json",
            )
            if uploaded is not None:
                st.caption(
                    "Fields: a_invariants/ainvs, optional points, family, parameter, "
                    "source, claimed_rank, and notes."
                )
                if st.button(
                    "Import JSON",
                    type="primary",
                    width="stretch",
                    key="curves-import-json-submit",
                ):
                    try:
                        results = import_curve_records(
                            db,
                            parse_curve_import_json(uploaded.getvalue()),
                            default_source=f"json:{uploaded.name}",
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        created_count = sum(1 for rec in results if rec["created"])
                        matched_count = len(results) - created_count
                        _reset_curve_filters()
                        if results:
                            _open_curve_detail(int(results[-1]["curve_id"]))
                            st.session_state["_rh-curves-section_value"] = "Inventory"
                        summary = f"Imported {created_count} curve(s)"
                        if matched_count:
                            summary += f"; matched {matched_count} existing curve(s)"
                        st.success(summary + ".")
                        if results:
                            st.rerun()

    with manual_col:
        with st.container(border=True):
            st.markdown("#### Manual curve")
            with st.form("curves-import-form"):
                c1, c2 = st.columns(2)
                family = c1.text_input(
                    "Family / label",
                    value="",
                    placeholder="Optional — e.g. Another Hunter family",
                )
                parameter = c2.text_input(
                    "Parameter / identifier",
                    value="",
                    placeholder="Optional — generated from the model if blank",
                )
                ainvs = st.text_input(
                    "a-invariants [a1,a2,a3,a4,a6]",
                    value="",
                    placeholder="[0,0,0,-1,0]",
                    help=(
                        "For y² = x³ + A x + B, enter [0,0,0,A,B]. "
                        "Rational entries are allowed."
                    ),
                )
                p1, p2 = st.columns(2)
                source = p1.text_input(
                    "Source",
                    value="",
                    placeholder="Optional — tool/site/run name or URL",
                )
                claimed_rank = p2.text_input(
                    "External rank claim",
                    value="",
                    placeholder="Optional — e.g. 11 or >=10",
                    help=(
                        "Recorded as provenance only; it does not become "
                        "Rank Hunter rank evidence."
                    ),
                )
                points = st.text_area(
                    "Known rational points",
                    value="",
                    placeholder="Optional — [[0,0],[1,0]] or one x,y pair per line",
                    height=100,
                    help=(
                        "Each point is checked exactly against the supplied curve "
                        "before import."
                    ),
                )
                notes = st.text_area(
                    "Notes",
                    value="",
                    placeholder="Optional — why this curve looked interesting",
                    height=80,
                )
                submitted = st.form_submit_button(
                    "Import curve",
                    type="primary",
                    width="stretch",
                )

            if submitted:
                try:
                    curve_id, created = import_curve(
                        db,
                        a_invariants=ainvs,
                        family=family,
                        parameter=parameter,
                        source=source,
                        claimed_rank=claimed_rank,
                        notes=notes,
                        points=points,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _reset_curve_filters()
                    _open_curve_detail(int(curve_id))
                    st.session_state["_rh-curves-section_value"] = "Inventory"
                    st.success(
                        f"{'Imported' if created else 'Matched existing'} "
                        f"curve #{curve_id}."
                    )
                    st.rerun()


def _render_curve_portrait(db, row):
    points = db.execute(
        """SELECT id,x,y,source,rigorous_independent
           FROM points
           WHERE curve_id=? AND exact_verified=1
           ORDER BY rigorous_independent DESC,id DESC
           LIMIT 250""",
        (int(row["id"]),),
    ).fetchall()
    portrait = curve_portrait_data(
        row["a_invariants_json"],
        [dict(point) for point in points],
    )
    chart_colors = _curve_chart_palette()

    with st.container(border=True):
        section_title("Curve portrait")
        if portrait is None:
            st.info(
                "A bounded real portrait is unavailable for this stored model. "
                "The exact model and Point Ledger remain authoritative."
            )
            return

        _themed_vega_lite_chart(
            portrait["rows"],
            {
                "height": 230,
                "layer": [
                    {
                        "transform": [{"filter": "datum.kind == 'curve'"}],
                        "mark": {
                            "type": "line",
                            "strokeWidth": 2,
                            "color": chart_colors["portrait_curve"],
                        },
                        "encoding": {
                            "x": {
                                "field": "x",
                                "type": "quantitative",
                                "title": "x",
                            },
                            "y": {
                                "field": "y",
                                "type": "quantitative",
                                "title": "y",
                            },
                            "detail": {
                                "field": "series",
                                "type": "nominal",
                            },
                        },
                    },
                    {
                        "transform": [{"filter": "datum.kind == 'point'"}],
                        "mark": {
                            "type": "point",
                            "filled": True,
                            "size": 85,
                        },
                        "encoding": {
                            "x": {"field": "x", "type": "quantitative"},
                            "y": {"field": "y", "type": "quantitative"},
                            "color": {
                                "field": "status",
                                "type": "nominal",
                                "title": "Point status",
                                "scale": {
                                    "domain": chart_colors["portrait_domain"],
                                    "range": chart_colors["portrait_range"],
                                },
                            },
                            "tooltip": [
                                {"field": "point_id", "type": "ordinal", "title": "Point"},
                                {"field": "x", "type": "quantitative", "title": "x"},
                                {"field": "y", "type": "quantitative", "title": "y"},
                                {"field": "status", "type": "nominal", "title": "Status"},
                                {"field": "source", "type": "nominal", "title": "Source"},
                            ],
                        },
                    },
                ],
            },
            width="stretch",
        )
        st.caption(
            f"{portrait['points_shown']}/{portrait['points_available']} exact point(s) shown · "
            "rigorous-witness labels from the Point Ledger · presentation only."
        )


def _render_curve_actions(db, ctx, row):
    st.markdown("")
    section_title(
        "Actions",
        "Continue research on this specialization after reviewing its evidence.",
    )
    explorer_route = _enabled_curve_explorer_route(db, ctx)
    a, b, c, d = st.columns(4)
    if a.button(
        "Target Search",
        width="stretch",
        type="primary",
        key=f"curve-target-{row['id']}",
    ):
        set_active_curve_id(
            st.session_state,
            int(row["id"]),
            "target_curve_id",
        )
        st.session_state["rh_page"] = "Target"
        st.rerun()
    if b.button("Points", width="stretch", key=f"curve-points-{row['id']}"):
        set_active_curve_id(
            st.session_state,
            int(row["id"]),
            "points_curve_id",
        )
        st.session_state["rh_page"] = "Points"
        st.rerun()
    if c.button(
        "Curve Explorer",
        width="stretch",
        key=f"curve-explorer-{row['id']}",
        disabled=explorer_route is None,
        help=(
            None
            if explorer_route
            else "Enable the Curve Explorer extension to open this workspace."
        ),
    ):
        st.session_state["curve_explorer_curve_id"] = int(row["id"])
        st.session_state["rh_page"] = explorer_route
        st.rerun()
    if d.button(
        "Lattices",
        width="stretch",
        key=f"curve-lattice-{row['id']}",
    ):
        set_active_curve_id(
            st.session_state,
            int(row["id"]),
            "analysis_curve_id",
        )
        st.session_state["rh_page"] = "Lattices"
        st.rerun()

    with st.expander("More actions", expanded=False):
        x1, x2, x3 = st.columns(3)
        check_disabled = not bool(row["a_invariants_json"])
        if x1.button(
            "Check ICARM",
            width="stretch",
            disabled=check_disabled,
            key=f"curve-check-icarm-{row['id']}",
            help=(
                "Manual only: refresh the public ICARM snapshot and check this "
                "exact Q-isomorphism class/rank."
            ),
        ):
            py = setting(db, "science_python", ctx.detected_science_python())
            cmd = [
                py,
                "-m",
                "rank42.icarm_check",
                "--db",
                ctx.db_path,
                "--curve-id",
                int(row["id"]),
                "--project-root",
                ctx.project_root,
            ]
            jid = launch(
                ctx,
                db,
                kind="icarm_check",
                label=f"Check ICARM · curve #{int(row['id'])}",
                command=cmd,
                metadata={"curve_id": int(row["id"])},
            )
            st.success(f"Started manual ICARM check job #{jid}.")
        if row["icarm_status"] == "known" and row["icarm_url"]:
            x2.link_button("Open ICARM", str(row["icarm_url"]), width="stretch")
        else:
            x2.button(
                "Open ICARM",
                width="stretch",
                disabled=True,
                key=f"curve-open-icarm-{row['id']}",
                help="Available after a manual ICARM match.",
            )
        if x3.button(
            "Submit to ICARM",
            width="stretch",
            key=f"curve-submit-icarm-{row['id']}",
        ):
            st.session_state["icarm_curve_id"] = int(row["id"])
            st.session_state["rh_page"] = "ICARM Submit"
            st.rerun()

    render_feature_hook(
        db,
        ctx,
        "curves.actions",
        curve_id=int(row["id"]),
        curve=dict(row),
    )


def _render_curve_detail(db, ctx, row):
    back_col, title_col = st.columns(
        [0.18, 0.82],
        vertical_alignment="center",
    )
    with back_col:
        if st.button(
            "Back",
            icon=":material/arrow_back:",
            width="stretch",
            key=f"curve-back-{int(row['id'])}",
        ):
            st.session_state.pop("curves_detail_id", None)
            _clear_curve_table_selection()
            st.rerun()
    with title_col:
        st.subheader(f"Curve #{int(row['id'])} · {row['family']}")
        st.caption(f"t = {row['parameter']} · status {row['status']}")

    point_stats = _point_summary(db, int(row["id"]))
    _render_curve_summary_cards(row, point_stats)

    detail_view = tabs(
        ["Overview", "Rank Evidence", "Arithmetic", "History"],
        value="Overview",
        key=f"curves-detail-{row['id']}",
        variant="line",
    )
    if detail_view == "Rank Evidence":
        _render_rank_evidence(db, row, point_stats)
    elif detail_view == "Arithmetic":
        _render_arithmetic(row)
    elif detail_view == "History":
        _render_history(db, row)
    else:
        _render_overview(db, row, point_stats)

    _render_next_action(db, row)
    _render_curve_actions(db, ctx, row)


def _render_browse(db, ctx):
    families = [
        r["family"]
        for r in db.execute(
            "SELECT DISTINCT family FROM curves ORDER BY family"
        ).fetchall()
    ]
    statuses = [
        r["status"]
        for r in db.execute(
            "SELECT DISTINCT status FROM curves ORDER BY status"
        ).fetchall()
    ]
    campaigns = list(list_campaigns(db))
    campaign_by_id = {int(row["id"]): row for row in campaigns}
    campaign_options = [0, *campaign_by_id]
    if st.session_state.get("curves-campaign", 0) not in campaign_options:
        st.session_state["curves-campaign"] = 0

    st.html(
        """
        <style>
        div[class*="st-key-curves-browse-filters"] {
            background: var(--rh-card,#FFFFFF) !important;
            border: 1px solid var(--rh-border,rgba(49,51,63,.14)) !important;
            border-radius: .8rem !important;
            padding: 1rem 1rem .65rem 1rem !important;
        }
        div[class*="st-key-curves-browse-filters"] > div,
        div[class*="st-key-curves-browse-filters"]
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: transparent !important;
        }
        </style>
        """
    )
    with st.container(key="curves-browse-filters", border=False):
        rank_ceiling = _max_rigorous_lower(db)
        current_range = st.session_state.get("curves-rank-range")
        if (
            not isinstance(current_range, (tuple, list))
            or len(current_range) != 2
            or int(current_range[0]) < 0
            or int(current_range[1]) > rank_ceiling
            or int(current_range[0]) > int(current_range[1])
        ):
            st.session_state.pop("curves-rank-range", None)

        search_col, rank_slider_col = st.columns(
            [4.2, 1.55],
            gap="small",
            vertical_alignment="center",
        )
        q = search_col.text_input(
            "Filter",
            value="",
            placeholder="Search family, parameter, curve ID, ICARM/LMFDB ID…",
            key="curves-filter",
            label_visibility="collapsed",
        )
        minrank, maxrank = (
            int(value)
            for value in rank_slider_col.slider(
                "Rigorous rank",
                min_value=0,
                max_value=rank_ceiling,
                value=(0, rank_ceiling),
                step=1,
                key="curves-rank-range",
                label_visibility="collapsed",
                help="Rigorous lower-bound rank range",
            )
        )

        c1, c2, c3, c4, c5 = st.columns(
            [2.2, 1.2, 1.2, 1.05, 1.05],
            gap="small",
        )
        fam = c1.selectbox("Family", ["All"] + families, key="curves-family")
        campaign_choice = c2.selectbox(
            "Campaign",
            campaign_options,
            key="curves-campaign",
            format_func=lambda value: (
                "Any"
                if int(value) == 0
                else (
                    f"#{int(value)} · {campaign_by_id[int(value)]['name']}"
                    + (
                        " (archived)"
                        if str(campaign_by_id[int(value)]["status"]) == "archived"
                        else ""
                    )
                )
            ),
        )
        evidence = c3.selectbox(
            "Evidence",
            ["Any", "Exact", "Rigorous lower only", "Rigorous interval"],
            key="curves-evidence",
        )
        torsion = c4.selectbox(
            "Torsion",
            ["Any", *MAZUR_TORSION_CHOICES, "Not stored"],
            key="curves-torsion",
        )
        status = c5.selectbox(
            "Status",
            ["Any"] + statuses,
            key="curves-status",
        )

    st.html("<div style='height:.85rem'></div>")
    inventory_view = tabs(
        ["Evidence", "Arithmetic", "Catalog"],
        value="Evidence",
        key="curves-inventory-view",
        variant="line",
    )

    rows = _curve_inventory_rows(
        db,
        minrank=minrank,
        maxrank=maxrank,
        family=fam,
        campaign_id=(
            None
            if int(campaign_choice) == 0
            else int(campaign_choice)
        ),
        evidence=evidence,
        torsion=torsion,
        status=status,
        query=q,
        include_arithmetic=inventory_view == "Arithmetic",
        include_point_counts=inventory_view == "Evidence",
        lightweight_metadata=inventory_view == "Evidence",
    )

    if not rows:
        st.info("No curves match the current filters.")
        st.button(
            "Reset filters",
            key="curves-reset-empty",
            on_click=_reset_curve_filters,
        )
        return None

    best_rigorous = max(int(row.get("rigorous_lower") or 0) for row in rows)
    exact_count = sum(1 for row in rows if row.get("exact_rank") is not None)
    st.caption(
        f"{len(rows):,} curves · best rigorous ≥{best_rigorous} · "
        f"{exact_count:,} exact · select a row to open"
    )
    table = _inventory_table(rows, inventory_view)
    column_config = {}
    if inventory_view == "Evidence":
        column_config = {
            "id": st.column_config.NumberColumn(format="%d"),
            "rank ≥": st.column_config.NumberColumn(format="%d"),
            "exact": st.column_config.NumberColumn(format="%d"),
            "points": st.column_config.NumberColumn(format="%d"),
            "rigorous pts": st.column_config.NumberColumn(format="%d"),
            "rigorous upper": st.column_config.NumberColumn(format="%d"),
            "Nagao": st.column_config.NumberColumn(format="%.3f"),
        }
    elif inventory_view == "Arithmetic":
        column_config = {
            "id": st.column_config.NumberColumn(format="%d"),
            "log N": st.column_config.NumberColumn(format="%.2f"),
            "naive height": st.column_config.NumberColumn(format="%.2f"),
            "Faltings height": st.column_config.NumberColumn(format="%.2f"),
            "log |Δ|": st.column_config.NumberColumn(format="%.2f"),
        }

    selected = selectable_dataframe(
        table,
        rows,
        semantic=f"curves-browse-table-{inventory_view.lower()}",
        id_key="id",
        allow_empty=True,
        height=700,
        width="stretch",
        hide_index=True,
        column_config=column_config,
        localized_integer_columns=(
            "id",
            "rank ≥",
            "exact",
            "points",
            "rigorous pts",
            "rigorous upper",
        )
        if inventory_view == "Evidence"
        else ("id",),
    )
    if selected is not None:
        return int(selected["id"])

    if inventory_view == "Evidence":
        st.caption(
            "Rank and rigorous upper use the reduced evidence ledger. "
            "Quick/heuristic bounds are excluded from the proof view."
        )
        _render_research_landscape(rows)
    elif inventory_view == "Arithmetic":
        st.caption(
            "Arithmetic projection is loaded only for this view and uses "
            "explicit local/reference provenance."
        )
        _render_arithmetic_backfill(db, ctx)
    else:
        st.caption(
            "Catalog absence is source-specific; it is not a global novelty claim."
        )

    return None


def _render_arithmetic_backfill(db, ctx):
    status = arithmetic_backfill_status(db)
    with st.container(border=True):
        section_title(
            "Arithmetic backfill",
            "Fill missing local arithmetic in bounded background batches. "
            "Timeouts and errors remain unresolved.",
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Eligible", int(status["eligible"]))
        c2.metric("Complete", int(status["complete"]))
        c3.metric("Actionable", int(status["actionable"]))
        c4.metric("Blocked", int(status["blocked"]))

        controls, action = st.columns([1.5, 1], gap="medium")
        with controls:
            batch_size = int(
                st.number_input(
                    "Batch curves",
                    min_value=1,
                    max_value=1000,
                    value=25,
                    step=5,
                    key="curves-arithmetic-batch-size-v2",
                    help="Each run advances through missing Curves inventory arithmetic only.",
                )
            )
            retry = st.checkbox(
                "Retry prior timeout/error rows",
                value=False,
                key="curves-arithmetic-retry",
            )
        with action:
            st.caption(
                "Uses bounded core metadata, conductor, size-metric, and torsion stages. "
                "Existing local values are preserved."
            )
            if st.button(
                "Backfill missing arithmetic",
                type="primary",
                width="stretch",
                key="curves-arithmetic-backfill",
                icon=":material/calculate:",
            ):
                py = setting(db, "science_python", ctx.detected_science_python())
                cmd = [
                    py,
                    "-m",
                    "rank42.curve_arithmetic_backfill",
                    "--db",
                    str(ctx.db_path),
                    "--limit",
                    str(batch_size),
                ]
                if retry:
                    cmd.append("--retry-unresolved")
                jid = launch(
                    ctx,
                    db,
                    kind="curve_arithmetic_backfill",
                    label=f"Backfill Curves arithmetic · up to {batch_size}",
                    command=cmd,
                    metadata={
                        "limit": int(batch_size),
                        "retry_unresolved": bool(retry),
                    },
                )
                st.success(f"Started arithmetic backfill job #{jid}.")

        st.caption(
            f"Missing now: core {int(status['missing_core'])} · "
            f"conductor {int(status['missing_conductor'])} · "
            f"size {int(status['missing_size'])} · "
            f"torsion {int(status['missing_torsion'])} · "
            f"timeout stages {int(status['timeout_stages'])}."
        )


def _render_hall_of_fame(db):
    all_rows = _curve_inventory_rows(
        db,
        include_arithmetic=False,
        include_point_counts=False,
    )
    summary = hall_of_fame_summary(all_rows)
    rows = [
        row
        for row in all_rows
        if int(row.get("rigorous_lower") or 0) > 0
    ]
    groups = hall_of_fame_groups(rows, top_per_group=5)

    section_title(
        "Hall of Fame",
        "Strongest stored rigorous curves by exact rational torsion group.",
    )
    if not groups:
        st.info("No positive rigorous-rank curves are available yet.")
        return

    rank_card, ranked_card, torsion_card = st.columns(3, gap="medium")
    with rank_card:
        with st.container(border=True):
            st.markdown("**Rank**")
            left, right = st.columns(2)
            left.metric(
                "Best Rig",
                (
                    f"≥{int(summary['best_rigorous_lower'])}"
                    if summary["best_rigorous_lower"] is not None
                    else "—"
                ),
            )
            right.metric(
                "Best Exact",
                (
                    int(summary["best_exact_rank"])
                    if summary["best_exact_rank"] is not None
                    else "—"
                ),
            )

    with ranked_card:
        with st.container(border=True):
            st.markdown("**Ranked Curves**")
            left, right = st.columns(2)
            left.metric("Rig", int(summary["rigorous_curve_count"]))
            right.metric("Exact", int(summary["exact_curve_count"]))

    with torsion_card:
        with st.container(border=True):
            st.markdown("**Torsion Groups**")
            left, right = st.columns(2)
            left.metric("Stored", int(summary["torsion_group_count"]))
            common = summary["most_common_torsion"]
            right.metric(
                "Most common",
                str(common["torsion"]) if common is not None else "—",
            )
            if common is not None:
                st.caption(
                    f"{int(common['count'])} retained ranked curve(s) in the most common stored torsion group."
                )

    st.caption(
        "Rank uses authoritative rigorous/exact state. Torsion statistics use "
        "locally stored torsion only; unknown torsion is excluded from the hero count. "
        "Nagao remains secondary context in the group tables."
    )

    for index, group in enumerate(groups):
        st.markdown(f"#### {group['torsion']}")
        st.caption(
            f"{group['curve_count']} retained ranked curve(s) · "
            f"best rigorous rank ≥{group['best_rigorous_lower']}"
        )
        records = list(group["rows"])
        table = [
            {
                "curve": int(row["id"]),
                "rank ≥": int(row["rigorous_lower"] or 0),
                "exact": row["exact_rank"],
                "family / t": f"{row['family']} · t={row['parameter']}",
                "Nagao": (
                    round(float(row["score"]), 3)
                    if row["score"] is not None
                    else None
                ),
                "status": row["status"],
            }
            for row in records
        ]
        selected = selectable_dataframe(
            table,
            records,
            semantic=f"curves-hof-{index}",
            id_key="id",
            allow_empty=True,
            height=min(260, 58 + 42 * len(records)),
            width="stretch",
            hide_index=True,
            localized_integer_columns=("curve", "rank ≥", "exact"),
        )
        if selected is not None:
            return int(selected["id"])
    return None


def page(db, ctx):
    title(
        "Curves",
        None,
        "Data",
    )
    render_feature_hook(db, ctx, "curves.after_header")
    ensure_torsion_schema(db)

    selected_id = st.session_state.get("curves_detail_id")
    if selected_id is not None:
        try:
            row = _curve_detail_row(db, int(selected_id))
        except (TypeError, ValueError):
            row = None
        if row is not None:
            _render_curve_detail(db, ctx, row)
            return
        st.session_state.pop("curves_detail_id", None)

    landing = st.empty()
    selected_curve_id = None
    with landing.container():
        section = tabs(
            ["Inventory", "Hall of Fame", "Import"],
            value="Inventory",
            key="curves-section",
            variant="line",
            width="content",
        )
        st.html("<div style='height:.85rem'></div>")
        if section == "Hall of Fame":
            selected_curve_id = _render_hall_of_fame(db)
        elif section == "Import":
            _render_import_curve(db)
        else:
            selected_curve_id = _render_browse(db, ctx)

    if selected_curve_id is not None:
        _open_curve_detail(int(selected_curve_id))
        landing.empty()
        row = _curve_detail_row(db, int(selected_curve_id))
        if row is not None:
            _render_curve_detail(db, ctx, row)

