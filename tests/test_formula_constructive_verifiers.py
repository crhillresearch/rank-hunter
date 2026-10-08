from types import SimpleNamespace

from rank42.constructive_artifact import (
    DIVISION_SCHEMA,
    SECTION_SCHEMA,
    SQUARE_SPECIALIZATION_SCHEMA,
    TRACE_SCHEMA,
    bind_artifact_hash,
    verify_division_artifact,
    verify_section_artifact,
    verify_square_specialization_artifact,
    verify_trace_artifact,
)
from rank42.formula_constructive_verifiers import (
    DIVISION_VERIFIER,
    SECTION_VERIFIER,
    SQUARE_VERIFIER,
    TRACE_VERIFIER,
)
from rank42.formula_family import FormulaFamily


def _family():
    return FormulaFamily({
        "name": "constructive verifier control",
        "generic_rank": 1,
        "parameter": "t",
        "a_invariants": ["0", "0", "0", "-1", "1"],
        "sections": [{
            "label": "P",
            "x": "0",
            "y": "1",
        }],
    })


def _context():
    return {
        "pipeline_run_id": 7,
        "pipeline_candidate_id": 11,
        "plugin": SimpleNamespace(id="demo"),
        "variant": SimpleNamespace(
            id="control",
            family_spec="json:/tmp/control-family.json",
        ),
        "family": _family(),
    }


def _family_identity():
    return {
        "plugin_id": "demo",
        "variant_id": "control",
        "family_spec": "json:/tmp/control-family.json",
    }


def _section_artifact(*, x="0", y="1"):
    return {
        "id": "section-P",
        "schema": SECTION_SCHEMA,
        "artifact_kind": "section",
        "family": _family_identity(),
        "section": {
            "parameter_variable": "t",
            "x": x,
            "y": y,
        },
        "height": {
            "kind": "family_enumeration_shell",
            "value": "1",
            "mode": "exact",
            "normalization": "family_defined",
            "coefficient_bound": 8,
        },
        "implementation": {
            "plugin_id": "demo",
            "section_index": 1,
            "enumeration_rule": "declared_section_index_1_based",
        },
        "verifier": SECTION_VERIFIER,
    }


def _section_request():
    return {
        "height": 1,
        "height_mode": "exact",
        "height_kind": "family_enumeration_shell",
        "height_normalization": "family_defined",
        "coefficient_bound": 8,
    }


def test_formula_family_constructive_verifiers_accept_exact_control_chain():
    context = _context()

    section = bind_artifact_hash(_section_artifact())
    section_verification = verify_section_artifact(
        section,
        context=context,
        request=_section_request(),
    )
    assert section_verification["verified"] is True
    section["exact_construction"] = True
    section["verification"] = section_verification

    trace = bind_artifact_hash({
        "id": "trace-2P",
        "schema": TRACE_SCHEMA,
        "artifact_kind": "trace_section",
        "family": _family_identity(),
        "source_section": {
            "artifact_id": section["id"],
            "artifact_hash": section["artifact_hash"],
        },
        "extension": {
            "degree": 2,
            "field": "QQ(t)(sqrt(2))",
            "construction": "base_rational_section_trace",
            "conjugates": [
                {"parameter_variable": "t", "x": "0", "y": "1"},
                {"parameter_variable": "t", "x": "0", "y": "1"},
            ],
        },
        "lineage": {
            "pipeline_run_id": 7,
            "pipeline_candidate_id": 11,
        },
        "trace": {
            "parameter_variable": "t",
            "x": "1/4",
            "y": "-7/8",
            "relation": "sum_of_conjugates",
            "base_field": "QQ",
        },
        "verifier": TRACE_VERIFIER,
    })
    trace_verification = verify_trace_artifact(
        trace,
        context=context,
        request={"extension_degree": 2},
        source_sections=[section],
    )
    assert trace_verification["verified"] is True
    trace["exact_construction"] = True
    trace["verification"] = trace_verification

    condition = bind_artifact_hash({
        "id": "condition-2P",
        "schema": DIVISION_SCHEMA,
        "artifact_kind": "division_condition",
        "family": _family_identity(),
        "source_trace": {
            "artifact_id": trace["id"],
            "artifact_hash": trace["artifact_hash"],
        },
        "lineage": {
            "pipeline_run_id": 7,
            "pipeline_candidate_id": 11,
        },
        "relation": {
            "kind": "division",
            "division": 2,
            "slope_mode": "family_exact",
            "condition": "1",
            "equation": "2*P=Q",
            "preimage": {
                "parameter_variable": "t",
                "x": "0",
                "y": "1",
            },
        },
        "verifier": DIVISION_VERIFIER,
    })
    division_verification = verify_division_artifact(
        condition,
        context=context,
        request={"division": 2, "slope_mode": "family_exact"},
        source_traces=[trace],
    )
    assert division_verification["verified"] is True
    condition["exact_construction"] = True
    condition["verification"] = division_verification

    square = {
        "id": "square-control",
        "schema": SQUARE_SPECIALIZATION_SCHEMA,
        "artifact_kind": "square_specialization",
        "family": _family_identity(),
        "lineage": {
            "pipeline_run_id": 7,
            "pipeline_candidate_id": 11,
        },
        "source_condition": {
            "artifact_id": condition["id"],
            "artifact_hash": condition["artifact_hash"],
        },
        "specialization": {"parameter": "2"},
        "square": {"value": "1", "root": "1"},
        "verifier": SQUARE_VERIFIER,
    }
    square_verification = verify_square_specialization_artifact(
        square,
        context=context,
        request={},
        source_conditions=[condition],
    )
    assert square_verification["verified"] is True
    assert square_verification["condition_value"] == "1"
    assert square_verification["child_parameter"] == "2"


def test_formula_family_declared_section_rejects_tampered_formula():
    result = verify_section_artifact(
        bind_artifact_hash(_section_artifact(x="1")),
        context=_context(),
        request=_section_request(),
    )
    assert result["verified"] is False
    assert result["reason"] == (
        "section_formula_does_not_match_declared_family_section"
    )
    assert result["verifier_result"]["reason"] == result["reason"]
