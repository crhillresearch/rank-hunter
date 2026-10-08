import subprocess
import time

from rank42.db import connect, get_curve, upsert_curve, update_curve
from rank42.saturation import (
    persist_saturation_evidence,
    primes_in_range,
    run_saturation,
    run_saturation_ladder,
)


def test_run_saturation_parses_completed_worker(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                'RANK42_SATURATION_RESULT='
                '{"status":"completed","attempted":true,"max_prime":100,'
                '"index":"1","regulator":"12.5","saturated_points":[["0","0"]]}\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_saturation(
        ["0", "0", "0", "-1", "0"],
        [["0", "0"]],
        max_prime=100,
        timeout=10,
    )
    assert result["status"] == "completed"
    assert result["index"] == "1"
    assert result["elapsed_seconds"] >= 0


def test_saturation_evidence_does_not_promote_rank(tmp_path):
    db = connect(tmp_path / "x.db")
    cid = upsert_curve(db, family="sat", parameter="1")
    update_curve(
        db,
        cid,
        a_invariants_json='["0","0","0","-1","0"]',
        generic_lower=5,
    )

    persist_saturation_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "0"],
        result={
            "attempted": True,
            "status": "completed",
            "max_prime": 100,
            "index": "3",
            "regulator": "7.25",
            "saturated_points": [["0", "0"]],
            "model_used": "global_minimal",
        },
        source="test",
    )

    row = get_curve(db, cid)
    assert row["generic_lower"] == 5
    assert row["exact_rank"] is None
    assert row["regulator"] is None
    evidence = db.execute(
        "SELECT * FROM rank_evidence WHERE curve_id=? AND evidence_type='saturation'",
        (cid,),
    ).fetchone()
    assert evidence is not None
    assert evidence["status"] == "completed"
    assert evidence["rigorous"] == 1
    import json
    options = json.loads(evidence["options_json"])
    assert options["witness_subgroup_regulator"] == "7.25"
    assert options["regulator_scope"] == "bounded_saturated_witness_subgroup"
    assert options["saturated_through_prime"] == 100


def test_run_saturation_emits_heartbeat_while_worker_is_busy(monkeypatch, capsys):
    def fake_run(*args, **kwargs):
        time.sleep(0.04)
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                'RANK42_SATURATION_RESULT='
                '{"status":"completed","attempted":true,"max_prime":2,'
                '"index":"1","regulator":"1","saturated_points":[["0","0"]]}\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_saturation(
        ["0", "0", "0", "-1", "0"],
        [["0", "0"]],
        max_prime=2,
        timeout=10,
        heartbeat_label="2-saturation curve #3010",
        heartbeat_seconds=0.01,
    )

    out = capsys.readouterr().out
    assert "[heartbeat] 2-saturation curve #3010 still running" in out
    assert result["status"] == "completed"


def test_saturation_evidence_persists_cache_metadata(tmp_path):
    db = connect(tmp_path / "cache-meta.db")
    cid = upsert_curve(db, family="sat-cache", parameter="1")

    persist_saturation_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        result={
            "attempted": True,
            "status": "completed",
            "max_prime": 2,
            "index": "4",
            "saturated_points": [["1", "1"]],
        },
        source="hard_case_basis_precondition",
        extra_options={
            "basis_fingerprint_before": "before",
            "basis_fingerprint_after": "after",
            "stage": "rigorous_basis_precondition",
        },
    )

    row = db.execute(
        "SELECT options_json FROM rank_evidence WHERE curve_id=? ORDER BY id DESC LIMIT 1",
        (cid,),
    ).fetchone()
    import json
    options = json.loads(row["options_json"])
    assert options["source"] == "hard_case_basis_precondition"
    assert options["max_prime"] == 2
    assert options["basis_fingerprint_before"] == "before"
    assert options["basis_fingerprint_after"] == "after"


def test_run_saturation_forwards_single_prime_range(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                'RANK42_SATURATION_RESULT='
                '{"status":"completed","attempted":true,"min_prime":5,"max_prime":5,'
                '"index":"1","regulator":"1","saturated_points":[["0","0"]]}\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_saturation(
        ["0", "0", "0", "-1", "0"],
        [["0", "0"]],
        min_prime=5,
        max_prime=5,
        timeout=10,
    )
    import json
    payload = json.loads(captured["input"])
    assert payload["min_prime"] == 5
    assert payload["max_prime"] == 5
    assert result["min_prime"] == 5
    assert result["max_prime"] == 5



def test_primes_in_range_is_inclusive_and_prime_only():
    assert primes_in_range(2, 11) == [2, 3, 5, 7, 11]
    assert primes_in_range(8, 10) == []
    assert primes_in_range(13, 13) == [13]


def test_saturation_ladder_carries_basis_and_multiplies_index(monkeypatch):
    calls = []

    def fake_run(a_invariants, points, *, min_prime, max_prime, timeout, heartbeat_label=None, heartbeat_seconds=30):
        calls.append({
            "prime": min_prime,
            "points": [list(point) for point in points],
            "timeout": timeout,
            "label": heartbeat_label,
        })
        if min_prime == 2:
            return {
                "status": "completed",
                "min_prime": 2,
                "max_prime": 2,
                "index": "2",
                "regulator": "12",
                "saturated_points": [["1", "2"]],
                "model_used": "global_minimal",
                "elapsed_seconds": 1.25,
            }
        return {
            "status": "completed",
            "min_prime": 3,
            "max_prime": 3,
            "index": "3",
            "regulator": "4",
            "saturated_points": [["5", "8"]],
            "model_used": "global_minimal",
            "elapsed_seconds": 2.5,
        }

    monkeypatch.setattr("rank42.saturation.run_saturation", fake_run)
    result = run_saturation_ladder(
        ["0", "0", "0", "-1", "1"],
        [["0", "1"]],
        min_prime=2,
        max_prime=3,
        timeout=9,
        heartbeat_label="fixture",
    )

    assert [call["prime"] for call in calls] == [2, 3]
    assert calls[1]["points"] == [["1", "2"]]
    assert result["status"] == "completed"
    assert result["index"] == "6"
    assert result["saturated_points"] == [["5", "8"]]
    assert result["saturated_through_prime"] == 3
    assert [step["index_factor"] for step in result["prime_results"]] == ["2", "3"]
    assert result["prime_results"][0]["basis_changed"] is True
    assert result["elapsed_seconds"] == 3.75


def test_saturation_ladder_timeout_is_partial_and_inconclusive(monkeypatch):
    def fake_run(a_invariants, points, *, min_prime, max_prime, timeout, heartbeat_label=None, heartbeat_seconds=30):
        if min_prime == 2:
            return {
                "status": "completed",
                "min_prime": 2,
                "max_prime": 2,
                "index": "2",
                "regulator": "12",
                "saturated_points": [["1", "2"]],
                "elapsed_seconds": 1.0,
            }
        from rank42.saturation import SaturationTimeout
        raise SaturationTimeout("fixture timeout")

    monkeypatch.setattr("rank42.saturation.run_saturation", fake_run)
    result = run_saturation_ladder(
        ["0", "0", "0", "-1", "1"],
        [["0", "1"]],
        min_prime=2,
        max_prime=3,
        timeout=1,
    )

    assert result["status"] == "timeout"
    assert result["partial"] is True
    assert result["index"] == "2"
    assert result["saturated_through_prime"] == 2
    assert result["prime_results"][-1]["status"] == "timeout"
    assert result["saturated_points"] == [["1", "2"]]


def test_saturation_evidence_retains_ladder_detail_without_rank_promotion(tmp_path):
    db = connect(tmp_path / "ladder.db")
    cid = upsert_curve(db, family="sat-ladder", parameter="1")
    update_curve(
        db,
        cid,
        a_invariants_json='["0","0","0","-1","0"]',
        generic_lower=4,
    )

    persist_saturation_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "0"],
        result={
            "attempted": True,
            "status": "completed",
            "mode": "ladder",
            "min_prime": 2,
            "max_prime": 7,
            "saturated_through_prime": 7,
            "index": "6",
            "regulator": "2.5",
            "saturated_points": [["0", "0"]],
            "basis_fingerprint_before": "before",
            "basis_fingerprint_after": "after",
            "prime_results": [
                {"prime": 2, "status": "completed", "index_factor": "2"},
                {"prime": 3, "status": "completed", "index_factor": "3"},
                {"prime": 5, "status": "completed", "index_factor": "1"},
                {"prime": 7, "status": "completed", "index_factor": "1"},
            ],
        },
        source="analysis_saturation",
    )

    row = get_curve(db, cid)
    assert row["generic_lower"] == 4
    assert row["exact_rank"] is None
    evidence = db.execute(
        "SELECT * FROM rank_evidence WHERE curve_id=? AND evidence_type='saturation'",
        (cid,),
    ).fetchone()
    import json
    options = json.loads(evidence["options_json"])
    assert options["mode"] == "ladder"
    assert options["saturated_through_prime"] == 7
    assert options["basis_fingerprint_before"] == "before"
    assert options["basis_fingerprint_after"] == "after"
    assert [step["prime"] for step in options["prime_results"]] == [2, 3, 5, 7]



def test_saturation_cli_accepts_prime_range_and_ladder_mode(monkeypatch):
    import sys
    from rank42.cli import parse_args

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rank42",
            "saturate",
            "--curve-id",
            "17",
            "--mode",
            "ladder",
            "--min-prime",
            "5",
            "--max-prime",
            "29",
            "--timeout",
            "77",
        ],
    )
    args = parse_args()

    assert args.command == "saturate"
    assert args.curve_id == 17
    assert args.mode == "ladder"
    assert args.min_prime == 5
    assert args.max_prime == 29
    assert args.timeout == 77
