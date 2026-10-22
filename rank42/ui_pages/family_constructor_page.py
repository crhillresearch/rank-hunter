"""Torsion-aware constructive family research workbench."""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone

import streamlit as st

from rank42.plugins import (
    Plugin, discover_plugins, is_enabled, variant_family_definition,
    variant_torsion_groups,
)
from rank42.torsion import MAZUR_TORSION_CHOICES
from .common import setting, title


WORKER = Path(__file__).resolve().parents[1] / "family_constructor_worker.py"
SCHEMA = "rank42.family_constructor.v1"


def _sources(db, project_root, torsion):
    choices = {}
    for plugin in discover_plugins(project_root):
        if not isinstance(plugin, Plugin) or plugin.plugin_type != "family":
            continue
        if not is_enabled(db, plugin):
            # Family construction is a read-only use of the source definition;
            # disabled plugins are intentionally omitted from the picker.
            continue
        for variant in plugin.variants:
            if torsion not in variant_torsion_groups(plugin, variant):
                continue
            family = variant_family_definition(plugin, variant)
            if str(family.get("kind") or "json") != "json":
                continue
            path = (plugin.root / str(family.get("file") or "family.json")).resolve()
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            label = f"{plugin.name} · {variant.name} [{variant.id}]"
            choices[label] = {
                "plugin_id": plugin.id, "variant_id": variant.id,
                "path": path, "family": data,
            }
    return choices


def _run(science_python, family, torsion, x_values, options, timeout):
    request = {
        "schema": SCHEMA, "family": family,
        "torsion_label": torsion, "x_values": list(x_values),
        "options": dict(options),
    }
    try:
        proc = subprocess.run(
            [str(science_python), str(WORKER)],
            input=json.dumps(request), text=True, capture_output=True,
            timeout=int(timeout), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Construction exceeded {timeout}s; no result was registered") from exc
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "Sage constructor failed").strip()[-1600:])
    try:
        result = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError("Sage constructor returned invalid JSON") from exc
    if result.get("schema") != SCHEMA:
        raise RuntimeError("Sage constructor returned an unsupported result")
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _register(project_root, result, source, torsion):
    family = result["family"]
    proof = result["construction"]
    provenance = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_plugin": source["plugin_id"],
        "source_variant": source["variant_id"],
        "torsion_group": torsion,
        **proof,
    }
    canonical = json.dumps({"family": family, "provenance": provenance}, sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "_", family["name"].lower()).strip("_")[:36]
    plugin_id = f"rh_constructed_{slug}_{digest}"
    root = Path(project_root).resolve() / "plugins" / "families" / plugin_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "family.json").write_text(
        json.dumps(family, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    (root / "construction.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "plugin_type": "family",
        "id": plugin_id,
        "name": family["name"],
        "version": "0.1.0",
        "icon": ":material/add_box:",
        "description": "Constructed by Rank Hunter's exact constant-x conic base-change workflow.",
        "enabled_by_default": True,
        "default_variant": "constructed",
        "capabilities": ["candidate_generation"],
        "variants": [{
            "id": "constructed",
            "name": "Conic-forced section",
            "curve_family_name": family["name"],
            "family": {"kind": "json", "file": "family.json"},
            "torsion_groups": [torsion],
            "torsion_provider_role": "prescribed_subfamily",
            "validation_parameter": proof["validation_parameter"],
            **({
                "generic_rank": 1,
                "verified_generic_rank_lower": 1,
                "generic_rank_claim_state": "generic_lower_bound_verified",
            } if proof.get("generic_rank_lower_bound") == 1 else {}),
            "candidate_defaults": {
                "a_min": -2000, "a_max": 2000, "b_min": 1, "b_max": 200,
                "stage_bounds": "500,1000,2000",
                "stage_keeps": "8000,2000,500", "top": 500, "engine": "sieve",
            },
        }],
        "construction_record": "construction.json",
    }
    (root / "plugin.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    receipt_dir = Path(project_root).resolve() / "runs" / "family-constructor"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{plugin_id}.json"
    receipt_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plugin_id, root, receipt_path


def _handoff(plugin_id):
    st.session_state["byo_builder_state_version"] = 2
    st.session_state["byo-target-mode"] = "Family"
    st.session_state["byo_target_mode_revision"] = int(
        st.session_state.get("byo_target_mode_revision") or 0
    ) + 1
    st.session_state["byo-family"] = f"{plugin_id}:constructed"
    st.session_state["byo-family-source"] = "Generated candidates"
    st.session_state["builder_main_view"] = "Editor"
    st.session_state["builder_view_revision"] = int(
        st.session_state.get("builder_view_revision") or 0
    ) + 1
    st.session_state["rh_page"] = "Pipelines"
    st.rerun()


def page(db, ctx):
    title("Torsion-aware Family Constructor", icon=":material/add_box:")
    st.markdown(
        "Construct a new one-parameter family from an existing prescribed-torsion "
        "family by forcing a rational point. The first engine uses a constant-x "
        "discriminant conic, then verifies the pullback identity exactly over Q(u)."
    )
    left, right = st.columns([1.1, 1])
    with left:
        torsion = st.selectbox("Target rational torsion", MAZUR_TORSION_CHOICES,
                               index=MAZUR_TORSION_CHOICES.index("C5"),
                               key="rh-family-constructor-torsion")
        sources = _sources(db, ctx.project_root, torsion)
        if sources:
            labels = list(sources)
            selected = st.selectbox("Source family", labels, key="rh-family-constructor-source")
            source = sources[selected]
            st.caption(
                f"{source['family'].get('name') or selected} · "
                f"{len(source['family'].get('sections') or [])} declared source sections"
            )
        else:
            source = None
            st.info(f"No enabled JSON family currently declares {torsion}.")
    with right:
        x_value = st.text_input(
            "Force a point with x =", value="1", key="rh-family-constructor-x",
            help="A rational constant. The constructor forms the exact y-discriminant cover at this x-coordinate.",
        )
        sweep = st.checkbox(
            "Try nearby rational x-values if this one fails", value=True,
            key="rh-family-constructor-x-sweep",
        )
        x_bound = st.select_slider(
            "Fallback x search range", options=[1, 2, 3, 5, 8], value=3,
            format_func=lambda n: f"numerators −{n}…{n} over denominators 1 and 2",
            disabled=not sweep, key="rh-family-constructor-x-bound",
        )
        timeout = st.select_slider(
            "Exact computation budget", options=[30, 60, 120, 240], value=120,
            format_func=lambda n: f"{n} seconds", key="rh-family-constructor-timeout",
        )
    with st.expander("Advanced construction search", expanded=False):
        base_col, spec_col = st.columns(2)
        base_num_bound = base_col.select_slider(
            "Conic basepoint numerator bound", options=[10, 30, 60], value=30,
            key="rh-family-constructor-base-num",
        )
        base_den_bound = base_col.select_slider(
            "Conic basepoint denominator bound", options=[6, 12, 24], value=12,
            key="rh-family-constructor-base-den",
        )
        specialization_depth = spec_col.radio(
            "Exact torsion and point checks", ["Quick", "Expanded"], horizontal=True,
            key="rh-family-constructor-specialization-depth",
        )
        spec_col.caption(
            "Expanded checks more rational parameter values. Every accepted specialization "
            "still gets an exact torsion and point-order computation."
        )
    st.caption("The constructor reports when a source and chosen bounds do not produce a rational conic construction.")

    if st.button("Construct and verify family", type="primary", disabled=source is None,
                 key="rh-family-constructor-run"):
        try:
            science_python = setting(db, "science_python", ctx.detected_science_python())
            try:
                initial_x = str(Fraction(x_value.strip()))
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError("x-coordinate must be a rational number, such as 1, -2, or 3/2") from exc
            x_values = [initial_x]
            if sweep:
                for denominator in (1, 2):
                    for numerator in range(-int(x_bound), int(x_bound) + 1):
                        value = str(Fraction(numerator, denominator))
                        if value not in x_values:
                            x_values.append(value)
            options = {
                "basepoint_numerator_bound": int(base_num_bound),
                "basepoint_denominator_bound": int(base_den_bound),
                "specialization_depth": specialization_depth.lower(),
            }
            with st.spinner("Factoring the exact discriminant cover and checking the pullback…"):
                result = _run(science_python, source["family"], torsion, x_values, options, timeout)
            result["source"] = {
                "plugin_id": source["plugin_id"], "variant_id": source["variant_id"],
                "name": source["family"].get("name"),
            }
            result["torsion_group"] = torsion
            st.session_state["rh-family-constructor-result"] = result
            st.session_state.pop("rh-family-constructor-registered", None)
            st.session_state.pop("rh-family-constructor-error", None)
        except Exception as exc:
            st.session_state["rh-family-constructor-error"] = str(exc)
            st.session_state.pop("rh-family-constructor-result", None)
    error = st.session_state.get("rh-family-constructor-error")
    if error:
        st.warning(error)
    result = st.session_state.get("rh-family-constructor-result")
    if not result:
        with st.expander("What this constructor proves", expanded=False):
            st.markdown(
                "A rational parametrization of the discriminant conic gives a new exact "
                "section after base change. A specialization with the selected exact "
                "torsion is required before the result can be registered for torsion "
                "search. If that specialization also shows the section has infinite "
                "order, the family receives a generic rank lower bound of one. It does "
                "not claim an exact generic rank or independence beyond that section."
            )
        return

    family, proof = result["family"], result["construction"]
    st.success("Exact conic parametrization and section identity verified.")
    st.code(
        f"source parameter: {proof['source_parameter']}\n"
        f"cover: z^2 = {proof['cover_equation_squarefree']}\n"
        f"base point: ({', '.join(proof['cover_basepoint'])})\n"
        f"parameter map: {proof['source_parameter']} = {proof['parameter_map']}\n"
        f"section: x = {proof['section']['x']}\ny = {proof['section']['y']}",
        language="text",
    )
    lower = proof.get("generic_rank_lower_bound")
    if lower:
        st.success(f"Specialization proves the constructed section is non-torsion: generic rank ≥{lower}.")
    else:
        st.info("The section identity is exact, but this run did not establish that the section is non-torsion.")
    with st.expander("Derived family equations", expanded=False):
        st.code("\n".join(
            f"a{i} = {expr}" for i, expr in zip((1, 2, 3, 4, 6), family["a_invariants"])
        ), language="text")
        st.json(proof)
    st.caption("x-values checked: " + ", ".join(proof.get("x_values_attempted") or []))
    if st.button("Register as a searchable family", type="primary", key="rh-family-constructor-register"):
        try:
            plugin_id, root, receipt_path = _register(
                ctx.project_root, result, result["source"], result["torsion_group"],
            )
            st.session_state["rh-family-constructor-registered"] = {
                "plugin_id": plugin_id, "root": str(root), "receipt": str(receipt_path),
            }
        except Exception as exc:
            st.error(f"Could not register the constructed family: {exc}")
    registered = st.session_state.get("rh-family-constructor-registered") or {}
    if registered:
        st.caption(f"Registered `{registered['plugin_id']}` · proof record `{registered['receipt']}`")
        if st.button("Open this family in Pipelines", key="rh-family-constructor-pipelines"):
            _handoff(registered["plugin_id"])
