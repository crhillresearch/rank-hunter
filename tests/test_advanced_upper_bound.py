from types import SimpleNamespace

from rank42.advanced_upper_bound import ENGINE, _prior_attempt
from rank42.db import connect, upsert_curve
from rank42.rank_evidence import record_rank_evidence


def test_advanced_simon_history_requires_equal_or_stronger_budget(tmp_path):
    db = connect(tmp_path / "advanced-upper.db")
    cid = upsert_curve(db, family="test", parameter="upper")
    record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": ENGINE,
            "engine_version": "1",
            "evidence_type": "advanced_upper_bound_attempt",
            "status": "timeout",
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "options": {
                "backend": "simon_strong",
                "basis_fingerprint": "basis-a",
                "timeout": 900,
                "lim1": 10,
                "lim3": 160,
                "limtriv": 5,
                "maxprob": 40,
                "limbigprime": 60,
            },
        },
    )

    args = SimpleNamespace(
        curve_id=cid,
        backend="simon_strong",
        timeout=600,
        lim1=8,
        lim3=120,
        limtriv=4,
        maxprob=30,
        limbigprime=50,
    )
    assert _prior_attempt(db, args, basis_fingerprint="basis-a") is not None
    assert _prior_attempt(db, args, basis_fingerprint="basis-b") is None

    args.lim3 = 200
    assert _prior_attempt(db, args, basis_fingerprint="basis-a") is None


def test_advanced_upper_backend_is_part_of_history_key(tmp_path):
    db = connect(tmp_path / "advanced-backend.db")
    cid = upsert_curve(db, family="test", parameter="backend")
    record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": ENGINE,
            "engine_version": "1",
            "evidence_type": "exact_certificate",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": 1,
            "rigorous_upper": 1,
            "exact_rank": 1,
            "options": {
                "backend": "sage_proof_rank",
                "timeout": 900,
            },
        },
    )
    args = SimpleNamespace(
        curve_id=cid,
        backend="sage_proof_rank",
        timeout=600,
        lim1=10,
        lim3=160,
        limtriv=5,
        maxprob=40,
        limbigprime=60,
    )
    assert _prior_attempt(db, args) is not None
    args.backend = "simon_strong"
    assert _prior_attempt(db, args) is None
