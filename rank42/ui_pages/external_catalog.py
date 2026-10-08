from __future__ import annotations

import json
import math

import streamlit as st

from rank42.catalog import (
    catalog_status,
    external_torsion_label,
    icarm_torsion_groups,
    list_external_curves,
)
from rank42.feature_hooks import render_feature_hook
from rank42.novelty import LMFDB_COMPLETE_CONDUCTOR, LMFDB_CURVE_BASE, fetch_lmfdb_cached
from rank42.ui_components import button, dataframe, number_input, region, selectbox, tabs, toggle
from .common import launch, setting, title


_ICARM_BEST_METRIC_OPTIONS = {
    "Conductor": "conductor",
    "Discriminant": "disc",
    "Faltings height": "faltings",
    "Naive height": "naive",
}


def _log_abs_integer(value):
    try:
        integer = abs(int(str(value)))
        return math.log(integer) if integer > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _icarm_tab(db, ctx):
    status = catalog_status(db, "icarm")
    if status:
        a, b, c = st.columns(3)
        a.metric("ICARM curves", int(status["curve_count"] or 0))
        b.metric(
            "External best rank",
            f"≥ {int(status['best_rank_lower'])}" if status["best_rank_lower"] is not None else "—",
        )
        c.metric("Last sync", str(status["fetched_at"] or "—")[:19])
    else:
        st.info("ICARM catalog has not been synchronized yet.")

    if button(
        "Sync ICARM catalog",
        semantic="external-catalog-icarm-sync",
        type="primary",
        width="stretch",
    ):
        py = setting(db, "science_python", ctx.detected_science_python())
        cmd = [py, "-m", "rank42.catalog", "--db", ctx.db_path, "sync", "--source", "icarm"]
        jid = launch(ctx, db, kind="catalog_sync", label="Sync ICARM catalog", command=cmd)
        st.success(f"Started catalog sync job #{jid}.")

    torsion_groups = icarm_torsion_groups(db)
    torsion_labels = {"Any": "Any"}
    torsion_labels.update(
        {str(group["key"]): str(group["label"]) for group in torsion_groups}
    )
    torsion_options = list(torsion_labels)
    with region("external-catalog-icarm-filters", border=True):
        c1, c2, c3, c4 = st.columns([0.8, 1.35, 1.2, 0.8], gap="medium")
        with c1:
            minrank = int(
                number_input(
                    "Rank ≥",
                    semantic="external-catalog-icarm-min-rank",
                    min_value=0,
                    value=20,
                    step=1,
                )
            )
        with c2:
            torsion_choice = selectbox(
                "Torsion",
                torsion_options,
                semantic="external-catalog-icarm-torsion",
                format_func=lambda value: torsion_labels[str(value)],
            )
        with c3:
            best_metric_label = selectbox(
                "Best metric",
                list(_ICARM_BEST_METRIC_OPTIONS),
                semantic="external-catalog-icarm-best-metric",
                help="Used when Only best is enabled.",
            )
        with c4:
            only_best = toggle(
                "Only best",
                semantic="external-catalog-icarm-only-best",
                value=False,
                help=(
                    "Keep only the selected-metric frontier: no curve of equal "
                    "or higher rank in the current torsion group has a smaller value."
                ),
            )

        if not torsion_groups:
            st.caption(
                "This synchronized snapshot has no source-provided torsion metadata. "
                "Sync ICARM to load the current leaderboard fields."
            )
        elif only_best:
            scope = (
                "all torsion groups"
                if torsion_choice == "Any"
                else torsion_labels[str(torsion_choice)]
            )
            st.caption(
                f"ICARM best-only rule · {best_metric_label.lower()} · {scope}: "
                "no curve of equal or higher rank has a strictly smaller value; ties remain."
            )

    rows = list_external_curves(
        db,
        source="icarm",
        rank_at_least=minrank,
        torsion=None if torsion_choice == "Any" else str(torsion_choice),
        only_best=bool(only_best),
        best_metric=_ICARM_BEST_METRIC_OPTIONS[best_metric_label],
        limit=500,
    )
    if not rows:
        st.caption("No synchronized ICARM rows match the current filters.")
        return

    st.caption(
        f"{len(rows):,} curves shown"
        + (" · display limit reached" if len(rows) >= 500 else "")
    )

    data = []
    for row in rows:
        log_n = _log_abs_integer(row["conductor"])
        log_disc = _log_abs_integer(row["discriminant"])
        data.append(
            {
                "ICARM id": row["source_id"],
                "rank ≥": row["rank_lower_bound"],
                "torsion": external_torsion_label(row) or "—",
                "log N": round(log_n, 3) if log_n is not None else None,
                "log |Δ|": round(log_disc, 3) if log_disc is not None else None,
                "Faltings": (
                    round(float(row["faltings_height"]), 4)
                    if row["faltings_height"] is not None
                    else None
                ),
                "naive": (
                    round(float(row["naive_height"]), 4)
                    if row["naive_height"] is not None
                    else None
                ),
                "submitter": row["submitter"],
                "updated": row["source_updated_at"],
                "source": row["source_url"],
            }
        )
    dataframe(
        data,
        semantic="external-catalog-icarm-table",
        width="stretch",
        hide_index=True,
        column_config={
            "source": st.column_config.LinkColumn("source", display_text="open"),
        },
    )


def _lmfdb_cache_summary(db):
    rows = db.execute(
        """
        SELECT query_key, payload_json, fetched_at
        FROM catalog_query_cache
        WHERE source='lmfdb' AND query_key LIKE 'conductor:%'
        ORDER BY fetched_at DESC
        """
    ).fetchall()
    cached_curves = 0
    best_rank = None
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "[]")
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        cached_curves += len(payload)
        for rec in payload:
            if not isinstance(rec, dict) or rec.get("rank") is None:
                continue
            try:
                rank = int(rec["rank"])
            except (TypeError, ValueError):
                continue
            best_rank = rank if best_rank is None else max(best_rank, rank)
    return {
        "conductors": len(rows),
        "curves": cached_curves,
        "best_rank": best_rank,
        "last_lookup": rows[0]["fetched_at"] if rows else None,
    }


def _lmfdb_tab(db):
    summary = _lmfdb_cache_summary(db)
    a, b, c, d = st.columns(4)
    a.metric("Cached conductors", int(summary["conductors"]))
    b.metric("Cached LMFDB rows", int(summary["curves"]))
    c.metric("Highest cached rank", summary["best_rank"] if summary["best_rank"] is not None else "—")
    d.metric("Last lookup", str(summary["last_lookup"] or "—")[:19])

    st.caption(
        "LMFDB lookup uses Rank Hunter's existing SQL-first read-only catalog path with a bounded HTTP API "
        "fallback. Results are cached by conductor and remain reference data; they do not alter local rank proof state."
    )

    with region("external-catalog-lmfdb-query", border=True):
        left, middle, right = st.columns([1.1, 1.35, 0.8], gap="medium")
        with left:
            conductor = int(
                number_input(
                    "Conductor",
                    semantic="external-catalog-lmfdb-conductor",
                    min_value=1,
                    value=88024,
                    step=1,
                )
            )
        with middle:
            transport_label = selectbox(
                "Transport",
                ["Auto · SQL → API", "SQL mirror only", "HTTP API only"],
                semantic="external-catalog-lmfdb-transport",
            )
        with right:
            timeout = int(
                number_input(
                    "Timeout (s)",
                    semantic="external-catalog-lmfdb-timeout",
                    min_value=1,
                    max_value=60,
                    value=8,
                    step=1,
                )
            )

        force = toggle(
            "Refresh cached result",
            semantic="external-catalog-lmfdb-force",
            value=False,
            help="Ignore a fresh conductor cache entry and query LMFDB again.",
        )

        transport = {
            "Auto · SQL → API": "auto",
            "SQL mirror only": "sql",
            "HTTP API only": "api",
        }[transport_label]

        if button(
            "Query LMFDB",
            semantic="external-catalog-lmfdb-query-button",
            type="primary",
            width="stretch",
        ):
            try:
                rows, meta = fetch_lmfdb_cached(
                    db,
                    conductor,
                    timeout=timeout,
                    force=force,
                    transport=transport,
                    api_fallback=(transport == "auto"),
                )
            except Exception as exc:
                st.session_state["rh_external_lmfdb_result"] = {
                    "conductor": conductor,
                    "error": repr(exc),
                }
            else:
                st.session_state["rh_external_lmfdb_result"] = {
                    "conductor": conductor,
                    "rows": rows,
                    "meta": meta,
                }

    result = st.session_state.get("rh_external_lmfdb_result")
    if not result:
        st.info("Enter a conductor and query LMFDB. No full LMFDB mirror is imported into Rank Hunter.")
        return

    if result.get("error"):
        st.error(f"LMFDB lookup failed: {result['error']}")
        st.caption("Auto mode falls back to the documented HTTP API when the SQL mirror or driver is unavailable.")
        return

    result_conductor = int(result["conductor"])
    rows = list(result.get("rows") or [])
    meta = dict(result.get("meta") or {})
    transport_used = str(meta.get("transport") or "cache")
    cache_state = str(meta.get("cache") or "—")
    complete = result_conductor <= int(LMFDB_COMPLETE_CONDUCTOR)

    a, b, c = st.columns(3)
    a.metric("Rows at conductor", len(rows))
    b.metric("Transport", transport_used)
    c.metric("Cache", cache_state)
    st.caption(
        f"Conductor {result_conductor:,} · Rank Hunter configured complete-range threshold: "
        f"{int(LMFDB_COMPLETE_CONDUCTOR):,} · {'inside' if complete else 'outside'} that range."
    )

    if not rows:
        st.info(
            "No LMFDB curve rows were returned for this conductor. Catalog absence is reference information, "
            "not by itself proof that a mathematical curve is new."
        )
        return

    table_rows = []
    for rec in rows:
        label = str(rec.get("lmfdb_label") or rec.get("Clabel") or "")
        table_rows.append(
            {
                "LMFDB label": label or "—",
                "Cremona label": rec.get("Clabel") or "—",
                "rank": rec.get("rank"),
                "conductor": rec.get("conductor"),
                "a-invariants": str(rec.get("ainvs") or []),
                "source": (LMFDB_CURVE_BASE + label) if label else None,
            }
        )
    dataframe(
        table_rows,
        semantic="external-catalog-lmfdb-table",
        width="stretch",
        hide_index=True,
        column_config={
            "source": st.column_config.LinkColumn("source", display_text="open"),
        },
    )

    with st.expander("Lookup metadata"):
        st.json(meta)


def page(db, ctx):
    title(
        "Catalogs",
        None,
        "Data",
        icon="public",
    )
    render_feature_hook(db, ctx, "data.catalogs.after_header")

    active = tabs(
        ["ICARM", "LMFDB"],
        value="ICARM",
        key="external-catalog-source",
        variant="line",
        width="content",
    )
    st.html("<div style='height:.85rem'></div>")
    if active == "LMFDB":
        _lmfdb_tab(db)
    else:
        _icarm_tab(db, ctx)
