import json
from types import SimpleNamespace

import pytest

import rank42.rank_bounds as rank_bounds
import rank42.rank_pipeline as rank_pipeline
from rank42.db import connect, get_curve, upsert_curve, update_curve


def _completed(engine, upper, *, options=None):
    return {
        "engine": engine,
        "engine_version": "test-version",
        "sage_version": None,
        "evidence_type": "rank_bounds",
        "status": "completed",
        "rigorous": True,
        "rigorous_lower": None,
        "rigorous_upper": int(upper),
        "exact_rank": None,
        "conditional_analytic_upper": None,
        "numerical_rank_signal": None,
        "assumptions": [],
        "points_found": [],
        "timed_out": False,
        "partial": False,
        "minimal_model_a_invariants": None,
        "options": dict(options or {}),
    }


def _timeout(engine, *, options=None):
    return {
        "engine": engine,
        "engine_version": "test-version",
        "sage_version": None,
        "evidence_type": "rank_bounds",
        "status": "timeout",
        "rigorous": True,
        "rigorous_lower": None,
        "rigorous_upper": None,
        "exact_rank": None,
        "conditional_analytic_upper": None,
        "numerical_rank_signal": None,
        "assumptions": [],
        "points_found": [],
        "timed_out": True,
        "partial": False,
        "minimal_model_a_invariants": None,
        "options": dict(options or {}),
    }


def _test_curve(db, parameter):
    cid = upsert_curve(db, family="test", parameter=parameter, score=0.0)
    model = ["0", "19", "0", "-78", "0"]
    update_curve(
        db,
        cid,
        a_invariants_json=json.dumps(model),
        generic_lower=1,
    )
    return cid, model


def test_eclib_rh_wrapper_passes_known_lower_and_tight(monkeypatch, tmp_path):
    exe = tmp_path / "rh_upper_bound"
    exe.write_text("test")
    exe.chmod(0o755)
    monkeypatch.setattr(rank_bounds, "_resolve_eclib_rh_upper", lambda: str(exe))
    monkeypatch.setattr(
        rank_bounds,
        "runtime_versions",
        lambda engine: {"sage_version": None, "engine_version": "sha256:test"},
    )

    seen = {}

    def fake_run(cmd, *, input, text, capture_output, timeout):
        seen["cmd"] = list(cmd)
        seen["input"] = input
        seen["timeout"] = timeout
        raw = {
            "schema": "eclib-rh-upper-v1",
            "curve": ["0", "19", "0", "-78", "0"],
            "status": "ok",
            "upper_bound": 1,
            "selmer_rank_bound": 2,
            "tight_requested": True,
            "initial_upper_bound": 3,
            "upper_tightened": True,
            "tight_status": "ok",
            "known_lower": 1,
            "known_lower_rigorous": True,
            "rank_gap": 0,
            "exact_rank": 1,
            "certification": "exact",
            "two_torsion": True,
            "second_local_descent": True,
            "global_generator_search": False,
            "saturation": False,
            "rigorous": True,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(raw) + "\n", stderr="")

    monkeypatch.setattr(rank_bounds.subprocess, "run", fake_run)
    result = rank_bounds.run_engine_bounds(
        ["0", "19", "0", "-78", "0"],
        engine="eclib_rh",
        timeout=7,
        known_lower=1,
        tight=True,
    )

    assert seen["cmd"] == [str(exe), "--tight", "--known-lower", "1"]
    assert seen["input"] == "[0,19,0,-78,0]\n"
    assert seen["timeout"] == 7.0
    assert result["status"] == "completed"
    assert result["rigorous_upper"] == 1
    # eclib-rh did not prove the lower bound; Rank Hunter supplied it.
    assert result["rigorous_lower"] is None
    assert result["eclib_rh"]["upper_tightened"] is True


def test_auto_pipeline_stops_after_completed_pari_gap_by_default(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank.db")
    cid, _model = _test_curve(db, "pari-gap")
    calls = []

    monkeypatch.setattr(rank_pipeline, "eclib_rh_available", lambda: True)
    monkeypatch.setattr(
        rank_pipeline,
        "runtime_versions",
        lambda engine: {"sage_version": None, "engine_version": "test-version"},
    )

    def fake_bounds(
            a_invariants,
            *,
            engine,
            timeout,
            known_lower=None,
            tight=True,
            pari_stack_max_bytes=None,
        ):
        calls.append((engine, known_lower, bool(tight), int(timeout)))
        if engine == "pari":
            return _completed("pari", 3, options={"timeout": int(timeout)})
        raise AssertionError(f"unexpected default engine after completed PARI gap: {engine}")

    monkeypatch.setattr(rank_pipeline, "run_engine_bounds", fake_bounds)

    result = rank_pipeline.run_rank_bounds_for_curve(
        db,
        cid,
        engine="auto",
        timeout=11,
        eclib_rh_timeout=5,
        mwrank_timeout=99,
    )

    assert [c[0] for c in calls] == ["pari"]
    assert result["rigorous_lower"] == 1
    assert result["rigorous_upper"] == 3
    assert result["exact_rank"] is None
    assert result["status"] == "completed"


def test_auto_pipeline_uses_eclib_rh_after_pari_timeout(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank.db")
    cid, _model = _test_curve(db, "pari-timeout")
    calls = []

    monkeypatch.setattr(rank_pipeline, "eclib_rh_available", lambda: True)
    monkeypatch.setattr(
        rank_pipeline,
        "runtime_versions",
        lambda engine: {"sage_version": None, "engine_version": "test-version"},
    )

    def fake_bounds(
            a_invariants,
            *,
            engine,
            timeout,
            known_lower=None,
            tight=True,
            pari_stack_max_bytes=None,
        ):
        calls.append((engine, known_lower, bool(tight), int(timeout)))
        options = {"timeout": int(timeout)}
        if engine == "pari":
            return _timeout("pari", options=options)
        if engine == "eclib_rh":
            options.update({"known_lower": int(known_lower), "tight": bool(tight)})
            return _completed("eclib_rh", 1, options=options)
        raise AssertionError(f"full Sage mwrank should not run after eclib-rh closure: {engine}")

    monkeypatch.setattr(rank_pipeline, "run_engine_bounds", fake_bounds)

    result = rank_pipeline.run_rank_bounds_for_curve(
        db,
        cid,
        engine="auto",
        timeout=11,
        eclib_rh_timeout=5,
        mwrank_timeout=99,
    )

    assert [c[0] for c in calls] == ["pari", "eclib_rh"]
    assert calls[1] == ("eclib_rh", 1, True, 5)
    assert result["rigorous_lower"] == 1
    assert result["rigorous_upper"] == 1
    assert result["exact_rank"] == 1
    assert result["status"] == "exact"


def test_auto_pipeline_real_eclib_rh_projects_exact_rank_after_pari_timeout(monkeypatch, tmp_path):
    if not rank_bounds.eclib_rh_available():
        pytest.skip("real rh_upper_bound executable is not available")

    db = connect(tmp_path / "rank.db")
    cid, _model = _test_curve(db, "real-eclib-rh")

    calls = []
    real_runtime_versions = rank_bounds.runtime_versions
    real_run_engine_bounds = rank_bounds.run_engine_bounds

    monkeypatch.setattr(rank_pipeline, "eclib_rh_available", rank_bounds.eclib_rh_available)

    def scheduler_versions(engine):
        if engine == "pari":
            return {"sage_version": None, "engine_version": "forced-pari-timeout"}
        return real_runtime_versions(engine)

    def scheduler_bounds(a_invariants, *, engine, timeout, known_lower=None, tight=True):
        calls.append((engine, known_lower, bool(tight), int(timeout)))
        if engine == "pari":
            return _timeout("pari", options={"timeout": int(timeout)})
        if engine == "eclib_rh":
            return real_run_engine_bounds(
                a_invariants,
                engine="eclib_rh",
                timeout=timeout,
                known_lower=known_lower,
                tight=tight,
            )
        raise AssertionError(f"full Sage mwrank should not run after exact closure: {engine}")

    monkeypatch.setattr(rank_pipeline, "runtime_versions", scheduler_versions)
    monkeypatch.setattr(rank_pipeline, "run_engine_bounds", scheduler_bounds)

    result = rank_pipeline.run_rank_bounds_for_curve(
        db,
        cid,
        engine="auto",
        timeout=11,
        eclib_rh_timeout=5,
        mwrank_timeout=99,
        force=True,
    )

    assert [c[0] for c in calls] == ["pari", "eclib_rh"]
    assert calls[1] == ("eclib_rh", 1, True, 5)
    assert result["rigorous_lower"] == 1
    assert result["rigorous_upper"] == 1
    assert result["exact_rank"] == 1
    assert result["status"] == "exact"

    row = get_curve(db, cid)
    assert int(row["descent_upper"]) == 1
    assert int(row["exact_rank"]) == 1
    assert int(row["certain"]) == 1
    assert row["status"] == "exact"

    evidence = db.execute(
        "SELECT engine,status,rigorous_upper FROM rank_evidence WHERE curve_id=? ORDER BY id",
        (cid,),
    ).fetchall()
    assert [(r["engine"], r["status"], r["rigorous_upper"]) for r in evidence] == [
        ("pari", "timeout", None),
        ("eclib_rh", "completed", 1),
    ]
