"""Validate and register a local formula family for candidate generation.

This module runs in Science Python, outside the Streamlit process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

from rank42.formula_family import FormulaFamily


RESULT_MARKER = "RANK42_CUSTOM_FAMILY_RESULT="


def register_custom_candidate_family(project_root, formula):
    data = {
        "name": str(formula.get("name") or "").strip(),
        "parameter": str(formula.get("parameter") or "t").strip(),
        "a_invariants": [str(value).strip() for value in formula.get("a_invariants") or []],
        "sections": [],
    }
    if not data["name"]:
        raise ValueError("Give the custom family a name")
    if len(data["a_invariants"]) != 5 or any(not value for value in data["a_invariants"]):
        raise ValueError("Enter all five Weierstrass coefficients")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", data["parameter"]):
        raise ValueError("Parameter must be an identifier such as t or u")

    family = FormulaFamily(data)
    try:
        proof = family.validate_symbolically()
    except (ArithmeticError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"The generic equation is invalid: {exc}") from exc
    validation_parameter = None
    for value in (0, 1, -1, 2, -2, "1/2", "-1/2", 3, -3):
        if family.curve(value) is not None:
            validation_parameter = str(value)
            break
    if validation_parameter is None:
        raise ValueError("No smooth fiber was found at the standard validation parameters")

    identity = json.dumps(data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    plugin_id = f"rh_custom_equation_{digest}"
    parent = Path(project_root).resolve() / "plugins" / "families"
    destination = parent / plugin_id
    manifest = {
        "schema_version": 1,
        "plugin_type": "family",
        "id": plugin_id,
        "name": data["name"],
        "version": "0.1.0",
        "icon": ":material/functions:",
        "description": "Locally registered exact Weierstrass formula for candidate generation.",
        "enabled_by_default": True,
        "default_variant": "custom",
        "capabilities": ["candidate_generation"],
        "variants": [{
            "id": "custom",
            "name": "Custom equation",
            "curve_family_name": data["name"],
            "family": {"kind": "json", "file": "family.json"},
            "validation_parameter": validation_parameter,
            "candidate_defaults": {
                "a_min": -3000, "a_max": 3000, "b_min": 1, "b_max": 300,
                "stage_bounds": "127,523", "stage_keeps": "1000,250",
                "top": 250, "engine": "sampled",
            },
        }],
    }
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = destination / "family.json"
        if not existing.is_file() or json.loads(existing.read_text(encoding="utf-8")) != data:
            raise FileExistsError(f"{plugin_id} already exists with different equations")
    else:
        with tempfile.TemporaryDirectory(prefix=f".{plugin_id}-", dir=parent) as temp:
            temporary = Path(temp)
            (temporary / "family.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
            )
            (temporary / "plugin.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
            )
            temporary.rename(destination)
    return {
        "plugin_id": plugin_id,
        "variant_id": "custom",
        "family_name": data["name"],
        "validation_parameter": validation_parameter,
        "discriminant": proof["discriminant"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    formula = json.load(sys.stdin)
    result = register_custom_candidate_family(args.project_root, formula)
    print(RESULT_MARKER + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
