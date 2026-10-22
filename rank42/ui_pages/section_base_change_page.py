"""Exact family section and base-change workbenches in core Tools."""
from __future__ import annotations

import hashlib
import json

import streamlit as st

from rank42.geometry_research import recent_experiments, run_experiment, save_experiment
from rank42.plugins import Plugin, discover_plugins, variant_family_definition
from .common import setting


def _formula_families(project_root):
    choices = {}
    for plugin in discover_plugins(project_root):
        if not isinstance(plugin, Plugin) or plugin.plugin_type != "family":
            continue
        for variant in plugin.variants:
            definition = variant_family_definition(plugin, variant)
            if str(definition.get("kind") or "json") != "json":
                continue
            source = plugin.root / str(definition.get("file") or "family.json")
            if not source.is_file():
                continue
            label = f"{plugin.name} · {variant.name}" if len(plugin.variants) > 1 else plugin.name
            choices[f"{label} [{plugin.id}/{variant.id}]"] = source
    return choices


def _custom_formula(mode):
    name = st.text_input(
        "Family name", value="Research family", key=f"rh-geometry-{mode}-custom-name",
    )
    parameter = st.text_input(
        "Parameter", value="t", key=f"rh-geometry-{mode}-custom-parameter",
    ).strip() or "t"
    coefficients = st.columns(5)
    defaults = ("0", "0", "0", "-t^2", "1")
    ainvs = [
        column.text_input(
            f"a{index}", value=default,
            key=f"rh-geometry-{mode}-custom-a{index}",
            help=f"Coefficient a{index} in the Weierstrass model.",
        )
        for column, index, default in zip(coefficients, (1, 2, 3, 4, 6), defaults)
    ]
    with st.expander("Known sections (optional)", expanded=False):
        raw_sections = st.text_area(
            "Sections as JSON", value="[]", height=90,
            help='Use entries like {"label":"P", "x":"0", "y":"1"}.',
            key=f"rh-geometry-{mode}-custom-sections",
        )
    try:
        sections = json.loads(raw_sections)
        if not isinstance(sections, list):
            raise ValueError("known sections must be a JSON list")
    except (ValueError, TypeError) as exc:
        st.error(f"Known sections: {exc}")
        return None, None
    return {
        "name": name,
        "parameter": parameter,
        "a_invariants": ainvs,
        "sections": sections,
    }, None


def _family_selector(ctx, mode, source_kind=None):
    if source_kind == "Custom Formula":
        return _custom_formula(mode)
    choices = _formula_families(ctx.project_root)
    if not choices and source_kind == "Family":
        st.info("No JSON formula families are available here. Choose Custom Formula to enter a model directly.")
        return None, None
    options = [*choices]
    if source_kind is None:
        options.append("Custom formula")
    selected = st.selectbox(
        "Family", options,
        key=f"rh-geometry-{mode}-source-family" if source_kind else f"rh-geometry-{mode}-family",
    )
    if selected == "Custom formula":
        return _custom_formula(mode)
    source = choices[selected]
    try:
        family = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(family, dict) or len(family.get("a_invariants") or []) != 5:
            raise ValueError("family file does not contain five a-invariants")
    except Exception as exc:
        st.error(f"Cannot load the selected formula family: {exc}")
        return None, None
    st.caption(
        f"{family.get('name') or selected} · parameter {family.get('parameter') or 't'} · "
        f"{len(family.get('sections') or [])} declared sections · source `{source.name}`"
    )
    with st.expander("Source family equations", expanded=False):
        st.code("\n".join(f"a{i} = {expr}" for i, expr in zip((1, 2, 3, 4, 6), family["a_invariants"])),
                language="text")
    return family, source


def _search_controls(prefix, *, compact=False):
    columns = st.columns(4)
    degree = columns[0].selectbox("x degree", [0, 1, 2, 3], index=1,
                                  key=f"{prefix}-degree", help="Maximum numerator degree of x(t).")
    bound = columns[1].selectbox("Coefficient bound", [1, 2, 3, 4, 5, 6], index=1,
                                 key=f"{prefix}-coeff")
    trials = columns[2].selectbox("Candidate budget", [250, 1000, 3000, 10000, 20000], index=1,
                                  key=f"{prefix}-trials")
    timeout = columns[3].selectbox("Time budget", [30, 60, 120, 300], index=1,
                                   format_func=lambda n: f"{n}s", key=f"{prefix}-timeout")
    rational_x = st.checkbox("Also try x with a linear denominator", value=not compact,
                             key=f"{prefix}-rational")
    return {"degree": degree, "coefficient_bound": bound, "max_trials": trials,
            "max_results": 32, "rational_x": rational_x}, timeout


def _handoff_to_base_change(family, sections):
    """Carry the exact source formula into the Base Change workbench."""
    st.session_state["tools_selection"] = "Base Change"
    st.session_state["rh_page"] = "Tools"
    st.session_state["rh-geometry-base_change-source-kind"] = "Custom Formula"
    st.session_state["rh-geometry-base_change-custom-name"] = str(
        family.get("name") or "Research family"
    )
    st.session_state["rh-geometry-base_change-custom-parameter"] = str(
        family.get("parameter") or "t"
    )
    for index, value in zip((1, 2, 3, 4, 6), family.get("a_invariants") or []):
        st.session_state[f"rh-geometry-base_change-custom-a{index}"] = str(value)
    st.session_state["rh-geometry-base_change-custom-sections"] = json.dumps(
        sections, indent=2,
    )
    st.session_state.pop("rh-geometry-base_change-result", None)


def _result_panel(result, source, path):
    body = result.get("result") or {}
    mode = result.get("mode")
    st.success("Exact experiment completed. Equations and provenance were saved.")
    st.caption(f"Source SHA-256: `{result['source_sha256'][:20]}` · artifact: `{path}`")
    if mode == "sections":
        found = body.get("sections") or []
        known = body.get("known_sections_verified") or []
        cards = st.columns(3)
        cards[0].metric("Exact sections found", len(found))
        cards[1].metric("Known sections verified", len(known))
        cards[2].metric("x candidates checked", body.get("x_candidates_checked") or 0)
        st.caption(f"Coverage: {body.get('coverage')} within the stated degree and coefficient bounds.")
    else:
        st.markdown(f"**Parameter map:** `{source.get('parameter') or 't'} = {body.get('parameter_map')}`")
        st.caption(f"Degree {body.get('map_degree')} · inherited sections verified: "
                   f"{len(body.get('inherited_sections_verified') or [])}")
        search = body.get("new_section_search") or {}
        if search:
            st.metric("Sections outside inherited coordinates", len(search.get("sections") or []))
            st.caption(f"{search.get('x_candidates_checked')} x candidates checked · {search.get('coverage')}")
        with st.expander("Pulled-back Weierstrass equation", expanded=False):
            st.code("\n".join(f"a{i}(u) = {expr}" for i, expr in zip((1, 2, 3, 4, 6), body.get("a_invariants") or [])),
                    language="text")
    found = body.get("sections") if mode == "sections" else (body.get("new_section_search") or {}).get("sections")
    manual = body.get("manual_candidate")
    if manual:
        st.markdown("**Verified candidate section**")
        st.code(f"x = {manual['x']}\ny = {manual['y']}", language="text")
    if found:
        st.dataframe([{"x": rec["x"], "y": rec["y"], "proof": rec["verification"],
                       "independence": rec["independence"]} for rec in found],
                     hide_index=True, width="stretch")
    if mode == "sections":
        if body.get("known_sections_invalid"):
            with st.expander("Declared sections that failed verification", expanded=True):
                st.dataframe(body["known_sections_invalid"], hide_index=True, width="stretch")
        if not found and not manual:
            st.info("No section was found in this bounded search. Wider degree or coefficient bounds may find more.")
    elif body.get("new_section_search") is None:
        st.caption("Section search was not requested for this base change.")
    elif not found and not manual:
        st.info("No additional section was found in this bounded search. Larger bounds or another parameter map may produce one.")
    invalid = body.get("known_sections_invalid") or body.get("inherited_sections_invalid") or []
    if invalid:
        st.warning(f"{len(invalid)} declared sections failed exact verification; inspect the artifact before using them.")
    if mode == "sections":
        st.info(
            "Each listed point satisfies the family equation identically over Q(t). "
            "The search does not certify that sections are independent or that the full generic rank is known."
        )
        inherited = [
            {"label": item.get("label") or f"S{index}", "x": item["x"], "y": item["y"]}
            for index, item in enumerate(body.get("known_sections_verified") or [], 1)
        ]
        inherited.extend(
            {"label": f"Found section {index}", "x": item["x"], "y": item["y"]}
            for index, item in enumerate(found or [], 1)
        )
        st.button(
            "Use verified sections in Base Change",
            icon=":material/transform:",
            key=f"rh-geometry-sections-to-base-change-{path.stem}",
            on_click=_handoff_to_base_change,
            args=(source, inherited),
        )
    else:
        st.caption("An exact section identity does not establish independence or a higher generic rank. "
                   "A base change alone does not increase rank.")
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    st.download_button("Download exact artifact", payload, file_name=path.name,
                       mime="application/json", key=f"rh-geometry-download-{mode}")
    with st.expander("Full experiment record", expanded=False):
        st.json(result)


def _page(db, ctx, mode):
    is_sections = mode == "sections"
    st.markdown("#### Discover rational sections" if is_sections else "#### Construct a base change")
    st.caption(
        "Search for points whose coordinates are rational functions of the family parameter. "
        "Rank Hunter checks every proposed section by exact substitution and saves the experiment."
        if is_sections else
        "Pull a formula family through an explicit rational parameter map, verify inherited sections, "
        "and search the derived family for further exact sections."
    )
    source_kind = st.segmented_control(
        "Start from", ["Family", "Custom Formula"],
        default="Family", key=f"rh-geometry-{mode}-source-kind",
        help="Choose a registered Rank Hunter family or enter an exact Weierstrass formula.",
    ) or "Family"
    with st.container(key=f"rh-geometry-{mode}-source", border=True):
        if source_kind == "Custom Formula":
            st.markdown("**1 · Define the family**")
            st.caption("Enter the five Weierstrass coefficients as functions of one parameter.")
        elif source_kind == "Family":
            st.markdown("**1 · Choose a registered family**")
        family, source = _family_selector(ctx, mode, source_kind)
    if family is None:
        return
    prefix = f"rh-geometry-{mode}"
    config = {}
    if not is_sections:
        with st.container(key="rh-geometry-base-change-map", border=True):
            st.markdown("**2 · Define the base change**")
            source_parameter = str(family.get("parameter") or "t")
            map_col, search_col = st.columns([1.5, 1], vertical_alignment="bottom")
            config["parameter_map"] = map_col.text_input(
                f"Parameter map {source_parameter} = f(u)", value="u^2", key=f"{prefix}-map",
                help="Use a rational expression in u. The exact map degree must be 2–8.",
            ).strip()
            config["search_new"] = search_col.checkbox(
                "Search for new sections", value=True, key=f"{prefix}-search-new",
                help="Search the pulled-back family for exact sections beyond the listed inherited ones.",
            )
    if is_sections:
        st.markdown("**2 · Set the search scope**")
        st.caption("Bounds control which rational-function x-coordinates are tried; a bounded search is not exhaustive.")
        with st.container(key="rh-geometry-sections-search", border=True):
            search_config, timeout = _search_controls(prefix, compact=False)
            config.update(search_config)
    elif config["search_new"]:
        with st.expander("Section search limits", expanded=False):
            search_config, timeout = _search_controls(prefix, compact=not is_sections)
            config.update(search_config)
    else:
        timeout = st.selectbox("Time budget", [30, 60, 120, 300], index=1,
                               key=f"{prefix}-timeout")
    with st.expander("Verify a candidate section", expanded=False):
        st.caption("Enter both coordinates to check a proposed section by exact substitution into the generic equation.")
        candidate_cols = st.columns(2)
        symbol = family.get("parameter") or "t" if is_sections else "u"
        config["candidate_x"] = candidate_cols[0].text_input(
            f"x({symbol})", key=f"{prefix}-candidate-x",
        ).strip()
        config["candidate_y"] = candidate_cols[1].text_input(
            f"y({symbol})", key=f"{prefix}-candidate-y",
        ).strip()

    source_hash = hashlib.sha256(json.dumps(family, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    action_label = "Search and verify sections" if is_sections else "Construct and verify base change"
    action_icon = ":material/search:" if is_sections else ":material/transform:"
    if st.button(action_label, type="primary", icon=action_icon,
                 key=f"{prefix}-run", width="stretch"):
        science_python = setting(db, "science_python", ctx.detected_science_python())
        try:
            with st.spinner("Running bounded exact Sage arithmetic…"):
                result = run_experiment(science_python=science_python, family=family,
                                        mode=mode, config=config, timeout=timeout)
                path = save_experiment(ctx.project_root, result)
            st.session_state[f"{prefix}-result"] = (result, str(path))
        except Exception as exc:
            st.error(f"Experiment did not complete: {exc}")

    saved = st.session_state.get(f"{prefix}-result")
    if (saved and saved[0].get("source_sha256") == source_hash
            and (saved[0].get("experiment") or {}).get("config") == config):
        from pathlib import Path
        _result_panel(saved[0], family, Path(saved[1]))

    history = [
        path for path in recent_experiments(ctx.project_root, limit=30)
        if path.name.startswith(f"{mode}-")
    ][:8]
    if history:
        with st.expander("Recent exact experiments", expanded=False):
            for path in history:
                st.caption(path.name)


def sections_page(db, ctx):
    _page(db, ctx, "sections")


def base_change_page(db, ctx):
    _page(db, ctx, "base_change")
