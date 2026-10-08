import json
from types import SimpleNamespace

from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.hard_case_escalator import (
    ENGINE,
    _active_interrupted_stage,
    _budget_dominates,
    _clear_bookmark_for_exact_curve,
    _close_from_exact_ceiling,
    _independence_command,
    _legacy_independence_exhaustion,
    _parse_primes,
    _prior_stage_attempt,
    _record_stage_attempt,
    _recover_legacy_interrupted_stage,
    _saturation_family_blocker,
    _trial_saturation_budget,
)
from rank42.points import set_point_hard_flag, upsert_point
from rank42.rank_evidence import record_rank_evidence


def test_parse_prime_ladder_is_ordered_and_deduplicated():
    assert _parse_primes("2,3,5,3,7") == [2, 3, 5, 7]


def test_escalator_independence_command_can_defer_and_target_prime():
    args = SimpleNamespace(
        db="rank42.db",
        curve_id=3010,
        certificate_timeout=600,
        max_halvings=250,
        max_prime=2000000,
        max_columns=1280,
    )
    cmd = _independence_command(
        args,
        [68989],
        strategy="trial-saturation",
        saturation_timeout=300,
        saturation_prime=5,
        defer_full_trial=True,
    )
    joined = " ".join(str(x) for x in cmd)
    assert "--strategy trial-saturation" in joined
    assert "--saturation-prime 5" in joined
    assert "--no-full-trial-fallback" in cmd
    assert "--point-id 68989" in joined


def test_exact_rank_ceiling_closes_hard_point_as_dependent(tmp_path):
    db = connect(tmp_path / "x.db")
    cid = upsert_curve(db, family="test", parameter="1")
    update_curve(
        db,
        cid,
        a_invariants_json='["0","0","0","-1","1"]',
        descent_lower=1,
        descent_upper=1,
        exact_rank=1,
        certain=1,
        generators_json='[["0","1"]]',
        status="exact",
    )
    rec = upsert_point(
        db,
        curve_id=cid,
        x="2",
        y="3",
        source="test",
        exact_verified=True,
        independence_status="inconclusive",
        rigorous_independent=False,
    )
    set_point_hard_flag(db, int(rec["id"]), True, reason="hard")

    status = _close_from_exact_ceiling(db, cid, int(rec["id"]))

    assert status == "dependent"
    point = db.execute("SELECT * FROM points WHERE id=?", (int(rec["id"]),)).fetchone()
    assert point["independence_status"] == "dependent"
    assert point["hard_flag"] == 0
    meta = json.loads(point["metadata_json"])
    assert meta["last_hard_case_resolution"]["mechanism"] == "exact_rank_ceiling"


def test_curve_exact_can_clear_bookmark_without_claiming_point_dependence(tmp_path):
    db = connect(tmp_path / "y.db")
    cid = upsert_curve(db, family="test", parameter="2")
    rec = upsert_point(
        db,
        curve_id=cid,
        x="4",
        y="5",
        source="test",
        exact_verified=True,
        independence_status="inconclusive",
        rigorous_independent=False,
    )
    set_point_hard_flag(db, int(rec["id"]), True, reason="hard")

    status = _clear_bookmark_for_exact_curve(db, cid, int(rec["id"]), 19)

    assert status == "curve_exact"
    point = db.execute("SELECT * FROM points WHERE id=?", (int(rec["id"]),)).fetchone()
    assert point["hard_flag"] == 0
    assert point["independence_status"] == "inconclusive"
    meta = json.loads(point["metadata_json"])
    assert meta["last_hard_case_resolution"]["exact_rank"] == 19
    assert meta["last_hard_case_resolution"]["point_relation"] == "not_individually_resolved"


def test_budget_dominance_requires_equal_or_stronger_values():
    previous = {"timeout": 1200, "max_halvings": 250}
    assert _budget_dominates(previous, {"timeout": 600, "max_halvings": 250})
    assert not _budget_dominates(previous, {"timeout": 1800, "max_halvings": 250})
    assert not _budget_dominates(previous, {"timeout": 600, "max_halvings": 300})


def test_prior_stage_skips_equal_or_weaker_budget_on_same_basis(tmp_path):
    db = connect(tmp_path / "history.db")
    cid = upsert_curve(db, family="test", parameter="history")
    model = ["0", "0", "0", "-1", "1"]
    evidence_id = _record_stage_attempt(
        db,
        curve_id=cid,
        model=model,
        method="basis_2_saturation",
        basis_fingerprint="basis-a",
        budget={
            "certificate_timeout": 600,
            "max_halvings": 250,
            "max_prime": 2000000,
            "max_columns": 1280,
            "saturation_timeout": 1200,
        },
        status="timeout",
        point_id=None,
    )

    prior = _prior_stage_attempt(
        db,
        curve_id=cid,
        method="basis_2_saturation",
        basis_fingerprint="basis-a",
        budget={
            "certificate_timeout": 600,
            "max_halvings": 250,
            "max_prime": 2000000,
            "max_columns": 1280,
            "saturation_timeout": 600,
        },
        point_id=99,
        point_scoped=False,
    )
    assert prior is not None
    assert int(prior["id"]) == evidence_id

    stronger = _prior_stage_attempt(
        db,
        curve_id=cid,
        method="basis_2_saturation",
        basis_fingerprint="basis-a",
        budget={
            "certificate_timeout": 600,
            "max_halvings": 250,
            "max_prime": 2000000,
            "max_columns": 1280,
            "saturation_timeout": 1800,
        },
        point_id=99,
        point_scoped=False,
    )
    assert stronger is None

    changed_basis = _prior_stage_attempt(
        db,
        curve_id=cid,
        method="basis_2_saturation",
        basis_fingerprint="basis-b",
        budget={
            "certificate_timeout": 600,
            "max_halvings": 250,
            "max_prime": 2000000,
            "max_columns": 1280,
            "saturation_timeout": 600,
        },
        point_id=99,
        point_scoped=False,
    )
    assert changed_basis is None


def test_targeted_prime_history_is_point_and_prime_specific(tmp_path):
    db = connect(tmp_path / "prime-history.db")
    cid = upsert_curve(db, family="test", parameter="prime-history")
    _record_stage_attempt(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        method="trial_saturation",
        basis_fingerprint="basis-a",
        budget={"saturation_timeout": 300},
        status="timeout",
        point_id=7,
        prime=3,
    )

    assert _prior_stage_attempt(
        db,
        curve_id=cid,
        method="trial_saturation",
        basis_fingerprint="basis-a",
        budget={"saturation_timeout": 300},
        point_id=7,
        prime=3,
    ) is not None
    assert _prior_stage_attempt(
        db,
        curve_id=cid,
        method="trial_saturation",
        basis_fingerprint="basis-a",
        budget={"saturation_timeout": 300},
        point_id=7,
        prime=5,
    ) is None
    assert _prior_stage_attempt(
        db,
        curve_id=cid,
        method="trial_saturation",
        basis_fingerprint="basis-a",
        budget={"saturation_timeout": 300},
        point_id=8,
        prime=3,
    ) is None


def test_legacy_basis_saturation_timeout_is_recognized(tmp_path):
    db = connect(tmp_path / "legacy.db")
    cid = upsert_curve(db, family="test", parameter="legacy")
    record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": "rank42.independence_check",
            "engine_version": "5",
            "evidence_type": "exact_certificate",
            "status": "timeout",
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "options": {
                "candidate_point_id": 42,
                "basis_fingerprint_before": "basis-a",
                "strategy": "saturation",
                "resolution_path": "basis_precondition",
                "saturation_timeout": 1200,
                "max_halvings": 250,
                "max_prime": 2000000,
                "max_columns": 1280,
            },
        },
    )

    prior = _legacy_independence_exhaustion(
        db,
        curve_id=cid,
        point_id=999,
        basis_fingerprint="basis-a",
        method="basis_2_saturation",
        budget={
            "saturation_timeout": 600,
            "max_halvings": 250,
            "max_prime": 2000000,
            "max_columns": 1280,
        },
    )
    assert prior is not None


def test_interrupted_basis_stage_uses_observed_heartbeat_not_configured_budget():
    row = {
        "kind": "independence",
        "metadata_json": json.dumps({
            "mode": "hard_case_basis_first_saturation",
            "curve_id": 3010,
            "point_ids": [68989],
            "saturation_timeout": 1200,
        }),
    }
    log = """[stage 1/3] rigorous-basis 2-saturation started · points=18
[heartbeat] basis 2-saturation curve #3010 still running · 750s elapsed
[heartbeat] basis 2-saturation curve #3010 still running · 780s elapsed
"""
    stage = _active_interrupted_stage(row, log)
    assert stage["method"] == "basis_2_saturation"
    assert stage["elapsed_seconds"] == 780
    assert stage["configured_budget"] == 1200


def test_recover_legacy_killed_basis_job_as_stage_evidence(tmp_path):
    from rank42.ui_store import create_job, ensure_ui_schema, update_job

    db = connect(tmp_path / "legacy-kill.db")
    ensure_ui_schema(db)
    cid = upsert_curve(db, family="test", parameter="killed")
    update_curve(
        db,
        cid,
        a_invariants_json='["0","0","0","-1","1"]',
        descent_lower=1,
        generators_json='[["0","1"]]',
    )
    # Give recovery an authoritative pre-job fingerprint source.
    record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": "rank42.independence_check",
            "engine_version": "5",
            "evidence_type": "exact_certificate",
            "status": "inconclusive",
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "options": {
                "candidate_point_id": 7,
                "basis_fingerprint_before": "legacy-basis",
                "strategy": "standard",
            },
        },
    )
    log = tmp_path / "job.log"
    jid = create_job(
        db,
        kind="independence",
        label="legacy killed basis",
        command=[
            "python", "-m", "rank42.independence_check",
            "--timeout", "600",
            "--max-halvings", "250",
            "--max-prime", "2000000",
            "--max-columns", "1280",
        ],
        cwd=tmp_path,
        log_path=log,
        metadata={
            "curve_id": cid,
            "point_ids": [7],
            "mode": "hard_case_basis_first_saturation",
            "saturation_timeout": 1200,
        },
    )
    log.write_text(
        "[stage 1/3] rigorous-basis 2-saturation started · points=1\n"
        "[heartbeat] basis 2-saturation curve #1 still running · 780s elapsed\n"
    )
    update_job(
        db,
        jid,
        status="killed",
        started_at="2099-01-01T00:00:00+00:00",
        finished_at="2099-01-01T00:13:10+00:00",
    )

    # For this synthetic row, current basis fingerprint differs from our marker;
    # use the actual current fingerprint as the authoritative pre-job evidence.
    from rank42.hard_case_escalator import _basis_fingerprint_from_row
    current_fp = _basis_fingerprint_from_row(get_curve(db, cid))
    latest = db.execute(
        "SELECT id,options_json FROM rank_evidence WHERE curve_id=? ORDER BY id DESC LIMIT 1",
        (cid,),
    ).fetchone()
    opts = json.loads(latest["options_json"])
    opts["basis_fingerprint_before"] = current_fp
    db.execute(
        "UPDATE rank_evidence SET options_json=?,created_at=? WHERE id=?",
        (json.dumps(opts), "2098-12-31T23:59:59+00:00", int(latest["id"])),
    )
    db.commit()

    rec = _recover_legacy_interrupted_stage(
        db,
        curve_id=cid,
        method="basis_2_saturation",
        basis_fingerprint=current_fp,
        budget={
            "saturation_timeout": 600,
            "certificate_timeout": 600,
            "max_halvings": 250,
            "max_prime": 2000000,
            "max_columns": 1280,
        },
        point_id=7,
    )
    assert rec is not None
    assert rec["status"] == "interrupted"
    options = json.loads(rec["options_json"])
    assert options["budget"]["saturation_timeout"] == 780


def test_saturation_family_blocker_accepts_legacy_full_trial_timeout(tmp_path):
    db = connect(tmp_path / "family-legacy.db")
    cid = upsert_curve(db, family="test", parameter="family")
    record_rank_evidence(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        data={
            "engine": "rank42.independence_check",
            "engine_version": "5",
            "evidence_type": "exact_certificate",
            "status": "timeout",
            "rigorous": False,
            "rigorous_lower": None,
            "rigorous_upper": None,
            "exact_rank": None,
            "options": {
                "candidate_point_id": 77,
                "basis_fingerprint_before": "basis-a",
                "strategy": "saturation",
                "resolution_path": None,
                "saturation_timeout": 1200,
            },
        },
    )

    blocker = _saturation_family_blocker(
        db,
        curve_id=cid,
        point_id=77,
        basis_fingerprint="basis-a",
    )
    assert blocker is not None
    assert blocker["status"] == "timeout"


def test_saturation_family_blocker_ignores_other_point_or_basis(tmp_path):
    db = connect(tmp_path / "family-scope.db")
    cid = upsert_curve(db, family="test", parameter="scope")
    _record_stage_attempt(
        db,
        curve_id=cid,
        model=["0", "0", "0", "-1", "1"],
        method="trial_saturation",
        basis_fingerprint="basis-a",
        budget={"saturation_timeout": 90},
        status="timeout",
        point_id=7,
        prime=3,
    )

    assert _saturation_family_blocker(
        db, curve_id=cid, point_id=7, basis_fingerprint="basis-a"
    ) is not None
    assert _saturation_family_blocker(
        db, curve_id=cid, point_id=8, basis_fingerprint="basis-a"
    ) is None
    assert _saturation_family_blocker(
        db, curve_id=cid, point_id=7, basis_fingerprint="basis-b"
    ) is None


def test_targeted_saturation_uses_short_probe_unless_deep():
    args = SimpleNamespace(
        trial_saturation_timeout=300,
        saturation_probe_timeout=90,
        deep_saturation=False,
    )
    assert _trial_saturation_budget(args, 2) == 300
    assert _trial_saturation_budget(args, 3) == 90
    assert _trial_saturation_budget(args, 7) == 90

    args.deep_saturation = True
    assert _trial_saturation_budget(args, 3) == 300
    assert _trial_saturation_budget(args, 7) == 300


def test_resolver_orders_orthogonal_methods_before_saturation_family():
    import inspect
    from rank42 import hard_case_escalator

    source = inspect.getsource(hard_case_escalator.resolve_point)
    selmer = source.index('"mwrank_selmer"')
    simon = source.index('"simon_known"')
    coverings = source.index('"mwrank_coverings"')
    proof = source.index("_run_proof_rank(")
    basis_sat = source.index('history_method="basis_2_saturation"')
    family = source.index("_saturation_family_blocker(")

    assert selmer < simon < coverings < proof < basis_sat < family
