import json
import time
from types import SimpleNamespace

from sage.all import EllipticCurve, QQ

from rank42.exact_lb import ExactCertificateTimeout, run_exact_certificate
from rank42 import independence_check as independence
from rank42.independence_check import (
    _basis_fingerprint,
    _cached_basis_saturation,
    certify_stored_independence,
    run_basis_first_independence,
    run_maximal_independence,
    run_saturation_independence,
)
from rank42.saturation import SaturationFailure
from rank42.db import connect, upsert_curve
from rank42.points import upsert_point
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.independence_page import _hard_reason_from_last


def test_maximal_subset_keeps_testing_after_dependent_candidate():
    basis = ["B1"]
    candidates = [
        {"point": "P1", "id": 1},
        {"point": "P2", "id": 2},
        {"point": "P3", "id": 3},
    ]

    def certify(trial):
        point = trial[-1]
        if point == "P2":
            return {"status": "dependent", "independent": False}
        return {"status": "certified_independent", "independent": True}

    working, outcomes = run_maximal_independence(basis, candidates, certify)

    assert working == ["B1", "P1", "P3"]
    assert [item["status"] for item in outcomes] == [
        "independent",
        "dependent",
        "independent",
    ]
    assert [item["before"] for item in outcomes] == [1, 2, 2]
    assert [item["after"] for item in outcomes] == [2, 2, 3]
    assert outcomes[0]["trial"] == ["B1", "P1"]
    assert outcomes[1]["trial"] == ["B1", "P1", "P2"]
    assert outcomes[2]["trial"] == ["B1", "P1", "P3"]


def test_timeout_is_inconclusive_and_does_not_poison_following_candidate():
    basis = ["B1"]
    candidates = [
        {"point": "P1", "id": 1},
        {"point": "P2", "id": 2},
    ]

    def certify(trial):
        if trial[-1] == "P1":
            raise ExactCertificateTimeout("budget")
        return {"status": "certified_independent", "independent": True}

    working, outcomes = run_maximal_independence(basis, candidates, certify)

    assert working == ["B1", "P2"]
    assert outcomes[0]["status"] == "timeout"
    assert outcomes[0]["before"] == outcomes[0]["after"] == 1
    assert outcomes[1]["status"] == "independent"
    assert outcomes[1]["before"] == 1
    assert outcomes[1]["after"] == 2


def test_pipeline_shared_independence_all_timeout_is_not_completed(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "pipeline-independence-timeout.db")
    model = ["0", "0", "1", "-1", "0"]
    curve_id = upsert_curve(
        db,
        family="independence-timeout",
        parameter="1",
        a_invariants_json=json.dumps(model),
    )
    E = EllipticCurve(QQ, [QQ(value) for value in model])
    P = E(0, 0)
    candidate = upsert_point(
        db,
        curve_id=curve_id,
        x=P[0],
        y=P[1],
        source="test",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
    )

    monkeypatch.setattr(
        independence,
        "run_exact_certificate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ExactCertificateTimeout("fixture certificate timeout")
        ),
    )

    result = certify_stored_independence(
        db,
        curve_id=curve_id,
        E=E,
        source="pipeline_independence",
        search_ref="pipeline:7:independence:3",
        certificate_timeout=1,
        max_candidates=8,
    )

    assert result["status"] == "timeout"
    assert result["exact_attempts"] == 1
    assert result["timeouts"] == 1
    assert result["independent"] == 0
    assert result["dependent"] == 0
    assert result["not_attempted"] == 0
    assert result["attempt_outcomes"][0]["point_id"] == int(candidate["id"])
    assert result["attempt_outcomes"][0]["status"] == "timeout"

    evidence = db.execute(
        """SELECT * FROM rank_evidence
           WHERE curve_id=? AND engine='rank42.independence_check'""",
        (curve_id,),
    ).fetchone()
    assert evidence is not None
    assert evidence["status"] == "timeout"
    options = json.loads(evidence["options_json"])
    assert options["candidate_point_id"] == int(candidate["id"])

    stored = db.execute(
        "SELECT * FROM points WHERE id=?",
        (int(candidate["id"]),),
    ).fetchone()
    meta = json.loads(stored["metadata_json"])
    assert meta["last_independence"]["status"] == "timeout"
    assert meta["last_independence"]["evidence_id"] == int(evidence["id"])


def test_pipeline_shared_independence_preserves_dependent_certificate_provenance(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "pipeline-independence-dependent.db")
    model = ["0", "0", "1", "-1", "0"]
    curve_id = upsert_curve(
        db,
        family="independence-dependent",
        parameter="1",
        a_invariants_json=json.dumps(model),
    )
    E = EllipticCurve(QQ, [QQ(value) for value in model])
    P = E(0, 0)
    Q = 2 * P

    record_rank_evidence(
        db,
        curve_id=curve_id,
        model=model,
        data={
            "engine": "fixture",
            "evidence_type": "certified_subgroup",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": 1,
            "rigorous_upper": None,
            "exact_rank": None,
            "assumptions": [],
            "points_found": [[str(P[0]), str(P[1])]],
            "options": {},
        },
    )
    upsert_point(
        db,
        curve_id=curve_id,
        x=P[0],
        y=P[1],
        source="fixture",
        role="rigorous_witness",
        exact_verified=True,
        independence_status="rigorous_independent",
        rigorous_independent=True,
    )
    candidate = upsert_point(
        db,
        curve_id=curve_id,
        x=Q[0],
        y=Q[1],
        source="test",
        role="candidate_extra",
        exact_verified=True,
        independence_status="unknown",
        rigorous_independent=False,
    )

    certificate = {
        "status": "dependent",
        "independent": False,
        "runtime_seconds": 0.01,
        "certificate": {
            "relation": ["2", "-1"],
            "matrix_rank": 1,
        },
    }
    monkeypatch.setattr(
        independence,
        "run_exact_certificate",
        lambda *args, **kwargs: certificate,
    )

    result = certify_stored_independence(
        db,
        curve_id=curve_id,
        E=E,
        source="pipeline_independence",
        search_ref="pipeline:8:independence:4",
        max_candidates=8,
    )

    assert result["status"] == "completed"
    assert result["dependent"] == 1
    assert result["rank_growth"] == 0
    evidence_id = result["evidence_ids"][0]
    evidence = db.execute(
        "SELECT * FROM rank_evidence WHERE id=?",
        (evidence_id,),
    ).fetchone()
    summary = json.loads(evidence["stdout_summary"])
    assert summary["outcome"] == "dependent"
    assert summary["certificate"]["certificate"]["relation"] == ["2", "-1"]

    stored = db.execute(
        "SELECT * FROM points WHERE id=?",
        (int(candidate["id"]),),
    ).fetchone()
    assert stored["independence_status"] == "dependent"
    meta = json.loads(stored["metadata_json"])
    assert (
        meta["last_independence"]["certificate"]["certificate"]["relation"]
        == ["2", "-1"]
    )


def test_results_summary_surfaces_independence_growth():
    from rank42.ui_results import summarize_job

    row = {
        "id": 17,
        "kind": "independence",
        "label": "Independence curve #5",
        "status": "succeeded",
        "exit_code": 0,
        "metadata_json": "{}",
    }
    log = (
        'RANK42_INDEPENDENCE_RESULT='
        '{"status":"rank_growth","new_independent":2,"exact_dependent":3,'
        '"exact_inconclusive":1,"best_rigorous_lower":21}'
    )
    summary = summarize_job(row, log)

    assert summary["result_status"] == "RANK GROWTH"
    assert summary["new_independent"] == 2
    assert summary["exact_dependent"] == 3
    assert summary["best_rigorous_lower"] == 21
    assert summary["interesting"] is True


def test_hard_reason_recognizes_halving_budget_exhaustion():
    last = {
        "status": "inconclusive",
        "options": {"max_halvings": 150},
        "certificate": {
            "certificate": {
                "halvings": 151,
            }
        },
    }
    assert _hard_reason_from_last(last) == "halving budget exhausted (151/150)"


def test_exact_certificate_forwards_advanced_budgets(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stdout='RANK42_EXACT_LB_JSON={"status":"inconclusive","independent":false}\n',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("rank42.exact_lb.subprocess.run", fake_run)
    result = run_exact_certificate(
        ["0", "0", "0", "-1", "1"],
        [["0", "1"]],
        timeout=9,
        max_halvings=250,
        max_prime=2000000,
        max_columns=1280,
    )

    payload = json.loads(captured["input"])
    assert payload["max_halvings"] == 250
    assert payload["max_prime"] == 2000000
    assert payload["max_columns"] == 1280
    assert captured["timeout"] == 9.0
    assert result["status"] == "inconclusive"


def test_saturation_strategy_replaces_trial_basis_before_exact_certificate(capsys):
    basis = ["B1"]
    candidates = [{"point": "P1", "id": 1}]
    seen = {}

    def saturate(trial):
        assert trial == ["B1", "P1"]
        return {
            "status": "completed",
            "index": "256",
            "saturated_points": [["S1"], ["S2"]],
        }

    def point_factory(xy):
        return xy[0]

    def certify(trial):
        seen["trial"] = list(trial)
        return {"status": "certified_independent", "independent": True}

    working, outcomes = run_saturation_independence(
        basis,
        candidates,
        saturate,
        certify,
        point_factory,
    )

    assert seen["trial"] == ["S1", "S2"]
    assert working == ["S1", "S2"]
    assert outcomes[0]["status"] == "independent"
    assert outcomes[0]["after"] == 2
    assert outcomes[0]["saturation"]["index"] == "256"
    assert outcomes[0]["working_after"] == ["S1", "S2"]
    log = capsys.readouterr().out
    assert "[stage 1/2] exact 2-saturation started" in log
    assert "[stage 1/2] 2-saturation completed" in log
    assert "[stage 2/2] exact independence certificate started" in log
    assert "[stage 2/2] exact certificate finished" in log


def test_saturation_strategy_never_promotes_on_preconditioner_failure():
    basis = ["B1"]
    candidates = [{"point": "P1", "id": 1}]

    def saturate(_trial):
        raise SaturationFailure("not independent enough for saturation")

    working, outcomes = run_saturation_independence(
        basis,
        candidates,
        saturate,
        lambda trial: {"status": "certified_independent", "independent": True},
        lambda xy: xy,
    )

    assert working == ["B1"]
    assert outcomes[0]["status"] == "error"
    assert outcomes[0]["before"] == outcomes[0]["after"] == 1


def test_exact_certificate_emits_heartbeat_while_worker_is_busy(monkeypatch, capsys):
    def fake_run(cmd, **kwargs):
        time.sleep(0.04)
        return SimpleNamespace(
            stdout='RANK42_EXACT_LB_JSON={"status":"inconclusive","independent":false}\n',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("rank42.exact_lb.subprocess.run", fake_run)
    result = run_exact_certificate(
        ["0", "0", "0", "-1", "1"],
        [["0", "1"]],
        timeout=9,
        heartbeat_label="exact certificate curve #3010",
        heartbeat_seconds=0.01,
    )

    out = capsys.readouterr().out
    assert "[heartbeat] exact certificate curve #3010 still running" in out
    assert result["status"] == "inconclusive"


def test_basis_first_resolves_before_full_trial_saturation(capsys):
    basis = [("B1x", "B1y")]
    candidates = [{"point": ("Px", "Py"), "id": 7}]
    saturation_calls = []

    def certify(trial):
        assert trial == [("B1x", "B1y"), ("Px", "Py")]
        return {
            "status": "certified_independent",
            "independent": True,
            "runtime_seconds": 0.1,
            "certificate": {"matrix_rank": 2, "halvings": 0},
        }

    def saturate_trial(trial):
        saturation_calls.append(list(trial))
        raise AssertionError("full-trial saturation should not run")

    working, outcomes = run_basis_first_independence(
        basis,
        candidates,
        certify,
        saturate_trial,
        lambda xy: tuple(xy),
        basis_preconditioning={"cache_hit": True, "index": "4"},
    )

    assert saturation_calls == []
    assert working == [("B1x", "B1y"), ("Px", "Py")]
    assert outcomes[0]["status"] == "independent"
    assert outcomes[0]["resolution_path"] == "prepared_basis_certificate"
    log = capsys.readouterr().out
    assert "[stage 2/3] exact certificate on prepared basis started" in log
    assert "full-trial 2-saturation fallback started" not in log


def test_basis_first_uses_full_trial_saturation_only_after_inconclusive(capsys):
    basis = [("B1x", "B1y")]
    candidates = [{"point": ("Px", "Py"), "id": 8}]
    cert_calls = []

    def certify(trial):
        cert_calls.append(list(trial))
        if len(cert_calls) == 1:
            return {
                "status": "inconclusive",
                "independent": False,
                "runtime_seconds": 0.1,
                "certificate": {"matrix_rank": 1, "halvings": 251},
            }
        return {
            "status": "certified_independent",
            "independent": True,
            "runtime_seconds": 0.2,
            "certificate": {"matrix_rank": 2, "halvings": 0},
        }

    def saturate_trial(trial):
        assert trial == [("B1x", "B1y"), ("Px", "Py")]
        return {
            "status": "completed",
            "index": "8",
            "elapsed_seconds": 0.3,
            "saturated_points": [["S1x", "S1y"], ["S2x", "S2y"]],
        }

    working, outcomes = run_basis_first_independence(
        basis,
        candidates,
        certify,
        saturate_trial,
        lambda xy: tuple(xy),
        basis_preconditioning={"cache_hit": False, "index": "2"},
    )

    assert cert_calls[0] == [("B1x", "B1y"), ("Px", "Py")]
    assert cert_calls[1] == [("S1x", "S1y"), ("S2x", "S2y")]
    assert working == [("S1x", "S1y"), ("S2x", "S2y")]
    assert outcomes[0]["status"] == "independent"
    assert outcomes[0]["resolution_path"] == "full_trial_saturation"
    log = capsys.readouterr().out
    assert "full-trial 2-saturation fallback started" in log


def test_cached_basis_saturation_reuses_persisted_after_basis(tmp_path):
    db = connect(tmp_path / "cache.db")
    cid = upsert_curve(db, family="cache", parameter="1")
    before = [("B1x", "B1y")]
    after = [("S1x", "S1y")]
    before_fp = _basis_fingerprint(before)
    after_fp = _basis_fingerprint(after)

    evidence_id = record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": "sage_saturation",
            "evidence_type": "saturation",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "points_found": [["S1x", "S1y"]],
            "options": {
                "source": "hard_case_basis_precondition",
                "max_prime": 2,
                "index": "4",
                "basis_fingerprint_before": before_fp,
                "basis_fingerprint_after": after_fp,
            },
        },
    )

    points, meta = _cached_basis_saturation(
        db,
        cid,
        after_fp,
        1,
        lambda xy: tuple(xy),
    )

    assert points == after
    assert meta["cache_hit"] is True
    assert meta["evidence_id"] == evidence_id
    assert meta["index"] == "4"


def test_basis_first_can_defer_full_trial_fallback(capsys):
    basis = [("B1x", "B1y")]
    candidates = [{"point": ("Px", "Py"), "id": 9}]
    saturation_calls = []

    def certify(_trial):
        return {
            "status": "inconclusive",
            "independent": False,
            "runtime_seconds": 0.1,
            "certificate": {"matrix_rank": 1, "halvings": 251},
        }

    def saturate_trial(trial):
        saturation_calls.append(list(trial))
        raise AssertionError("deferred full-trial saturation must not run")

    working, outcomes = run_basis_first_independence(
        basis,
        candidates,
        certify,
        saturate_trial,
        lambda xy: tuple(xy),
        basis_preconditioning={"cache_hit": True, "index": "2"},
        allow_full_trial_fallback=False,
    )

    assert working == basis
    assert saturation_calls == []
    assert outcomes[0]["status"] == "inconclusive"
    assert outcomes[0]["resolution_path"] == "prepared_basis_unresolved"
    assert "fallback deferred to escalator" in capsys.readouterr().out


def test_saturation_strategy_closes_candidate_when_saturated_trial_is_exactly_dependent():
    basis = ["B1"]
    candidates = [{"point": "P1", "id": 11}]

    def saturate(_trial):
        return {
            "status": "completed",
            "index": "2",
            "saturated_points": [["S1"], ["S2"]],
        }

    def certify(_trial):
        return {"status": "dependent", "independent": False}

    working, outcomes = run_saturation_independence(
        basis,
        candidates,
        saturate,
        certify,
        lambda xy: xy[0],
    )

    assert working == basis
    assert outcomes[0]["status"] == "dependent"
    assert outcomes[0]["after"] == 1
