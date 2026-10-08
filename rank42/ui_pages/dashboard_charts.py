from __future__ import annotations

import html
import math
from fractions import Fraction

import streamlit as st

from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_research_state import curve_research_state_map
from rank42.dashboard_rank_state import dashboard_exact_rank_distribution
from rank42.ui_components import region
from .dashboard_sections import render_torsion_landscape


def _number(value):
    if value in (None, ""):
        return None
    try:
        result = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _dashboard_chart_palette():
    """Return a restrained Axiom-aligned chart palette for the active appearance."""
    palette = st.session_state.get("_rh_theme_palette") or {}
    if not isinstance(palette, dict):
        palette = {}
    appearance = str(st.session_state.get("_rh_appearance") or "light").lower()
    primary = str(palette.get("rh-primary") or "#246BFD")
    primary_deep = str(palette.get("rh-primary-deep") or primary)
    muted = str(palette.get("rh-muted") or "#7D899B")
    muted_2 = str(palette.get("rh-muted-2") or muted)
    card = str(palette.get("rh-card") or "transparent")
    card_2 = str(palette.get("rh-card-2") or card)
    surface_active = str(palette.get("rh-surface-active") or card_2)
    if appearance == "dark":
        yield_domain = [0, 1]
        yield_range = [card_2, primary_deep]
    else:
        yield_domain = [0, 0.5, 1]
        yield_range = [card, surface_active, primary_deep]
    return {
        "appearance": appearance,
        "primary": primary,
        "primary_deep": primary_deep,
        "muted": muted,
        "muted_2": muted_2,
        "card": card,
        "card_2": card_2,
        "surface_active": surface_active,
        "yield_domain": yield_domain,
        "yield_range": yield_range,
    }


def _dashboard_chart_surface(chart):
    """Apply the resolved Rank Hunter appearance to Altair chart chrome."""
    palette = st.session_state.get("_rh_theme_palette") or {}
    if not isinstance(palette, dict):
        palette = {}
    card = str(palette.get("rh-card") or "transparent")
    text = str(palette.get("rh-text") or "#31333f")
    secondary = str(palette.get("rh-text-secondary") or text)
    muted = str(palette.get("rh-muted") or secondary)
    border = str(palette.get("rh-border") or "rgba(127,127,127,.22)")
    return (
        chart.properties(background=card)
        .configure_view(fill=card, stroke=None)
        .configure_axis(
            labelColor=secondary,
            titleColor=text,
            gridColor=border,
            domainColor=border,
            tickColor=border,
        )
        .configure_legend(
            labelColor=secondary,
            titleColor=text,
            strokeColor=border,
        )
        .configure_title(color=text, subtitleColor=muted)
    )


def _parameter_search_rows(db, *, pool_id=None):
    """Return searched numeric a/b candidates for ALL pools or one pool."""
    sql = """
        SELECT c.id,c.pool_id,p.name AS pool_name,
               c.parameter,c.a,c.b,c.curve_id
        FROM candidates c
        JOIN candidate_pools p ON p.id=c.pool_id
        WHERE c.status!='unsearched'
          AND c.a IS NOT NULL
          AND c.b IS NOT NULL
    """
    params = []
    if pool_id is not None:
        sql += " AND c.pool_id=?"
        params.append(int(pool_id))
    sql += " ORDER BY c.pool_id,c.id"
    rows = db.execute(sql, params).fetchall()

    curve_ids = {
        int(row["curve_id"])
        for row in rows
        if row["curve_id"] is not None
    }
    states = curve_research_state_map(db, curve_ids=curve_ids) if curve_ids else {}
    data = []
    for row in rows:
        a = _number(row["a"])
        b = _number(row["b"])
        if a is None or b is None:
            continue
        state = states.get(int(row["curve_id"])) if row["curve_id"] is not None else None
        valid_state = state is not None and not state["rank_inconsistent"]
        exact = state["exact_rank"] if valid_state else None
        lower = int(state["specialization_rigorous_lower"] or 0) if valid_state else 0
        retained = lower > 0
        evidence = (
            f"= {int(exact)}"
            if exact is not None
            else f"≥ {lower}"
            if retained
            else "none retained"
        )
        data.append({
            "Candidate": int(row["id"]),
            "Pool": f'#{int(row["pool_id"])} · {row["pool_name"]}',
            "Parameter": str(row["parameter"]),
            "Parameter a": a,
            "Parameter b": b,
            "Yield": 1 if retained else 0,
            "Rigorous lower": lower,
            "Rank evidence": evidence,
            "Curve": f"#{int(row['curve_id'])}" if row["curve_id"] is not None else "—",
        })
    return data


def _parameter_pool_rows(db, *, limit=200):
    """Return Candidate Pools eligible for parameter-space narrowing."""
    return db.execute(
        """
        SELECT p.id,p.name,p.plugin_id,p.family_spec,
               COUNT(c.id) AS searched_numeric
        FROM candidate_pools p
        JOIN candidates c ON c.pool_id=p.id
        WHERE c.status!='unsearched'
          AND c.a IS NOT NULL
          AND c.b IS NOT NULL
        GROUP BY p.id,p.name,p.plugin_id,p.family_spec
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()


def _dashboard_parameter_scope(db):
    """Default to the old global ALL view, with optional one-pool narrowing."""
    rows = _parameter_pool_rows(db)
    if not rows:
        st.caption(
            "Parameter-space charts need searched candidates with numeric a/b coordinates."
        )
        return {
            "pool_id": None,
            "scope": "all",
            "scope_label": "All candidate pools",
            "searched_numeric": 0,
        }

    by_id = {int(row["id"]): row for row in rows}
    ids = list(by_id)
    options = ["all", *ids]
    current = st.session_state.get("dashboard_chart_parameter_scope", "all")
    if current not in options:
        current = "all"

    selected = st.selectbox(
        "Candidate Pool",
        options,
        index=options.index(current),
        format_func=lambda value: (
            f'All candidate pools · {sum(int(row["searched_numeric"] or 0) for row in rows):,} searched'
            if value == "all"
            else (
                f'#{int(value)} · {by_id[int(value)]["name"]} · '
                f'{by_id[int(value)]["plugin_id"]} · '
                f'{int(by_id[int(value)]["searched_numeric"]):,} searched'
            )
        ),
        key="dashboard-chart-parameter-pool-select",
        label_visibility="collapsed",
    )
    st.session_state["dashboard_chart_parameter_scope"] = selected

    if selected == "all":
        return {
            "pool_id": None,
            "scope": "all",
            "scope_label": "All candidate pools · combined",
            "searched_numeric": sum(
                int(row["searched_numeric"] or 0) for row in rows
            ),
        }

    pool_id = int(selected)
    row = by_id[pool_id]
    return {
        "pool_id": pool_id,
        "scope": "pool",
        "scope_label": f'Pool #{pool_id} · {row["name"]}',
        "searched_numeric": int(row["searched_numeric"] or 0),
    }



def _with_local_density(rows):
    if not rows:
        return []
    bin_count = max(8, min(28, int(math.sqrt(len(rows))) or 8))
    a_values = [row["Parameter a"] for row in rows]
    b_values = [row["Parameter b"] for row in rows]
    a_min, a_max = min(a_values), max(a_values)
    b_min, b_max = min(b_values), max(b_values)

    def bin_index(value, low, high):
        if high <= low:
            return 0
        scaled = (value - low) / (high - low)
        return min(bin_count - 1, max(0, int(scaled * bin_count)))

    counts, bins = {}, []
    for row in rows:
        key = (bin_index(row["Parameter a"], a_min, a_max), bin_index(row["Parameter b"], b_min, b_max))
        bins.append(key)
        counts[key] = counts.get(key, 0) + 1
    max_density = max(counts.values())
    result = []
    for row, key in zip(rows, bins):
        density = counts[key]
        density_score = 0.35 if max_density <= 1 else math.log1p(density) / math.log1p(max_density)
        enriched = dict(row)
        enriched["Local density"] = density
        enriched["Density opacity"] = density_score
        result.append(enriched)
    return result


def _log_discriminant(value):
    if value in (None, ""):
        return None
    try:
        discriminant = abs(int(str(value).strip()))
    except (TypeError, ValueError):
        return None
    if discriminant <= 0:
        return None
    return math.log10(discriminant)


def _arithmetic_source_label(state, field):
    info = (state.get("provenance") or {}).get(field) or {}
    kind = str(info.get("kind") or "missing")
    source = str(info.get("source") or "").strip()
    if kind == "external_reference":
        label = f"{source.upper()} reference" if source else "external reference"
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
    if field in set(state.get("conflicts") or []):
        label += " · conflict"
    return label


def _exceptional_curve_rows(db):
    states = curve_research_state_map(db)
    eligible_ids = [
        int(curve_id)
        for curve_id, state in states.items()
        if not state["rank_inconsistent"]
        and int(state["specialization_rigorous_lower"] or 0) > 0
    ]
    arithmetic = curve_arithmetic_state_map(db, eligible_ids)
    point_counts = {
        int(row["curve_id"]): int(row["independent_points"] or 0)
        for row in db.execute(
            """SELECT curve_id,
                      SUM(CASE WHEN rigorous_independent=1 THEN 1 ELSE 0 END) AS independent_points
               FROM points
               GROUP BY curve_id"""
        ).fetchall()
    }

    data = []
    for curve_id in sorted(eligible_ids):
        state = states[curve_id]
        arithmetic_state = arithmetic[curve_id]
        complexity = _log_discriminant(arithmetic_state["discriminant"])
        if complexity is None:
            continue
        lower = int(state["specialization_rigorous_lower"] or 0)
        exact = state["exact_rank"]
        data.append({
            "Curve": f"#{curve_id}", "Family": state["family"], "Parameter": state["parameter"],
            "log10|Δ|": complexity, "Δ source": _arithmetic_source_label(arithmetic_state, "discriminant"),
            "Rigorous rank evidence": lower,
            "Evidence type": "Exact rank" if exact is not None else "Rigorous lower bound",
            "Rank certificate": f"= {int(exact)}" if exact is not None else f"≥ {lower}",
            "Independent points": point_counts.get(curve_id, 0),
        })
    return data


def _complexity_frontier(rows):
    if not rows:
        return []
    low = min(row["log10|Δ|"] for row in rows)
    high = max(row["log10|Δ|"] for row in rows)
    if high <= low:
        return [{"log10|Δ|": low, "Frontier rigorous lower": max(row["Rigorous rank evidence"] for row in rows)}]
    bin_count = max(6, min(16, int(math.sqrt(len(rows))) or 6))
    width = (high - low) / bin_count
    maxima = {}
    for row in rows:
        index = min(bin_count - 1, max(0, int((row["log10|Δ|"] - low) / width)))
        maxima[index] = max(maxima.get(index, 0), int(row["Rigorous rank evidence"]))
    frontier, data = 0, []
    for index in range(bin_count):
        if index not in maxima:
            continue
        frontier = max(frontier, maxima[index])
        data.append({"log10|Δ|": low + (index + 0.5) * width, "Frontier rigorous lower": frontier})
    return data


def render_landscape_charts(
    db,
    *,
    parameter_pool_id=None,
    parameter_scope_label=None,
):
    import altair as alt
    import pandas as pd

    chart_colors = _dashboard_chart_palette()
    row1_left, row1_right = st.columns(2, gap="medium")
    parameter_rows = _parameter_search_rows(db, pool_id=parameter_pool_id)
    scope_caption = str(parameter_scope_label or "All candidate pools · combined")

    rank_rows = dashboard_exact_rank_distribution(db)
    with row1_left:
        with region("dashboard-chart-rank-distribution", border=False, height="stretch"):
            st.html('<div class="rh-dashboard-chart-title"><strong>Rank Distribution</strong><span>Exact-rank specializations only</span></div>')
            if rank_rows:
                rank_df = pd.DataFrame([{"Rank": int(row["rank_value"]), "Curves": int(row["curve_count"] or 0)} for row in rank_rows])
                chart = alt.Chart(rank_df).mark_bar(
                    cornerRadiusTopLeft=3,
                    cornerRadiusTopRight=3,
                    color=chart_colors["primary"],
                ).encode(
                    x=alt.X("Rank:O", title="Exact rank", sort="ascending"), y=alt.Y("Curves:Q", title="Curves"),
                    tooltip=[alt.Tooltip("Rank:O", title="Exact rank"), alt.Tooltip("Curves:Q", title="Curves", format=",")],
                ).properties(height=225)
                st.altair_chart(
                    _dashboard_chart_surface(chart),
                    width="stretch",
                    theme=None,
                )
            else:
                st.caption("No exact specialization ranks are stored yet.")

    with row1_right:
        with region("dashboard-chart-parameter-yield", border=False, height="stretch"):
            st.html('<div class="rh-dashboard-chart-title"><strong>Parameter-Space Yield Heatmap</strong><span>Share of searched a/b candidates with retained rigorous specialization evidence</span></div>')
            if parameter_rows:
                yield_df = pd.DataFrame(parameter_rows)
                chart = alt.Chart(yield_df).mark_rect().encode(
                    x=alt.X("Parameter a:Q", bin=alt.Bin(maxbins=12), title="Parameter a"),
                    y=alt.Y("Parameter b:Q", bin=alt.Bin(maxbins=12), title="Parameter b"),
                    color=alt.Color(
                        "mean(Yield):Q",
                        title="Yield",
                        scale=alt.Scale(
                            domain=chart_colors["yield_domain"],
                            range=chart_colors["yield_range"],
                        ),
                        legend=alt.Legend(format=".0%"),
                    ),
                    tooltip=[alt.Tooltip("count():Q", title="Searched", format=","), alt.Tooltip("mean(Yield):Q", title="Yield", format=".0%")],
                ).properties(height=225)
                st.altair_chart(
                    _dashboard_chart_surface(chart),
                    width="stretch",
                    theme=None,
                )
            else:
                st.caption("No searched candidates with numeric a/b parameter coordinates are stored yet.")
            st.caption(scope_caption)

    row2_left, row2_right = st.columns(2, gap="medium")
    neighborhood_rows = _with_local_density(parameter_rows)
    with row2_left:
        with region("dashboard-chart-search-progress", border=False, height="stretch"):
            st.html('<div class="rh-dashboard-chart-title"><strong>High-Rank Neighborhood Map</strong><span>Compatible parameter frame · size = rigorous rank evidence · opacity = local density</span></div>')
            if neighborhood_rows:
                neighborhood_df = pd.DataFrame(neighborhood_rows)
                chart = alt.Chart(neighborhood_df).mark_circle(
                    filled=True,
                    color=chart_colors["primary"],
                    stroke=chart_colors["primary_deep"],
                    strokeWidth=0.7,
                ).encode(
                    x=alt.X("Parameter a:Q", title="Parameter a"), y=alt.Y("Parameter b:Q", title="Parameter b"),
                    size=alt.Size("Rigorous lower:Q", title="Rigorous lower", scale=alt.Scale(range=[24, 520])),
                    opacity=alt.Opacity("Density opacity:Q", scale=alt.Scale(domain=[0, 1], range=[0.32, 0.9]), legend=None),
                    tooltip=[alt.Tooltip("Candidate:Q", title="Candidate #", format=","), alt.Tooltip("Pool:N"), alt.Tooltip("Parameter:N"), alt.Tooltip("Parameter a:Q"), alt.Tooltip("Parameter b:Q"), alt.Tooltip("Rank evidence:N"), alt.Tooltip("Local density:Q", title="Local density", format=","), alt.Tooltip("Curve:N")],
                ).properties(height=225)
                st.altair_chart(
                    _dashboard_chart_surface(chart),
                    width="stretch",
                    theme=None,
                )
            else:
                st.caption("No searched candidates with numeric a/b parameter coordinates are stored yet.")
            st.caption(scope_caption)

    exceptional_rows = _exceptional_curve_rows(db)
    frontier_rows = _complexity_frontier(exceptional_rows)
    with row2_right:
        with region("dashboard-chart-root-rank", border=False, height="stretch"):
            st.html('<div class="rh-dashboard-chart-title"><strong>Exceptional Curve Radar</strong><span>log₁₀|Δ| vs rigorous rank evidence · size = rigorously independent stored points</span></div>')
            if exceptional_rows:
                exceptional_df = pd.DataFrame(exceptional_rows)
                points_chart = alt.Chart(exceptional_df).mark_circle(
                    filled=True,
                    opacity=0.62,
                ).encode(
                    x=alt.X("log10|Δ|:Q", title="log₁₀ |Δ|"), y=alt.Y("Rigorous rank evidence:Q", title="Rigorous rank evidence"),
                    size=alt.Size("Independent points:Q", title="Rigorously independent points", scale=alt.Scale(range=[28, 480])),
                    color=alt.Color(
                        "Evidence type:N",
                        title="Evidence type",
                        scale=alt.Scale(
                            domain=["Exact rank", "Rigorous lower bound"],
                            range=[chart_colors["primary"], chart_colors["muted_2"]],
                        ),
                    ),
                    tooltip=[alt.Tooltip("Curve:N"), alt.Tooltip("Family:N"), alt.Tooltip("Parameter:N"), alt.Tooltip("log10|Δ|:Q", title="log₁₀ |Δ|", format=".2f"), alt.Tooltip("Δ source:N"), alt.Tooltip("Rank certificate:N"), alt.Tooltip("Independent points:Q", title="Rigorously independent points", format=",")],
                )
                if frontier_rows:
                    frontier_df = pd.DataFrame(frontier_rows)
                    frontier_chart = alt.Chart(frontier_df).mark_line(
                        strokeWidth=2.4,
                        point=True,
                        color=chart_colors["primary_deep"],
                    ).encode(
                        x=alt.X("log10|Δ|:Q", title="log₁₀ |Δ|"), y=alt.Y("Frontier rigorous lower:Q", title="Rigorous rank evidence"),
                        tooltip=[alt.Tooltip("log10|Δ|:Q", title="Complexity bin", format=".2f"), alt.Tooltip("Frontier rigorous lower:Q", title="Frontier rigorous lower")],
                    )
                    chart = (points_chart + frontier_chart).properties(height=225)
                else:
                    chart = points_chart.properties(height=225)
                st.altair_chart(
                    _dashboard_chart_surface(chart),
                    width="stretch",
                    theme=None,
                )
            else:
                st.caption("No curves have both authoritative discriminant data and rigorous specialization-rank evidence yet.")



def render_research_charts(db):
    """Render Dashboard research charts plus authoritative torsion summaries."""
    with region("dashboard-research-landscape", border=True):
        st.html(
            '<div class="rh-dashboard-section-heading">'
            '<span class="rh-dashboard-section-title">RESEARCH LANDSCAPE</span></div>'
        )
        parameter_scope = _dashboard_parameter_scope(db)
        render_landscape_charts(
            db,
            parameter_pool_id=parameter_scope["pool_id"],
            parameter_scope_label=parameter_scope["scope_label"],
        )
    return render_torsion_landscape(db)
