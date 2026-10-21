"""Generic exact verifiers for JSON FormulaFamily Constructive artifacts.

Family plugins emit typed artifacts. Core reconstructs the declared JSON family
over its rational-function field and independently checks the claimed identities
exactly before an artifact may be marked exact.
"""
from __future__ import annotations


SECTION_VERIFIER = "formula_family_declared_section_v1"
TRACE_VERIFIER = "formula_family_base_section_trace_v1"
DIVISION_VERIFIER = "formula_family_exact_division_v1"
SQUARE_VERIFIER = "formula_family_condition_evaluation_v1"


def _formula_family(context):
    family = context.get("family")
    data = getattr(family, "data", None)
    parameter = getattr(family, "parameter", None)
    if family is None or not isinstance(data, dict) or not parameter:
        raise ValueError("constructive verifier requires a JSON FormulaFamily")
    sections = data.get("sections")
    ainvs = data.get("a_invariants")
    if not isinstance(sections, list):
        raise ValueError("FormulaFamily sections are unavailable")
    if not isinstance(ainvs, list) or len(ainvs) != 5:
        raise ValueError("FormulaFamily a-invariants are unavailable")
    return family, data, str(parameter)


def _generic_curve(context):
    from sage.all import QQ, EllipticCurve, PolynomialRing, sage_eval

    family, data, parameter = _formula_family(context)
    ring = PolynomialRing(QQ, parameter)
    field = ring.fraction_field()
    t = field(ring.gen())

    def evaluate(expr):
        return field(sage_eval(str(expr), locals={parameter: t}))

    curve = EllipticCurve(
        field,
        [evaluate(expr) for expr in data["a_invariants"]],
    )
    return family, data, parameter, field, curve, evaluate


def _point(curve, evaluate, record, *, label):
    if not isinstance(record, dict):
        raise ValueError(f"{label} must be an object")
    if record.get("x") is None or record.get("y") is None:
        raise ValueError(f"{label} requires exact x/y formulas")
    try:
        return curve(evaluate(record["x"]), evaluate(record["y"]))
    except Exception as exc:
        raise ValueError(f"{label} is not on the generic curve") from exc


def verify_declared_section(
    *, artifact, context, request, family, section, height
):
    _family, data, parameter, _field, curve, evaluate = _generic_curve(
        context
    )
    if str(section.get("parameter_variable") or "") != parameter:
        return {
            "verified": False,
            "reason": "section_parameter_variable_mismatch",
        }

    implementation = dict(artifact.get("implementation") or {})
    if (
        str(implementation.get("enumeration_rule") or "")
        != "declared_section_index_1_based"
    ):
        return {
            "verified": False,
            "reason": "unsupported_section_enumeration_rule",
        }
    try:
        section_index = int(implementation.get("section_index"))
    except Exception:
        return {
            "verified": False,
            "reason": "section_index_missing_or_invalid",
        }
    if section_index < 1 or section_index > len(data["sections"]):
        return {
            "verified": False,
            "reason": "section_index_out_of_range",
        }

    requested_height = int(request.get("height") or 0)
    mode = str(request.get("height_mode") or "")
    if mode == "exact" and section_index != requested_height:
        return {
            "verified": False,
            "reason": "section_index_not_in_exact_shell",
        }
    if mode == "up_to" and section_index > requested_height:
        return {
            "verified": False,
            "reason": "section_index_above_requested_shell",
        }

    source = data["sections"][section_index - 1]
    if not isinstance(source, dict):
        return {
            "verified": False,
            "reason": "declared_section_record_is_not_object",
        }
    try:
        source_x = evaluate(source["x"])
        source_y = evaluate(source["y"])
        artifact_x = evaluate(section["x"])
        artifact_y = evaluate(section["y"])
    except Exception as exc:
        return {
            "verified": False,
            "reason": "section_formula_parse_failed",
            "error": str(exc),
        }
    if artifact_x != source_x or artifact_y != source_y:
        return {
            "verified": False,
            "reason": "section_formula_does_not_match_declared_family_section",
        }

    try:
        point = curve(artifact_x, artifact_y)
    except Exception as exc:
        return {
            "verified": False,
            "reason": "declared_section_not_on_generic_curve",
            "error": str(exc),
        }

    return {
        "verified": True,
        "method": "formula_family_declared_section_exact_identity",
        "section_index": section_index,
        "section_label": source.get("label"),
        "parameter_variable": parameter,
        "point": [str(point[0]), str(point[1])],
        "height_kind": height.get("kind"),
        "height_value": height.get("value"),
    }


def verify_base_section_trace(
    *, artifact, context, request, source_section, extension, trace
):
    _family, _data, parameter, _field, curve, evaluate = _generic_curve(
        context
    )
    if (
        str(extension.get("construction") or "")
        != "base_rational_section_trace"
    ):
        return {
            "verified": False,
            "reason": "unsupported_trace_construction",
        }
    degree = int(extension.get("degree") or 0)
    if degree < 1:
        return {"verified": False, "reason": "invalid_trace_degree"}

    source = dict(source_section.get("section") or {})
    try:
        point = _point(
            curve, evaluate, source, label="source section"
        )
    except ValueError as exc:
        return {"verified": False, "reason": str(exc)}

    conjugates = extension.get("conjugates") or []
    if len(conjugates) != degree:
        return {
            "verified": False,
            "reason": "trace_conjugate_count_mismatch",
        }
    for conjugate in conjugates:
        try:
            conjugate_point = _point(
                curve,
                evaluate,
                conjugate,
                label="trace conjugate",
            )
        except ValueError as exc:
            return {"verified": False, "reason": str(exc)}
        if conjugate_point != point:
            return {
                "verified": False,
                "reason": "base_section_trace_conjugate_not_fixed",
            }

    if str(trace.get("parameter_variable") or "") != parameter:
        return {
            "verified": False,
            "reason": "trace_parameter_variable_mismatch",
        }
    try:
        claimed = _point(
            curve, evaluate, trace, label="claimed trace"
        )
    except ValueError as exc:
        return {"verified": False, "reason": str(exc)}

    expected = degree * point
    if claimed != expected:
        return {
            "verified": False,
            "reason": "trace_does_not_equal_sum_of_conjugates",
        }
    return {
        "verified": True,
        "method": "formula_family_base_section_exact_trace",
        "degree": degree,
        "source_point": [str(point[0]), str(point[1])],
        "trace_point": [str(expected[0]), str(expected[1])],
    }


def verify_exact_division(
    *, artifact, context, request, source_trace, relation
):
    _family, _data, parameter, field, curve, evaluate = _generic_curve(
        context
    )
    if str(relation.get("slope_mode") or "") != "family_exact":
        return {
            "verified": False,
            "reason": "formula_family_division_requires_family_exact_mode",
        }
    preimage = relation.get("preimage")
    if not isinstance(preimage, dict):
        return {
            "verified": False,
            "reason": "division_preimage_is_required",
        }
    if str(preimage.get("parameter_variable") or "") != parameter:
        return {
            "verified": False,
            "reason": "division_preimage_parameter_variable_mismatch",
        }

    try:
        point = _point(
            curve, evaluate, preimage, label="division preimage"
        )
        target = _point(
            curve,
            evaluate,
            dict(source_trace.get("trace") or {}),
            label="source trace",
        )
    except ValueError as exc:
        return {"verified": False, "reason": str(exc)}

    division = int(relation.get("division") or 0)
    if division * point != target:
        return {
            "verified": False,
            "reason": "division_preimage_does_not_multiply_to_source_trace",
        }

    try:
        condition = evaluate(relation.get("condition"))
    except Exception as exc:
        return {
            "verified": False,
            "reason": "division_condition_parse_failed",
            "error": str(exc),
        }
    if condition != field(1):
        return {
            "verified": False,
            "reason": "exact_division_control_requires_identity_condition_one",
        }

    return {
        "verified": True,
        "method": "formula_family_exact_division_identity",
        "division": division,
        "condition": str(condition),
        "preimage": [str(point[0]), str(point[1])],
        "source_trace": [str(target[0]), str(target[1])],
    }


def verify_condition_specialization(
    *,
    artifact,
    context,
    request,
    source_condition,
    child_parameter,
    square_value,
    square_root,
):
    from sage.all import QQ, sage_eval

    family, _data, parameter = _formula_family(context)
    relation = dict(source_condition.get("relation") or {})
    condition = relation.get("condition")
    if condition is None:
        return {
            "verified": False,
            "reason": "source_condition_expression_missing",
        }
    try:
        value = QQ(
            sage_eval(
                str(condition),
                locals={parameter: QQ(str(child_parameter))},
            )
        )
    except Exception as exc:
        return {
            "verified": False,
            "reason": "condition_specialization_failed",
            "error": str(exc),
        }

    try:
        child_curve = family.curve(QQ(str(child_parameter)))
    except Exception:
        child_curve = None
    if child_curve is None:
        return {
            "verified": False,
            "reason": "child_specialization_is_singular_or_undefined",
        }

    return {
        "verified": True,
        "method": "formula_family_exact_condition_evaluation",
        "condition_value": str(value),
        "child_parameter": str(QQ(str(child_parameter))),
    }
