from __future__ import annotations

import json
import math

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.lattice_analysis import analyze_lattice, point_ledger_candidate_residuals
from rank42.lattice_store import list_lattices
from rank42.points import list_points
from rank42.ui_components import region, selectable_dataframe, selectable_dataframe_rows
from .common import active_curve_selector, curve_options, launch, setting, title


def _themed_altair_chart(chart, **kwargs):
    """Apply the resolved Rank Hunter appearance to Lattices chart chrome."""
    palette = st.session_state.get("_rh_theme_palette") or {}
    if not isinstance(palette, dict):
        palette = {}

    card = str(palette.get("rh-card") or "transparent")
    text = str(palette.get("rh-text") or "#31333f")
    secondary = str(palette.get("rh-text-secondary") or text)
    border = str(palette.get("rh-border") or "rgba(127,127,127,.22)")

    themed = (
        chart
        .configure(background=card)
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
    )
    return st.altair_chart(themed, theme=None, **kwargs)


def _fmt_number(value, *, digits=4):
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)
    if not math.isfinite(number):
        return str(value)
    magnitude = abs(number)
    if magnitude == 0:
        return "0"
    if magnitude >= 1e5 or magnitude < 1e-3:
        return f"{number:.{digits}e}"
    return f"{number:,.{digits}g}"


def _source_label(value):
    return {
        "stored-generators": "Stored generators",
        "generic": "Generic sections",
        "points-json": "Imported points",
        "point-ledger": "Point Ledger",
    }.get(str(value), str(value))


def _json_dict(value):
    try:
        out = json.loads(value or "{}")
    except Exception:
        out = {}
    return out if isinstance(out, dict) else {}


def _find_number(value, keys, *, depth=0):
    if depth > 3 or not isinstance(value, dict):
        return None
    for key in keys:
        if key in value and value[key] not in (None, ""):
            try:
                return float(value[key])
            except (TypeError, ValueError, OverflowError):
                pass
    for child in value.values():
        if isinstance(child, dict):
            found = _find_number(child, keys, depth=depth + 1)
            if found is not None:
                return found
    return None


def _candidate_geometry_info(rec):
    meta = _json_dict(rec["metadata_json"])
    return {
        "point": int(rec["id"]),
        "status": str(rec["independence_status"] or "unknown"),
        "source": rec["source"],
        "height": _find_number(meta, ("canonical_height", "height", "point_height", "nt_height")),
        "stored MW residual": _find_number(
            meta,
            (
                "relative_residual",
                "rel_residual",
                "novelty_relative_residual",
                "best_novelty_relative_residual",
                "mw_residual",
                "residual",
            ),
        ),
        "x": rec["x"],
        "search": rec["search_ref"] or "—",
    }


def _status_message(analysis):
    n = analysis["basis_count"]
    positive = analysis["positive_directions"]
    status = analysis["status"]
    if analysis["status_level"] == "success":
        st.success(
            f"{positive} / {n} positive numerical directions · {status}. "
            "No numerical rank proof is implied."
        )
    elif analysis["status_level"] == "danger":
        st.error(
            f"{positive} / {n} positive numerical directions · {status}. "
            "Recompute at higher precision and investigate possible dependence."
        )
    else:
        st.warning(
            f"{positive} / {n} positive numerical directions · {status}. "
            "Treat the weakest directions cautiously."
        )


def _render_basis_quality(analysis):
    strongest = analysis["strongest_pair"]
    strongest_corr = strongest["absolute_correlation"] if strongest is not None else None
    lll_state = "Reduced" if analysis["metadata"].get("lll_reduced") else "Not reduced"
    height_range = (
        f"{_fmt_number(analysis['min_height'])}–{_fmt_number(analysis['max_height'])}"
        if analysis["min_height"] is not None and analysis["max_height"] is not None
        else "—"
    )
    st.markdown(
        " · ".join(
            [
                f"**Generator heights** {height_range}",
                f"**Height skew** {_fmt_number(analysis['height_skew'])}×",
                f"**Strongest |ρ|** {_fmt_number(strongest_corr, digits=3)}",
                f"**LLL** {lll_state}",
            ]
        )
    )


def _render_status(chosen, analysis):
    with region("lattice-status", border=True):
        st.subheader("Lattice status")
        _status_message(analysis)

        a, b, c, d = st.columns(4, gap="medium")
        a.metric(
            "Independent directions",
            f"{analysis['positive_directions']} / {analysis['basis_count']}",
            help=(
                "Apparent numerical dimension from positive eigen-directions of the stored canonical-height pairing. "
                "This is a numerical independence screen, not a Mordell–Weil rank certificate."
            ),
        )
        b.metric(
            "Height determinant",
            _fmt_number(analysis["determinant"]),
            help=(
                "Determinant of this supplied subgroup's height Gram matrix. It is only the Mordell–Weil regulator "
                "if this basis is known to be the full Mordell–Weil basis."
            ),
        )
        c.metric(
            "Weakest direction",
            _fmt_number(analysis["min_eigenvalue"]),
            help="Smallest numerical eigenvalue λmin of the canonical-height pairing.",
        )
        d.metric(
            "Condition number",
            _fmt_number(analysis["condition"]),
            help="λmax / λmin when λmin > 0. Large values indicate a strongly skewed or near-degenerate numerical lattice.",
        )

        _render_basis_quality(analysis)
        st.caption(
            f"Basis: {analysis['basis_count']} points · Precision: {int(chosen['precision_bits'])} bits · "
            f"Source: {_source_label(chosen['source'])}"
        )


def _render_geometry(analysis):
    import altair as alt
    import pandas as pd

    left, right = st.columns(2, gap="medium")
    with left:
        with region("lattice-correlation-heatmap", border=True):
            st.subheader("Generator geometry")
            st.caption("Normalized canonical-height correlation. Diagonal cells are self-correlations.")
            heat_rows = []
            n = analysis["basis_count"]
            for i in range(n):
                for j in range(n):
                    heat_rows.append(
                        {
                            "Generator": f"P{j + 1}",
                            "Compared with": f"P{i + 1}",
                            "Correlation": analysis["correlations"][i][j],
                            "Absolute correlation": abs(analysis["correlations"][i][j]),
                            "Height pairing": analysis["gram"][i][j],
                        }
                    )
            if heat_rows:
                heat_df = pd.DataFrame(heat_rows)
                chart = (
                    alt.Chart(heat_df)
                    .mark_rect()
                    .encode(
                        x=alt.X("Generator:N", sort=[f"P{i + 1}" for i in range(n)], title=None),
                        y=alt.Y("Compared with:N", sort=[f"P{i + 1}" for i in range(n)], title=None),
                        color=alt.Color(
                            "Absolute correlation:Q",
                            scale=alt.Scale(domain=[0, 1]),
                            title="|ρ|",
                        ),
                        tooltip=[
                            "Compared with:N",
                            "Generator:N",
                            alt.Tooltip("Height pairing:Q", format=".6g"),
                            alt.Tooltip("Correlation:Q", format=".4f"),
                        ],
                    )
                    .properties(height=max(260, min(500, 22 * n)))
                )
                _themed_altair_chart(chart, width="stretch")
            else:
                st.caption("No Gram matrix is stored for this lattice.")

    with right:
        with region("lattice-direction-strength", border=True):
            st.subheader("Direction strength")
            st.caption("Eigenvalue strength relative to λmax; hover for the raw eigenvalue.")
            eigenvalues = analysis["eigenvalues"]
            max_abs = max((abs(value) for value in eigenvalues), default=0.0)
            eig_rows = [
                {
                    "Direction": f"λ{i + 1}",
                    "Eigenvalue": value,
                    "Relative strength": (value / max_abs) if max_abs else 0.0,
                }
                for i, value in enumerate(eigenvalues)
            ]
            if eig_rows:
                eig_df = pd.DataFrame(eig_rows)
                all_nonnegative = all(value >= 0 for value in eigenvalues)
                strength_scale = alt.Scale(domain=[0, 1]) if all_nonnegative else alt.Scale(domain=[-1, 1])
                chart = (
                    alt.Chart(eig_df)
                    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("Direction:N", sort=eig_df["Direction"].tolist(), title=None),
                        y=alt.Y(
                            "Relative strength:Q",
                            scale=strength_scale,
                            title="λ / max |λ|",
                        ),
                        tooltip=[
                            "Direction:N",
                            alt.Tooltip("Eigenvalue:Q", format=".8g"),
                            alt.Tooltip("Relative strength:Q", format=".4f"),
                        ],
                    )
                    .properties(height=320)
                )
                _themed_altair_chart(chart, width="stretch")
            else:
                st.caption("No eigenvalue data is available.")


def _render_features(analysis):
    with region("lattice-interesting-features", border=True):
        st.subheader("Interesting features")
        for level, message in analysis["features"]:
            icon = {
                "success": ":material/check_circle:",
                "warning": ":material/warning:",
                "danger": ":material/error:",
                "info": ":material/info:",
            }.get(level, ":material/info:")
            st.markdown(f"{icon} {message}")


def _render_diagnostics(analysis):
    import pandas as pd

    with region("lattice-generator-diagnostics", border=True):
        st.subheader("Generator diagnostics")
        st.caption(
            "Canonical height and strongest normalized relationship for each stored generator. "
            "Exact point coordinates remain available under Raw numerical evidence."
        )
        rows = []
        for rec in analysis["generator_rows"]:
            rows.append(
                {
                    "Generator": rec["generator"],
                    "Height": rec["height"],
                    "Max |correlation|": rec["max_correlation"],
                    "Closest generator": rec["closest_generator"],
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        if analysis["pairs"]:
            st.markdown("**Strongest relationships**")
            pair_rows = []
            for rec in analysis["pairs"][: min(8, len(analysis["pairs"]))]:
                pair_rows.append(
                    {
                        "Pair": f"P{rec['left'] + 1} ↔ P{rec['right'] + 1}",
                        "Correlation": rec["correlation"],
                        "|Correlation|": rec["absolute_correlation"],
                        "Height pairing": rec["pairing"],
                    }
                )
            st.dataframe(pd.DataFrame(pair_rows), width="stretch", hide_index=True)


def _stored_lattice_rows(lattices):
    rows = []
    for rec in lattices:
        try:
            analysis = analyze_lattice(rec)
        except Exception:
            analysis = None
        rows.append(
            {
                "ID": f"#{int(rec['id'])}",
                "Basis": int(rec["basis_count"]),
                "Source": _source_label(rec["source"]),
                "Precision": int(rec["precision_bits"]),
                "Status": analysis["status"] if analysis else str(rec["status"]),
                "λmin": analysis["min_eigenvalue"] if analysis else rec["min_eigenvalue"],
                "Condition": analysis["condition"] if analysis else None,
                "Determinant": _fmt_number(rec["determinant"]),
                "Created": str(rec["created_at"] or "")[:19],
            }
        )
    return rows


def _render_point_ledger_comparison(chosen):
    residuals = point_ledger_candidate_residuals(chosen)
    metadata = _json_dict(chosen["metadata_json"])
    ledger = metadata.get("point_ledger") if isinstance(metadata.get("point_ledger"), dict) else {}
    if not ledger:
        return
    with region("lattice-point-ledger-comparison", border=True):
        st.subheader("Candidate vs rigorous basis")
        st.caption(
            "Orthogonal height residuals are computed numerically from the stored pre-LLL height Gram matrix. "
            "They prioritize exact candidates for follow-up; they do not certify Mordell–Weil independence."
        )
        if residuals:
            st.dataframe(
                [
                    {
                        "Point": rec["point_id"],
                        "Basis directions": rec["basis_count"],
                        "Candidate height": rec["height"],
                        "Orthogonal residual": rec["orthogonal_height_residual"],
                        "Relative residual": rec["relative_residual"],
                    }
                    for rec in residuals
                ],
                width="stretch",
                hide_index=True,
                column_config={
                    "Candidate height": st.column_config.NumberColumn(format="%.6g"),
                    "Orthogonal residual": st.column_config.NumberColumn(format="%.6g"),
                    "Relative residual": st.column_config.NumberColumn(format="%.4g"),
                },
            )
        elif ledger.get("selected_point_ids"):
            st.warning(
                "Selected candidate points did not produce a separate stored direction in this input basis. "
                "Review the exact point state in Independence."
            )
        skipped = list(ledger.get("skipped_duplicate_point_ids") or [])
        if skipped:
            st.info(
                "Point Ledger candidate(s) "
                + ", ".join(f"#{int(pid)}" for pid in skipped)
                + " matched an existing basis direction up to sign and were omitted from the numerical lattice input."
            )


def _render_lll_comparison(chosen, analysis):
    metadata = _json_dict(chosen["metadata_json"])
    if not metadata.get("lll_reduced"):
        return
    input_eigs = []
    for value in metadata.get("input_eigenvalues") or []:
        try:
            input_eigs.append(float(value))
        except (TypeError, ValueError, OverflowError):
            pass
    positive = [value for value in input_eigs if value > 0]
    input_condition = (
        max(positive) / min(positive)
        if len(positive) == len(input_eigs) and positive
        else None
    )
    with region("lattice-lll-comparison", border=True):
        st.subheader("Original vs LLL-reduced basis")
        a, b, c = st.columns(3)
        a.metric("Input condition", _fmt_number(input_condition))
        b.metric("Reduced condition", _fmt_number(analysis["condition"]))
        c.metric("Basis size", analysis["basis_count"])
        st.caption(
            "LLL changes the basis representation through a unimodular transform; it does not change the subgroup rank. "
            "The stored input Gram matrix and transform remain available under Raw numerical evidence."
        )


def _target_geometry_hint(chosen, analysis):
    strongest = analysis.get("strongest_pair")
    strongest_pair = None
    if strongest is not None:
        strongest_pair = {
            "left": int(strongest["left"]) + 1,
            "right": int(strongest["right"]) + 1,
            "correlation": float(strongest["correlation"]),
            "absolute_correlation": float(strongest["absolute_correlation"]),
        }
    return {
        "curve_id": int(chosen["curve_id"]),
        "lattice_id": int(chosen["id"]),
        "source": str(chosen["source"]),
        "basis_count": int(analysis["basis_count"]),
        "condition": analysis.get("condition"),
        "weakest_ratio": analysis.get("weakest_ratio"),
        "min_eigenvalue": analysis.get("min_eigenvalue"),
        "strongest_pair": strongest_pair,
        "claim": "numerical_scheduling_evidence_only",
    }


def _render_target_handoff(chosen, analysis):
    hint = _target_geometry_hint(chosen, analysis)
    with region("lattice-target-handoff", border=True):
        st.subheader("Use this geometry in Target")
        st.caption(
            "Send the selected curve and a numerical lattice scheduling hint to Target. "
            "Target will display this context but will not automatically rewrite height, denominator, "
            "chart, or plugin search parameters."
        )
        if st.button(
            "Open Target with Lattice hint",
            width="stretch",
            key=f"mw-geometry-target-{int(chosen['id'])}",
        ):
            st.session_state["target_geometry_hint"] = hint
            set_active_curve_id(
                st.session_state,
                int(chosen["curve_id"]),
                "target_curve_id",
            )
            st.session_state["rh_page"] = "Target"
            st.rerun()


def _render_raw(chosen, analysis):
    import pandas as pd

    st.subheader("Raw numerical evidence")
    with st.expander("Raw height Gram matrix"):
        labels = [f"P{i + 1}" for i in range(analysis["basis_count"])]
        st.dataframe(pd.DataFrame(analysis["gram"], index=labels, columns=labels), width="stretch")
    with st.expander("Eigenvalues"):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Direction": f"λ{i + 1}", "Eigenvalue": value}
                    for i, value in enumerate(analysis["eigenvalues"])
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    with st.expander("Stored basis"):
        st.code(json.dumps(analysis["basis"], indent=2), language="json")
    with st.expander("Numerical metadata"):
        metadata = dict(analysis["metadata"])
        metadata.update(
            {
                "lattice_id": int(chosen["id"]),
                "precision_bits": int(chosen["precision_bits"]),
                "positive_definite_screen": bool(chosen["positive_definite_screen"]),
                "determinant": chosen["determinant"],
                "min_eigenvalue": chosen["min_eigenvalue"],
            }
        )
        st.code(json.dumps(metadata, indent=2, sort_keys=True, default=str), language="json")


def page(db, ctx):
    title(
        "Lattices",
        "Compare the rigorous Mordell–Weil basis with exact candidates, weak directions, correlations, and LLL geometry.",
        "Analysis",
    )
    render_feature_hook(db, ctx, "analysis.mw_geometry.after_header")

    rows = curve_options(db)
    if not rows:
        st.info("No curves yet.")
        return

    wanted = get_active_curve_id(st.session_state, "analysis_curve_id")
    with region("lattice-controls", border=True):
        st.subheader("Mordell–Weil Geometry Inspector")
        st.caption("Analyze an existing generator set without upgrading numerical evidence into a rank proof.")
        row = active_curve_selector(
            db,
            rows,
            wanted=wanted,
            state_prefix="lattice",
            widget_prefix="lattice-curve",
            compatibility_keys=("analysis_curve_id",),
        )
        if row is None:
            return

        a, b, c = st.columns([1.35, 0.8, 1.05], gap="medium")
        source_label = a.selectbox(
            "Point source",
            [
                "Rigorous witness basis",
                "Rigorous basis + selected candidates",
                "Stored generators",
                "Generic sections",
            ],
        )
        source = {
            "Rigorous witness basis": "point-ledger",
            "Rigorous basis + selected candidates": "point-ledger",
            "Stored generators": "stored-generators",
            "Generic sections": "generic",
        }[source_label]
        precision_choice = b.selectbox("Height precision", [128, 256, 512, "Custom"], index=1)
        precision = (
            int(b.number_input("Custom precision", min_value=64, value=256, step=64))
            if precision_choice == "Custom"
            else int(precision_choice)
        )
        preserve_basis = c.toggle(
            "Preserve original generator basis",
            value=False,
            help="Skip height-LLL reduction and analyze the supplied generators exactly as provided.",
        )

        selected_candidates = []
        if source_label == "Rigorous basis + selected candidates":
            exact_points = list_points(
                db,
                curve_id=int(row["id"]),
                exact_only=True,
                limit=10000,
            )
            candidates = [
                rec for rec in exact_points
                if not int(rec["rigorous_independent"] or 0)
                and str(rec["independence_status"] or "unknown") != "dependent"
            ]
            if candidates:
                st.markdown("#### Exact candidate comparison")
                st.caption(
                    "Select exact Point Ledger candidates to append to the current rigorous witness basis. "
                    "The resulting height residuals remain numerical scheduling evidence only."
                )
                selected_candidates = selectable_dataframe_rows(
                    [_candidate_geometry_info(rec) for rec in candidates],
                    candidates,
                    semantic="mw-geometry-candidate-table",
                    selected_ids=st.session_state.get("mw_geometry_selected_point_ids"),
                    selection_state_key="mw_geometry_selected_point_ids",
                    column_config={
                        "height": st.column_config.NumberColumn(format="%.6g"),
                        "stored MW residual": st.column_config.NumberColumn(format="%.4g"),
                    },
                )
                handoff_col, note_col = st.columns([1.0, 2.2])
                with handoff_col:
                    if st.button(
                        "Open selected in Independence",
                        width="stretch",
                        disabled=not selected_candidates,
                    ):
                        st.session_state["independence_selected_point_ids"] = [
                            int(rec["id"]) for rec in selected_candidates
                        ]
                        if selected_candidates:
                            st.session_state["independence_point_id"] = int(selected_candidates[0]["id"])
                        set_active_curve_id(
                            st.session_state,
                            int(row["id"]),
                            "independence_curve_id",
                        )
                        st.session_state["rh_page"] = "Independence"
                        st.rerun()
                with note_col:
                    st.caption(
                        "Use Independence for exact certification. MW Geometry does not promote rank from a residual or positive-definite screen."
                    )
            else:
                st.info("No unresolved exact candidate points are stored for this curve.")

        family_spec = None
        if source == "generic":
            rec = db.execute(
                """
                SELECT cp.family_spec
                FROM candidates c
                JOIN candidate_pools cp ON cp.id=c.pool_id
                WHERE c.curve_id=?
                ORDER BY c.id DESC LIMIT 1
                """,
                (int(row["id"]),),
            ).fetchone()
            family_spec = rec["family_spec"] if rec else None
            if not family_spec:
                st.warning("No plugin/family provenance is attached to this curve; use stored generators.")

        compare_missing = (
            source_label == "Rigorous basis + selected candidates"
            and not selected_candidates
        )
        if st.button(
            "Analyze lattice",
            type="primary",
            width="stretch",
            icon=":material/analytics:",
            disabled=(source == "generic" and not family_spec) or compare_missing,
        ):
            py = setting(db, "science_python", ctx.detected_science_python())
            cmd = [
                py,
                "-m",
                "rank42.lattice",
                "--db",
                ctx.db_path,
                "--curve-id",
                int(row["id"]),
                "--source",
                source,
                "--precision",
                precision,
            ]
            if family_spec:
                cmd += ["--family", family_spec]
            if source == "point-ledger":
                for candidate in selected_candidates:
                    cmd += ["--point-id", int(candidate["id"])]
            if preserve_basis:
                cmd.append("--no-lll")
            jid = launch(
                ctx,
                db,
                kind="lattice",
                label=f"Lattice curve #{row['id']}",
                command=cmd,
                metadata={
                    "curve_id": int(row["id"]),
                    "precision_bits": precision,
                    "source": source,
                    "point_ids": [int(rec["id"]) for rec in selected_candidates],
                    "mode": (
                        "rigorous_basis_plus_candidates"
                        if selected_candidates
                        else "rigorous_basis"
                        if source == "point-ledger"
                        else source
                    ),
                },
            )
            st.success(f"Started lattice analysis job #{jid}.")

    lattices = list_lattices(db, curve_id=row["id"], limit=30)
    if not lattices:
        st.info("No stored lattice analysis exists for this curve yet. Run Analyze lattice above.")
        st.caption(
            "Numerical lattice analysis is evidence, not a Mordell–Weil rank proof. "
            "Positive definiteness and canonical-height matrices do not certify that the supplied subgroup is the full Mordell–Weil group."
        )
        return

    with region("lattice-stored-analyses", border=True):
        st.subheader("Stored lattices")
        st.caption("Select a row to inspect that analysis in detail.")
        chosen = selectable_dataframe(
            _stored_lattice_rows(lattices),
            lattices,
            semantic=f"lattice-stored-table-{row['id']}",
            selected_id=st.session_state.get(f"lattice_selected_id_{row['id']}"),
            selection_state_key=f"lattice_selected_id_{row['id']}",
        )
    if chosen is None:
        return

    try:
        analysis = analyze_lattice(chosen)
    except Exception as exc:
        st.error(f"Could not interpret stored lattice #{chosen['id']}: {exc}")
        return

    _render_status(chosen, analysis)
    _render_point_ledger_comparison(chosen)
    _render_lll_comparison(chosen, analysis)
    _render_geometry(analysis)
    _render_features(analysis)
    _render_diagnostics(analysis)
    _render_target_handoff(chosen, analysis)

    _render_raw(chosen, analysis)
    st.caption(
        "Numerical lattice analysis is evidence, not a Mordell–Weil rank proof. "
        "Positive definiteness, eigenvalues, correlations, and canonical-height matrices can provide strong numerical evidence for independence, "
        "but they do not by themselves prove that the supplied subgroup is the full Mordell–Weil group."
    )
