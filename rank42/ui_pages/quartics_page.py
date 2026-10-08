from __future__ import annotations

import json

import streamlit as st

from rank42.db import connect_existing
from rank42.feature_hooks import render_feature_hook
from rank42.quartic_workbench import (
    covering_command,
    covering_local_command,
    covering_history,
    covering_research_record,
    covering_matrix,
    covering_matrix_view,
    covering_next_attack_plan,
    covering_point_provenance,
    covering_yield_series,
    covering_yield_recap,
    family_quartic_signature_matrix,
    mapped_points_for_covering,
    mapped_points_for_quartic,
    pointed_quartic_command,
    quartic_arithmetic,
    quartic_curve_summary,
    quartic_history,
    quartic_local_evidence,
    quartic_model_comparison,
    quartic_point_inventory,
    quartic_overview_accounting,
    quartic_search_research_record,
    quartic_coverage_chart_data,
    quartic_coverage_density,
    quartic_model_yield_series,
    quartic_model_yield_recap,
    quartic_next_attack_plan,
    point_height_summary,
    quartic_region_overlap,
    quartic_search_coverage,
    raw_quartic_command,
)
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.ui_components import (
    metric_card,
    region,
    selectable_dataframe,
    tabs,
)

from .common import (
    active_curve_selector,
    curve_options,
    launch,
    ratpoints_selector,
    select_row,
    setting,
    title,
)


def _appearance_vega_spec(spec):
    """Apply the resolved Rank Hunter appearance to Quartics chart chrome."""
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


def _themed_vega_lite_chart(*, spec, **kwargs):
    return st.vega_lite_chart(
        spec=_appearance_vega_spec(spec),
        theme=None,
        **kwargs,
    )


def _render_curve_browser(db, rows, wanted):
    """Render Quartics through the shared native active-curve browser."""
    return active_curve_selector(
        db,
        rows,
        wanted=wanted,
        state_prefix="quartics",
        widget_prefix="quartics-curve",
        compatibility_keys=("quartics_curve_id", "analysis_curve_id"),
        latest_limit=50,
    )


def _fmt_int(value):
    if value in (None, ""):
        return "—"
    return f"{int(value):,}"


def _fmt_log_height(value):
    if value is None:
        return "—"
    return f"{float(value):.3f}"


def _fmt_band(low, high):
    if low is None and high is None:
        return "all"
    return (
        f"{_fmt_int(low) if low is not None else '—'}–"
        f"{_fmt_int(high) if high is not None else '—'}"
    )


def _quartic_table(rows):
    return [
        {
            "ID": int(rec["id"]),
            "Type": rec["kind"],
            "Status": rec["status"],
            "H": int(rec["height"]),
            "Denominator": _fmt_band(rec["denominator_low"], rec["denominator_high"]),
            "Quartic hits": int(rec["quartic_points"]),
            "Mapped": int(rec["mapped_points"]),
            "Runtime s": rec["runtime"],
            "Anchor / hole": rec["hole_label"] or "—",
            "Reduced": bool(rec["reduced"]),
            "Map failures": int(rec["map_back_failures"]),
        }
        for rec in rows
    ]


def _covering_table(rows):
    return [
        {
            "ID": int(rec["id"]),
            "Status": rec["status"],
            "Last outcome": rec["outcome"] or "—",
            "H": int(rec["height"] or 0),
            "Timeout s": rec["timeout"],
            "Ratpoints hits": int(rec["ratpoints_hits"]),
            "Mapped": int(rec["mapped_points"]),
            "Runtime s": rec["runtime"],
            "Lattice": rec["lattice_id"] or "—",
            "Hole": rec["hole_id"] or "—",
        }
        for rec in rows
    ]


def _pct(numerator, denominator):
    if not denominator:
        return "—"
    return f"{(100.0 * float(numerator) / float(denominator)):.1f}%"


def _rank_text(curve):
    if curve.get("rank_inconsistent"):
        lower = int(curve.get("rigorous_lower") or 0)
        upper = curve.get("rigorous_upper")
        return f"conflict ≥{lower}" + (f" / ≤{int(upper)}" if upper is not None else "")
    if curve.get("exact_rank") is not None:
        return f"= {int(curve['exact_rank'])}"
    if curve.get("rigorous_lower") is not None:
        return f"≥ {int(curve['rigorous_lower'])}"
    return "unknown"


def _covering_matrix_table(rows):
    return [
        {
            "Covering": f"C{int(rec['id'])}",
            "State": rec["workbench_status"],
            "Local": rec.get("local_state") or "not checked / no local record",
            "Stored status": rec["status"],
            "Searches": int(rec["search_count"]),
            "Search max H": int(rec["max_height"]),
            "Best log₁₀ Hx": (
                None
                if rec.get("best_point_log10_x_height") is None
                else round(float(rec["best_point_log10_x_height"]), 3)
            ),
            "Median log₁₀ Hx": (
                None
                if rec.get("median_point_log10_x_height") is None
                else round(float(rec["median_point_log10_x_height"]), 3)
            ),
            "Quartic hits": int(rec["total_quartic_hits"]),
            "Mapped unique": int(rec["unique_mapped_points"]),
            "New here": int(rec.get("discovered_here_points") or 0),
            "Rediscovered": int(rec.get("rediscovered_points") or 0),
            "Pre-existing": int(rec.get("preexisting_points") or 0),
            "Origin unclear": int(rec.get("origin_unclear_points") or 0),
            "Independent": int(rec["independent_points"]),
            "Last outcome": rec["outcome"] or "—",
        }
        for rec in rows
    ]


def _covering_provenance_table(records):
    return [
        {
            "Point": f"P#{int(rec['point_id'])}",
            "Origin": rec["origin"],
            "Evidence": rec["evidence"],
            "Point created": rec["point_created_at"] or "—",
            "Covering events": int(rec["covering_discovery_events"]),
            "All discovery events": int(rec["discovery_events"]),
            "Source": rec["point"]["source"] or "—",
            "Search ref": rec["point"]["search_ref"] or "—",
        }
        for rec in records
    ]


def _point_height_quality_table(profiles, provenance=None):
    origin_by_id = {
        int(rec["point_id"]): rec["origin"]
        for rec in (provenance or ())
    }
    rows = []
    for profile in profiles or ():
        point = profile["point"]
        point_id = int(profile["point_id"])
        rows.append(
            {
                "Point": f"P#{point_id}",
                "log₁₀ Hx": (
                    None
                    if profile["x_height_log10"] is None
                    else round(float(profile["x_height_log10"]), 3)
                ),
                "Exact Hx": profile["x_height_text"] or "—",
                "Canonical height": (
                    "—"
                    if profile["canonical_height"] is None
                    else f"{float(profile['canonical_height']):.6g}"
                ),
                "State": _point_significance(point),
                "Origin": origin_by_id.get(point_id, "—"),
            }
        )
    return rows


def _point_significance(point):
    if bool(point["rigorous_independent"]):
        return "★ rigorous independent"
    if not bool(point["exact_verified"]):
        return "needs exact verification"
    status = str(point["independence_status"] or "unknown")
    if status == "dependent":
        return "dependent"
    if status == "numerical_novel":
        return "promising — needs proof"
    if status == "inconclusive":
        return "inconclusive — retry"
    return "exact — independence untested"


def _point_rank_meaning(point):
    if bool(point["rigorous_independent"]):
        return "Stored rigorous witness"
    if str(point["independence_status"] or "unknown") == "dependent":
        return "No rank increase"
    return "No rank increase proven"


def _mapped_point_table(points):
    return [
        {
            "Point": f"P#{int(point['id'])}",
            "Significance": _point_significance(point),
            "Exact": "yes" if bool(point["exact_verified"]) else "no",
            "Role": point["role"] or "—",
            "Source": point["source"] or "—",
            "Search ref": point["search_ref"] or "—",
            "log₁₀ Hx": _fmt_log_height(
                point_height_summary([point])["minimum_log10_x_height"]
            ),
            "Rank meaning": _point_rank_meaning(point),
        }
        for point in points
    ]


def _coordinate_table(points):
    return [
        {
            "Point": f"P#{int(point['id'])}",
            "x": point["x"],
            "y": point["y"],
        }
        for point in points
    ]


def _inventory_table(inventory):
    return [
        {
            "Point": f"P#{int(rec['id'])}",
            "Significance": rec["significance"],
            "Exact": "yes" if rec["exact_verified"] else "no",
            "Provenance": ", ".join(rec["provenance"]) or rec["search_ref"],
            "Source": rec["source"],
            "Role": rec["role"],
            "log₁₀ Hx": _fmt_log_height(rec.get("x_height_log10")),
            "Canonical height": (
                "—"
                if rec.get("canonical_height") is None
                else f"{float(rec['canonical_height']):.6g}"
            ),
            "Rank meaning": rec["rank_meaning"],
        }
        for rec in inventory
    ]


def _render_rank_impact(curve, inventory, *, accounting=None, compact=False):
    rigorous = [rec for rec in inventory if rec["rigorous_independent"]]
    unresolved = [
        rec
        for rec in inventory
        if rec["exact_verified"]
        and not rec["rigorous_independent"]
        and rec["independence_status"] in {"unknown", "numerical_novel", "inconclusive"}
    ]

    st.subheader("Rank impact & point discoveries")

    if accounting is None:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current rigorous rank", _rank_text(curve))
        c2.metric("Quartic-associated points", len(inventory))
        c3.metric("Rigorous witnesses", len(rigorous))
        c4.metric("Needs independence work", len(unresolved))
    else:
        funnel = accounting["funnel"]
        mapped_count = int(funnel["mapped_points"])
        tested_count = int(funnel["independence_tested"])
        rigorous_count = int(funnel["rigorous_witnesses"])
        proven_count = int(accounting["proven_quartics_origin_rigorous_witnesses"])

        if mapped_count and rigorous_count == 0:
            st.error(
                f"{mapped_count} mapped → {tested_count} independence-tested → "
                "0 rigorous witnesses. Quartics have not yet added a stored rigorous "
                "witness to this curve's rank proof."
            )
        elif rigorous_count and proven_count == 0:
            st.warning(
                f"{rigorous_count} rigorous witness(es) are currently associated with "
                "Quartics, but immutable provenance proves 0 were originally discovered "
                "by Quartics."
            )
        elif proven_count:
            st.success(
                f"{proven_count} rigorous witness(es) have immutable evidence that "
                "Quartics originally discovered them."
            )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metric_card(
                "Proven Quartics-origin",
                proven_count,
                description=(
                    "Rigorous witnesses whose original discovery by Quartics is "
                    "supported by immutable new-vs-preexisting evidence."
                ),
                key="quartics-impact-proven-origin",
            )
        with c2:
            metric_card(
                "Associated rigorous",
                int(accounting["rigorous_witnesses"]),
                description=(
                    "Current Quartics-associated Point Ledger records carrying the "
                    "rigorous independent flag."
                ),
                key="quartics-impact-associated-rigorous",
            )
        with c3:
            metric_card(
                "Exact unresolved",
                int(accounting["unresolved_points"]),
                description="Exact associated points that still need a decisive independence result.",
                key="quartics-impact-unresolved",
            )
        with c4:
            metric_card(
                "Dependent",
                int(accounting["dependent_points"]),
                description="Exact associated points already persisted as dependent.",
                key="quartics-impact-dependent",
            )

        st.caption(
            f"{int(accounting['associated_points'])} current Quartics-associated point(s). "
            "Association is not treated as historical rank contribution."
        )

        st.markdown("#### Independence status")
        st.markdown(
            f"**Tested {tested_count}** · "
            f"Dependent {int(accounting['dependent_points'])} · "
            f"Numerically novel {int(accounting['numerical_novel_points'])} · "
            f"Inconclusive {int(accounting['inconclusive_points'])} · "
            f"Untested {int(accounting['untested_points'])}"
        )
        latest_independence = accounting.get("latest_independence_updated_at")
        if latest_independence:
            st.caption(
                f"Latest persisted update: {latest_independence} · "
                "Numerical novelty is scheduling evidence; only rigorous witnesses affect proof state."
            )
        else:
            st.caption("No persisted independence-state update is available.")

        st.markdown("#### Coverings → rank funnel")
        st.markdown(
            f"**{int(funnel['coverings'])} coverings** → "
            f"{int(funnel['quartic_hits'])} quartic hits → "
            f"{mapped_count} elliptic points → "
            f"{tested_count} independence-tested → "
            f"**{rigorous_count} rigorous**"
        )
        f1, f2, f3 = st.columns(3)
        f1.metric(
            "Hit → Elliptic",
            _pct(mapped_count, int(funnel["quartic_hits"])),
            help="Descriptive yield only: quartic hits and unique mapped elliptic points are different units.",
        )
        f2.metric("Elliptic → Tested", _pct(tested_count, mapped_count))
        f3.metric("Tested → Rigorous", _pct(rigorous_count, tested_count))
        qualification_loss = max(0, mapped_count - tested_count)
        independence_loss = max(0, tested_count - rigorous_count)
        if tested_count and independence_loss >= qualification_loss:
            message = (
                f"Main loss: independence stage ({tested_count} → {rigorous_count} rigorous)."
            )
            if rigorous_count == 0:
                st.error(message)
            else:
                st.caption(message)
        elif mapped_count:
            st.caption(
                f"Main loss: mapped → independence-tested "
                f"({mapped_count} → {tested_count})."
            )

        heights = accounting["height_summary"]
        if int(heights["x_height_count"] or 0):
            st.markdown("#### Height distribution")
            h1, h2, h3, h4, h5 = st.columns(5)
            h1.metric("Min log₁₀ Hx", _fmt_log_height(heights["minimum_log10_x_height"]))
            h2.metric("p25", _fmt_log_height(heights["p25_log10_x_height"]))
            h3.metric("Median", _fmt_log_height(heights["median_log10_x_height"]))
            h4.metric("p75", _fmt_log_height(heights["p75_log10_x_height"]))
            h5.metric("Max", _fmt_log_height(heights["maximum_log10_x_height"]))

            threshold_counts = accounting.get("height_threshold_counts") or {}
            st.caption(
                f"log₁₀ Hx < 16: **{int(threshold_counts.get(16.0, 0))}** · "
                f"< 18: **{int(threshold_counts.get(18.0, 0))}** · "
                f"< 20: **{int(threshold_counts.get(20.0, 0))}** · "
                f"canonical heights: **{int(heights['canonical_height_count'])}/"
                f"{int(heights['count'])}**. "
                "Canonical/Néron–Tate height is used when explicitly persisted; "
                "log₁₀ Hx is the exact-coordinate proxy for the rest."
            )

        first_activity = accounting.get("first_quartics_activity_at")
        if first_activity:
            st.markdown("#### Before vs now")
            b1, b2, b3 = st.columns(3)
            before = accounting.get("point_ledger_before_first_activity")
            before_text = "—" if before is None else str(int(before))
            b1.metric(
                "Point Ledger",
                f"{before_text} → {int(accounting['point_ledger_now'])}",
            )
            prior_lower = accounting.get("recorded_rank_lower_before_first_activity")
            b2.metric(
                "Recorded pre-Quartics lower",
                "—" if prior_lower is None else f"≥ {int(prior_lower)}",
                help=(
                    "Maximum rigorous lower/exact rank recorded before the first "
                    "persisted Quartics activity."
                ),
            )
            b3.metric("Proven new + rigorous", proven_count)
            st.caption(f"First persisted Quartics activity: {first_activity}")

        top_unresolved = list(accounting.get("top_unresolved") or ())
        if top_unresolved:
            st.markdown("#### Top unresolved candidates")
            ranked = sorted(
                [
                    rec
                    for rec in inventory
                    if rec.get("x_height_log10") is not None
                ],
                key=lambda rec: (
                    float(rec["x_height_log10"]),
                    int(rec["id"]),
                ),
            )
            hx_rank = {
                int(rec["id"]): index
                for index, rec in enumerate(ranked, 1)
            }
            hx_total = len(ranked)
            st.dataframe(
                [
                    {
                        "Point": f"P#{int(rec['id'])}",
                        "Status": rec["significance"],
                        "Height": (
                            round(float(rec["canonical_height"]), 6)
                            if rec.get("canonical_height") is not None
                            else (
                                None
                                if rec.get("x_height_log10") is None
                                else round(float(rec["x_height_log10"]), 3)
                            )
                        ),
                        "Height basis": (
                            "canonical ĥ"
                            if rec.get("canonical_height") is not None
                            else "log₁₀ Hx proxy"
                        ),
                        "Hx rank": (
                            "—"
                            if int(rec["id"]) not in hx_rank
                            else f"{hx_rank[int(rec['id'])]}/{hx_total}"
                        ),
                        "Source": rec["source"],
                        "Provenance": ", ".join(rec["provenance"]) or rec["search_ref"],
                    }
                    for rec in top_unresolved
                ],
                width="stretch",
                hide_index=True,
            )
            a1, a2 = st.columns(2)
            with a1:
                if st.button(
                    f"Send top {len(top_unresolved)} to Independence",
                    key=f"quartics-top-unresolved-independence-{int(curve['id'])}",
                    type="primary",
                    width="stretch",
                ):
                    _handoff_to_independence(
                        int(curve["id"]),
                        [rec["row"] for rec in top_unresolved],
                    )
            with a2:
                if unresolved and st.button(
                    f"Send all {len(unresolved)} unresolved",
                    key=f"quartics-all-unresolved-independence-{int(curve['id'])}",
                    width="stretch",
                ):
                    _handoff_to_independence(
                        int(curve["id"]),
                        [rec["row"] for rec in unresolved],
                    )

        st.info(str(accounting["diagnosis"]))

    if inventory:
        if compact:
            with st.expander(f"Point inventory ({len(inventory)})", expanded=False):
                st.dataframe(
                    _inventory_table(inventory),
                    width="stretch",
                    hide_index=True,
                )
                with st.expander("Show exact coordinates", expanded=False):
                    st.dataframe(
                        _coordinate_table([rec["row"] for rec in inventory]),
                        width="stretch",
                        hide_index=True,
                    )
        else:
            st.dataframe(
                _inventory_table(inventory),
                width="stretch",
                hide_index=True,
            )
            with st.expander("Show exact coordinates", expanded=False):
                st.dataframe(
                    _coordinate_table([rec["row"] for rec in inventory]),
                    width="stretch",
                    hide_index=True,
                )

def _render_quartic_fingerprint(arithmetic, local_evidence, *, title_text="Quartic fingerprint"):
    st.markdown(f"#### {title_text}")
    st.caption(
        f"{arithmetic['fingerprint']} · exact degree {int(arithmetic['degree'])} · "
        f"ratpoints integral scale Y={int(arithmetic['y_scale'])}·y"
    )

    a, b, c, d, e = st.columns(5)
    a.metric("Coeff height", arithmetic["coefficient_height"])
    b.metric("Content", arithmetic["content"])
    c.metric("Primitive", "yes" if arithmetic["primitive"] else "no")
    d.metric("Reduced", "yes" if arithmetic["reduced"] else "no")
    nonsingular = arithmetic["nonsingular_binary_quartic"]
    e.metric(
        "Binary quartic Δ",
        "nonzero" if nonsingular is True else ("zero" if nonsingular is False else "n/a"),
    )

    if arithmetic["I"] is not None:
        st.dataframe(
            [
                {"Invariant": "I", "Exact value": arithmetic["I"]},
                {"Invariant": "J", "Exact value": arithmetic["J"]},
                {
                    "Invariant": "4I³ − J²",
                    "Exact value": arithmetic["invariant_delta"],
                },
                {
                    "Invariant": "polynomial discriminant",
                    "Exact value": arithmetic["polynomial_discriminant"],
                },
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Classical binary-quartic convention: I=12ae−3bd+c², "
            "J=72ace+9bcd−27ad²−27eb²−2c³; the displayed polynomial "
            "discriminant is (4I³−J²)/27."
        )
    else:
        st.caption(
            "I/J are shown only for stored degree-4 models; this record is lower degree."
        )

    st.markdown("##### Local information")
    if local_evidence["recorded"]:
        summary = str(local_evidence["overall"])
        if summary == "everywhere locally soluble":
            st.success(summary)
        elif summary.startswith("obstructed"):
            st.error(summary)
        else:
            st.info(summary)

        if local_evidence["places"]:
            st.dataframe(
                [
                    {
                        "Place": rec["place"],
                        "State": rec["state"],
                        "Recorded value": str(rec["recorded"]),
                    }
                    for rec in local_evidence["places"]
                ],
                width="stretch",
                hide_index=True,
            )
        if local_evidence.get("proof_scope"):
            st.caption(
                f"Proof scope: {local_evidence['proof_scope']} · "
                f"all places checked: {'yes' if local_evidence['all_places_checked'] else 'no'}"
            )
    elif local_evidence.get("attempted"):
        reason = local_evidence.get("unsupported_reason") or "unsupported model"
        st.info(f"Local analysis was attempted but not certified: {reason}.")
    else:
        st.info(
            "No authoritative local-solubility check is persisted for this model. "
            "A zero-hit search is not treated as a local obstruction."
        )

    with st.expander("Exact fingerprint payload", expanded=False):
        st.json(
            {
                "schema": arithmetic["schema"],
                "fingerprint": arithmetic["fingerprint"],
                "sha256": arithmetic["fingerprint_sha256"],
                "coefficients": arithmetic["coefficients"],
                "integer_coefficients": arithmetic["integer_coefficients"],
                "y_scale": arithmetic["y_scale"],
                "local_sources": local_evidence["sources"],
            }
        )


def _fmt_intervals(intervals):
    intervals = list(intervals or ())
    if not intervals:
        return "—"
    return ", ".join(
        str(low) if int(low) == int(high) else f"{int(low)}–{int(high)}"
        for low, high in intervals
    )


def _fmt_seconds_per(value):
    if value is None:
        return "—"
    value = float(value)
    if value < 1:
        return f"{value:.2f}s"
    if value < 100:
        return f"{value:.1f}s"
    return f"{value:.0f}s"


def _model_coverage_table(rows):
    return [
        {
            "Model": rec["fingerprint"],
            "Type": ", ".join(rec["kinds"]),
            "State": rec["state"],
            "Jobs": int(rec["jobs"]),
            "Complete": int(rec["completed_regions"]),
            "Incomplete": int(rec["incomplete_attempts"]),
            "Max H": int(rec["max_height"]),
            "Denominator coverage": _fmt_intervals(rec["denominator_intervals"]),
            "Repeat overlap": f"{100.0 * float(rec['overlap_fraction']):.1f}%",
            "Hits": int(rec["quartic_hits"]),
            "Mapped": int(rec["mapped_points"]),
            "Rigorous": int(rec["rigorous_points"]),
            "s / hit": _fmt_seconds_per(rec["seconds_per_hit"]),
            "s / mapped": _fmt_seconds_per(rec["seconds_per_mapped"]),
        }
        for rec in rows
    ]


def _covering_coverage_table(rows):
    return [
        {
            "Covering": f"C{int(rec['id'])}",
            "State": rec["state"],
            "Attempts": int(rec["attempts"]),
            "Complete": int(rec["completed_regions"]),
            "Incomplete": int(rec["incomplete_attempts"]),
            "Early stop": int(rec["early_stop_attempts"]),
            "Max H": int(rec["max_height"]),
            "Repeat overlap": f"{100.0 * float(rec['overlap_fraction']):.1f}%",
            "Hits": int(rec["quartic_hits"]),
            "Mapped unique": int(rec["mapped_points"]),
            "Rigorous": int(rec["rigorous_points"]),
            "s / hit": _fmt_seconds_per(rec["seconds_per_hit"]),
            "s / mapped": _fmt_seconds_per(rec["seconds_per_mapped"]),
        }
        for rec in rows
    ]


def _render_search_coverage(coverage):
    summary = coverage["summary"]
    with region("quartics-search-coverage", border=True):
        st.subheader("Search coverage & empirical yield")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Exact models", int(summary["models"]))
        c2.metric("Search attempts", int(summary["attempts"]))
        c3.metric("Completed regions", int(summary["completed_regions"]))
        c4.metric("Incomplete", int(summary["incomplete_attempts"]))
        c5.metric(
            "Mean repeat overlap",
            f"{100.0 * float(summary['mean_repeat_overlap']):.1f}%",
        )

        st.caption(
            "Coverage counts only bounded searches known to have completed. Timeouts/errors "
            "do not become covered territory. Repeat overlap is a same-model rectangle proxy "
            "in ratpoints height/denominator coordinates; it is not a percentage of rational "
            "points and never implies exhaustion."
        )

        coverage_view = tabs(
            ["Quartic models", "Exact coverings"],
            value="Quartic models",
            key="quartics-coverage-view",
            variant="line",
        )
        if coverage_view == "Quartic models":
            st.markdown("#### Quartic-model coverage")
            if coverage["models"]:
                st.dataframe(
                    _model_coverage_table(coverage["models"]),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "State labels such as lightly searched, no recent yield, productive, and "
                    "historically productive describe persisted attempts only."
                )
            else:
                st.caption("No persisted quartic-model search regions for this curve.")
        else:
            st.markdown("#### Exact-covering search depth")
            if coverage["coverings"]:
                st.dataframe(
                    _covering_coverage_table(coverage["coverings"]),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "One-point/early-stop attempts contribute empirical yield but are not counted "
                    "as complete bounded coverage."
                )
            else:
                st.caption("No exact-covering search attempts for this curve.")


def _render_attack_guidance(plan):
    evidence = plan["evidence"]
    st.markdown("#### Evidence-backed compute guidance")
    if plan["launch_recommended"]:
        st.info(plan["headline"])
    else:
        st.warning(plan["headline"])

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Completed", int(evidence["completed_regions"]))
    c2.metric("Incomplete", int(evidence["incomplete_attempts"]))
    c3.metric("Recent hits", int(evidence["recent_hits"]))
    c4.metric("Mapped", int(evidence["mapped_points"]))
    c5.metric("Repeat overlap", f"{100.0 * float(evidence['repeat_overlap']):.1f}%")

    st.caption(
        f"Recorded state: {evidence['state']} · local: {evidence['local']} · "
        f"max completed H: {evidence['max_height'] or '—'} · "
        f"runtime observed: {evidence['runtime_seconds']:.1f}s"
    )
    for reason in plan["why"]:
        st.markdown(f"- {reason}")
    st.caption(
        "This ordering allocates compute from persisted evidence. It is not a probability "
        "estimate that a rational or independent point exists."
    )


def _comparison_table(rows):
    return [
        {
            "Model": rec["fingerprint"],
            "Searches": ", ".join(f"S{x}" for x in rec["search_ids"]) or "—",
            "Coverings": ", ".join(f"C{x}" for x in rec["covering_ids"]) or "—",
            "Kinds": ", ".join(rec["kinds"]),
            "Local": rec["local_state"],
            "I": rec["I"] if rec["I"] is not None else "—",
            "J": rec["J"] if rec["J"] is not None else "—",
            "Δ": rec["polynomial_discriminant"] if rec["polynomial_discriminant"] is not None else "—",
            "Jobs": int(rec["search_jobs"]) + int(rec["covering_attempts"]),
            "Complete": int(rec["completed_regions"]),
            "Hits": int(rec["quartic_hits"]),
            "Mapped": int(rec["mapped_points"]),
            "Rigorous": int(rec["rigorous_points"]),
        }
        for rec in rows
    ]


def _render_research_comparison(rows):
    with region("quartics-research-comparison", border=True):
        st.subheader("Exact-model research comparison")
        st.caption(
            "Rows are grouped only when Rank Hunter stores the exact same rational coefficient "
            "vector, identified by the quartic fingerprint. This is exact-model identity, not "
            "a claim of GL₂-equivalence between different models."
        )
        if rows:
            st.dataframe(
                _comparison_table(rows),
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("No quartic models are stored for this curve.")


def _render_provenance_recipe(record, *, title_text):
    st.markdown(f"#### {title_text}")
    st.dataframe(
        [
            {
                "Stage": item["stage"],
                "Record": item["record"],
                "State": item["state"],
                "Count": int(item["count"]),
            }
            for item in record["provenance"]
        ],
        width="stretch",
        hide_index=True,
    )
    with st.expander("Copyable reproduction recipe", expanded=False):
        st.code(
            json.dumps(record["recipe"], indent=2, sort_keys=True),
            language="json",
        )
        st.caption(
            "The recipe preserves exact stored mathematics and provenance. Paths to Python, "
            "the database, and ratpoints are machine-specific and must be supplied on replay."
        )


def _render_search_coverage_map(density, *, show_incomplete=True):
    st.subheader("Search coverage density")
    st.caption(
        "Completed bounded searches are aggregated into log-decade height × denominator cells. "
        "Darker cells mean more completed searches touched that band; blank cells had no completed "
        "search touch them. This is computational coverage, not mathematical exhaustion."
    )
    cells = list(density.get("cells") or ())
    if not cells:
        st.info("No valid quartic search regions match the current filters.")
        return

    summary = density["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Completed", int(summary["completed_searches"]))
    c2.metric("Incomplete", int(summary["incomplete_searches"]))
    c3.metric("Max H", _fmt_int(summary["max_height"]))
    c4.metric(
        "Blank log cells",
        f"{int(summary['blank_cells'])}/{int(summary['total_cells'])}",
        help="Log-decade cells touched by no completed persisted search.",
    )
    if int(summary["densest_searches"]):
        st.caption(
            f"Densest band: {summary['densest_label']} · "
            f"{int(summary['densest_searches'])} completed search(es) touched it."
        )

    heatmap = {
        "data": {"values": cells},
        "mark": {"type": "rect", "cornerRadius": 2},
        "encoding": {
            "x": {
                "field": "denominator_label",
                "type": "ordinal",
                "sort": {"field": "denominator_index", "order": "ascending"},
                "title": "Denominator band",
                "axis": {"labelAngle": 0},
            },
            "y": {
                "field": "height_label",
                "type": "ordinal",
                "sort": {"field": "height_index", "order": "descending"},
                "title": "Search-height band",
            },
            "color": {
                "field": "completed_searches",
                "type": "quantitative",
                "title": "Completed searches",
                "scale": {"scheme": "blues"},
            },
            "tooltip": [
                {"field": "height_label", "type": "nominal", "title": "Height band"},
                {"field": "denominator_label", "type": "nominal", "title": "Denominator band"},
                {"field": "completed_searches", "type": "quantitative", "title": "Completed searches"},
                {"field": "distinct_models", "type": "quantitative", "title": "Exact models"},
                {"field": "hit_bearing_searches", "type": "quantitative", "title": "Searches with hits"},
                {"field": "mapped_result_searches", "type": "quantitative", "title": "Searches with mapped points"},
                {"field": "rigorous_result_searches", "type": "quantitative", "title": "Searches with rigorous witness"},
                {"field": "incomplete_requests", "type": "quantitative", "title": "Incomplete requests"},
            ],
        },
    }

    layers = [heatmap]
    if show_incomplete:
        incomplete_cells = [
            rec for rec in cells if int(rec["incomplete_requests"]) > 0
        ]
        if incomplete_cells:
            layers.append(
                {
                    "data": {"values": incomplete_cells},
                    "mark": {
                        "type": "point",
                        "shape": "diamond",
                        "filled": False,
                        "size": 55,
                        "strokeWidth": 1.2,
                        "opacity": 0.65,
                    },
                    "encoding": {
                        "x": {
                            "field": "denominator_label",
                            "type": "ordinal",
                            "sort": {"field": "denominator_index", "order": "ascending"},
                        },
                        "y": {
                            "field": "height_label",
                            "type": "ordinal",
                            "sort": {"field": "height_index", "order": "descending"},
                        },
                        "tooltip": [
                            {"field": "height_label", "type": "nominal", "title": "Height band"},
                            {"field": "denominator_label", "type": "nominal", "title": "Denominator band"},
                            {"field": "incomplete_requests", "type": "quantitative", "title": "Incomplete requests"},
                        ],
                    },
                }
            )

    markers = list(density.get("markers") or ())
    if markers:
        layers.append(
            {
                "data": {"values": markers},
                "mark": {
                    "type": "point",
                    "filled": True,
                    "size": 70,
                    "stroke": "white",
                    "strokeWidth": 1,
                },
                "encoding": {
                    "x": {
                        "field": "denominator_label",
                        "type": "ordinal",
                        "sort": {"field": "denominator_index", "order": "ascending"},
                    },
                    "y": {
                        "field": "height_label",
                        "type": "ordinal",
                        "sort": {"field": "height_index", "order": "descending"},
                    },
                    "color": {
                        "field": "result_state",
                        "type": "nominal",
                        "title": "Result",
                    },
                    "tooltip": [
                        {"field": "result_state", "type": "nominal", "title": "Best result"},
                        {"field": "searches", "type": "quantitative", "title": "Result-bearing searches"},
                        {"field": "quartic_hits", "type": "quantitative", "title": "Quartic hits"},
                        {"field": "mapped_points", "type": "quantitative", "title": "Mapped points"},
                        {"field": "rigorous_points", "type": "quantitative", "title": "Rigorous witnesses"},
                    ],
                },
            }
        )

    _themed_vega_lite_chart(
        spec={
            "height": 360,
            "layer": layers,
            "config": {
                "view": {"stroke": None},
                "axis": {"grid": False},
            },
        },
        use_container_width=True,
    )
    st.caption(
        "◇ = at least one incomplete request touched the cell. Colored result markers summarize "
        "hit-bearing search frontiers. Blank cells mean no completed persisted search touched that "
        "log band; they are not claims about rational points."
    )


def _render_model_yield_charts(rows):
    rows = list(rows or ())
    if not rows:
        st.caption("No completed same-model search sequence is available for a yield view.")
        return

    recap = quartic_model_yield_recap(rows)
    st.markdown("#### Yield profile")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Max H", _fmt_int(recap["max_height"]))
    c2.metric("Quartic hits", int(recap["hits"]))
    c3.metric("Mapped E(Q)", int(recap["mapped"]))
    c4.metric("Rigorous", int(recap["rigorous"]))
    st.caption(
        f"{recap['state']} · latest step Δ hits {recap['recent_hit_gain']:+d}, "
        f"mapped {recap['recent_mapped_gain']:+d}, rigorous {recap['recent_rigorous_gain']:+d} · "
        f"{int(recap['attempts'])} completed search(es) · {recap['runtime_seconds']:.1f}s observed runtime"
    )

    trend_mark = (
        {"type": "line", "point": {"filled": True, "size": 45}, "strokeWidth": 2}
        if recap["trend_ready"]
        else {"type": "point", "filled": True, "size": 90}
    )
    axis_config = {
        "grid": False,
        "labelFontSize": 11,
        "titleFontSize": 11,
        "domainOpacity": 0.35,
        "tickOpacity": 0.35,
    }

    left, right = st.columns(2)
    with left:
        st.caption("Quartic hits")
        _themed_vega_lite_chart(
            spec={
                "height": 190,
                "data": {"values": rows},
                "mark": trend_mark,
                "encoding": {
                    "x": {
                        "field": "height",
                        "type": "quantitative",
                        "scale": {"type": "log"},
                        "axis": {"format": "~s"},
                        "title": "Search height",
                    },
                    "y": {
                        "field": "cumulative_hits",
                        "type": "quantitative",
                        "title": "Cumulative hits",
                    },
                    "tooltip": [
                        {"field": "height", "type": "quantitative", "title": "Height"},
                        {"field": "cumulative_hits", "type": "quantitative", "title": "Hits"},
                        {"field": "attempts", "type": "quantitative", "title": "Completed searches"},
                        {"field": "cumulative_runtime_seconds", "type": "quantitative", "title": "Runtime s", "format": ".1f"},
                    ],
                },
                "config": {"axis": axis_config, "view": {"stroke": None}},
            },
            use_container_width=True,
        )

    point_rows = []
    for row in rows:
        point_rows.extend(
            [
                {
                    "height": int(row["height"]),
                    "metric": "Mapped E(Q)",
                    "count": int(row["cumulative_mapped"]),
                },
                {
                    "height": int(row["height"]),
                    "metric": "Rigorous",
                    "count": int(row["cumulative_rigorous"]),
                },
            ]
        )
    with right:
        st.caption("Useful elliptic points")
        _themed_vega_lite_chart(
            spec={
                "height": 190,
                "data": {"values": point_rows},
                "mark": trend_mark,
                "encoding": {
                    "x": {
                        "field": "height",
                        "type": "quantitative",
                        "scale": {"type": "log"},
                        "axis": {"format": "~s"},
                        "title": "Search height",
                    },
                    "y": {
                        "field": "count",
                        "type": "quantitative",
                        "title": "Cumulative points",
                    },
                    "color": {
                        "field": "metric",
                        "type": "nominal",
                        "title": None,
                        "legend": {"orient": "bottom", "direction": "horizontal"},
                    },
                    "tooltip": [
                        {"field": "height", "type": "quantitative", "title": "Height"},
                        {"field": "metric", "type": "nominal", "title": "Metric"},
                        {"field": "count", "type": "quantitative", "title": "Count"},
                    ],
                },
                "config": {"axis": axis_config, "view": {"stroke": None}},
            },
            use_container_width=True,
        )

    if not recap["trend_ready"]:
        st.caption(
            "Only one completed height level is available, so the chart shows a point rather than implying a trend."
        )
    else:
        st.caption(
            "Cumulative exact-model history only. Rigorous counts come from persisted Point Ledger proof state."
        )


def _render_covering_yield_chart(rows):
    rows = list(rows or ())
    if not rows:
        st.caption("No covering search-attempt history is available for a yield view.")
        return

    recap = covering_yield_recap(rows)
    st.markdown("#### Covering yield")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Max H", int(recap["max_height"]))
    c2.metric("Quartic hits", int(recap["hits"]))
    c3.metric("Mapped reports", int(recap["mapped"]))
    c4.metric("Attempts", int(recap["attempts"]))
    st.caption(
        f"{recap['state']} · latest step Δ hits {recap['recent_hit_gain']:+d}, "
        f"mapped reports {recap['recent_mapped_gain']:+d} · "
        f"{recap['runtime_seconds']:.1f}s observed runtime"
    )

    long_rows = []
    for row in rows:
        long_rows.extend(
            [
                {
                    "height": int(row["height"]),
                    "metric": "Quartic hits",
                    "count": int(row["cumulative_hits"]),
                },
                {
                    "height": int(row["height"]),
                    "metric": "Mapped reports",
                    "count": int(row["cumulative_mapped_reports"]),
                },
            ]
        )
    trend_mark = (
        {"type": "line", "point": {"filled": True, "size": 45}, "strokeWidth": 2}
        if recap["trend_ready"]
        else {"type": "point", "filled": True, "size": 90}
    )
    _themed_vega_lite_chart(
        spec={
            "height": 190,
            "data": {"values": long_rows},
            "mark": trend_mark,
            "encoding": {
                "x": {
                    "field": "height",
                    "type": "quantitative",
                    "scale": {"type": "log"},
                    "axis": {"format": "~s"},
                    "title": "Search height",
                },
                "y": {
                    "field": "count",
                    "type": "quantitative",
                    "title": "Cumulative result count",
                },
                "color": {
                    "field": "metric",
                    "type": "nominal",
                    "title": None,
                    "legend": {"orient": "bottom", "direction": "horizontal"},
                },
                "tooltip": [
                    {"field": "height", "type": "quantitative", "title": "Height"},
                    {"field": "metric", "type": "nominal", "title": "Metric"},
                    {"field": "count", "type": "quantitative", "title": "Count"},
                ],
            },
            "config": {
                "axis": {
                    "grid": False,
                    "labelFontSize": 11,
                    "titleFontSize": 11,
                    "domainOpacity": 0.35,
                    "tickOpacity": 0.35,
                },
                "view": {"stroke": None},
            },
        },
        use_container_width=True,
    )
    if not recap["trend_ready"]:
        st.caption(
            "Only one configured height is present, so this is a point summary rather than a trend."
        )
    else:
        st.caption(
            "Mapped reports are persisted per-attempt counts; they are not relabeled as unique or rigorous points."
        )


def _render_family_signature_heatmap(signature):
    st.subheader("Family quartic signatures")
    st.caption(
        f"Exploratory arithmetic-feature prevalence across family {signature['family']} by strongest "
        "rigorous lower bound. This is pattern mining, not a causal or rank-prediction claim."
    )
    rows = list(signature.get("rows") or ())
    if not rows:
        st.info("Not enough stored family quartic models with authoritative rank state to build this heatmap.")
        return
    _themed_vega_lite_chart(
        spec={
            "height": 300,
            "data": {"values": rows},
            "layer": [
                {
                    "mark": {"type": "rect"},
                    "encoding": {
                        "x": {
                            "field": "rank_label",
                            "type": "ordinal",
                            "sort": {"field": "rank_lower", "order": "ascending"},
                            "title": "Rigorous rank lower bound",
                        },
                        "y": {
                            "field": "feature",
                            "type": "nominal",
                            "title": "Quartic feature",
                        },
                        "color": {
                            "field": "prevalence_pct",
                            "type": "quantitative",
                            "title": "Models %",
                            "scale": {"domain": [0, 100]},
                        },
                        "tooltip": [
                            {"field": "feature", "type": "nominal", "title": "Feature"},
                            {"field": "rank_label", "type": "nominal", "title": "Rank lower"},
                            {"field": "models_with_feature", "type": "quantitative", "title": "With feature"},
                            {"field": "models_total", "type": "quantitative", "title": "Models"},
                            {"field": "prevalence_pct", "type": "quantitative", "title": "Prevalence %", "format": ".1f"},
                        ],
                    },
                },
                {
                    "mark": {"type": "text", "baseline": "middle"},
                    "encoding": {
                        "x": {
                            "field": "rank_label",
                            "type": "ordinal",
                            "sort": {"field": "rank_lower", "order": "ascending"},
                        },
                        "y": {"field": "feature", "type": "nominal"},
                        "text": {
                            "field": "prevalence_pct",
                            "type": "quantitative",
                            "format": ".0f",
                        },
                    },
                },
            ],
        },
        use_container_width=True,
    )
    note = (
        f"{signature['models']} unique curve/model fingerprints across {signature['curves']} curves "
        f"and {signature['rank_buckets']} rigorous-lower-bound bucket(s)."
    )
    if signature.get("truncated"):
        note += f" Chart input was capped at the latest {signature['max_models']} stored models."
    st.caption(note)


def _render_intelligence_board(curve, summary):
    st.caption(
        f"Curve #{int(curve['id'])} · rigorous rank {_rank_text(curve)} · "
        "persisted Quartics research state"
    )
    cols = st.columns(5)
    cards = (
        (
            "Coverings",
            summary["coverings"],
            "Stored exact coverings for this curve.",
        ),
        (
            "Soluble",
            summary["locally_soluble_coverings"],
            (
                "Coverings whose persisted local evidence reports soluble at every "
                "recorded place; rigorous everywhere-locally-soluble records are included."
            ),
        ),
        (
            "Quartic Hits",
            summary["quartic_hits"],
            "Exact quartic points / ratpoints hits recorded by persisted searches.",
        ),
        (
            "Elliptic Points",
            summary["mapped_points"],
            "Unique Point Ledger records mapped from quartic or covering provenance.",
        ),
        (
            "Independent",
            summary["rigorous_independent_points"],
            "Mapped points already carrying Rank Hunter's rigorous independent flag.",
        ),
    )
    for col, (label, value, description) in zip(cols, cards):
        with col:
            metric_card(
                label,
                value,
                description=description,
                key=f"quartics-board-{label}",
            )

    st.caption(
        f"Local covering records: {int(summary['local_record_coverings'])}/"
        f"{int(summary['coverings'])} · obstructed "
        f"{int(summary['locally_obstructed_coverings'])}"
    )

    st.markdown("#### Search efficacy")
    e1, e2, e3, e4, e5 = st.columns(5)
    efficacy = (
        (
            e1,
            "Search jobs",
            int(summary["search_jobs"]),
            "Persisted quartic searches plus exact-covering search attempts.",
        ),
        (
            e2,
            "Searches with hits",
            f"{int(summary['searches_with_hits'])}/{int(summary['persisted_searches'])}",
            "Persisted quartic searches that produced at least one quartic hit.",
        ),
        (
            e3,
            "Hit → Elliptic",
            _pct(summary["mapped_points"], summary["quartic_hits"]),
            "Unique mapped elliptic points divided by recorded quartic hits. Different units; descriptive yield only.",
        ),
        (
            e4,
            "Elliptic → Independent",
            _pct(summary["rigorous_independent_points"], summary["mapped_points"]),
            "Current rigorous independent associated points divided by unique mapped elliptic points.",
        ),
        (
            e5,
            "Timeout / error",
            int(summary["timeout_or_error"]),
            "Persisted quartic searches ending in timeout or error.",
        ),
    )
    for col, label, value, description in efficacy:
        with col:
            metric_card(
                label,
                value,
                description=description,
                key=f"quartics-efficacy-{label}",
            )


def _handoff_to_independence(curve_id, points):
    ids = [int(rec["id"]) for rec in points]
    if not ids:
        return
    st.session_state["independence_selected_point_ids"] = ids
    st.session_state["independence_point_id"] = ids[0]
    set_active_curve_id(
        st.session_state,
        int(curve_id),
        "independence_curve_id",
    )
    st.session_state["rh_page"] = "Independence"
    st.rerun()


def _render_quartic_detail(db, ctx, curve_id, rec, *, searches=None, coverage=None):
    row = rec["row"]
    metadata = rec["metadata"]
    mapped = mapped_points_for_quartic(db, curve_id, rec["id"])
    arithmetic = quartic_arithmetic(
        json.loads(row["polynomial_json"] or "[]"),
        reduced=bool(rec["reduced"]),
    )
    local_evidence = quartic_local_evidence(metadata, status=rec["status"])

    st.subheader(f"Quartic search #{rec['id']} · {rec['kind']}")
    detail_view = tabs(
        ["Summary", "Arithmetic", "Provenance", "Next Attack"],
        value="Summary",
        key=f"quartics-search-detail-view-{rec['id']}",
        variant="line",
    )

    if detail_view == "Summary":
        with region("quartics-search-detail-summary", border=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Status", rec["status"])
            c2.metric("Height", _fmt_int(rec["height"]))
            c3.metric("Quartic hits", int(rec["quartic_points"]))
            c4.metric("Mapped elliptic points", len(mapped))
            st.caption(
                f"{arithmetic['fingerprint']} · denominator {_fmt_band(rec['denominator_low'], rec['denominator_high'])} · "
                f"runtime {row['runtime'] if row['runtime'] is not None else '—'} s · "
                f"local {local_evidence['overall']}"
            )

            if mapped:
                with st.expander(f"Mapped elliptic points ({len(mapped)})", expanded=False):
                    st.dataframe(
                        _mapped_point_table(mapped),
                        width="stretch",
                        hide_index=True,
                    )
                    with st.expander("Show exact coordinates", expanded=False):
                        st.dataframe(
                            _coordinate_table(mapped),
                            width="stretch",
                            hide_index=True,
                        )
                actionable = [
                    point
                    for point in mapped
                    if bool(point["exact_verified"])
                    and not bool(point["rigorous_independent"])
                    and str(point["independence_status"] or "unknown")
                    in {"unknown", "numerical_novel", "inconclusive"}
                ]
                if actionable and st.button(
                    "Open unresolved mapped points in Independence",
                    key=f"quartics-open-independence-{rec['id']}",
                    width="stretch",
                ):
                    _handoff_to_independence(curve_id, actionable)

            qpoints = db.execute(
                """SELECT * FROM quartic_points
                   WHERE search_id=?
                   ORDER BY id DESC LIMIT 200""",
                (int(rec["id"]),),
            ).fetchall()
            if qpoints:
                with st.expander(f"Exact quartic hits ({len(qpoints)})", expanded=False):
                    st.dataframe(
                        [
                            {
                                "id": int(p["id"]),
                                "x": p["x"],
                                "y": p["y"],
                                "projective": p["projective_json"],
                                "exact": bool(p["exact_verified"]),
                            }
                            for p in qpoints
                        ],
                        width="stretch",
                        hide_index=True,
                    )

    elif detail_view == "Arithmetic":
        with region("quartics-search-detail-arithmetic", border=True):
            _render_quartic_fingerprint(arithmetic, local_evidence)
            with st.expander("Exact search polynomial", expanded=False):
                st.code(str(row["polynomial_json"] or "[]"), language="json")
                if row["integer_polynomial_json"]:
                    st.code(str(row["integer_polynomial_json"]), language="json")
                    st.caption(f"Y scale: {row['y_scale']} · degree: {row['degree']}")
            if metadata:
                with st.expander("Transform / provenance metadata", expanded=False):
                    st.json(metadata)

    elif detail_view == "Provenance":
        with region("quartics-search-detail-provenance", border=True):
            research_record = quartic_search_research_record(db, curve_id, rec)
            _render_provenance_recipe(
                research_record,
                title_text="Provenance & reproduction",
            )

    else:
        backend, ratpoints = ratpoints_selector(
            db,
            key=f"quartics-engine-{rec['id']}",
            label="Point engine",
        )
        py = setting(db, "science_python", ctx.detected_science_python())
        model_coverage = next(
            (
                item
                for item in ((coverage or {}).get("models") or ())
                if item["fingerprint"] == arithmetic["fingerprint"]
            ),
            None,
        )
        attack_plan = quartic_next_attack_plan(
            searches if searches is not None else quartic_history(db, curve_id),
            rec,
            model_coverage=model_coverage,
        )
        suggested = attack_plan["suggested"]

        timeout_key = f"quartics-timeout-{rec['id']}"
        if st.button(
            "Apply suggested setup",
            key=f"quartics-apply-plan-{rec['id']}",
            disabled=not attack_plan["launch_recommended"],
            width="stretch",
        ):
            base_timeout = max(30, int(row["runtime"] or 0) * 2 or 60)
            st.session_state[timeout_key] = max(
                1,
                int(base_timeout * int(suggested.get("timeout_multiplier") or 1)),
            )
            if rec["kind"] == "point-centered":
                factor_value = int(suggested.get("height_factor") or 5)
                if factor_value in {2, 5, 10, 25}:
                    st.session_state[f"quartics-height-factor-{rec['id']}"] = factor_value
                st.session_state[f"quartics-anchors-{rec['id']}"] = int(
                    suggested.get("anchors") or 8
                )
            else:
                factor_value = int(suggested.get("height_factor") or 5)
                if factor_value in {2, 5, 10, 25}:
                    st.session_state[f"quartics-raw-height-factor-{rec['id']}"] = factor_value
                if suggested.get("neighbor_low") is not None:
                    st.session_state[f"quartics-band-low-{rec['id']}"] = int(
                        suggested["neighbor_low"]
                    )
                    st.session_state[f"quartics-band-high-{rec['id']}"] = int(
                        suggested["neighbor_high"]
                    )

        timeout = int(
            st.number_input(
                "Search timeout (s)",
                min_value=1,
                value=max(30, int(row["runtime"] or 0) * 2 or 60),
                step=30,
                key=timeout_key,
            )
        )

        with region("quartics-next-attack", border=True):
            st.subheader("Next geometric attack")
            _render_attack_guidance(attack_plan)
            st.caption(
                "Actions below launch the same persisted quartic / ratpoints / map-back services used by automated search. "
                "Opening this page never runs arithmetic."
            )
            if rec["kind"] == "point-centered":
                factor = int(
                    st.selectbox(
                        "Height widening",
                        [2, 5, 10, 25],
                        index=1,
                        key=f"quartics-height-factor-{rec['id']}",
                    )
                )
                anchors = int(
                    st.number_input(
                        "Anchor portfolio",
                        min_value=1,
                        value=8,
                        step=1,
                        key=f"quartics-anchors-{rec['id']}",
                        help="The selected search's stored anchor vector is preferred when available. Alternate-anchor mode deliberately explores a broader portfolio.",
                    )
                )
                a, b, c = st.columns(3)
                if a.button(
                    "Retry incomplete geometry",
                    key=f"quartics-retry-pointed-{rec['id']}",
                    type="primary" if attack_plan["action"] == "retry_incomplete" else "secondary",
                    width="stretch",
                    disabled=not ratpoints,
                ):
                    cmd = pointed_quartic_command(
                        row,
                        python=py,
                        db_path=ctx.db_path,
                        ratpoints=ratpoints,
                        timeout=timeout,
                        height=int(rec["height"]),
                        anchors=anchors,
                        alternate_anchors=False,
                    )
                    jid = launch(
                        ctx,
                        db,
                        kind="quartic",
                        label=f"Quartics retry curve #{curve_id} · search #{rec['id']}",
                        command=cmd,
                        metadata={
                            "curve_id": int(curve_id),
                            "quartic_search_id": int(rec["id"]),
                            "mode": "pointed_retry",
                            "ratpoints_backend": backend,
                        },
                    )
                    st.success(f"Started Quartics job #{jid}.")
                if b.button(
                    f"Widen height ×{factor}",
                    key=f"quartics-widen-pointed-{rec['id']}",
                    type="primary" if attack_plan["action"] == "widen_height" else "secondary",
                    width="stretch",
                    disabled=not ratpoints,
                ):
                    cmd = pointed_quartic_command(
                        row,
                        python=py,
                        db_path=ctx.db_path,
                        ratpoints=ratpoints,
                        timeout=timeout,
                        height=max(1, int(rec["height"]) * factor),
                        anchors=anchors,
                        alternate_anchors=False,
                    )
                    jid = launch(
                        ctx,
                        db,
                        kind="quartic",
                        label=f"Quartics widen curve #{curve_id} · search #{rec['id']}",
                        command=cmd,
                        metadata={
                            "curve_id": int(curve_id),
                            "quartic_search_id": int(rec["id"]),
                            "mode": "pointed_widen",
                            "height_factor": factor,
                            "ratpoints_backend": backend,
                        },
                    )
                    st.success(f"Started Quartics job #{jid}.")
                if c.button(
                    "Try alternate anchors",
                    key=f"quartics-alt-pointed-{rec['id']}",
                    type="primary" if attack_plan["action"] == "alternate_anchors" else "secondary",
                    width="stretch",
                    disabled=not ratpoints,
                ):
                    cmd = pointed_quartic_command(
                        row,
                        python=py,
                        db_path=ctx.db_path,
                        ratpoints=ratpoints,
                        timeout=timeout,
                        height=int(rec["height"]),
                        anchors=max(anchors, 8),
                        alternate_anchors=True,
                    )
                    jid = launch(
                        ctx,
                        db,
                        kind="quartic",
                        label=f"Quartics alternate anchors curve #{curve_id}",
                        command=cmd,
                        metadata={
                            "curve_id": int(curve_id),
                            "quartic_search_id": int(rec["id"]),
                            "mode": "pointed_alternate_anchors",
                            "ratpoints_backend": backend,
                        },
                    )
                    st.success(f"Started Quartics job #{jid}.")
            else:
                factor = int(
                    st.selectbox(
                        "Height widening",
                        [2, 5, 10, 25],
                        index=1,
                        key=f"quartics-raw-height-factor-{rec['id']}",
                    )
                )
                a, b = st.columns(2)
                if a.button(
                    "Retry same model",
                    key=f"quartics-retry-raw-{rec['id']}",
                    type="primary" if attack_plan["action"] == "retry_incomplete" else "secondary",
                    width="stretch",
                    disabled=not ratpoints,
                ):
                    cmd = raw_quartic_command(
                        row,
                        python=py,
                        db_path=ctx.db_path,
                        ratpoints=ratpoints,
                        timeout=timeout,
                        force=True,
                    )
                    jid = launch(
                        ctx,
                        db,
                        kind="quartic",
                        label=f"Quartics retry search #{rec['id']}",
                        command=cmd,
                        metadata={
                            "curve_id": int(curve_id),
                            "quartic_search_id": int(rec["id"]),
                            "mode": "raw_retry",
                            "ratpoints_backend": backend,
                        },
                    )
                    st.success(f"Started Quartics job #{jid}.")
                if b.button(
                    f"Widen height ×{factor}",
                    key=f"quartics-widen-raw-{rec['id']}",
                    type="primary" if attack_plan["action"] == "widen_height" else "secondary",
                    width="stretch",
                    disabled=not ratpoints,
                ):
                    cmd = raw_quartic_command(
                        row,
                        python=py,
                        db_path=ctx.db_path,
                        ratpoints=ratpoints,
                        timeout=timeout,
                        height=max(1, int(rec["height"]) * factor),
                    )
                    jid = launch(
                        ctx,
                        db,
                        kind="quartic",
                        label=f"Quartics widen search #{rec['id']}",
                        command=cmd,
                        metadata={
                            "curve_id": int(curve_id),
                            "quartic_search_id": int(rec["id"]),
                            "mode": "raw_widen",
                            "height_factor": factor,
                            "ratpoints_backend": backend,
                        },
                    )
                    st.success(f"Started Quartics job #{jid}.")

                low_default = int(rec["denominator_low"] or 1)
                high_default = int(rec["denominator_high"] or max(low_default, 10))
                d1, d2, d3 = st.columns(3)
                next_low = int(
                    d1.number_input(
                        "Neighbor band low",
                        min_value=1,
                        value=max(1, high_default + 1),
                        step=1,
                        key=f"quartics-band-low-{rec['id']}",
                    )
                )
                next_high = int(
                    d2.number_input(
                        "Neighbor band high",
                        min_value=next_low,
                        value=max(next_low, high_default + max(10, high_default - low_default + 1)),
                        step=1,
                        key=f"quartics-band-high-{rec['id']}",
                    )
                )
                proposed_overlap = quartic_region_overlap(
                    searches if searches is not None else quartic_history(db, curve_id),
                    rec,
                    height=int(rec["height"]),
                    denominator_low=next_low,
                    denominator_high=next_high,
                )
                if proposed_overlap["valid"]:
                    d3.metric(
                        "Prior overlap",
                        f"{100.0 * float(proposed_overlap['overlap_fraction']):.1f}%",
                        help=(
                            "Fraction of this proposed same-model ratpoints rectangle already "
                            "covered by completed persisted searches. This is a search-coordinate "
                            "proxy, not a rational-point probability."
                        ),
                    )
                else:
                    d3.metric(
                        "Prior overlap",
                        "n/a",
                        help="This denominator band lies outside the current ratpoints height ceiling.",
                    )
                if d3.button(
                    "Search neighboring band",
                    key=f"quartics-band-run-{rec['id']}",
                    type="primary" if attack_plan["action"] == "neighbor_band" else "secondary",
                    width="stretch",
                    disabled=not ratpoints,
                ):
                    cmd = raw_quartic_command(
                        row,
                        python=py,
                        db_path=ctx.db_path,
                        ratpoints=ratpoints,
                        timeout=timeout,
                        denominator_low=next_low,
                        denominator_high=next_high,
                    )
                    jid = launch(
                        ctx,
                        db,
                        kind="quartic",
                        label=f"Quartics denominator band {next_low}–{next_high}",
                        command=cmd,
                        metadata={
                            "curve_id": int(curve_id),
                            "quartic_search_id": int(rec["id"]),
                            "mode": "neighbor_denominator_band",
                            "denominator_low": next_low,
                            "denominator_high": next_high,
                            "ratpoints_backend": backend,
                        },
                    )
                    st.success(f"Started Quartics job #{jid}.")



def _render_covering_detail(db, ctx, curve_id, rec, *, coverage=None):
    row = rec["row"]
    point_provenance = list(
        rec.get("point_provenance")
        or covering_point_provenance(db, curve_id, rec)
    )
    mapped = [item["point"] for item in point_provenance]
    search_count = int(rec.get("search_count", 0) or 0)
    max_height = int(rec.get("max_height", rec["height"] or 0) or 0)
    total_hits = int(rec.get("total_quartic_hits", rec["ratpoints_hits"] or 0) or 0)
    unique_mapped = int(rec.get("unique_mapped_points", len(mapped)) or 0)
    independent = int(
        rec.get(
            "independent_points",
            sum(bool(point["rigorous_independent"]) for point in mapped),
        )
        or 0
    )
    discovered_here = int(rec.get("discovered_here_points") or 0)
    rediscovered = int(rec.get("rediscovered_points") or 0)
    preexisting = int(rec.get("preexisting_points") or 0)
    origin_unclear = int(rec.get("origin_unclear_points") or 0)
    origin_resolved = int(rec.get("origin_resolved_points") or 0)
    height_summary = rec.get("point_height_summary") or point_height_summary(mapped)
    workbench_status = rec.get("workbench_status") or rec["status"]
    quartic_data = json.loads(row["quartic_json"] or "{}")
    arithmetic = quartic_arithmetic(
        quartic_data.get("coefficients") or [],
        reduced=bool(
            rec["metadata"].get("reduced")
            or rec["metadata"].get("reduction")
        ),
    )
    local_evidence = rec.get("local_evidence") or quartic_local_evidence(
        rec["metadata"],
        status=rec["status"],
    )

    st.subheader(f"Selected covering C{int(rec['id'])}")
    st.caption(
        f"{workbench_status} · {arithmetic['fingerprint']} · "
        f"lattice {rec['lattice_id'] or '—'} · hole {rec['hole_id'] or '—'}"
    )
    detail_view = tabs(
        ["Summary", "Arithmetic", "Provenance", "Next Attack"],
        value="Summary",
        key=f"quartics-covering-detail-view-{rec['id']}",
        variant="line",
    )

    if detail_view == "Summary":
        with region("quartics-covering-detail-summary", border=True):
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Searches", search_count)
            c2.metric("Search max H", _fmt_int(max_height) if max_height else "—")
            c3.metric("Quartic hits", total_hits)
            c4.metric("Mapped unique", unique_mapped)
            c5.metric("Independent", independent)

            if unique_mapped:
                p1, p2, p3, p4, p5 = st.columns(5)
                p1.metric("New here", discovered_here)
                p2.metric("Rediscovered", rediscovered)
                p3.metric("Pre-existing", preexisting)
                p4.metric("Origin unclear", origin_unclear)
                p5.metric("Origin resolved", f"{origin_resolved}/{unique_mapped}")

            if search_count == 0 and unique_mapped:
                st.warning(
                    f"{unique_mapped} Point Ledger record(s) are currently associated with "
                    f"C{int(rec['id'])}, but no persisted covering-search attempt supports "
                    "original discovery by this covering. "
                    f"Proven new here: {discovered_here}; rediscovered: {rediscovered}; "
                    f"pre-existing: {preexisting}; origin unclear: {origin_unclear}."
                )
            elif independent:
                st.success(
                    f"This covering has {independent} mapped point(s) already stored as rigorous independent witnesses."
                )
            elif unique_mapped:
                st.info(
                    "This covering currently has associated elliptic points, but none is stored "
                    "as a rigorous independent witness. Point-origin accounting above distinguishes "
                    "discovery from later association."
                )
            elif total_hits:
                st.warning(
                    "This covering has quartic hits but no unique mapped elliptic point recorded yet."
                )
            elif search_count == 0:
                st.warning("This exact covering has not been searched yet.")
            elif str(rec["outcome"] or "") in {"timeout", "error"}:
                st.warning(
                    "The latest covering search was incomplete; zero yield here is not exhaustion."
                )
            else:
                st.caption(
                    "Searches are recorded with no mapped elliptic point yet. Finite search coverage is not exhaustion."
                )

            st.caption(
                f"Latest outcome: {rec['outcome'] or '—'} · last search H: "
                f"{_fmt_int(rec['height']) if rec['height'] else '—'} · "
                f"runtime: {rec['runtime'] if rec['runtime'] is not None else '—'} s · "
                f"local: {local_evidence['overall']}"
            )

            if int(height_summary.get("x_height_count") or 0):
                st.markdown("##### Point height quality")
                h1, h2, h3, h4, h5 = st.columns(5)
                h1.metric("Best log₁₀ Hx", _fmt_log_height(height_summary["minimum_log10_x_height"]))
                h2.metric("p25", _fmt_log_height(height_summary["p25_log10_x_height"]))
                h3.metric("Median", _fmt_log_height(height_summary["median_log10_x_height"]))
                h4.metric("p75", _fmt_log_height(height_summary["p75_log10_x_height"]))
                h5.metric("Largest", _fmt_log_height(height_summary["maximum_log10_x_height"]))
                st.caption(
                    "Point quality uses the exact rational x-coordinate projective height "
                    "Hx=max(|numerator(x)|, denominator(x)); log₁₀ Hx is displayed so large "
                    "coordinates remain readable. Lower is smaller. This is not canonical/Néron–Tate height."
                )
                st.dataframe(
                    _point_height_quality_table(
                        height_summary["best_points"],
                        point_provenance,
                    ),
                    width="stretch",
                    hide_index=True,
                )
                if height_summary["best_unresolved_points"]:
                    best_unresolved = height_summary["best_unresolved_points"][0]
                    st.caption(
                        f"Lowest-height unresolved candidate: P#{int(best_unresolved['point_id'])} · "
                        f"log₁₀ Hx={_fmt_log_height(best_unresolved['x_height_log10'])}."
                    )

            _render_covering_yield_chart(
                covering_yield_series(db, int(rec["id"]))
            )

            if mapped:
                with st.expander(f"Associated points ({len(mapped)})", expanded=False):
                    st.dataframe(
                        _covering_provenance_table(point_provenance),
                        width="stretch",
                        hide_index=True,
                    )
                    with st.expander("Show exact coordinates", expanded=False):
                        st.dataframe(
                            _coordinate_table(mapped),
                            width="stretch",
                            hide_index=True,
                        )
                actionable = [
                    point
                    for point in mapped
                    if bool(point["exact_verified"])
                    and not bool(point["rigorous_independent"])
                    and str(point["independence_status"] or "unknown")
                    in {"unknown", "numerical_novel", "inconclusive"}
                ]
                if actionable and st.button(
                    "Open unresolved covering points in Independence",
                    key=f"quartics-covering-independence-{rec['id']}",
                    width="stretch",
                ):
                    _handoff_to_independence(curve_id, actionable)

    elif detail_view == "Arithmetic":
        with region("quartics-covering-detail-arithmetic", border=True):
            _render_quartic_fingerprint(
                arithmetic,
                local_evidence,
                title_text="Exact covering fingerprint",
            )
            with st.expander("Exact covering quartic and map", expanded=False):
                st.code(str(row["quartic_json"] or "{}"), language="json")
                st.code(
                    json.dumps({"x": row["map_x"], "y": row["map_y"]}, indent=2),
                    language="json",
                )
            if rec["metadata"]:
                with st.expander("Covering metadata", expanded=False):
                    st.json(rec["metadata"])

    elif detail_view == "Provenance":
        with region("quartics-covering-detail-provenance", border=True):
            if point_provenance:
                st.markdown("##### Point origin accounting")
                st.dataframe(
                    _covering_provenance_table(point_provenance),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "Original discovery is credited only when immutable point-discovery evidence "
                    "supports it. Current Point Ledger attribution alone is not treated as proof "
                    "that this covering originally found the point."
                )
            research_record = covering_research_record(db, curve_id, rec)
            _render_provenance_recipe(
                research_record,
                title_text="Covering provenance & reproduction",
            )

    else:
        covering_coverage = next(
            (
                item
                for item in ((coverage or {}).get("coverings") or ())
                if int(item["id"]) == int(rec["id"])
            ),
            None,
        )
        attack_plan = covering_next_attack_plan(rec, coverage=covering_coverage)
        suggested = attack_plan["suggested"]

        with region("quartics-covering-next-attack", border=True):
            st.subheader("Next covering attack")
            _render_attack_guidance(attack_plan)

        backend, ratpoints = ratpoints_selector(
            db,
            key=f"quartics-covering-engine-{rec['id']}",
            label="Covering point engine",
        )
        default_height = max(1, int(rec["height"] or 100000))
        default_timeout = max(30, int(rec["timeout"] or 60))
        height_key = f"quartics-covering-height-{rec['id']}"
        timeout_key = f"quartics-covering-timeout-{rec['id']}"
        one_key = f"quartics-covering-one-{rec['id']}"
        if st.button(
            "Apply suggested covering setup",
            key=f"quartics-covering-apply-plan-{rec['id']}",
            disabled=not attack_plan["launch_recommended"],
            width="stretch",
        ):
            st.session_state[height_key] = int(suggested.get("height") or default_height)
            st.session_state[timeout_key] = int(suggested.get("timeout") or default_timeout)
            st.session_state[one_key] = bool(suggested.get("one_point", False))

        a, b, c = st.columns(3)
        height = int(
            a.number_input(
                "Covering height",
                min_value=1,
                value=default_height,
                step=max(1, default_height // 10),
                key=height_key,
            )
        )
        timeout = int(
            b.number_input(
                "Covering timeout (s)",
                min_value=1,
                value=default_timeout,
                step=30,
                key=timeout_key,
            )
        )
        one_point = c.toggle(
            "Stop after one quartic hit",
            value=False,
            key=one_key,
        )
        if st.button(
            "Retry / widen this covering",
            key=f"quartics-covering-run-{rec['id']}",
            type="primary" if attack_plan["launch_recommended"] else "secondary",
            width="stretch",
            disabled=not ratpoints,
        ):
            py = setting(db, "science_python", ctx.detected_science_python())
            cmd = covering_command(
                row,
                python=py,
                db_path=ctx.db_path,
                ratpoints=ratpoints,
                timeout=timeout,
                height=height,
                one_point=one_point,
            )
            jid = launch(
                ctx,
                db,
                kind="quartic",
                label=f"Quartics covering #{rec['id']} curve #{curve_id}",
                command=cmd,
                metadata={
                    "curve_id": int(curve_id),
                    "covering_id": int(rec["id"]),
                    "mode": "covering_retry",
                    "height": height,
                    "ratpoints_backend": backend,
                },
            )
            st.success(f"Started Quartics covering job #{jid}.")



@st.fragment
def _render_covering_selector_fragment(ctx, curve_id, matrix, coverage):
    """Keep covering selection/detail interactions off the full-page rerun path.

    Fragment reruns outlive the full-page SQLite connection, so any detail work
    that needs the database must open its own short-lived connection.
    """
    f1, f2 = st.columns([1, 1])
    show_choice = f1.selectbox(
        "Show",
        [
            "All",
            "Productive",
            "Has unresolved candidates",
            "Local obstruction",
            "Local evidence recorded",
            "Provenance complete",
            "Provenance incomplete",
            "Search attempted",
            "Never searched",
        ],
        key=f"quartics-covering-show-{curve_id}",
    )
    order_choice = f2.selectbox(
        "Order by",
        [
            "Default",
            "Most new points",
            "Most currently mapped",
            "Most rigorous witnesses",
            "Lowest best point height",
            "Lowest median point height",
        ],
        key=f"quartics-covering-order-{curve_id}",
    )
    matrix_view = list(
        covering_matrix_view(
            matrix,
            show=show_choice,
            order_by=order_choice,
        )
    )
    if not matrix_view:
        st.info("No coverings match this view.")
        return

    selected_covering = selectable_dataframe(
        _covering_matrix_table(matrix_view),
        matrix_view,
        semantic=f"quartics-covering-matrix-{curve_id}",
        selected_id=st.session_state.get(
            f"quartics_selected_covering_{curve_id}"
        ),
        id_key="id",
        selection_state_key=f"quartics_selected_covering_{curve_id}",
        localized_integer_columns=(
            "Searches",
            "Search max H",
            "Quartic hits",
            "Mapped unique",
            "New here",
            "Rediscovered",
            "Pre-existing",
            "Origin unclear",
            "Independent",
        ),
    )
    if selected_covering is not None:
        live_db = connect_existing(ctx.db_path)
        try:
            _render_covering_detail(
                live_db,
                ctx,
                curve_id,
                selected_covering,
                coverage=coverage,
            )
        finally:
            live_db.close()


def page(db, ctx):
    title("Quartics")

    rows = curve_options(db)
    if not rows:
        st.info("No curves yet.")
        return

    wanted = get_active_curve_id(st.session_state, "quartics_curve_id", "analysis_curve_id")
    row = _render_curve_browser(db, rows, wanted)
    if row is None:
        return
    curve_id = int(row["id"])
    set_active_curve_id(st.session_state, curve_id, "quartics_curve_id")

    workspace = tabs(
        ["Overview", "Coverings", "Search", "Research", "Audit"],
        value="Overview",
        key="quartics-workspace",
        variant="page",
        width="stretch",
    )
    st.html("<div style='height:.45rem' aria-hidden='true'></div>")
    render_feature_hook(db, ctx, "analysis.quartics.after_header")

    searches = quartic_history(db, curve_id)
    coverings = covering_history(db, curve_id)

    if workspace == "Overview":
        summary = quartic_curve_summary(
            db,
            curve_id,
            searches=searches,
            coverings=coverings,
        )
        point_inventory = quartic_point_inventory(
            db,
            curve_id,
            searches=searches,
            coverings=coverings,
        )
        accounting = quartic_overview_accounting(
            db,
            curve_id,
            inventory=point_inventory,
            summary=summary,
            searches=searches,
            coverings=coverings,
        )
        _render_intelligence_board(row, summary)
        _render_rank_impact(
            row,
            point_inventory,
            accounting=accounting,
            compact=True,
        )

    elif workspace == "Coverings":
        matrix = covering_matrix(db, curve_id, coverings=coverings)
        coverage = quartic_search_coverage(
            db,
            curve_id,
            searches=searches,
            coverings=coverings,
        )
        st.subheader("Covering matrix")
        st.caption(
            "Select one exact covering to inspect. Detail is split into Summary, Arithmetic, "
            "Provenance, and Next Attack so only one layer is open at a time."
        )
        unchecked_local = sum(
            not bool((rec.get("local_evidence") or {}).get("recorded"))
            and not bool((rec.get("local_evidence") or {}).get("attempted"))
            for rec in matrix
        )
        if matrix:
            local_label = (
                f"Run local checks ({unchecked_local} without records)"
                if unchecked_local
                else "Refresh local checks"
            )
            if st.button(
                local_label,
                key=f"quartics-covering-local-{curve_id}",
                width="stretch",
            ):
                py = setting(db, "science_python", ctx.detected_science_python())
                cmd = covering_local_command(
                    python=py,
                    db_path=ctx.db_path,
                    curve_id=curve_id,
                    timeout=30,
                    max_coverings=max(1, len(coverings)),
                    base_height=10000,
                )
                jid = launch(
                    ctx,
                    db,
                    kind="quartic",
                    label=f"Quartics local checks curve #{curve_id}",
                    command=cmd,
                    metadata={
                        "curve_id": int(curve_id),
                        "mode": "covering_local_checks",
                        "coverings_requested": len(coverings),
                    },
                )
                st.success(f"Started covering local-check job #{jid}.")
        if not matrix:
            st.info("No stored exact coverings for this curve.")
        else:
            _render_covering_selector_fragment(
                ctx,
                curve_id,
                matrix,
                coverage,
            )


    elif workspace == "Search":
        coverage = quartic_search_coverage(
            db,
            curve_id,
            searches=searches,
            coverings=coverings,
        )
        search_view = tabs(
            ["Coverage Map", "Yield & Search", "Coverage Table"],
            value="Coverage Map",
            key=f"quartics-search-view-{curve_id}",
            variant="line",
        )

        if search_view == "Coverage Map":
            coverage_rows = quartic_coverage_chart_data(
                db,
                curve_id,
                searches=searches,
            )
            fingerprint_options = sorted(
                {str(rec["fingerprint"]) for rec in coverage_rows}
            )
            kind_options = sorted({str(rec["kind"]) for rec in coverage_rows})
            f1, f2, f3 = st.columns([2, 1, 1])
            fingerprint_choice = f1.selectbox(
                "Exact model",
                ["All models", *fingerprint_options],
                key=f"quartics-coverage-model-{curve_id}",
            )
            kind_choice = f2.selectbox(
                "Model type",
                ["All types", *kind_options],
                key=f"quartics-coverage-kind-{curve_id}",
            )
            show_incomplete = f3.toggle(
                "Incomplete",
                value=True,
                key=f"quartics-coverage-incomplete-{curve_id}",
                help="Overlay cells touched by timeout/error/incomplete requested searches.",
            )
            density = quartic_coverage_density(
                coverage_rows,
                fingerprint=(
                    None if fingerprint_choice == "All models" else fingerprint_choice
                ),
                kind=None if kind_choice == "All types" else kind_choice,
            )
            _render_search_coverage_map(
                density,
                show_incomplete=show_incomplete,
            )

        elif search_view == "Coverage Table":
            _render_search_coverage(coverage)

        else:
            st.subheader("Quartic search workbench")
            if not searches:
                st.info(
                    "No persisted quartic search exists for this curve yet. Run a Search/Pipeline "
                    "geometric attack first; this page does not invent a second quartic generator."
                )
            else:
                selected_search = select_row(
                    "Inspect persisted search",
                    searches,
                    index=0,
                    key=f"quartics-search-inspect-{curve_id}",
                    format_func=lambda rec: (
                        f"#{int(rec['id'])} · {rec['kind']} · {rec['status']} · "
                        f"H={_fmt_int(rec['height'])} · hits={_fmt_int(rec['quartic_points'])} · "
                        f"mapped={_fmt_int(rec['mapped_points'])}"
                    ),
                )
                yield_rows = quartic_model_yield_series(
                    db,
                    curve_id,
                    selected_search,
                    searches=searches,
                )
                _render_model_yield_charts(yield_rows)
                _render_quartic_detail(
                    db,
                    ctx,
                    curve_id,
                    selected_search,
                    searches=searches,
                    coverage=coverage,
                )

    elif workspace == "Research":
        research_view = tabs(
            ["Family signatures", "Exact-model comparison"],
            value="Family signatures",
            key=f"quartics-research-view-{curve_id}",
            variant="line",
        )
        if research_view == "Family signatures":
            signature = family_quartic_signature_matrix(
                db,
                row["family"],
            )
            _render_family_signature_heatmap(signature)
        else:
            matrix = covering_matrix(db, curve_id, coverings=coverings)
            coverage = quartic_search_coverage(
                db,
                curve_id,
                searches=searches,
                coverings=coverings,
            )
            comparison = quartic_model_comparison(
                db,
                curve_id,
                searches=searches,
                coverings=coverings,
                coverage=coverage,
                matrix=matrix,
            )
            point_inventory = quartic_point_inventory(
                db,
                curve_id,
                searches=searches,
                coverings=coverings,
            )
            _render_research_comparison(comparison)
            with st.expander(
                f"Quartic-associated Point Ledger inventory ({len(point_inventory)})",
                expanded=False,
            ):
                if point_inventory:
                    st.dataframe(
                        _inventory_table(point_inventory),
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.caption("No quartic-associated Point Ledger records for this curve.")

    else:
        st.subheader("Raw data & audit trail")
        st.caption(
            "Exact job history for debugging, provenance, reproducibility, runtime inspection, and export."
        )
        with st.expander(f"Quartic search history ({len(searches)})", expanded=True):
            if searches:
                st.dataframe(
                    _quartic_table(searches),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No persisted quartic searches.")

        with st.expander(f"Exact covering history ({len(coverings)})", expanded=False):
            if coverings:
                st.dataframe(
                    _covering_table(coverings),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No stored exact coverings.")

        st.caption(
            "Search actions remain explicit Jobs. Exact mapped points enter the ordinary Point Ledger; "
            "rank changes only through Independence/certification."
        )
