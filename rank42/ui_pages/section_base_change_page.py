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


def _family_selector(ctx, mode):
    choices = _formula_families(ctx.project_root)
    selected = st.selectbox("Family", [*choices, "Custom formula"], key=f"rh-geometry-{mode}-family")
    if selected == "Custom formula":
        name = st.text_input("Family name", value="Research family", key=f"rh-geometry-{mode}-custom-name")
        parameter = st.text_input("Parameter", value="t", key=f"rh-geometry-{mode}-custom-parameter")
        coefficients = st.columns(5)
        defaults = ("0", "0", "0", "-t^2", "1")
        ainvs = [column.text_input(f"a{index}", value=default,
                                   key=f"rh-geometry-{mode}-custom-a{index}")
                 for column, index, default in zip(coefficients, (1, 2, 3, 4, 6), defaults)]
        raw_sections = st.text_area("Known sections (JSON)", value="[]", height=72,
                                    help='Optional list of {"label":"P", "x":"...", "y":"..."} objects.',
                                    key=f"rh-geometry-{mode}-custom-sections")
        try:
            sections = json.loads(raw_sections)
            if not isinstance(sections, list):
                raise ValueError("known sections must be a JSON list")
        except (ValueError, TypeError) as exc:
            st.error(f"Known sections: {exc}")
            return None, None
        return {"name": name, "parameter": parameter, "a_invariants": ainvs,
                "sections": sections}, None
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


def _result_panel(result, source, path):
    body = result.get("result") or {}
    mode = result.get("mode")
    st.success("Exact experiment completed. Equations and provenance were saved.")
    st.caption(f"Source SHA-256: `{result['source_sha256'][:20]}` · artifact: `{path}`")
    if mode == "sections":
        found = body.get("sections") or []
        cards = st.columns(3)
        cards[0].metric("Exact sections found", len(found))
        cards[1].metric("Known sections verified", len(body.get("known_sections_verified") or []))
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
    elif mode == "base_change" and body.get("new_section_search") is None:
        st.caption("Section search was not requested for this base change.")
    elif not manual:
        st.info("No additional section was found in this bounded search. Larger bounds or another parameter map may produce one.")
    invalid = body.get("known_sections_invalid") or body.get("inherited_sections_invalid") or []
    if invalid:
        st.warning(f"{len(invalid)} declared sections failed exact verification; inspect the artifact before using them.")
    st.caption("An exact section identity does not establish independence or a higher generic rank. "
               "A base change alone does not increase rank.")
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    st.download_button("Download exact artifact", payload, file_name=path.name,
                       mime="application/json", key=f"rh-geometry-download-{mode}")
    with st.expander("Full experiment record", expanded=False):
        st.json(result)


def _page(db, ctx, mode):
    is_sections = mode == "sections"
    st.markdown("#### Find rational sections" if is_sections else "#### Construct a base change")
    st.caption(
        "Search bounded rational-function x-coordinates over Q(t), verify each point identity exactly, "
        "and save reproducible results."
        if is_sections else
        "Pull a formula family through an explicit rational parameter map, verify inherited sections, "
        "and search the derived family for further exact sections."
    )
    family, source = _family_selector(ctx, mode)
    if family is None:
        return
    prefix = f"rh-geometry-{mode}"
    config = {}
    if not is_sections:
        config["parameter_map"] = st.text_input(
            "Parameter map t = f(u)", value="u^2", key=f"{prefix}-map",
            help="Use a rational expression in u. The exact rational-map degree must be 2–8.",
        ).strip()
        config["search_new"] = st.checkbox("Search for sections on the derived family", value=True,
                                            key=f"{prefix}-search-new")
    if is_sections or config["search_new"]:
        search_config, timeout = _search_controls(prefix, compact=not is_sections)
        config.update(search_config)
    else:
        timeout = st.selectbox("Time budget", [30, 60, 120, 300], index=1,
                               key=f"{prefix}-timeout")
    with st.expander("Verify a candidate section", expanded=False):
        candidate_cols = st.columns(2)
        symbol = family.get("parameter") or "t" if is_sections else "u"
        config["candidate_x"] = candidate_cols[0].text_input(
            f"x({symbol})", key=f"{prefix}-candidate-x",
        ).strip()
        config["candidate_y"] = candidate_cols[1].text_input(
            f"y({symbol})", key=f"{prefix}-candidate-y",
        ).strip()

    source_hash = hashlib.sha256(json.dumps(family, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if st.button("Search sections" if is_sections else "Construct base change", type="primary",
                 key=f"{prefix}-run"):
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

    history = recent_experiments(ctx.project_root, limit=8)
    if history:
        with st.expander("Recent exact experiments", expanded=False):
            for path in history:
                st.caption(path.name)


def sections_page(db, ctx):
    _page(db, ctx, "sections")


def base_change_page(db, ctx):
    _page(db, ctx, "base_change")
