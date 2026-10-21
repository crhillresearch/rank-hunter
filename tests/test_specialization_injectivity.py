import json
from types import SimpleNamespace

from sage.all import QQ

from rank42 import pipeline_runner as runner
from rank42.db import connect, get_curve, upsert_curve
from rank42.family_evidence import (
    family_evidence_key,
    list_family_evidence,
    record_family_evidence,
    reduce_family_evidence,
)
from rank42.formula_family import FormulaFamily
from rank42.rank_evidence import record_rank_evidence
from rank42.specialization_injectivity import run_specialization_injectivity


def _family():
    return FormulaFamily({
        "name": "Gusic-Tadic control family",
        "generic_rank": 1,
        "parameter": "t",
        "a_invariants": ["0", "t", "0", "t*(t+1)", "0"],
        "sections": [{
            "label": "P",
            "x": "1",
            "y": "t+1",
        }],
    })


def test_real_gusic_tadic_control_passes_and_square_divisor_control_fails():
    family = _family()

    passed = run_specialization_injectivity(
        family,
        2,
        timeout=30,
        max_divisors=256,
    )
    assert passed["status"] == "completed"
    assert passed["injective"] is True
    assert passed["rigorous"] is True
    assert passed["assumptions"] == []
    assert passed["specialization_parameter"] == "2"
    assert passed["checks_completed"] > 0
    assert all(not rec["is_square_in_Q"] for rec in passed["checks"])

    failed = run_specialization_injectivity(
        family,
        3,
        timeout=30,
        max_divisors=256,
    )
    assert failed["status"] == "rejected"
    assert failed["reason"] == "squarefree_divisor_specializes_to_square"
    assert failed["failed_check"]["is_square_in_Q"] is True
    assert failed["failed_check"]["value"] == "4"


def test_noncriterion_formula_family_is_explicitly_unsupported():
    family = FormulaFamily({
        "name": "unsupported short family",
        "parameter": "t",
        "a_invariants": ["0", "0", "0", "t", "1"],
        "sections": [],
    })
    result = run_specialization_injectivity(
        family,
        2,
        timeout=30,
        max_divisors=256,
    )
    assert result["status"] == "unsupported"
    assert result["reason"] == (
        "criterion_requires_y2_eq_x3_plus_Ax2_plus_Bx"
    )


def test_pipeline_persists_generic_evidence_separately_from_fiber_rank(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "injectivity.db")
    family = _family()
    E = family.curve(QQ(2))
    family_spec = "json:/tmp/gusic-tadic-control.json"
    family_sha = "control-sha"
    curve_id = upsert_curve(
        db,
        family="Gusic-Tadic control family",
        parameter="2",
        a_invariants_json=json.dumps([str(x) for x in E.a_invariants()]),
        family_spec=family_spec,
        family_sha256=family_sha,
        plugin_id="control-plugin",
        plugin_version="1.0",
    )
    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=E.a_invariants(),
        data={
            "engine": "test-rigorous-upper",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": 1,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "conditional_mw_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
    )

    monkeypatch.setattr(
        runner,
        "run_specialization_injectivity",
        lambda *args, **kwargs: {
            "status": "completed",
            "injective": True,
            "rigorous": True,
            "criterion": "Gusic-Tadic Theorem 1.3",
            "specialization_parameter": "2",
            "checks": [{"divisor": "t", "value": "2", "is_square_in_Q": False}],
            "checks_completed": 1,
            "assumptions": [],
        },
    )
    monkeypatch.setattr(
        runner,
        "run_exact_certificate",
        lambda *args, **kwargs: {
            "status": "completed",
            "independent": True,
            "certificate": {"rank": 1, "kind": "test"},
        },
    )

    result, terminal, stop = runner._stage_result(
        "specialization_injectivity_certificate",
        db=db,
        run={"id": 1, "target_mode": "family"},
        run_config={"target_rank": 10},
        target={},
        context={
            "curve_id": curve_id,
            "E": E,
            "source_E": E,
            "family": family,
            "plugin": SimpleNamespace(id="control-plugin", version="1.0"),
            "variant": SimpleNamespace(family_spec=family_spec),
            "parameter": "2",
        },
        config={
            "timeout": 60,
            "max_divisors": 4096,
            "certificate_timeout": 120,
        },
        stage_index=1,
    )

    assert result["status"] == "completed"
    assert result["injectivity_certified"] is True
    assert result["generic_lower_from_sections"] == 1
    assert result["generic_upper_from_injective_specialization"] == 1
    assert result["exact_generic_rank"] == 1
    assert result["generic_rank_is_separate_from_specialization_rank"] is True
    assert terminal is None
    assert stop is False

    family_key = family_evidence_key(family_spec, family_sha)
    state = reduce_family_evidence(db, family_key)
    assert state["rigorous_generic_lower"] == 1
    assert state["rigorous_generic_upper"] == 1
    assert state["exact_generic_rank"] == 1
    assert state["rank_inconsistent"] is False

    rows = list_family_evidence(db, family_key)
    assert len(rows) == 1
    assert rows[0]["specialized_curve_id"] == curve_id
    certificate = json.loads(rows[0]["certificate_json"])
    assert certificate["specialized_rigorous_upper"] == 1
    assert certificate["section_certificate_status"] == "certified"

    curve = get_curve(db, curve_id)
    assert curve["exact_rank"] is None
    assert int(curve["descent_upper"] or 0) == 0


def test_criterion_failure_is_not_recorded_as_noninjectivity(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "criterion-fail.db")
    family = _family()
    E = family.curve(QQ(3))
    curve_id = upsert_curve(
        db,
        family="Gusic-Tadic control family",
        parameter="3",
        a_invariants_json=json.dumps([str(x) for x in E.a_invariants()]),
        family_spec="json:/tmp/gusic-tadic-control.json",
        family_sha256="control-sha",
    )
    monkeypatch.setattr(
        runner,
        "run_specialization_injectivity",
        lambda *args, **kwargs: {
            "status": "rejected",
            "reason": "squarefree_divisor_specializes_to_square",
            "failed_check": {
                "divisor": "t+1",
                "value": "4",
                "is_square_in_Q": True,
            },
        },
    )

    result, terminal, stop = runner._stage_result(
        "specialization_injectivity_certificate",
        db=db,
        run={"id": 1, "target_mode": "family"},
        run_config={"target_rank": 10},
        target={},
        context={
            "curve_id": curve_id,
            "E": E,
            "family": family,
            "plugin": None,
            "variant": SimpleNamespace(
                family_spec="json:/tmp/gusic-tadic-control.json"
            ),
            "parameter": "3",
        },
        config={
            "timeout": 60,
            "max_divisors": 4096,
            "certificate_timeout": 120,
        },
        stage_index=1,
    )

    assert result["status"] == "completed"
    assert result["outcome"] == "criterion_not_satisfied"
    assert result["injectivity_certified"] is False
    assert result["criterion_failure_is_not_noninjectivity_proof"] is True
    assert terminal is None
    assert stop is False

    count = db.execute("SELECT COUNT(*) FROM family_evidence").fetchone()[0]
    assert int(count) == 0


def test_family_evidence_rerun_updates_one_stable_record(tmp_path):
    db = connect(tmp_path / "family-evidence.db")
    kwargs = {
        "family_spec": "json:/tmp/stable-family.json",
        "family_sha256": "stable-sha",
        "criterion": "Gusic-Tadic Theorem 1.3",
        "specialization_parameter": "2",
        "specialized_curve_id": None,
        "generic_lower": 1,
        "generic_upper": 2,
        "exact_generic_rank": None,
        "status": "completed",
    }
    first = record_family_evidence(
        db,
        certificate={"run": 1, "runtime": 0.1},
        **kwargs,
    )
    second = record_family_evidence(
        db,
        certificate={"run": 2, "runtime": 0.2},
        **kwargs,
    )
    assert first == second
    family_key = family_evidence_key(
        "json:/tmp/stable-family.json", "stable-sha"
    )
    rows = list_family_evidence(db, family_key)
    assert len(rows) == 1
    assert json.loads(rows[0]["certificate_json"])["run"] == 2


def test_generic_section_specialization_exception_is_structured(
    tmp_path,
    monkeypatch,
):
    db = connect(tmp_path / "section-error.db")
    family = _family()
    E = family.curve(QQ(2))
    curve_id = upsert_curve(
        db,
        family="Gusic-Tadic control family",
        parameter="2",
        a_invariants_json=json.dumps([str(x) for x in E.a_invariants()]),
        family_spec="json:/tmp/gusic-tadic-control.json",
        family_sha256="control-sha",
    )
    monkeypatch.setattr(
        runner,
        "run_specialization_injectivity",
        lambda *args, **kwargs: {
            "status": "completed",
            "injective": True,
            "rigorous": True,
            "criterion": "Gusic-Tadic Theorem 1.3",
            "assumptions": [],
        },
    )
    monkeypatch.setattr(
        family,
        "generic_section_points",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("injected section failure")
        ),
    )

    result, terminal, stop = runner._stage_result(
        "specialization_injectivity_certificate",
        db=db,
        run={"id": 1, "target_mode": "family"},
        run_config={"target_rank": 10},
        target={},
        context={
            "curve_id": curve_id,
            "E": E,
            "family": family,
            "plugin": None,
            "variant": SimpleNamespace(
                family_spec="json:/tmp/gusic-tadic-control.json"
            ),
            "parameter": "2",
        },
        config={
            "timeout": 60,
            "max_divisors": 4096,
            "certificate_timeout": 120,
        },
        stage_index=1,
    )
    assert result["status"] == "error"
    assert result["reason"] == "generic_section_specialization_failed"
    assert "injected section failure" in result["error"]
    assert terminal is None
    assert stop is False
    assert db.execute(
        "SELECT COUNT(*) FROM family_evidence"
    ).fetchone()[0] == 0
