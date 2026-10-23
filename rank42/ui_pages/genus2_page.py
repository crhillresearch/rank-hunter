"""Saved genus-two curves, Jacobian evidence, and research handoffs."""
from __future__ import annotations

import json
from fractions import Fraction

import streamlit as st

from rank42.genus2_pipeline import audit_model, double_cover_from_elliptic
from rank42.genus2_registry import (
    list_genus2_models,
    register_double_cover_from_curve,
    register_genus2_model,
)
from rank42.pipeline_catalog import template_stages
from rank42.pipeline_state import get_pipeline_run
from rank42.ui_pages.build_your_own import _render_genus2_run_report
from rank42.ui_components import selectable_dataframe, tabs
from rank42.ui_pages.common import section_title, title


def _equation(coefficients):
    terms = []
    for power in range(len(coefficients) - 1, -1, -1):
        value = Fraction(str(coefficients[power]))
        if not value:
            continue
        factor = "" if abs(value) == 1 and power else str(abs(value))
        variable = "x" if power == 1 else f"x^{power}" if power else ""
        terms.append(("−" if value < 0 else "+", f"{factor}{variable}"))
    if not terms:
        return "y² = 0"
    first_sign, first_term = terms[0]
    return "y² = " + ("−" if first_sign == "−" else "") + first_term + "".join(
        f" {sign} {term}" for sign, term in terms[1:]
    )


def _run_index(db, models):
    by_id = {int(row["id"]): [] for row in models}
    by_fingerprint = {
        str(row["model_fingerprint_sha256"]): int(row["id"]) for row in models
    }
    if not by_id:
        return by_id
    table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_pipeline_runs'"
    ).fetchone()
    if table is None:
        return by_id
    rows = db.execute(
        """SELECT r.id,r.target_json,r.pipeline_name,r.status,r.finished_at,
                  c.rigorous_lower,c.status AS candidate_status
           FROM search_pipeline_runs r
           LEFT JOIN search_pipeline_candidates c
             ON c.run_id=r.id AND c.provider_key='genus2_jacobian'
           WHERE r.target_mode='genus2_jacobian'
           ORDER BY r.id DESC"""
    ).fetchall()
    for row in rows:
        try:
            target = json.loads(row["target_json"] or "{}")
        except json.JSONDecodeError:
            continue
        model_id = target.get("genus2_model_id")
        try:
            model_id = int(model_id)
        except (TypeError, ValueError):
            coefficients = target.get("coefficients_ascending") or []
            audit = audit_model(coefficients)
            model_id = by_fingerprint.get(audit.get("model_fingerprint_sha256"))
        if model_id in by_id:
            by_id[model_id].append(row)
    return by_id


def _best_lower(runs):
    completed = [
        int(row["rigorous_lower"] or 0)
        for row in runs
        if row["status"] in {"completed", "target_hit"}
        and row["candidate_status"] == "completed"
    ]
    return max(completed, default=0)


def _open_builder(model_id):
    st.session_state["byo_builder_state_version"] = 2
    st.session_state["byo_stage_mode"] = "genus2_jacobian"
    st.session_state["byo_stages"] = template_stages(
        "genus2_research", "genus2_jacobian"
    )
    st.session_state["byo_loaded_pipeline_id"] = None
    st.session_state.pop("byo_duplicate_source_pipeline_id", None)
    st.session_state["byo_pipeline_name_input"] = "My Genus 2 Jacobian Pipeline"
    st.session_state["byo-target-mode"] = "Genus 2 Jacobian"
    st.session_state["byo_target_mode_revision"] = int(
        st.session_state.get("byo_target_mode_revision") or 0
    ) + 1
    st.session_state["byo-genus2-model-source"] = "Saved models"
    st.session_state["byo-genus2-model-id"] = int(model_id)
    st.session_state["builder_main_view"] = "Editor"
    st.session_state["builder_view_revision"] = int(
        st.session_state.get("builder_view_revision") or 0
    ) + 1
    st.session_state["rh_page"] = "Pipelines"
    st.rerun()


def _render_inventory(models, runs_by_model):
    if not models:
        st.info("No models saved yet. Use Create model to start from an elliptic curve or enter an equation.")
        return

    best_overall = max((_best_lower(runs_by_model[int(m["id"])]) for m in models), default=0)
    a, b, c = st.columns(3)
    a.metric("Saved models", len(models))
    b.metric("With a rank bound", sum(_best_lower(runs_by_model[int(m["id"])]) > 0 for m in models))
    c.metric("Best Jacobian rank", f"≥{best_overall}" if best_overall else "Unknown")

    section_title("Model inventory", "Select a row to inspect its equation, source, and research results.")
    search_col, source_col = st.columns([3, 1.4], vertical_alignment="bottom")
    query = search_col.text_input(
        "Find a model", placeholder="Search label, model ID, or source curve…",
        key="models-filter", label_visibility="collapsed",
    ).strip().lower()
    source_filter = source_col.selectbox(
        "Source", ["All models", "Elliptic covers", "Entered equations"],
        key="models-source-filter", label_visibility="collapsed",
    )
    visible = []
    for model in models:
        source_id = model["source_curve_id"]
        if source_filter == "Elliptic covers" and source_id is None:
            continue
        if source_filter == "Entered equations" and source_id is not None:
            continue
        haystack = f"{model['id']} {model['label']} {source_id or ''}".lower()
        if query and query not in haystack:
            continue
        visible.append(model)
    if not visible:
        st.info("No models match those filters.")
        return
    table = [
        {
            "ID": int(model["id"]),
            "Model": str(model["label"]),
            "Origin": (
                f"Curve #{int(model['source_curve_id'])}"
                if model["source_curve_id"] is not None else "Entered equation"
            ),
            "Jacobian rank": (
                f"≥{_best_lower(runs_by_model[int(model['id'])])}"
                if _best_lower(runs_by_model[int(model["id"])]) else "Unknown"
            ),
            "Searches": len(runs_by_model[int(model["id"])]),
        }
        for model in visible
    ]
    selected = selectable_dataframe(
        table, visible, semantic="models-inventory-table", allow_empty=True,
        id_key="id", height=max(180, min(640, 44 + 36 * len(visible))), width="stretch",
        hide_index=True,
    )
    if selected is not None:
        st.session_state["models-detail-id"] = int(selected["id"])
        st.rerun()


def _render_model_detail(db, model, runs):
    selected_id = int(model["id"])
    coefficients = json.loads(model["coefficients_json"])
    construction = json.loads(model["construction_json"] or "{}")
    back_col, heading_col = st.columns([0.18, 0.82], vertical_alignment="center")
    if back_col.button("Back", icon=":material/arrow_back:", width="stretch",
                       key=f"models-back-{selected_id}"):
        st.session_state.pop("models-detail-id", None)
        st.session_state["rh-models-section"] = "Inventory"
        for key in list(st.session_state):
            if "models-inventory-table" in str(key):
                st.session_state.pop(key, None)
        st.rerun()
    heading_col.subheader(f"Model #{selected_id} · {model['label']}")

    best = _best_lower(runs)
    m1, m2, m3 = st.columns(3)
    m1.metric("Jacobian rank bound", f"≥{best}" if best else "Unknown")
    m2.metric("Research runs", len(runs))
    m3.metric("Elliptic source", f"#{int(model['source_curve_id'])}" if model["source_curve_id"] is not None else "No linked curve")

    actions = st.columns(3)
    if actions[0].button("Search model", type="primary", icon=":material/search:",
                         key=f"models-search-{selected_id}", width="stretch"):
        _open_builder(selected_id)
    if model["source_curve_id"] is not None and actions[1].button(
        "Open source curve", icon=":material/line_curve:",
        key=f"models-source-{selected_id}", width="stretch",
    ):
        st.session_state["curves_detail_id"] = int(model["source_curve_id"])
        st.session_state["rh_page"] = "Curves"
        st.rerun()
    if runs and actions[2].button(
        "Open latest run", icon=":material/flowsheet:",
        key=f"models-runs-{selected_id}", width="stretch",
    ):
        st.session_state["byo_focus_run_id"] = int(runs[0]["id"])
        st.session_state["builder_main_view"] = "Runs"
        st.session_state["builder_view_revision"] = int(
            st.session_state.get("builder_view_revision") or 0
        ) + 1
        st.session_state["rh_page"] = "Pipelines"
        st.rerun()

    detail_view = tabs(
        ["Overview", "Research history"], value="Overview",
        key=f"models-detail-{selected_id}", variant="line",
    )
    if detail_view == "Overview":
        section_title("Equation", "Exact smooth genus 2 model over Q.")
        st.code(_equation(coefficients), language="text")
        if model["source_curve_id"] is not None and construction.get("map_degree") == 2:
            short = construction.get("short_model") or {}
            st.info(
                f"Degree-two cover of elliptic curve #{int(model['source_curve_id'])}. "
                f"The verified map is (x,y) ↦ (x²,y) into "
                f"Y²=X³+({short.get('A')})X+({short.get('B')}). "
                "Pipeline projection can return found points to the source curve."
            )
        elif model["source_curve_id"] is None:
            st.caption("Entered equation · no elliptic source map is registered for this model.")
        with st.expander("Model identity and coefficients"):
            st.caption(f"SHA-256 {model['model_fingerprint_sha256']}")
            st.code(", ".join(str(value) for value in coefficients), language="text")
        if not runs:
            st.info("No search has run for this model. Use Search model to build a Genus 2 pipeline.")
        else:
            st.caption("Open Research history to inspect Jacobian evidence and projected elliptic points.")
        return

    section_title("Research history", "Select a run to inspect its exact results and proof status.")
    if not runs:
        st.info("No research runs yet. Use Search model to start one.")
        return
    selected_run = selectable_dataframe(
        [
            {
                "Run": int(row["id"]),
                "Pipeline": row["pipeline_name"],
                "Status": row["status"],
                "Jacobian rank": (
                    f"≥{int(row['rigorous_lower'])}"
                    if int(row["rigorous_lower"] or 0) > 0 else "Unknown"
                ),
                "Finished": str(row["finished_at"] or "—")[:19],
            }
            for row in runs
        ],
        runs, semantic=f"models-runs-table-{selected_id}", allow_empty=True,
        id_key="id", height=max(180, min(460, 44 + 36 * len(runs))), width="stretch",
        hide_index=True,
    )
    if selected_run is None:
        st.caption("Select a run to open its research report.")
        return
    run_id = int(selected_run["id"])
    run = get_pipeline_run(db, run_id)
    candidate = db.execute(
        """SELECT * FROM search_pipeline_candidates
           WHERE run_id=? AND provider_key='genus2_jacobian'
           ORDER BY id DESC LIMIT 1""",
        (int(run_id),),
    ).fetchone()
    if run is not None and candidate is not None:
        _render_genus2_run_report(run, candidate)
    elif run is not None:
        st.info("This run has not produced a Jacobian candidate record yet.")


def _render_create(db):
    section_title("Create a model", "Start with an elliptic cover or enter a genus 2 equation directly.")
    source = tabs(
        ["From elliptic curve", "Enter equation"],
        value="From elliptic curve", key="models-create-source", variant="line",
    )
    if source == "From elliptic curve":
        rows = db.execute(
            """SELECT id,family,parameter,a_invariants_json FROM curves
               WHERE a_invariants_json IS NOT NULL
                 AND TRIM(a_invariants_json) != ''
               ORDER BY id DESC"""
        ).fetchall()
        if not rows:
            st.info("No stored elliptic curves have Weierstrass models.")
            return
        by_id = {int(row["id"]): row for row in rows}
        curve_col, label_col = st.columns([1.5, 1], vertical_alignment="bottom")
        curve_id = curve_col.selectbox(
            "Source curve",
            list(by_id),
            format_func=lambda value: (
                f"#{value} · {by_id[value]['family']} · "
                f"{str(by_id[value]['parameter'])[:44]}"
            ),
            key="models-create-curve-id",
        )
        label = label_col.text_input(
            "Model label", value=f"Double cover of curve #{int(curve_id)}",
            key=f"models-create-cover-label-{int(curve_id)}",
        )
        row = by_id[int(curve_id)]
        try:
            construction = double_cover_from_elliptic(
                json.loads(row["a_invariants_json"] or "[]")
            )
        except (ValueError, ArithmeticError, json.JSONDecodeError) as exc:
            st.error(f"This source cannot produce a smooth cover: {exc}")
            return
        st.caption("Preview · exact degree-two cover with map (x,y) ↦ (x²,y)")
        st.code(_equation(construction["coefficients_ascending"]), language="text")
        if st.button("Save model", type="primary", key="models-create-cover",
                     icon=":material/save:"):
            model_id = register_double_cover_from_curve(
                db, curve_id=int(curve_id), label=label
            )
            st.session_state["genus2-pending-selected-id"] = model_id
            st.rerun()
    else:
        label_col, coefficients_col = st.columns([1, 2], vertical_alignment="bottom")
        label = label_col.text_input("Model label", key="models-create-manual-label",
                                     placeholder="e.g. new sextic family")
        raw = coefficients_col.text_input(
            "Coefficients a₀–a₅ or a₆",
            value="1, 1, 0, 0, 0, 1",
            key="models-create-manual-coefficients",
            help="Enter 6 or 7 rational coefficients in ascending order for y²=f(x).",
        )
        try:
            coefficients = [str(Fraction(part.strip())) for part in raw.split(",")]
            audit = audit_model(coefficients)
            if audit["status"] != "completed":
                raise ValueError(audit["reason"])
        except (ValueError, ZeroDivisionError) as exc:
            st.error(f"Invalid genus-two model: {exc}")
            return
        st.caption("Preview · smooth genus 2 equation over Q")
        st.code(_equation(coefficients), language="text")
        if st.button("Save model", type="primary", key="models-create-manual",
                     icon=":material/save:"):
            model_id = register_genus2_model(
                db, label=label, coefficients=coefficients
            )
            st.session_state["genus2-pending-selected-id"] = model_id
            st.rerun()


def page(db, ctx):
    title(
        "Models",
        "Genus 2 curves and Jacobian research.",
        "Data",
        icon=":material/category:",
    )
    pending = st.session_state.pop("genus2-pending-selected-id", None)
    if pending is not None:
        st.session_state["models-detail-id"] = int(pending)
        st.session_state["rh-models-section"] = "Inventory"
    models = list_genus2_models(db)
    by_id = {int(model["id"]): model for model in models}
    selected_id = st.session_state.get("models-detail-id")
    if selected_id is not None:
        try:
            model = by_id.get(int(selected_id))
        except (TypeError, ValueError):
            model = None
        if model is not None:
            _render_model_detail(db, model, _run_index(db, models)[int(model["id"])])
            return
        st.session_state.pop("models-detail-id", None)

    view = tabs(["Inventory", "Create model"], value="Inventory",
                key="models-section", variant="line")
    if view == "Create model":
        _render_create(db)
    else:
        _render_inventory(models, _run_index(db, models))
