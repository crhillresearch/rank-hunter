import json
import hashlib

from sage.all import EllipticCurve

from rank42.db import connect, upsert_curve
from rank42.heegner_point import run_heegner_point
from rank42.pipeline_catalog import (
    normalize_pipeline, stage_spec, template_stages, validate_pipeline,
)
from rank42.pipeline_runner import _rank_population_rows, _stage_result
from rank42.formula_family import FormulaFamily
from rank42.section_independence import certify_discovered_sections


def test_heegner_point_reaches_ordinary_independence_certificate(tmp_path):
    E = EllipticCurve("37a1")
    db = connect(str(tmp_path / "heegner.db"))
    curve_id = upsert_curve(
        db, family="heegner-test", parameter="1",
        a_invariants_json=json.dumps([str(a) for a in E.a_invariants()]),
    )
    assert validate_pipeline("curve", normalize_pipeline(["heegner_point"])) == []
    result, _, _ = _stage_result(
        "heegner_point", db=db, run={"id": 1}, run_config={},
        target={"mode": "curve"},
        context={"curve_id": curve_id, "E": E, "parameter": "1"},
        config=stage_spec("heegner_point").defaults, stage_index=1,
    )
    assert result["status"] == "completed"
    assert result["rank_growth"] == 1
    assert result["rigorous_lower"] == 1
    point = db.execute(
        "SELECT exact_verified, rigorous_independent FROM points WHERE curve_id=?",
        (curve_id,),
    ).fetchone()
    assert point["exact_verified"] == 1
    assert point["rigorous_independent"] == 1


def test_heegner_worker_marks_rank_zero_curve_inapplicable():
    result = run_heegner_point(EllipticCurve("11a1"), timeout=15)
    assert result["status"] == "not_applicable"
    assert "point" not in result


def test_survivor_ranking_prefers_certified_growth_over_point_inventory(tmp_path):
    db = connect(str(tmp_path / "growth.db"))
    rows = [
        {"id": 1, "curve_id": None, "rigorous_lower": 3, "score": 20,
         "stage_results_json": json.dumps({"1": {"stage_id": "affine_search",
                                              "result": {"status": "completed", "rank_growth": 0}}})},
        {"id": 2, "curve_id": None, "rigorous_lower": 3, "score": 10,
         "stage_results_json": json.dumps({"1": {"stage_id": "pointed_quartic",
                                              "result": {"status": "completed", "rank_growth": 1}}})},
    ]
    ranked = _rank_population_rows(db, rows, "rank_then_growth_then_score")
    assert [row["id"] for row, _ in ranked] == [2, 1]
    assert validate_pipeline(
        "curve", template_stages("frontiermath_record_breaker", "curve")
    ) == []


def test_section_independence_proves_generic_lower_from_exact_fiber():
    E = EllipticCurve("37a1")
    data = {
        "name": "Constant test family", "parameter": "t",
        "a_invariants": [str(a) for a in E.a_invariants()], "sections": [],
    }
    family = FormulaFamily(data)
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prior = {
        "status": "completed", "evidence": "exact_function_field_identity",
        "source_sha256": digest, "sections": [{"x": "0", "y": "0"}],
    }
    stages = normalize_pipeline([
        "nagao_screen", "bounded_section_search", "generic_section_independence",
    ])
    assert validate_pipeline("family", stages) == []
    result = certify_discovered_sections(family, "1", E, prior, timeout=20)
    assert result["status"] == "completed"
    assert result["generic_lower"] == 1
    assert result["specialization_certificate"]["independent"] is True

    prior["sections"] = [{"x": "2", "y": "0"}]
    invalid = certify_discovered_sections(family, "1", E, prior, timeout=20)
    assert invalid["status"] == "error"
