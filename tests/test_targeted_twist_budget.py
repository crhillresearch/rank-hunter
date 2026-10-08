import sqlite3
import subprocess

from sage.all import EllipticCurve, QQ

from rank42 import pipeline_transforms as transforms
from rank42 import prime_local
from rank42 import targeted_twist_scan
from rank42.db import connect, upsert_curve


def _prime_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        """CREATE TABLE curves(
               id INTEGER PRIMARY KEY,
               conductor TEXT,
               discriminant TEXT,
               bad_primes_json TEXT,
               root_number INTEGER,
               updated_at TEXT
           )"""
    )
    db.execute(
        """CREATE TABLE quartic_searches(
               id INTEGER PRIMARY KEY,
               curve_id INTEGER,
               degree INTEGER,
               polynomial_json TEXT,
               hole_label TEXT,
               FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
           )"""
    )
    prime_local.ensure_prime_schema(db)
    db.execute("INSERT INTO curves(id) VALUES(1)")
    return db


def _curve():
    return EllipticCurve(QQ, [0, 0, 0, -1, 0])


def _score_payload(score, *, status="completed"):
    return {
        "status": status,
        "algorithm": "cached-nagao-log-cardinality-v1",
        "heuristic_kind": "rank_hunter_historical_nagao_style",
        "published_mestre_nagao_formula": False,
        "prime_bound": 29,
        "pipeline_score": float(score),
        "primes_used": 8,
        "score_domain": {
            "algorithm": "cached-nagao-log-cardinality-v1",
            "prime_bounds": [29],
            "good_primes_requested": [3, 5, 7],
            "good_primes_used": [3, 5, 7],
            "failed_primes": [],
            "unknown_reduction_primes": [],
            "cache_status": "not_used",
            "cache_used": False,
            "source": "direct_exact_frobenius",
            "complete": status == "completed",
        },
        "source_provenance": {
            "source": "direct_exact_frobenius",
            "cache_used": False,
            "model_key": "fixture",
        },
        "heuristic_only": True,
    }


def test_direct_targeted_twist_score_matches_cached_shared_algorithm():
    E = _curve()
    db = _prime_db()
    cached = prime_local.multi_scale_frobenius_score(
        db,
        curve_id=1,
        E=E,
        bounds=[29],
    )
    direct = prime_local.direct_nagao_log_cardinality_score(
        E,
        prime_bound=29,
    )

    assert cached["status"] == "completed"
    assert direct["status"] == "completed"
    assert cached["algorithm"] == direct["algorithm"] == (
        "cached-nagao-log-cardinality-v1"
    )
    assert direct["heuristic_kind"] == (
        "rank_hunter_historical_nagao_style"
    )
    assert direct["published_mestre_nagao_formula"] is False
    assert direct["pipeline_score"] == cached["pipeline_score"]
    assert direct["primes_used"] == cached["primes_used"]
    assert direct["score_domain"]["cache_used"] is False
    assert direct["score_domain"]["source"] == "direct_exact_frobenius"


def test_targeted_twist_worker_returns_shared_score_provenance():
    E = _curve()
    result = targeted_twist_scan.run_targeted_twist_trial(
        E.a_invariants(),
        2,
        prime_bound=29,
        root_sign="any",
        timeout=10,
    )

    assert result["status"] in {"completed", "partial"}
    assert result["twist_d"] == 2
    assert result["root_number"] in {-1, 1}
    assert len(result["a_invariants"]) == 5
    score = result["score"]
    assert score["algorithm"] == "cached-nagao-log-cardinality-v1"
    assert score["published_mestre_nagao_formula"] is False
    assert score["score_domain"]["cache_used"] is False
    assert score["source_provenance"]["source"] == (
        "direct_exact_frobenius"
    )


def test_targeted_twist_trial_timeout_kills_worker(monkeypatch):
    killed = []

    class Proc:
        pid = 12345
        returncode = None

        def communicate(self, payload, timeout):
            raise subprocess.TimeoutExpired("targeted-twist", timeout)

    monkeypatch.setattr(
        targeted_twist_scan.subprocess,
        "Popen",
        lambda *args, **kwargs: Proc(),
    )
    monkeypatch.setattr(
        targeted_twist_scan,
        "_terminate_process_tree",
        lambda proc: killed.append(proc.pid),
    )

    result = targeted_twist_scan.run_targeted_twist_trial(
        _curve().a_invariants(),
        2,
        prime_bound=29,
        root_sign="any",
        timeout=0.2,
    )

    assert result["status"] == "timeout"
    assert result["twist_d"] == 2
    assert result["timeout_seconds"] == 0.2
    assert killed == [12345]


def test_targeted_twist_mixed_success_timeout_keeps_ranked_child(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    E = _curve()
    parent_id = upsert_curve(
        db,
        family="targeted-twist-test",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )

    def trial(ainvs, d, *, prime_bound, root_sign, timeout):
        if int(d) == 2:
            child = E.quadratic_twist(2)
            return {
                "status": "completed",
                "twist_d": 2,
                "root_number": int(child.root_number()),
                "a_invariants": [
                    str(x) for x in child.a_invariants()
                ],
                "score": _score_payload(4.25),
                "runtime_seconds": 0.01,
                "timeout_seconds": timeout,
            }
        return {
            "status": "timeout",
            "twist_d": int(d),
            "reason": "fixture timeout",
            "runtime_seconds": timeout,
            "timeout_seconds": timeout,
        }

    monkeypatch.setattr(transforms, "run_targeted_twist_trial", trial)
    result = transforms.targeted_twist_children(
        db,
        context={"curve_id": parent_id, "E": E},
        config={
            "d_values": [2, 3],
            "scan_limit": 2,
            "max_children": 2,
            "prime_bound": 29,
            "timeout": 30,
            "per_twist_timeout": 2,
        },
    )

    assert result["status"] == "partial"
    assert result["attempted"] == 2
    assert result["eligible"] == 1
    assert result["selected_for_output"] == 1
    assert result["failed"] == 1
    assert [item["status"] for item in result["outcomes"]] == [
        "completed", "timeout"
    ]
    child = result["children"][0]
    assert child["score"] == 4.25
    meta = child["metadata"]
    assert meta["score_inherited"] is False
    assert meta["score_algorithm"] == "cached-nagao-log-cardinality-v1"
    assert meta["score_heuristic_kind"] == (
        "rank_hunter_historical_nagao_style"
    )
    assert meta["score_domain"]["cache_used"] is False
    assert meta["score_source_provenance"]["source"] == (
        "direct_exact_frobenius"
    )


def test_targeted_twist_stage_budget_keeps_completed_partial_result(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "budget.db")
    E = _curve()
    parent_id = upsert_curve(
        db,
        family="targeted-twist-budget",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    times = iter([0.0, 0.0, 2.0, 2.0])
    monkeypatch.setattr(
        transforms.time, "monotonic", lambda: next(times)
    )

    def first_only(ainvs, d, *, prime_bound, root_sign, timeout):
        assert int(d) == 2
        child = E.quadratic_twist(2)
        return {
            "status": "completed",
            "twist_d": 2,
            "root_number": int(child.root_number()),
            "a_invariants": [str(x) for x in child.a_invariants()],
            "score": _score_payload(7.5),
            "runtime_seconds": 0.01,
            "timeout_seconds": timeout,
        }

    monkeypatch.setattr(
        transforms, "run_targeted_twist_trial", first_only
    )
    result = transforms.targeted_twist_children(
        db,
        context={"curve_id": parent_id, "E": E},
        config={
            "d_values": [2, 3],
            "scan_limit": 2,
            "max_children": 2,
            "prime_bound": 29,
            "timeout": 1,
            "per_twist_timeout": 10,
        },
    )

    assert result["status"] == "partial"
    assert result["reason"] == "stage_timeout_budget_exhausted"
    assert result["budget_exhausted"] is True
    assert result["attempted"] == 1
    assert result["not_attempted"] == 1
    assert result["children"][0]["score"] == 7.5
    assert result["outcomes"][1] == {
        "twist_d": 3,
        "status": "not_attempted",
        "reason": "stage_timeout_budget_exhausted",
    }


def test_targeted_twist_all_trial_timeouts_are_not_completed(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "all-timeout.db")
    E = _curve()
    parent_id = upsert_curve(
        db,
        family="targeted-twist-timeout",
        parameter="parent",
        a_invariants_json='["0","0","0","-1","0"]',
        status="test",
    )
    monkeypatch.setattr(
        transforms,
        "run_targeted_twist_trial",
        lambda ainvs, d, **kwargs: {
            "status": "timeout",
            "twist_d": int(d),
            "reason": "fixture timeout",
            "runtime_seconds": 0.1,
            "timeout_seconds": kwargs["timeout"],
        },
    )

    result = transforms.targeted_twist_children(
        db,
        context={"curve_id": parent_id, "E": E},
        config={
            "d_values": [2, 3],
            "scan_limit": 2,
            "timeout": 30,
            "per_twist_timeout": 2,
        },
    )

    assert result["status"] == "timeout"
    assert result["children"] == []
    assert result["attempted"] == 2
    assert result["failed"] == 2
    assert all(
        item["status"] == "timeout" for item in result["outcomes"]
    )


def test_targeted_twist_root_sign_filter_uses_exact_sage_control():
    E = _curve()
    baseline = targeted_twist_scan.run_targeted_twist_trial(
        E.a_invariants(),
        2,
        prime_bound=29,
        root_sign="any",
        timeout=10,
    )
    assert baseline["root_number"] in {-1, 1}
    opposite = "+1" if baseline["root_number"] == -1 else "-1"
    filtered = targeted_twist_scan.run_targeted_twist_trial(
        E.a_invariants(),
        2,
        prime_bound=29,
        root_sign=opposite,
        timeout=10,
    )
    assert filtered["status"] == "filtered"
    assert filtered["reason"] == "root_sign_mismatch"
    assert filtered["root_number"] == baseline["root_number"]
