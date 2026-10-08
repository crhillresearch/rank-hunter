from types import SimpleNamespace

from sage.all import EllipticCurve, QQ

from rank42.db import connect, upsert_curve
from rank42.plugin_hook_runner import run_isolated_plugin_hook
from rank42 import classical_descent_worker as classical_worker
from rank42 import plugin_rank_certificate as rank_cert
from rank42 import research_modules as research


def _curve_db(tmp_path):
    db = connect(tmp_path / "rank42.db")
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    curve_id = upsert_curve(
        db,
        family="test",
        parameter="1",
        a_invariants_json='["0","0","0","-1","1"]',
        status="test",
    )
    return db, E, curve_id


def test_higher_descent_reports_unsupported_real_higher_level(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([P], 1, True),
    )
    monkeypatch.setattr(research, "load_adapter", lambda plugin: None)
    result = research.higher_descent_ladder(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo"),
            "parameter": "1",
        },
        config={"levels": [4], "timeout": 1},
        run_id=3,
        stage_index=2,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "unsupported"
    assert result["branches"][0]["level"] == 4
    assert result["branches"][0]["status"] == "unsupported"
    assert result["branch_coverage"]["unsupported_branches"] == 1
    assert result["branch_coverage"]["complete"] is False
    assert result["retryable"] is False
    assert result["retry_policy"] == "manual"
    assert result["higher_levels_require_real_engine_hook"] is True


def test_higher_descent_reads_authoritative_curve_research_state(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    calls = []
    monkeypatch.setattr(
        research,
        "get_curve_research_state",
        lambda db, curve_id: (
            calls.append(int(curve_id))
            or {"rigorous_lower": 7, "rank_inconsistent": False}
        ),
    )
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "unsupported",
            "reason": "fixture",
        },
    )
    result = research.higher_descent_ladder(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo"),
            "parameter": "1",
        },
        config={"levels": [4], "timeout": 1},
        run_id=30,
        stage_index=3,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["rigorous_lower"] == 7
    assert calls == [curve_id, curve_id]


def test_higher_descent_reports_bounded_plugin_point_rejection_samples(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([], 0, True),
    )
    monkeypatch.setattr(
        research,
        "_persist_plugin_upper",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "points": [["bad"], ["0", "0"]],
        },
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
            "basis_complete": True,
        },
    )
    result = research.higher_descent_ladder(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo"),
            "parameter": "1",
        },
        config={"levels": [4], "timeout": 1},
        run_id=31,
        stage_index=4,
        certificate_timeout=10,
        exact_candidates=8,
    )
    branch = result["branches"][0]
    assert branch["points_rejected"] == 2
    assert len(branch["point_rejection_samples"]) == 2
    assert branch["point_rejection_samples"][0]["index"] == 1
    assert branch["point_rejection_samples"][0]["error_class"] == "ValueError"
    assert branch["point_rejection_samples"][1]["index"] == 2
    assert branch["point_rejection_samples"][1]["error_class"]


def test_padic_covering_search_never_fakes_engine(tmp_path):
    db, E, curve_id = _curve_db(tmp_path)
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={"prime": 3, "precision": 50, "timeout": 5},
        run_id=4,
        stage_index=3,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "unsupported"
    assert result["prime"] == 3
    assert result["precision"] == 50
    assert "p-adic" in result["reason"]


def test_selmer_element_fanout_is_inconclusive_without_coverings(tmp_path):
    db, E, curve_id = _curve_db(tmp_path)
    result = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={"max_coverings": 4, "height": 1000, "timeout": 1},
        ratpoints=None,
        run_id=5,
        stage_index=4,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "inconclusive"
    assert result["reason"] == "covering_derivation_capability_absent"
    assert result["coverings_seen"] == 0
    assert result["module_label"] == "Exact Covering Fan-Out"
    assert result["legacy_stage_id"] == "selmer_element_fanout"
    assert result["covering_import"]["status"] == "capability_absent"
    assert result["covering_provenance_scope"] == "exact_covering_only"
    assert result["selmer_class_verified"] is False


def test_saturation_index_recovery_keeps_global_completeness_false(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)

    monkeypatch.setattr(
        research,
        "saturate_curve",
        lambda *args, **kwargs: {
            "status": "completed",
            "index": "1",
            "regulator": "2.5",
            "saturated_points": [[str(P[0]), str(P[1])]],
        },
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 1,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )
    result = research.saturation_index_recovery(
        db,
        context={"curve_id": curve_id, "E": E},
        config={
            "prime_bounds": [7, 31],
            "timeout": 1,
            "stop_on_unit_index": True,
        },
        run_id=6,
        stage_index=5,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert len(result["rounds"]) == 1
    assert result["rounds"][0]["index"] == "1"
    assert result["complete_saturation_proved"] is False
    assert "global index bound" in result["proof_note"]


def test_saturation_index_recovery_all_timeouts_is_not_completed(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    calls = []

    def timeout_round(*args, **kwargs):
        calls.append(int(kwargs["max_prime"]))
        return {
            "status": "timeout",
            "index": "1",
            "error": "fixture timeout",
            "saturated_points": [],
        }

    monkeypatch.setattr(research, "saturate_curve", timeout_round)
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )

    result = research.saturation_index_recovery(
        db,
        context={"curve_id": curve_id, "E": E},
        config={
            "prime_bounds": [7, 31, 127],
            "timeout": 2,
            "stop_on_unit_index": True,
            "retry_policy": "escalated",
            "retry_timeout": 20,
            "_attempt_number": 2,
        },
        run_id=61,
        stage_index=8,
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert calls == [7, 31, 127]
    assert result["status"] == "timeout"
    assert result["mathematical_outcome"] == "timeout"
    assert result["retryable"] is True
    assert result["retry_policy"] == "escalated"
    assert result["retry_timeout"] == 20
    assert result["attempt_number"] == 2
    assert result["attempt_timeout"] == 2
    assert result["round_coverage"]["completed_rounds"] == 0
    assert result["round_coverage"]["timeout_rounds"] == 3
    assert result["complete_saturation_proved"] is False


def test_saturation_index_recovery_mixed_rounds_is_partial(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    outcomes = iter([
        {"status": "completed", "index": "2", "saturated_points": []},
        {"status": "error", "error": "fixture error", "saturated_points": []},
    ])
    monkeypatch.setattr(
        research,
        "saturate_curve",
        lambda *args, **kwargs: next(outcomes),
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )
    result = research.saturation_index_recovery(
        db,
        context={"curve_id": curve_id, "E": E},
        config={"prime_bounds": [7, 31], "timeout": 2},
        run_id=62,
        stage_index=9,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "partial"
    assert result["round_coverage"]["completed_rounds"] == 1
    assert result["round_coverage"]["error_rounds"] == 1
    assert result["retryable"] is True


def test_height_lattice_reduction_persists_exact_reduced_basis(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([P], 1, True),
    )
    monkeypatch.setattr(
        research,
        "run_lattice_build",
        lambda ainvs, basis, precision, lll_reduce, timeout: {
            "status": "completed",
            "points": [[str(P[0]), str(P[1])]],
            "gram": [["1.25"]],
            "determinant": "1.25",
            "min_eigenvalue": "1.25",
            "positive_definite_screen": True,
            "lll_transform": [["1"]],
            "input_points": [[str(P[0]), str(P[1])]],
            "input_gram": [["1.25"]],
            "input_determinant": "1.25",
            "eigenvalues": ["1.25"],
            "runtime_seconds": 0.25,
            "basis_fingerprint": "basis-fp",
            "sage_version": "test",
            "engine_version": "test",
        },
    )
    monkeypatch.setattr(
        research,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "completed",
            "exact_attempts": 1,
            "rank_growth": 0,
            "rigorous_lower": 1,
            "attempt_outcomes": [],
            "reason": None,
        },
    )
    result = research.height_lattice_reduction(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "parameter": "1",
            "variant": None,
        },
        config={
            "precision_bits": 256,
            "timeout": 5,
            "retry_policy": "manual",
            "retry_timeout": 20,
        },
        run_id=7,
        stage_index=6,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "completed"
    assert result["lattice_status"] == "completed"
    assert result["certification_status"] == "completed"
    assert result["lll_reduced"] is True
    assert result["basis_count"] == 1
    assert result["rigorous_lower"] == 1
    assert result["runtime_seconds"] == 0.25
    assert result["basis_fingerprint"] == "basis-fp"
    assert result["numerical_lattice_screen_is_not_rank_proof"] is True


def test_height_lattice_timeout_is_explicit_and_stores_no_lattice(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([P], 1, True),
    )
    monkeypatch.setattr(
        research,
        "run_lattice_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            research.LatticeTimeout("fixture lattice timeout")
        ),
    )
    result = research.height_lattice_reduction(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "parameter": "1",
            "variant": None,
        },
        config={
            "precision_bits": 256,
            "timeout": 3,
            "retry_policy": "escalated",
            "retry_timeout": 12,
        },
        run_id=70,
        stage_index=11,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "timeout"
    assert result["lattice_status"] == "timeout"
    assert result["certification_status"] == "not_attempted"
    assert result["attempt_timeout"] == 3
    assert result["retry_policy"] == "escalated"
    assert result["retry_timeout"] == 12
    assert result["retryable"] is True
    assert db.execute(
        "SELECT COUNT(*) AS n FROM mw_lattices WHERE curve_id=?",
        (curve_id,),
    ).fetchone()["n"] == 0


def test_height_lattice_completed_with_certificate_timeout_is_partial(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([P], 1, True),
    )
    monkeypatch.setattr(
        research,
        "run_lattice_build",
        lambda *args, **kwargs: {
            "status": "completed",
            "points": [[str(P[0]), str(P[1])]],
            "gram": [["1"]],
            "determinant": "1",
            "min_eigenvalue": "1",
            "positive_definite_screen": True,
            "lll_transform": [["1"]],
            "input_points": [[str(P[0]), str(P[1])]],
            "input_gram": [["1"]],
            "input_determinant": "1",
            "eigenvalues": ["1"],
            "runtime_seconds": 0.1,
            "basis_fingerprint": "fp-timeout",
            "sage_version": "test",
            "engine_version": "test",
        },
    )
    monkeypatch.setattr(
        research,
        "certify_stored_independence",
        lambda *args, **kwargs: {
            "status": "timeout",
            "reason": "all_certificate_attempts_timed_out",
            "exact_attempts": 1,
            "rank_growth": 0,
            "rigorous_lower": 1,
            "attempt_outcomes": [
                {"point_id": 1, "status": "timeout", "evidence_id": 9}
            ],
        },
    )
    result = research.height_lattice_reduction(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "parameter": "1",
            "variant": None,
        },
        config={"precision_bits": 256, "timeout": 5},
        run_id=71,
        stage_index=12,
        certificate_timeout=2,
        exact_candidates=8,
    )
    assert result["status"] == "partial"
    assert result["reason"] == "lattice_completed_recertification_incomplete"
    assert result["lattice_status"] == "completed"
    assert result["certification_status"] == "timeout"
    assert result["certificate_attempts"] == 1
    assert result["certificate_reason"] == "all_certificate_attempts_timed_out"
    assert result["retryable"] is True
    assert result["lattice_id"] > 0



def test_simon_worker_only_exposes_upper_in_deterministic_mode(monkeypatch):
    captured = []

    class FakeCurve:
        def simon_two_descent(self, **kwargs):
            captured.append(dict(kwargs))
            return 1, 3, []

    fake = FakeCurve()
    monkeypatch.setattr(
        classical_worker,
        "_curve",
        lambda payload: (fake, []),
    )

    deterministic = classical_worker.run_simon({
        "a_invariants": ["0", "0", "1", "-1", "0"],
        "limbigprime": 0,
    })
    assert captured[-1]["limbigprime"] == 0
    assert deterministic["reported_upper"] == 3
    assert deterministic["rigorous_upper"] == 3
    assert deterministic["upper_bound_rigorous"] is True
    assert deterministic["upper_bound_scope"] == (
        "deterministic_simon_local_tests"
    )

    probabilistic = classical_worker.run_simon({
        "a_invariants": ["0", "0", "1", "-1", "0"],
        "limbigprime": 30,
    })
    assert captured[-1]["limbigprime"] == 30
    assert probabilistic["reported_upper"] == 3
    assert probabilistic["rigorous_upper"] is None
    assert probabilistic["upper_bound_rigorous"] is False
    assert probabilistic["upper_bound_scope"] == (
        "probabilistic_simon_large_prime_local_tests"
    )


def test_deterministic_simon_is_compatible_with_modern_controls_on_known_curves():
    controls = [
        (["0", "0", "0", "-1", "0"], [], 0),
        (["0", "0", "1", "-1", "0"], [["0", "0"]], 1),
    ]
    for ainvs, known_points, expected in controls:
        payload = {
            "a_invariants": ainvs,
            "known_points": known_points,
            "limbigprime": 0,
            "lim1": 5,
            "lim3": 40,
            "limtriv": 3,
            "maxprob": 20,
            "first_limit": 20,
            "second_limit": 10,
            "n_aux": -1,
        }
        simon = classical_worker.run_simon(payload)
        mwrank = classical_worker.run_mwrank(payload, selmer_only=False)
        E = EllipticCurve(QQ, [QQ(value) for value in ainvs])
        pari_upper = int(E.rank_bound(algorithm="pari"))

        assert simon["upper_bound_rigorous"] is True
        # A rigorous upper need not be sharp.  Simon can legitimately return
        # a looser bound than modern mwrank/PARI controls on the same curve.
        assert simon["rigorous_upper"] >= expected
        assert mwrank["rigorous_upper"] == expected
        assert pari_upper == expected
        assert simon["rigorous_upper"] >= mwrank["rigorous_upper"]
        assert simon["rigorous_upper"] >= pari_upper


def test_isolated_plugin_hook_enforces_hard_timeout(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "import time\n"
        "def run_pipeline_higher_descent(payload):\n"
        "    time.sleep(10)\n"
        "    return {'status': 'completed'}\n",
        encoding="utf-8",
    )
    plugin = SimpleNamespace(id="slow-demo", adapter_path=adapter)
    result = run_isolated_plugin_hook(
        plugin,
        "run_pipeline_higher_descent",
        {"descent_level": 4},
        timeout=1,
    )
    assert result["status"] == "timeout"
    assert result["timeout_seconds"] == 1
    assert result["hook_name"] == "run_pipeline_higher_descent"


def test_higher_descent_plugin_timeout_is_branch_timeout(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([P], 1, True),
    )
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "timeout",
            "reason": "hook exceeded core timeout",
            "timeout_seconds": timeout,
            "runtime_seconds": 1.0,
            "hook_name": hook_name,
        },
    )
    result = research.higher_descent_ladder(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"levels": [4], "timeout": 7},
        run_id=8,
        stage_index=7,
        certificate_timeout=10,
        exact_candidates=8,
    )
    branch = result["branches"][0]
    assert branch["status"] == "timeout"
    assert branch["hook_result"]["timeout_seconds"] == 7
    assert result["status"] == "timeout"
    assert result["branch_coverage"]["timeout_branches"] == 1
    assert result["retryable"] is True
    assert result["attempt_complete"] is True
    assert result["mathematical_outcome"] == "timeout"


def test_padic_plugin_timeout_is_stage_timeout(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "timeout",
            "reason": "hook exceeded core timeout",
            "timeout_seconds": timeout,
            "runtime_seconds": 1.0,
            "hook_name": hook_name,
        },
    )
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"prime": 5, "precision": 60, "timeout": 9},
        run_id=9,
        stage_index=8,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "timeout"
    assert result["timeout_seconds"] == 9
    assert result["prime"] == 5
    assert result["precision"] == 60



def test_plugin_upper_plain_boolean_claim_is_not_rigorous(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "promote_rigorous_rank_interval",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unverified plugin upper must not be promoted")
        ),
    )
    result = {
        "status": "completed",
        "rigorous": True,
        "rigorous_upper": 4,
        "assumptions": [],
        "engine": "demo-higher-descent",
    }
    upper = research._persist_plugin_upper(
        db,
        curve_id=curve_id,
        E=E,
        plugin=SimpleNamespace(id="demo"),
        result=result,
        source="test",
        stage_id="higher_descent_4",
        known_points=[],
        certificate_timeout=5,
    )
    assert upper is None
    assert result["_rigorous_upper_verification"]["verified"] is False
    assert (
        result["_rigorous_upper_verification"]["reason"]
        == "missing_typed_certificate"
    )


def test_timeout_plugin_upper_without_completed_certificate_is_rejected(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    result = {
        "status": "timeout",
        "rigorous": True,
        "rigorous_upper": 9,
        "engine": "demo-timeout",
    }
    monkeypatch.setattr(
        research,
        "promote_rigorous_rank_interval",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("bare timeout upper must not promote")
        ),
    )
    upper = research._persist_plugin_upper(
        db,
        curve_id=curve_id,
        E=E,
        plugin=SimpleNamespace(id="demo"),
        result=result,
        source="test",
        stage_id="higher_descent_8",
        known_points=[],
        certificate_timeout=5,
    )
    assert upper is None
    assert result["_rigorous_upper_verification"]["verified"] is False
    assert result["_rigorous_upper_verification"]["reason"] == (
        "missing_typed_certificate"
    )


def test_typed_plugin_upper_certificate_is_core_verified(monkeypatch):
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    monkeypatch.setattr(
        rank_cert,
        "run_classical_descent",
        lambda *args, **kwargs: {
            "status": "completed",
            "engine": "mwrank_coverings",
            "rigorous_upper": 3,
            "minimal_model_a_invariants": [str(x) for x in E.a_invariants()],
            "runtime_seconds": 0.25,
            "worker_exit_code": 0,
            "options": {"first_limit": 20, "second_limit": 10},
        },
    )
    certificate = {
        "schema": rank_cert.SCHEMA,
        "status": "completed",
        "verifier": "core_mwrank_full_two_descent",
        "rigorous_upper": 3,
        "assumptions": [],
    }
    verified = rank_cert.verify_plugin_rigorous_upper_certificate(
        E,
        certificate,
        known_points=[],
        timeout=5,
    )
    assert verified["verified"] is True
    assert verified["rigorous_upper"] == 3
    assert verified["engine"] == "eclib_mwrank_full_two_descent"
    assert verified["certificate"]["core_verification"]["rigorous_upper"] == 3


def test_typed_plugin_upper_certificate_rejects_upper_mismatch(monkeypatch):
    E = EllipticCurve(QQ, [0, 0, 0, -1, 1])
    monkeypatch.setattr(
        rank_cert,
        "run_classical_descent",
        lambda *args, **kwargs: {
            "status": "completed",
            "engine": "mwrank_coverings",
            "rigorous_upper": 2,
        },
    )
    verified = rank_cert.verify_plugin_rigorous_upper_certificate(
        E,
        {
            "schema": rank_cert.SCHEMA,
            "status": "completed",
            "verifier": "core_mwrank_full_two_descent",
            "rigorous_upper": 3,
            "assumptions": [],
        },
        known_points=[],
        timeout=5,
    )
    assert verified["verified"] is False
    assert verified["reason"] == "certificate_upper_mismatch"
    assert verified["claimed_upper"] == 3
    assert verified["verified_upper"] == 2


def test_timeout_hook_can_only_promote_separately_verified_certificate(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    P = E(0, 1)
    monkeypatch.setattr(
        research,
        "rigorous_witness_basis",
        lambda db, curve_id, E: ([P], 1, True),
    )
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "timeout",
            "reason": "main hook timed out after certificate completed",
            "rigorous": True,
            "rigorous_upper": 3,
            "rigorous_upper_certificate": {
                "schema": rank_cert.SCHEMA,
                "status": "completed",
                "verifier": "core_mwrank_full_two_descent",
                "rigorous_upper": 3,
                "assumptions": [],
            },
        },
    )
    monkeypatch.setattr(
        research,
        "verify_plugin_rigorous_upper_certificate",
        lambda *args, **kwargs: {
            "verified": True,
            "verifier": "core_mwrank_full_two_descent",
            "rigorous_upper": 3,
            "engine": "eclib_mwrank_full_two_descent",
            "engine_version": "test",
            "certificate": {
                "schema": rank_cert.SCHEMA,
                "status": "completed",
                "verifier": "core_mwrank_full_two_descent",
                "rigorous_upper": 3,
                "assumptions": [],
                "core_verification": {"rigorous_upper": 3},
            },
            "elapsed_seconds": 0.1,
        },
    )
    captured = {}

    def fake_promote(db, **kwargs):
        captured.update(kwargs)
        return {
            "evidence_id": 91,
            "rigorous_lower": 1,
            "rigorous_upper": 3,
            "exact_rank": None,
            "rank_inconsistent": False,
        }

    monkeypatch.setattr(research, "promote_rigorous_rank_interval", fake_promote)
    result = research.higher_descent_ladder(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"levels": [4], "timeout": 5},
        run_id=10,
        stage_index=9,
        certificate_timeout=11,
        exact_candidates=8,
    )
    branch = result["branches"][0]
    assert branch["status"] == "timeout"
    assert branch["rigorous_upper"] == 3
    assert result["status"] == "timeout"
    assert branch["hook_result"]["_rigorous_upper_verification"]["verified"] is True
    assert captured["rigorous_upper"] == 3
    assert captured["engine"] == "eclib_mwrank_full_two_descent"
    assert captured["options"]["plugin_hook_status"] == "timeout"



def test_geometry_branch_summary_distinguishes_completed_partial_and_mixed_failure():
    completed = research.geometry_branch_summary([
        {"status": "completed"},
        {"status": "skipped-after-growth"},
    ])
    assert completed["status"] == "completed"
    assert completed["branch_coverage"]["completed_branches"] == 1
    assert completed["branch_coverage"]["skipped_branches"] == 1
    assert completed["branch_coverage"]["coverage_fraction"] == 1.0
    assert completed["retryable"] is False

    partial = research.geometry_branch_summary([
        {"status": "completed"},
        {"status": "timeout"},
        {"status": "unsupported"},
    ])
    assert partial["status"] == "partial"
    assert partial["branch_coverage"]["completed_branches"] == 1
    assert partial["branch_coverage"]["timeout_branches"] == 1
    assert partial["branch_coverage"]["unsupported_branches"] == 1
    assert partial["retryable"] is True

    branch_partial = research.geometry_branch_summary([
        {"status": "partial"},
        {"status": "unsupported"},
    ])
    assert branch_partial["status"] == "partial"
    assert branch_partial["branch_coverage"]["partial_branches"] == 1
    assert branch_partial["retryable"] is True

    mixed = research.geometry_branch_summary([
        {"status": "timeout"},
        {"status": "error"},
        {"status": "unsupported"},
    ])
    assert mixed["status"] == "inconclusive"
    assert mixed["branch_coverage"]["completed_branches"] == 0
    assert mixed["branch_coverage"]["incomplete_branches"] == 3
    assert mixed["retryable"] is True



def test_isolated_covering_hook_accepts_legacy_list_result(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "def derive_pipeline_coverings(payload):\n"
        "    return [{'quartic': {'coefficients': ['1', '0'], 'height': 10}, "
        "'map': {'x': 'u', 'y': 'v'}}]\n",
        encoding="utf-8",
    )
    plugin = SimpleNamespace(id="covering-demo", adapter_path=adapter)
    result = run_isolated_plugin_hook(
        plugin,
        "derive_pipeline_coverings",
        {"curve_id": 1},
        timeout=2,
    )
    assert result["status"] == "completed"
    assert result["legacy_list_result"] is True
    assert len(result["coverings"]) == 1


def test_covering_derivation_timeout_is_visible(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "timeout",
            "reason": "derive hook exceeded core timeout",
            "timeout_seconds": timeout,
            "runtime_seconds": 1.0,
        },
    )
    result = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={
            "max_coverings": 4,
            "height": 1000,
            "timeout": 1,
            "derive_timeout": 7,
        },
        ratpoints=None,
        run_id=11,
        stage_index=10,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "timeout"
    assert result["reason"] == "covering_derivation_timeout"
    assert result["covering_import"]["status"] == "timeout"
    assert result["covering_import"]["timeout_seconds"] == 7
    assert result["attempt_timeout"] == 7
    assert result["retry_policy"] == "manual"
    assert (
        result["covering_import"]["hook_result"]["reason"]
        == "derive hook exceeded core timeout"
    )


def test_invalid_plugin_covering_map_is_visible(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "coverings": [{
                "schema": "rank42.covering.v1",
                "quartic": {
                    "coefficients": ["1", "0", "1"],
                    "height": 100,
                },
                "map": {"x": "u"},
                "metadata": {"claimed_kind": "2-selmer-element"},
            }],
        },
    )
    result = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"max_coverings": 4, "height": 1000, "timeout": 1},
        ratpoints=None,
        run_id=12,
        stage_index=11,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "error"
    assert result["reason"] == "invalid_plugin_coverings"
    diagnostic = result["covering_import"]
    assert diagnostic["status"] == "invalid_coverings"
    assert diagnostic["records_returned"] == 1
    assert diagnostic["coverings_stored"] == 0
    assert diagnostic["map_schema_failures"] == 1
    assert diagnostic["invalid_coverings"] == 0
    assert diagnostic["rejections"][0]["kind"] == "map_schema_failure"
    assert diagnostic["selmer_class_verified"] is False



def test_covering_import_distinguishes_no_data_error_and_stored(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    plugin = SimpleNamespace(id="demo", adapter_path="/tmp/demo.py")
    context = {
        "curve_id": curve_id,
        "E": E,
        "plugin": plugin,
        "parameter": "1",
    }

    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "coverings": [],
        },
    )
    empty = research._import_plugin_coverings(
        db, context=context, config={"derive_timeout": 3}
    )
    assert empty["status"] == "no_coverings_derived"
    assert empty["records_returned"] == 0
    assert empty["coverings_stored"] == 0

    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "error",
            "reason": "adapter raised",
            "error": "ValueError('boom')",
        },
    )
    failed = research._import_plugin_coverings(
        db, context=context, config={"derive_timeout": 3}
    )
    assert failed["status"] == "error"
    assert failed["reason"] == "adapter raised"
    assert failed["error"] == "ValueError('boom')"

    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "coverings": [{
                "schema": "rank42.covering.v1",
                "quartic": {
                    "coefficients": ["1", "0", "1"],
                    "height": 100,
                },
                "map": {"x": "u", "y": "v"},
                "metadata": {"producer_claim": "selmer-element"},
            }],
        },
    )
    stored = research._import_plugin_coverings(
        db, context=context, config={"derive_timeout": 3}
    )
    assert stored["status"] == "completed"
    assert stored["coverings_stored"] == 1
    assert len(stored["stored_covering_ids"]) == 1
    row = db.execute(
        "SELECT metadata_json FROM coverings WHERE id=?",
        (stored["stored_covering_ids"][0],),
    ).fetchone()
    metadata = __import__("json").loads(row["metadata_json"])
    assert metadata["producer_claim"] == "selmer-element"
    assert metadata["rank42_core_provenance"] == {
        "kind": "exact_covering",
        "selmer_class_verified": False,
        "plugin_id": "demo",
        "hook": "derive_pipeline_coverings",
    }



def test_covering_map_back_failure_is_branch_error(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    row = research.store_covering(db, {
        "schema": "rank42.covering.v1",
        "curve_id": curve_id,
        "quartic": {
            "coefficients": ["1", "0", "1"],
            "height": 100,
        },
        "map": {
            "x": "not_a_valid_map_symbol",
            "y": "v",
        },
        "metadata": {},
    })
    monkeypatch.setattr(
        research,
        "_import_plugin_coverings",
        lambda db, context, config: {
            "status": "capability_absent",
            "reason": "fixture uses stored covering",
            "coverings_stored": 0,
            "records_returned": 0,
            "selmer_class_verified": False,
            "provenance_scope": "exact_covering_only",
        },
    )
    monkeypatch.setattr(
        research,
        "run_ratpoints",
        lambda *args, **kwargs: {
            "points": [SimpleNamespace(x=0, y=1)],
            "runtime": 0.01,
        },
    )
    result = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={"max_coverings": 4, "height": 1000, "timeout": 1},
        ratpoints=None,
        run_id=13,
        stage_index=12,
        certificate_timeout=10,
        exact_candidates=8,
    )
    branch = result["branches"][0]
    assert branch["covering_id"] == int(row["id"])
    assert branch["status"] == "error"
    assert branch["mapped_points"] == 0
    assert branch["map_back_failures"] == 1
    assert branch["map_back_errors"]
    stored = db.execute(
        "SELECT status,error FROM coverings WHERE id=?",
        (int(row["id"]),),
    ).fetchone()
    assert stored["status"] == "ready"
    assert "map-back failed" in stored["error"]



def _stored_covering(db, curve_id, constant):
    return research.store_covering(db, {
        "schema": "rank42.covering.v1",
        "curve_id": curve_id,
        "quartic": {
            "coefficients": [str(constant), "0", "0", "0", "1"],
            "height": 100,
        },
        "map": {"x": "u", "y": "v"},
        "metadata": {"fixture": constant},
    })


def test_covering_fanout_all_timeouts_aggregate_and_persist_attempts(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    coverings = [
        _stored_covering(db, curve_id, 1),
        _stored_covering(db, curve_id, 2),
    ]
    monkeypatch.setattr(
        research,
        "_import_plugin_coverings",
        lambda db, context, config: {
            "status": "capability_absent",
            "reason": "fixture uses stored coverings",
        },
    )

    def timeout_search(*args, **kwargs):
        raise research.RatpointsTimeout("fixture timeout")

    monkeypatch.setattr(research, "run_ratpoints", timeout_search)
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )

    result = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={
            "max_coverings": 4,
            "height": 5000,
            "timeout": 7,
            "retry_policy": "manual",
            "retry_timeout": 21,
        },
        ratpoints="/tmp/ratpoints",
        run_id=21,
        stage_index=8,
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert result["status"] == "timeout"
    assert result["reason"] == "covering_branch_coverage_timeout"
    assert result["branch_coverage"]["planned_branches"] == 2
    assert result["branch_coverage"]["timeout_branches"] == 2
    assert result["branch_coverage"]["completed_branches"] == 0
    assert result["retryable"] is True
    assert result["retry_policy"] == "manual"
    assert result["retry_timeout"] == 21
    assert all(branch["attempt_id"] for branch in result["branches"])

    rows = db.execute(
        """SELECT * FROM covering_search_attempts
           WHERE pipeline_run_id=? AND pipeline_stage_index=?
           ORDER BY id""",
        (21, 8),
    ).fetchall()
    assert len(rows) == 2
    assert {row["covering_id"] for row in rows} == {
        int(covering["id"]) for covering in coverings
    }
    assert {row["outcome"] for row in rows} == {"timeout"}
    assert {row["height"] for row in rows} == {5000}
    assert {row["timeout_seconds"] for row in rows} == {7}
    assert {row["backend"] for row in rows} == {"ratpoints"}
    assert all(row["runtime_seconds"] is not None for row in rows)
    assert all("fixture timeout" in row["error"] for row in rows)


def test_covering_fanout_mixed_completed_timeout_is_partial(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    _stored_covering(db, curve_id, 3)
    _stored_covering(db, curve_id, 4)
    monkeypatch.setattr(
        research,
        "_import_plugin_coverings",
        lambda db, context, config: {
            "status": "capability_absent",
            "reason": "fixture uses stored coverings",
        },
    )
    calls = {"n": 0}

    def mixed_search(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"points": [], "runtime": 0.02}
        raise research.RatpointsTimeout("second covering timed out")

    monkeypatch.setattr(research, "run_ratpoints", mixed_search)
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )

    result = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={"max_coverings": 4, "height": 1000, "timeout": 3},
        ratpoints=None,
        run_id=22,
        stage_index=9,
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert result["status"] == "partial"
    assert result["reason"] == "covering_branch_coverage_partial"
    assert result["branch_coverage"]["completed_branches"] == 1
    assert result["branch_coverage"]["timeout_branches"] == 1
    assert result["retryable"] is True
    rows = db.execute(
        """SELECT outcome FROM covering_search_attempts
           WHERE pipeline_run_id=? ORDER BY id""",
        (22,),
    ).fetchall()
    assert [row["outcome"] for row in rows] == ["completed", "timeout"]


def test_covering_fanout_resume_reuses_completed_and_retries_incomplete(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    first = _stored_covering(db, curve_id, 5)
    second = _stored_covering(db, curve_id, 6)
    monkeypatch.setattr(
        research,
        "_import_plugin_coverings",
        lambda db, context, config: {
            "status": "capability_absent",
            "reason": "fixture uses stored coverings",
        },
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )
    first_calls = {"n": 0}

    def first_pass(*args, **kwargs):
        first_calls["n"] += 1
        if first_calls["n"] == 1:
            return {"points": [], "runtime": 0.01}
        raise research.RatpointsTimeout("fixture timeout")

    monkeypatch.setattr(research, "run_ratpoints", first_pass)
    partial = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={"max_coverings": 4, "height": 2000, "timeout": 3},
        ratpoints="/tmp/ratpoints",
        run_id=23,
        stage_index=10,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert partial["status"] == "partial"
    assert first_calls["n"] == 2

    retry_calls = {"n": 0}

    def retry_pass(*args, **kwargs):
        retry_calls["n"] += 1
        return {"points": [], "runtime": 0.02}

    monkeypatch.setattr(research, "run_ratpoints", retry_pass)
    resumed = research.selmer_element_fanout(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": None,
            "parameter": "1",
        },
        config={"max_coverings": 4, "height": 2000, "timeout": 9},
        ratpoints="/tmp/ratpoints",
        run_id=23,
        stage_index=10,
        certificate_timeout=10,
        exact_candidates=8,
    )

    assert resumed["status"] == "completed"
    assert retry_calls["n"] == 1
    by_id = {branch["covering_id"]: branch for branch in resumed["branches"]}
    assert by_id[int(first["id"])]["resume_completed"] is True
    assert by_id[int(first["id"])]["prior_timeout_seconds"] == 3
    assert by_id[int(second["id"])]["resume_completed"] is False

    attempts = db.execute(
        """SELECT covering_id,outcome,timeout_seconds
           FROM covering_search_attempts
           WHERE pipeline_run_id=? AND pipeline_stage_index=?
           ORDER BY id""",
        (23, 10),
    ).fetchall()
    assert [
        (int(row["covering_id"]), row["outcome"], int(row["timeout_seconds"]))
        for row in attempts
    ] == [
        (int(first["id"]), "completed", 3),
        (int(second["id"]), "timeout", 3),
        (int(second["id"]), "completed", 9),
    ]


def test_covering_map_back_error_persists_attempt_row(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    row = research.store_covering(db, {
        "schema": "rank42.covering.v1",
        "curve_id": curve_id,
        "quartic": {
            "coefficients": ["9", "0", "1"],
            "height": 100,
        },
        "map": {"x": "invalid_symbol", "y": "v"},
        "metadata": {},
    })
    monkeypatch.setattr(
        research,
        "_import_plugin_coverings",
        lambda db, context, config: {
            "status": "capability_absent",
            "reason": "fixture uses stored covering",
        },
    )
    monkeypatch.setattr(
        research,
        "run_ratpoints",
        lambda *args, **kwargs: {
            "points": [SimpleNamespace(x=0, y=1)],
            "runtime": 0.03,
        },
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
        },
    )

    result = research.selmer_element_fanout(
        db,
        context={"curve_id": curve_id, "E": E, "plugin": None},
        config={"max_coverings": 1, "height": 1000, "timeout": 4},
        ratpoints=None,
        run_id=23,
        stage_index=10,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "error"
    branch = result["branches"][0]
    assert branch["status"] == "error"
    attempt = db.execute(
        "SELECT * FROM covering_search_attempts WHERE id=?",
        (branch["attempt_id"],),
    ).fetchone()
    assert attempt["covering_id"] == int(row["id"])
    assert attempt["outcome"] == "error"
    assert attempt["ratpoints_hits"] == 1
    assert attempt["mapped_points"] == 0
    assert "map-back failed" in attempt["error"]



def test_padic_plugin_ignoring_timeout_is_killed_by_core(tmp_path):
    db, E, curve_id = _curve_db(tmp_path)
    adapter = tmp_path / "slow_padic_adapter.py"
    adapter.write_text(
        "import time\n"
        "def run_pipeline_padic_covering_search(payload):\n"
        "    time.sleep(10)\n"
        "    return {'status': 'completed'}\n",
        encoding="utf-8",
    )
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="slow-padic", adapter_path=adapter),
            "parameter": "1",
        },
        config={"prime": 3, "precision": 50, "timeout": 1},
        run_id=31,
        stage_index=12,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "timeout"
    assert result["timeout_seconds"] == 1
    assert result["hook_result"]["hook_name"] == (
        "run_pipeline_padic_covering_search"
    )
    assert result["result_schema"] == research.PADIC_RESULT_SCHEMA


def test_untyped_padic_success_cannot_persist_points(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "completed",
            "engine": "legacy-padic",
            "points": [["0", "1"]],
        },
    )
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="legacy", adapter_path="/tmp/legacy.py"),
            "parameter": "1",
        },
        config={"prime": 3, "precision": 50, "timeout": 5},
        run_id=32,
        stage_index=13,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "error"
    assert result["reason"] == "invalid_padic_result_contract"
    assert result["points_persisted"] is False
    assert "schema" in result["error"]
    assert db.execute(
        "SELECT COUNT(*) AS n FROM points WHERE curve_id=?",
        (curve_id,),
    ).fetchone()["n"] == 0


def test_typed_padic_result_persists_engine_and_completeness_provenance(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    covering = research.store_covering(db, {
        "schema": "rank42.covering.v1",
        "curve_id": curve_id,
        "quartic": {
            "coefficients": ["1", "0", "0", "0", "1"],
            "height": 100,
        },
        "map": {"x": "u", "y": "v"},
        "metadata": {},
    })
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "schema": research.PADIC_RESULT_SCHEMA,
            "status": "completed",
            "engine": "demo-padic",
            "engine_version": "2.0",
            "algorithm": "hensel-covering-lift",
            "prime": 5,
            "precision": 70,
            "precision_semantics": "5-adic digits retained after each lift",
            "covering_ids": [int(covering["id"])],
            "local_lifting_bounds": {
                "max_lift_depth": 9,
                "residue_classes_checked": 25,
            },
            "completeness": "bounded",
            "points": [["0", "1"]],
            "metadata": {"plugin_detail": "fixture"},
        },
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 1,
            "growth": 0,
            "rigorous_lower": 0,
            "basis_complete": True,
        },
    )
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"prime": 5, "precision": 70, "timeout": 6},
        run_id=33,
        stage_index=14,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "completed"
    assert result["result_schema"] == research.PADIC_RESULT_SCHEMA
    assert result["engine"] == "demo-padic"
    assert result["engine_version"] == "2.0"
    assert result["algorithm"] == "hensel-covering-lift"
    assert result["covering_ids"] == [int(covering["id"])]
    assert result["completeness"] == "bounded"
    assert result["search_complete"] is False
    assert result["exact_points_accepted"] == 1

    point = db.execute(
        """SELECT metadata_json FROM points
           WHERE curve_id=? AND source='pipeline_padic_covering_search'""",
        (curve_id,),
    ).fetchone()
    metadata = __import__("json").loads(point["metadata_json"])
    assert metadata["engine"] == "demo-padic"
    assert metadata["engine_version"] == "2.0"
    assert metadata["algorithm"] == "hensel-covering-lift"
    assert metadata["covering_ids"] == [int(covering["id"])]
    assert metadata["completeness"] == "bounded"
    assert metadata["search_complete"] is False
    assert metadata["local_lifting_bounds"]["max_lift_depth"] == 9


def test_padic_result_reports_plugin_point_rejection_samples(
    tmp_path, monkeypatch
):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "schema": research.PADIC_RESULT_SCHEMA,
            "status": "completed",
            "engine": "demo-padic",
            "engine_version": "2.0",
            "algorithm": "hensel-covering-lift",
            "prime": 2,
            "precision": 40,
            "precision_semantics": "2-adic digits",
            "covering_ids": [],
            "local_lifting_bounds": {"max_lift_depth": 4},
            "completeness": "heuristic",
            "points": [["bad"], ["0", "0"]],
        },
    )
    monkeypatch.setattr(
        research,
        "certify_ledger_growth",
        lambda *args, **kwargs: {
            "attempts": 0,
            "growth": 0,
            "rigorous_lower": 0,
            "basis_complete": True,
        },
    )
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"prime": 2, "precision": 40, "timeout": 5},
        run_id=35,
        stage_index=16,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["points_rejected"] == 2
    assert len(result["point_rejection_samples"]) == 2
    assert all(sample["error"] for sample in result["point_rejection_samples"])


def test_padic_result_cannot_claim_unrequested_covering(tmp_path, monkeypatch):
    db, E, curve_id = _curve_db(tmp_path)
    monkeypatch.setattr(
        research,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "schema": research.PADIC_RESULT_SCHEMA,
            "status": "completed",
            "engine": "demo-padic",
            "engine_version": "2.0",
            "algorithm": "hensel-covering-lift",
            "prime": 2,
            "precision": 40,
            "precision_semantics": "2-adic digits",
            "covering_ids": [999999],
            "local_lifting_bounds": {"max_lift_depth": 4},
            "completeness": "heuristic",
            "points": [],
        },
    )
    result = research.padic_covering_point_search(
        db,
        context={
            "curve_id": curve_id,
            "E": E,
            "plugin": SimpleNamespace(id="demo", adapter_path="/tmp/demo.py"),
            "parameter": "1",
        },
        config={"prime": 2, "precision": 40, "timeout": 5},
        run_id=34,
        stage_index=15,
        certificate_timeout=10,
        exact_candidates=8,
    )
    assert result["status"] == "error"
    assert result["reason"] == "invalid_padic_result_contract"
    assert "unrequested covering ids" in result["error"]
