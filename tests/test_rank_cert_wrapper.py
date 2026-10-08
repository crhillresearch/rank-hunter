import json
import subprocess

import pytest

from rank42.rank_cert import MARKER, RankCertificationTimeout, run_rank_certification


def test_rank_cert_wrapper_parses_exact_result(monkeypatch):
    class CP:
        returncode = 0
        stdout = MARKER + json.dumps({"exact_rank": 3, "proof_mode": True}) + "\n"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: CP())
    out = run_rank_certification([0, 0, 0, 14, 1], timeout=5)
    assert out["exact_rank"] == 3
    assert out["proof_mode"] is True


def test_rank_cert_wrapper_has_hard_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=5)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(RankCertificationTimeout):
        run_rank_certification([0, 0, 0, 14, 1], timeout=5)
