from __future__ import annotations

import json
import math

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.lattice_analysis import analyze_lattice
from rank42.lattice_store import list_lattices
from rank42.ui_components import region, selectable_dataframe
from .common import curve_label, curve_options, launch, select_row, setting, title


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
    }.get(str(value), str(value))


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
                st.altair_chart(chart, width="stretch")
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
                st.altair_chart(chart, width="stretch")
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
        "Lattices & Heights",
        "Mordell–Weil geometry, numerical independence, weak directions, correlations, and basis quality.",
        "Analysis",
    )
    render_feature_hook(db, ctx, "analysis.lattices.after_header")

    rows = curve_options(db)
    if not rows:
        st.info("No curves yet.")
        return

    wanted = st.session_state.get("analysis_curve_id")
    idx = next((i for i, rec in enumerate(rows) if wanted and int(rec["id"]) == int(wanted)), 0)

    with region("lattice-controls", border=True):
        st.subheader("Mordell–Weil Geometry Inspector")
        st.caption("Analyze an existing generator set without upgrading numerical evidence into a rank proof.")
        row = select_row("Curve", rows, index=idx, format_func=curve_label)

        a, b, c = st.columns([1.15, 0.85, 1.1], gap="medium")
        source_label = a.selectbox("Point source", ["Stored generators", "Generic sections"])
        source = "stored-generators" if source_label == "Stored generators" else "generic"
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

        if st.button(
            "Analyze lattice",
            type="primary",
            width="stretch",
            icon=":material/analytics:",
            disabled=(source == "generic" and not family_spec),
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
            if preserve_basis:
                cmd.append("--no-lll")
            jid = launch(
                ctx,
                db,
                kind="lattice",
                label=f"Lattice curve #{row['id']}",
                command=cmd,
                metadata={"curve_id": int(row["id"]), "precision_bits": precision, "source": source},
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
    _render_geometry(analysis)
    _render_features(analysis)
    _render_diagnostics(analysis)

    _render_raw(chosen, analysis)
    st.caption(
        "Numerical lattice analysis is evidence, not a Mordell–Weil rank proof. "
        "Positive definiteness, eigenvalues, correlations, and canonical-height matrices can provide strong numerical evidence for independence, "
        "but they do not by themselves prove that the supplied subgroup is the full Mordell–Weil group."
    )
