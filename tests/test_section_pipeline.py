"""A bounded section identity can supply exact fiber points without rank claims."""

import json

from rank42.db import connect, upsert_curve
from rank42.formula_family import FormulaFamily
from rank42.section_pipeline import bounded_section_search


def test_bounded_section_search_specializes_exactly_and_reuses_family_search(tmp_path):
    family = FormulaFamily({
        "name": "Small constant positive control", "parameter": "t",
        "a_invariants": ["0", "0", "0", "-1", "0"], "sections": [],
    })
    db = connect(tmp_path / "rank42.db")
    curve = family.curve(2)
    curve_id = upsert_curve(
        db, family="section-test", parameter="2", status="pipeline_searching",
        a_invariants_json=json.dumps([str(a) for a in curve.a_invariants()]),
    )
    arguments = dict(
        run_id=17, curve_id=curve_id, curve=curve, family=family,
        parameter="2", project_root=tmp_path,
        config={"degree": 0, "coefficient_bound": 2, "max_trials": 20,
                "max_results": 8, "rational_x": False, "timeout": 10},
    )
    result = bounded_section_search(db, **arguments)
    replay = bounded_section_search(db, **arguments)

    assert result["status"] == "completed"
    assert result["sections_found"] == 3
    assert result["specialization_status"] == "exact_on_stored_curve"
    assert result["specialized_points"] == 3
    assert result["independence"] == "not_certified"
    assert result["experiment_artifact"] == replay["experiment_artifact"]
    rows = db.execute(
        "SELECT exact_verified,rigorous_independent FROM points WHERE curve_id=?",
        (curve_id,),
    ).fetchall()
    assert len(rows) == 3
    assert all(row["exact_verified"] and not row["rigorous_independent"]
               for row in rows)
    db.close()
