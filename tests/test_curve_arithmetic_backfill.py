from __future__ import annotations

import inspect
import json
import subprocess

import rank42.conductor_backfill as conductor_backfill
import rank42.curve_arithmetic_backfill as backfill
import rank42.curve_size_metrics as size_metrics
from rank42.curve_arithmetic_backfill import (
    _backfill_core,
    _run_core_worker,
    arithmetic_backfill_status,
    ensure_schema,
)
from rank42.curve_size_metrics import (
    ensure_curve_size_metrics_schema,
    store_curve_size_metrics,
)
from rank42.db import connect, get_curve, upsert_curve, update_curve
from rank42.torsion import ensure_torsion_schema


def _retained_curve(db):
    curve_id = upsert_curve(
        db,
        family="Arithmetic test",
        parameter="1",
        a_invariants_json=json.dumps([0, 0, 0, -1, 0]),
        descent_lower=2,
        status="proven_lower",
    )
    return int(curve_id)


def test_core_timeout_is_durable_unresolved_not_zero(monkeypatch, tmp_path):
    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    ensure_torsion_schema(db)
    curve_id = _retained_curve(db)

    monkeypatch.setattr(
        backfill,
        "_run_core_worker",
        lambda *args, **kwargs: ("timeout", "timeout after 1s"),
    )

    result = _backfill_core(
        db,
        db_path,
        [curve_id],
        timeout_seconds=1,
        retry_unresolved=False,
    )

    row = get_curve(db, curve_id)
    assert result["timeouts"] == 1
    assert row["discriminant"] is None
    assert row["bad_primes_json"] is None
    assert row["root_number"] is None

    attempt = db.execute(
        """SELECT status,detail
           FROM curve_arithmetic_backfill_attempts
           WHERE curve_id=? AND stage='core'""",
        (curve_id,),
    ).fetchone()
    assert attempt["status"] == "timeout"
    assert "timeout" in str(attempt["detail"]).lower()

    status = arithmetic_backfill_status(db)
    assert status["eligible"] == 1
    assert status["complete"] == 0
    assert status["unresolved"] == 1
    assert status["timeout_stages"] == 1
    db.close()


def test_core_worker_timeout_contract_is_hard_bounded(tmp_path):
    def timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    status, detail = _run_core_worker(
        tmp_path / "rank42.db",
        7,
        timeout_seconds=3,
        executable="/science/python",
        runner=timeout_runner,
    )

    assert status == "timeout"
    assert detail == "timeout after 3s"


def test_arithmetic_status_counts_only_complete_local_fields(tmp_path):
    db = connect(tmp_path / "rank42.db")
    ensure_torsion_schema(db)
    ensure_schema(db)
    curve_id = _retained_curve(db)

    update_curve(
        db,
        curve_id,
        discriminant="64",
        bad_primes_json=json.dumps([2]),
        root_number=1,
        conductor="32",
    )
    db.execute(
        """UPDATE curves
           SET torsion_order=4,
               torsion_invariants_json='[2, 2]',
               torsion_label='C2 × C2',
               torsion_computed_at='now',
               torsion_algorithm='test',
               torsion_error=NULL
           WHERE id=?""",
        (curve_id,),
    )
    db.commit()

    ensure_curve_size_metrics_schema(db)
    store_curve_size_metrics(
        db,
        curve_id,
        {
            "naive_height": "3.5",
            "faltings_height": "-0.25",
            "precision_bits": 128,
            "method": "test",
        },
    )

    status = arithmetic_backfill_status(db)
    assert status == {
        "eligible": 1,
        "complete": 1,
        "unresolved": 0,
        "actionable": 0,
        "blocked": 0,
        "timeout_stages": 0,
        "missing_core": 0,
        "missing_conductor": 0,
        "missing_size": 0,
        "missing_torsion": 0,
    }
    db.close()


def test_batch_orchestrator_reuses_existing_bounded_engines():
    source = inspect.getsource(backfill.backfill_curve_arithmetic)

    assert "_backfill_core(" in source
    assert "backfill_conductors(" in source
    assert "backfill_curve_size_metrics(" in source
    assert "_backfill_torsion(" in source
    assert "fill_conductor=False" in source
    assert "retry_unresolved" in source


def test_core_metadata_cli_is_missing_only_and_separate_from_full_icarm_prepare():
    import rank42.curve_metadata as metadata

    core = inspect.getsource(metadata.enrich_curve_core)
    complete = inspect.getsource(metadata.compute_metadata)
    cli = inspect.getsource(metadata.main)

    assert 'row["discriminant"] in (None, "")' in core
    assert 'row["bad_primes_json"] in (None, "")' in core
    assert 'row["root_number"] is None' in core
    assert "compute_curve_size_metrics(Em)" in complete
    assert "if args.core_only:" in cli
    assert "RANK42_CURVE_CORE_RESULT=" in cli



def test_conductor_backfill_respects_explicit_curve_scope(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    first = upsert_curve(
        db,
        family="Conductor scope",
        parameter="1",
        a_invariants_json=json.dumps([0, 0, 0, -1, 0]),
        discriminant="64",
        bad_primes_json=json.dumps([2]),
        descent_lower=1,
        status="proven_lower",
    )
    second = upsert_curve(
        db,
        family="Conductor scope",
        parameter="2",
        a_invariants_json=json.dumps([0, 0, 0, -1, 0]),
        discriminant="64",
        bad_primes_json=json.dumps([2]),
        descent_lower=1,
        status="proven_lower",
    )

    monkeypatch.setattr(
        conductor_backfill,
        "_run_worker",
        lambda *args, **kwargs: (
            {"conductor": "32", "bad_primes": ["2"], "method": "stored_bad_primes"},
            None,
            False,
        ),
    )
    result = conductor_backfill.backfill_conductors(
        db,
        timeout_seconds=1,
        curve_ids=[first],
    )

    assert result["selected"] == 1
    assert get_curve(db, first)["conductor"] == "32"
    assert get_curve(db, second)["conductor"] is None
    db.close()


def test_size_metric_backfill_respects_explicit_curve_scope(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    first = upsert_curve(
        db,
        family="Size scope",
        parameter="1",
        a_invariants_json=json.dumps([0, 0, 0, -1, 0]),
        descent_lower=1,
        status="proven_lower",
    )
    second = upsert_curve(
        db,
        family="Size scope",
        parameter="2",
        a_invariants_json=json.dumps([0, 0, 0, -1, 0]),
        descent_lower=1,
        status="proven_lower",
    )

    monkeypatch.setattr(
        size_metrics,
        "_run_curve_worker",
        lambda *args, **kwargs: (
            {
                "metrics": {
                    "naive_height": "3.0",
                    "faltings_height": "-0.5",
                    "precision_bits": 128,
                    "method": "test",
                },
                "discriminant": "64",
                "model_source": "test",
                "conductor": None,
            },
            None,
            False,
        ),
    )
    result = size_metrics.backfill_curve_size_metrics(
        db,
        fill_conductor=False,
        curve_timeout=1,
        curve_ids=[first],
    )

    assert result["selected"] == 1
    stored = db.execute(
        "SELECT curve_id,naive_height,faltings_height FROM curve_size_metrics ORDER BY curve_id"
    ).fetchall()
    assert [int(row["curve_id"]) for row in stored] == [first]
    assert get_curve(db, first)["discriminant"] == "64"
    assert get_curve(db, second)["discriminant"] is None
    db.close()
