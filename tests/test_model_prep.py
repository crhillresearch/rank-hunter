import subprocess

import pytest

from rank42.model_prep import ModelPrepFailure, ModelPrepTimeout, run_global_minimal_model


def test_model_prep_parses_minimal_model(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout='noise\nRANK42_MODEL_PREP={"minimal_a_invariants":["1","0","0","-2","3"]}\n',
            stderr='',
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_global_minimal_model([1, 0, 0, -2, 3], timeout=10)
    assert result["a_invariants"] == ["1", "0", "0", "-2", "3"]
    assert result["runtime"] >= 0


def test_model_prep_timeout_is_distinct(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 10))
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ModelPrepTimeout, match="exceeded 7s"):
        run_global_minimal_model([0, 0, 0, -1, 0], timeout=7)


def test_model_prep_bad_worker_output_is_failure(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout='', stderr='boom')
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ModelPrepFailure, match="worker failed"):
        run_global_minimal_model([0, 0, 0, -1, 0], timeout=10)
