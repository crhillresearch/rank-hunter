import json
from pathlib import Path

from rank42.db import connect, upsert_curve
from rank42.database_verification import persist_full_database_verification
from rank42.rank_evidence import record_rank_evidence
from rank42.diagnostics import collect_diagnostics, format_diagnostic_report
from rank42.manage_store import ensure_manage_schema
from rank42.ui_store import ensure_ui_schema


def _write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def _demo_plugin(root):
    plugin = root / "plugins" / "demo"
    plugin.mkdir(parents=True)
    _write(plugin / "family.json", json.dumps({
        "name": "Demo",
        "a_invariants": ["0", "0", "0", "0", "1"],
    }))
    _write(plugin / "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "family": {"kind": "json", "file": "family.json"},
        "corpora": [{
            "id": "history",
            "name": "Demo History",
            "cache_file": "curves2.db",
        }],
    }))


def _open(tmp_path):
    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    ensure_ui_schema(db)
    ensure_manage_schema(db)
    return db_path, db


def _check(result, check_id):
    return next(row for row in result["checks"] if row["id"] == check_id)



def _rank_evidence(db, curve_id, *, lower=None, upper=None, key=None):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "diagnostics-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def _rigorous_witness(db, curve_id, index, *, exact=True, rigorous=True, status="rigorous_independent", role="rigorous_witness"):
    ts = "2026-01-01T00:00:00+00:00"
    db.execute(
        """
        INSERT INTO points(
          curve_id,x,y,source,role,exact_verified,independence_status,
          rigorous_independent,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(curve_id), str(index), str(index + 1), "diagnostics-test", str(role),
            1 if exact else 0, str(status), 1 if rigorous else 0, "{}", ts, ts,
        ),
    )
    db.commit()

def test_diagnostics_reports_clean_evidence_and_missing_corpus_as_warning(tmp_path):
    _demo_plugin(tmp_path)
    db_path, db = _open(tmp_path)
    try:
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    assert result["overall"] != "ATTENTION REQUIRED"
    assert _check(result, "evidence.curve_bounds")["status"] == "pass"
    assert _check(result, "evidence.points")["status"] == "pass"
    assert _check(result, "plugins.registry")["status"] == "pass"
    assert _check(result, "corpora.registry")["status"] == "warn"
    assert result["corpora"][0]["exists"] is False


def test_full_diagnostics_collection_is_read_only_under_query_only(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(
            db,
            family="diagnostics-read-only",
            parameter="1",
        )
        _rank_evidence(db, curve_id, lower=1)
        _rigorous_witness(db, curve_id, 0)

        result_dir = tmp_path / ".rank42-ui" / "diagnostics"
        assert not result_dir.exists()

        before_changes = db.total_changes
        db.execute("PRAGMA query_only=ON")

        fast = collect_diagnostics(tmp_path, db_path, db, deep=False)
        deep = collect_diagnostics(tmp_path, db_path, db, deep=True)

        assert db.total_changes == before_changes
        assert fast["verification_mode"] == "fast"
        assert deep["verification_mode"] == "deep"
        assert not result_dir.exists()
    finally:
        db.close()


def test_diagnostics_fails_on_exact_rank_below_rigorous_lower(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="1")
        db.execute(
            "UPDATE curves SET exact_rank=2, descent_lower=3, status='exact' WHERE id=?",
            (curve_id,),
        )
        db.commit()
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    check = _check(result, "evidence.curve_bounds")
    assert check["status"] == "fail"
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_fails_on_nonexact_rigorous_point(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="2")
        ts = "2026-01-01T00:00:00+00:00"
        db.execute(
            """
            INSERT INTO points(
              curve_id,x,y,source,role,exact_verified,independence_status,
              rigorous_independent,metadata_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                curve_id, "0", "1", "test", "rigorous_witness",
                0, "rigorous_independent", 1, "{}", ts, ts,
            ),
        )
        db.commit()
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    assert _check(result, "evidence.points")["status"] == "fail"


def test_diagnostics_fails_when_reduced_lower_exceeds_durable_witness_basis(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="witness-shortage")
        _rank_evidence(db, curve_id, lower=3)
        _rigorous_witness(db, curve_id, 0)
        _rigorous_witness(db, curve_id, 1)
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    check = _check(result, "evidence.witness_basis")
    assert check["status"] == "fail"
    assert result["evidence"]["witness_basis_shortages"] == 1
    assert result["evidence"]["witness_basis_missing_points"] == 1
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_passes_witness_basis_when_reduced_lower_is_replayable(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="witness-complete")
        _rank_evidence(db, curve_id, lower=2)
        _rigorous_witness(db, curve_id, 0)
        _rigorous_witness(db, curve_id, 1)
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    assert _check(result, "evidence.witness_basis")["status"] == "pass"
    assert result["evidence"]["witness_basis_shortages"] == 0
    assert result["evidence"]["witness_basis_missing_points"] == 0


def test_diagnostics_rejects_inconsistent_rigorous_witness_flags(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="witness-flags")
        _rigorous_witness(
            db,
            curve_id,
            0,
            exact=True,
            rigorous=True,
            status="unknown",
        )
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    assert _check(result, "evidence.points")["status"] == "fail"
    assert result["evidence"]["invalid_rigorous_points"] == 1


def test_diagnostic_report_redacts_home_path():
    fake_home = Path.home()
    result = {
        "generated_at": "now",
        "overall": "READY",
        "deep": False,
        "core": {
            "version": "test",
            "project_root": str(fake_home / "rank-hunter"),
            "git": {"available": False},
        },
        "checks": [{
            "status": "pass",
            "area": "Core",
            "label": "Path",
            "value": str(fake_home / "rank-hunter"),
            "detail": "",
        }],
        "plugins": [],
        "corpora": [],
    }
    report = format_diagnostic_report(result, redact_home=True)
    assert str(fake_home) not in report
    assert "~" in report
    assert "rank-hunter" in report


def test_diagnostics_breaks_down_foreign_key_violations(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO events(curve_id,level,message,created_at) VALUES(?,?,?,?)",
            (999999, "info", "orphan fixture", "2026-01-01T00:00:00+00:00"),
        )
        db.commit()
        db.execute("PRAGMA foreign_keys=ON")
        result = collect_diagnostics(tmp_path, db_path, db, deep=True)
    finally:
        db.close()

    check = _check(result, "db.foreign_key_check")
    assert check["status"] == "fail"
    assert result["database"]["foreign_key_violations"] == 1
    assert result["database"]["foreign_key_breakdown"]["events -> curves"] == 1


def test_diagnostics_page_uses_global_strip_only_live_refresh():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "rank42" / "ui.py"
    ).read_text(encoding="utf-8")

    assert "def _live_job_strip" in source
    assert "def _live_main_content" not in source
    assert "_page_supports_live_refresh" not in source



def test_fast_diagnostics_skip_full_database_scans(tmp_path):
    db_path, db = _open(tmp_path)
    statements = []
    db.set_trace_callback(statements.append)
    try:
        result = collect_diagnostics(tmp_path, db_path, db, deep=False)
    finally:
        db.set_trace_callback(None)
        db.close()

    sql = "\n".join(statements).lower()
    assert "pragma integrity_check" not in sql
    assert "pragma foreign_key_check" not in sql
    assert result["database"]["deep_checks_run"] is False
    assert result["database"]["integrity"] is None
    assert result["database"]["foreign_key_violations"] is None
    assert result["overall"] == "FAST CHECKS READY"
    assert result["verification_mode"] == "fast"
    assert result["deep_checks_complete"] is False
    assert not any(
        row["id"] in {"db.integrity", "db.foreign_key_check"}
        for row in result["checks"]
    )


def test_deep_database_scan_timeout_is_inconclusive_not_failure(tmp_path, monkeypatch):
    from rank42 import diagnostics

    db_path, db = _open(tmp_path)
    original = diagnostics._pragma_rows_with_budget

    def bounded(db_, sql, *, timeout_seconds=8):
        if "integrity_check" in sql:
            return {
                "completed": False,
                "rows": [],
                "error": "timed out after 8s",
            }
        return original(db_, sql, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(diagnostics, "_pragma_rows_with_budget", bounded)
    try:
        result = diagnostics.collect_diagnostics(
            tmp_path, db_path, db, deep=True
        )
    finally:
        db.close()

    check = _check(result, "db.integrity")
    assert check["status"] == "warn"
    assert check["value"] == "inconclusive"
    assert check["detail"] == "timed out after 8s"
    assert result["overall"] == "DEEP CHECKS INCONCLUSIVE"
    assert result["verification_mode"] == "deep"
    assert result["deep_checks_complete"] is False

def test_diagnostics_detects_evidence_only_reduced_rank_conflict(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="evidence-conflict")
        # Each row is individually valid; the contradiction appears only after
        # reducing the ledger across multiple rigorous evidence records.
        _rank_evidence(db, curve_id, lower=7, key="diagnostics-conflict-lower")
        _rank_evidence(db, curve_id, upper=6, key="diagnostics-conflict-upper")
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    check = _check(result, "evidence.curve_bounds")
    assert check["status"] == "fail"
    assert result["evidence"]["reduced_rank_conflicts"] == 1
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_reports_compatibility_projection_drift(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        curve_id = upsert_curve(db, family="test", parameter="evidence-drift")
        _rank_evidence(db, curve_id, lower=5)
        for index in range(5):
            _rigorous_witness(db, curve_id, index)
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    bounds = _check(result, "evidence.curve_bounds")
    projection = _check(result, "evidence.rank_projection")
    witnesses = _check(result, "evidence.witness_basis")
    assert bounds["status"] == "pass"
    assert projection["status"] == "warn"
    assert witnesses["status"] == "pass"
    assert result["evidence"]["rank_projection_mismatches"] == 1
    assert result["overall"] != "ATTENTION REQUIRED"




def test_diagnostics_reports_core_ui_and_manage_schema_readiness(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    assert _check(result, "db.schema.core")["status"] == "pass"
    assert _check(result, "db.schema.ui")["status"] == "pass"
    assert _check(result, "db.schema.manage")["status"] == "pass"
    assert _check(result, "db.schema")["status"] == "pass"
    assert result["database"]["schema"]["core"]["ready"] is True
    assert result["database"]["schema"]["ui"]["ready"] is True
    assert result["database"]["schema"]["manage"]["ready"] is True


def test_diagnostics_fails_exact_core_schema_version_mismatch(tmp_path):
    from rank42.db import CURRENT_SCHEMA_VERSION

    db_path, db = _open(tmp_path)
    db.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
    try:
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    core = _check(result, "db.schema.core")
    compat = _check(result, "db.schema")
    assert core["status"] == "fail"
    assert compat["status"] == "fail"
    assert "expected" in core["value"]
    assert result["overall"] == "ATTENTION REQUIRED"



def test_diagnostics_runtime_setting_read_uses_typed_fallback_without_repair(tmp_path):
    from rank42 import diagnostics

    db_path, db = _open(tmp_path)
    try:
        db.execute(
            """INSERT INTO ui_settings(key,value_json,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
            ("science_python", json.dumps(123), "now"),
        )
        db.commit()

        assert diagnostics._setting(
            db,
            "science_python",
            "/fallback/python",
        ) == "/fallback/python"

        stored = db.execute(
            "SELECT value_json FROM ui_settings WHERE key='science_python'"
        ).fetchone()
        assert json.loads(stored["value_json"]) == 123
    finally:
        db.close()



def test_diagnostics_uses_shared_runtime_health_policy(tmp_path):
    from rank42 import diagnostics
    from rank42.ui_store import set_setting

    db_path, db = _open(tmp_path)
    try:
        set_setting(db, "ratpoints", str(tmp_path / "missing-cpu-ratpoints"))
        set_setting(
            db,
            "ratpoints_gpu",
            str(tmp_path / "missing-gpu-ratpoints"),
        )
        result = diagnostics.collect_diagnostics(
            tmp_path,
            db_path,
            db,
            deep=False,
        )
    finally:
        db.close()

    cpu = _check(result, "runtime.ratpoints")
    gpu = _check(result, "runtime.ratpoints_gpu")
    assert cpu["status"] == "fail"
    assert "does not resolve to an executable" in cpu["detail"]
    assert gpu["status"] == "warn"
    assert "optional runtime" in gpu["detail"]
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_runtime_checks_import_shared_probe_contract():
    source = (
        Path(__file__).resolve().parents[1] / "rank42" / "diagnostics.py"
    ).read_text(encoding="utf-8")

    assert "from rank42.runtime_validation import probe_runtime_setting" in source
    assert "probe_runtime_setting(" in source
    assert "def _configured_executable" not in source



def test_diagnostics_reports_covering_hook_capability(tmp_path):
    plugin = tmp_path / "plugins" / "covering_diag"
    plugin.mkdir(parents=True)
    _write(plugin / "family.json", json.dumps({
        "name": "Covering Diagnostics",
        "a_invariants": ["0", "0", "0", "0", "1"],
    }))
    _write(
        plugin / "adapter.py",
        "def derive_pipeline_coverings(payload):\n"
        "    return []\n",
    )
    _write(plugin / "plugin.json", json.dumps({
        "schema_version": 1,
        "id": "covering_diag",
        "name": "Covering Diagnostics",
        "version": "1.0.0",
        "family": {"kind": "json", "file": "family.json"},
        "search_adapter": "adapter.py",
    }))

    db_path, db = _open(tmp_path)
    try:
        result = collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    rec = next(x for x in result["plugins"] if x["id"] == "covering_diag")
    assert rec["research_hooks"]["derive_pipeline_coverings"] is True
    assert rec["adapter_error"] is None
    report = format_diagnostic_report(result)
    assert "covering_diag" in report
    assert "covering_hook=yes" in report


def test_deep_diagnostics_reports_completed_release_state(tmp_path):
    db_path, db = _open(tmp_path)
    try:
        result = collect_diagnostics(tmp_path, db_path, db, deep=True)
    finally:
        db.close()

    assert result["database"]["deep_checks_run"] is True
    assert result["database"]["integrity"] == "ok"
    assert result["database"]["foreign_key_violations"] == 0
    assert result["overall"] == "DEEP CHECKS READY"
    assert result["verification_mode"] == "deep"
    assert result["deep_checks_complete"] is True


def test_diagnostic_report_names_verification_mode_and_deep_completeness(
    tmp_path,
):
    db_path, db = _open(tmp_path)
    try:
        result = collect_diagnostics(tmp_path, db_path, db, deep=False)
    finally:
        db.close()

    report = format_diagnostic_report(result)
    assert "overall=FAST CHECKS READY" in report
    assert "verification_mode=fast" in report
    assert "deep_checks_complete=False" in report


def test_diagnostics_page_uses_explicit_fast_deep_release_states():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "diagnostics_page.py"
    ).read_text(encoding="utf-8")

    assert 'overall == "DEEP CHECKS INCONCLUSIVE"' in source
    assert 'overall == "FAST CHECKS READY"' in source
    assert "inconclusive, not a failed integrity result" in source
    assert "Run deep diagnostics for bounded full database" in source


def test_diagnostics_reports_persisted_full_database_verification(tmp_path):
    db_path, db = _open(tmp_path)
    persist_full_database_verification(
        db_path,
        {
            "status": "completed",
            "healthy": True,
            "job_id": 44,
            "integrity_ok": True,
            "foreign_key_violations": 0,
            "finished_at": "2026-09-26T23:30:00+00:00",
        },
    )
    try:
        result = collect_diagnostics(tmp_path, db_path, db, deep=False)
    finally:
        db.close()

    check = _check(result, "db.full_verification")
    assert check["status"] == "pass"
    assert check["value"] == "healthy"
    assert "job #44" in check["detail"]
    assert result["database"]["full_verification"]["healthy"] is True
    assert result["overall"] == "FAST CHECKS READY"


def test_diagnostics_fails_on_unhealthy_persisted_full_verification(tmp_path):
    db_path, db = _open(tmp_path)
    persist_full_database_verification(
        db_path,
        {
            "status": "completed",
            "healthy": False,
            "job_id": 45,
            "integrity_ok": True,
            "foreign_key_violations": 2,
            "finished_at": "2026-09-26T23:31:00+00:00",
        },
    )
    try:
        result = collect_diagnostics(tmp_path, db_path, db, deep=False)
    finally:
        db.close()

    check = _check(result, "db.full_verification")
    assert check["status"] == "fail"
    assert check["value"] == "issues found"
    assert "2 foreign-key violation(s)" in check["detail"]
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_remains_observer_for_full_database_verification():
    diagnostics_source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "diagnostics.py"
    ).read_text(encoding="utf-8")
    page_source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "diagnostics_page.py"
    ).read_text(encoding="utf-8")

    assert "load_full_database_verification" in diagnostics_source
    assert "enqueue_full_database_verification" not in diagnostics_source
    assert "enqueue_full_database_verification" not in page_source



def test_diagnostics_reports_healthy_queue_dispatcher_scheduler_snapshot(
    tmp_path,
    monkeypatch,
):
    from rank42 import diagnostics

    db_path, db = _open(tmp_path)
    monkeypatch.setattr(
        diagnostics,
        "dispatcher_status",
        lambda db_: {
            "healthy": True,
            "status": "running",
            "pid": 4242,
            "worker_id": "dispatcher-test",
            "updated_at": "2026-09-26T23:40:00+00:00",
            "max_workers": 2,
            "resource_limits": {
                "gpu_ratpoints": 1,
                "sage_heavy": 2,
                "database_maintenance": 1,
            },
        },
    )
    try:
        result = diagnostics.collect_diagnostics(
            tmp_path,
            db_path,
            db,
            deep=False,
        )
    finally:
        db.close()

    assert _check(result, "ops.dispatcher")["status"] == "pass"
    assert _check(result, "ops.queue_claims")["status"] == "pass"
    assert _check(result, "ops.scheduler_schema")["status"] == "pass"
    assert _check(result, "ops.scheduler_occurrences")["status"] == "pass"
    assert _check(result, "ops.queue_resources")["status"] == "pass"
    assert result["operations"]["queue"]["expired_claims"] == []
    assert result["operations"]["scheduler"]["failed_occurrences"] == 0
    assert result["overall"] == "FAST CHECKS READY"


def test_diagnostics_fails_when_work_is_queued_without_healthy_dispatcher(
    tmp_path,
    monkeypatch,
):
    from rank42 import diagnostics, ui_store

    db_path, db = _open(tmp_path)
    job_id = ui_store.create_job(
        db,
        kind="test",
        label="queued work",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        log_path=tmp_path / "queued.log",
    )
    ui_store.create_queue_item(db, job_id=job_id)
    monkeypatch.setattr(
        diagnostics,
        "dispatcher_status",
        lambda db_: {
            "healthy": False,
            "status": "stale",
            "pid": 999999,
            "updated_at": "2026-09-26T20:00:00+00:00",
        },
    )
    try:
        result = diagnostics.collect_diagnostics(tmp_path, db_path, db)
    finally:
        db.close()

    check = _check(result, "ops.dispatcher")
    assert check["status"] == "fail"
    assert "1 queued" in check["value"]
    assert result["operations"]["queue"]["counts"]["queued"] == 1
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_fails_on_expired_queue_claim_without_reconciling_it(
    tmp_path,
    monkeypatch,
):
    from rank42 import diagnostics, ui_store

    db_path, db = _open(tmp_path)
    job_id = ui_store.create_job(
        db,
        kind="test",
        label="expired claim",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        log_path=tmp_path / "expired.log",
    )
    ui_store.create_queue_item(db, job_id=job_id)
    claimed = ui_store.claim_next_queue_item(db, "dispatcher-test")
    assert int(claimed["job_id"]) == job_id
    db.execute(
        """UPDATE ui_job_queue
           SET lease_expires_at=?
           WHERE job_id=?""",
        ("2026-01-01T00:00:00+00:00", job_id),
    )
    db.commit()
    monkeypatch.setattr(
        diagnostics,
        "dispatcher_status",
        lambda db_: {
            "healthy": True,
            "status": "running",
            "pid": 4242,
            "updated_at": "2026-09-26T23:40:00+00:00",
        },
    )

    before_changes = db.total_changes
    result = diagnostics.collect_diagnostics(tmp_path, db_path, db)
    after = ui_store.queue_item_for_job(db, job_id)
    after_changes = db.total_changes
    db.close()

    check = _check(result, "ops.queue_claims")
    assert check["status"] == "fail"
    assert "1 expired" in check["value"]
    assert result["operations"]["queue"]["expired_claims"][0]["job_id"] == job_id
    assert after["state"] == "claimed"
    assert after["lease_expires_at"] == "2026-01-01T00:00:00+00:00"
    assert after_changes == before_changes
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_fails_on_failed_or_stuck_schedule_occurrence(
    tmp_path,
    monkeypatch,
):
    from datetime import datetime, timedelta, timezone

    from rank42 import diagnostics
    from rank42.manage_store import create_schedule

    db_path, db = _open(tmp_path)
    schedule_id = create_schedule(
        db,
        label="diagnostics schedule",
        kind="test",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        next_run_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    db.execute(
        """INSERT INTO ui_job_schedule_occurrences(
               schedule_id,scheduled_for,state,job_id,snapshot_json,last_error,
               created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            schedule_id,
            old,
            "pending",
            None,
            "{}",
            "injected scheduler failure",
            old,
            old,
        ),
    )
    db.commit()
    monkeypatch.setattr(
        diagnostics,
        "dispatcher_status",
        lambda db_: {
            "healthy": True,
            "status": "running",
            "pid": 4242,
            "updated_at": "2026-09-26T23:40:00+00:00",
        },
    )
    before_changes = db.total_changes
    result = diagnostics.collect_diagnostics(tmp_path, db_path, db)
    after_changes = db.total_changes
    row = db.execute(
        """SELECT state,last_error FROM ui_job_schedule_occurrences
           WHERE schedule_id=?""",
        (schedule_id,),
    ).fetchone()
    db.close()

    check = _check(result, "ops.scheduler_occurrences")
    assert check["status"] == "fail"
    assert result["operations"]["scheduler"]["stale_pending_occurrences"] == 1
    assert result["operations"]["scheduler"]["occurrence_errors"] == 1
    assert row["state"] == "pending"
    assert row["last_error"] == "injected scheduler failure"
    assert after_changes == before_changes
    assert result["overall"] == "ATTENTION REQUIRED"


def test_diagnostics_fails_on_invalid_persisted_queue_resource_limits(
    tmp_path,
    monkeypatch,
):
    from rank42 import diagnostics
    from rank42.ui_store import set_setting

    db_path, db = _open(tmp_path)
    set_setting(
        db,
        "queue_resource_limits",
        {"gpu_ratpoints": 0, "sage_heavy": 2},
    )
    monkeypatch.setattr(
        diagnostics,
        "dispatcher_status",
        lambda db_: {
            "healthy": True,
            "status": "running",
            "pid": 4242,
            "updated_at": "2026-09-26T23:40:00+00:00",
        },
    )
    try:
        result = diagnostics.collect_diagnostics(tmp_path, db_path, db)
        stored = db.execute(
            "SELECT value_json FROM ui_settings WHERE key='queue_resource_limits'"
        ).fetchone()
    finally:
        db.close()

    check = _check(result, "ops.queue_resources")
    assert check["status"] == "fail"
    assert "positive integers" in check["detail"]
    assert json.loads(stored["value_json"])["gpu_ratpoints"] == 0
    assert result["operations"]["resource_limits"]["resource_limits_valid"] is False
    assert result["overall"] == "ATTENTION REQUIRED"


def test_degraded_operational_health_is_detected_without_repair_under_query_only(
    tmp_path,
):
    from datetime import datetime, timedelta, timezone

    from rank42 import ui_store
    from rank42.manage_store import create_schedule

    db_path, db = _open(tmp_path)

    claimed_job = ui_store.create_job(
        db,
        kind="test",
        label="expired claim",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        log_path=tmp_path / "expired.log",
    )
    ui_store.create_queue_item(db, job_id=claimed_job)
    claimed = ui_store.claim_next_queue_item(db, "dispatcher-test")
    assert int(claimed["job_id"]) == claimed_job
    expired_at = "2026-01-01T00:00:00+00:00"
    db.execute(
        """UPDATE ui_job_queue
           SET lease_expires_at=?
           WHERE job_id=?""",
        (expired_at, claimed_job),
    )

    queued_job = ui_store.create_job(
        db,
        kind="test",
        label="queued without dispatcher",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        log_path=tmp_path / "queued.log",
    )
    ui_store.create_queue_item(db, job_id=queued_job)

    schedule_id = create_schedule(
        db,
        label="stuck diagnostics schedule",
        kind="test",
        command=["python", "-c", "pass"],
        cwd=tmp_path,
        next_run_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    )
    stale_for = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    occurrence_error = "injected stuck schedule"
    db.execute(
        """INSERT INTO ui_job_schedule_occurrences(
               schedule_id,scheduled_for,state,job_id,snapshot_json,last_error,
               created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            schedule_id,
            stale_for,
            "pending",
            None,
            "{}",
            occurrence_error,
            stale_for,
            stale_for,
        ),
    )

    invalid_limits = {"gpu_ratpoints": 0, "sage_heavy": 2}
    ui_store.set_setting(db, "queue_resource_limits", invalid_limits)
    db.commit()

    before_changes = db.total_changes
    db.execute("PRAGMA query_only=ON")

    try:
        result = collect_diagnostics(tmp_path, db_path, db, deep=False)

        claimed_after = ui_store.queue_item_for_job(db, claimed_job)
        queued_after = ui_store.queue_item_for_job(db, queued_job)
        occurrence_after = db.execute(
            """SELECT state,last_error
               FROM ui_job_schedule_occurrences
               WHERE schedule_id=?""",
            (schedule_id,),
        ).fetchone()
        stored_limits = db.execute(
            """SELECT value_json
               FROM ui_settings
               WHERE key='queue_resource_limits'"""
        ).fetchone()

        assert _check(result, "ops.queue_claims")["status"] == "fail"
        assert _check(result, "ops.dispatcher")["status"] == "fail"
        assert _check(result, "ops.scheduler_occurrences")["status"] == "fail"
        assert _check(result, "ops.queue_resources")["status"] == "fail"
        assert result["overall"] == "ATTENTION REQUIRED"

        assert claimed_after["state"] == "claimed"
        assert claimed_after["lease_expires_at"] == expired_at
        assert queued_after["state"] == "queued"
        assert occurrence_after["state"] == "pending"
        assert occurrence_after["last_error"] == occurrence_error
        assert json.loads(stored_limits["value_json"]) == invalid_limits
        assert db.total_changes == before_changes
    finally:
        db.close()


def test_diagnostics_operational_health_is_read_only_by_contract():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "diagnostics.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _operational_checks")
    end = source.index("def _storage_checks", start)
    operational = source[start:end]

    assert "reconcile_queue(" not in operational
    assert "recover_expired_queue_claims(" not in operational
    assert "dispatch_due_schedules(" not in operational
    assert "save_setting_value(" not in operational
    assert "UPDATE " not in operational
    assert "DELETE " not in operational
    assert "INSERT " not in operational



def test_diagnostics_snapshot_freshness_classifies_fast_deep_and_stale():
    from datetime import datetime, timedelta, timezone

    from rank42.ui_pages.diagnostics_page import _snapshot_freshness

    now = datetime(2026, 9, 26, 20, 0, 0, tzinfo=timezone.utc)
    fresh_fast = _snapshot_freshness(
        {
            "generated_at": (
                now - timedelta(seconds=30)
            ).isoformat(),
            "verification_mode": "fast",
            "deep": False,
        },
        now=now,
    )
    assert fresh_fast["mode"] == "fast"
    assert fresh_fast["freshness"] == "fresh"
    assert fresh_fast["stale"] is False
    assert fresh_fast["age_label"] == "30s old"

    stale_deep = _snapshot_freshness(
        {
            "generated_at": (
                now - timedelta(minutes=6)
            ).isoformat(),
            "verification_mode": "deep",
            "deep": True,
        },
        now=now,
    )
    assert stale_deep["mode"] == "deep"
    assert stale_deep["freshness"] == "stale"
    assert stale_deep["stale"] is True
    assert stale_deep["age_label"] == "6m old"


def test_diagnostics_snapshot_invalid_timestamp_is_explicitly_stale():
    from rank42.ui_pages.diagnostics_page import _snapshot_freshness

    snapshot = _snapshot_freshness({
        "generated_at": "not-a-timestamp",
        "verification_mode": "fast",
    })
    assert snapshot["freshness"] == "unknown"
    assert snapshot["stale"] is True
    assert snapshot["age_seconds"] is None
    assert snapshot["age_label"] == "unknown age"


def test_diagnostics_cache_replaces_only_after_successful_collection(
    monkeypatch,
):
    from types import SimpleNamespace

    import rank42.ui_pages.diagnostics_page as diagnostics_page

    diagnostics_page.st.session_state.clear()
    old = {
        "generated_at": "2026-09-26T20:00:00+00:00",
        "verification_mode": "fast",
        "deep": False,
        "counts": {"pass": 1, "warn": 0, "fail": 0},
        "overall": "FAST CHECKS READY",
    }
    diagnostics_page.st.session_state["diagnostics_result_cache"] = old
    new = {
        "generated_at": "2026-09-26T20:01:00+00:00",
        "verification_mode": "deep",
        "deep": True,
        "counts": {"pass": 2, "warn": 0, "fail": 0},
        "overall": "DEEP CHECKS READY",
    }
    monkeypatch.setattr(
        diagnostics_page,
        "collect_diagnostics",
        lambda *args, **kwargs: new,
    )
    ctx = SimpleNamespace(project_root="/tmp", db_path="/tmp/rank42.db")

    result = diagnostics_page._diagnostic_result(
        object(),
        ctx,
        deep=True,
        force=True,
    )

    assert result is new
    assert (
        diagnostics_page.st.session_state["diagnostics_result_cache"]
        is new
    )


def test_diagnostics_cache_preserves_previous_snapshot_on_collection_error(
    monkeypatch,
):
    from types import SimpleNamespace

    import pytest
    import rank42.ui_pages.diagnostics_page as diagnostics_page

    diagnostics_page.st.session_state.clear()
    old = {
        "generated_at": "2026-09-26T20:00:00+00:00",
        "verification_mode": "fast",
        "deep": False,
        "counts": {"pass": 1, "warn": 0, "fail": 0},
        "overall": "FAST CHECKS READY",
    }
    diagnostics_page.st.session_state["diagnostics_result_cache"] = old

    def fail(*args, **kwargs):
        raise RuntimeError("injected diagnostics failure")

    monkeypatch.setattr(diagnostics_page, "collect_diagnostics", fail)
    ctx = SimpleNamespace(project_root="/tmp", db_path="/tmp/rank42.db")

    with pytest.raises(RuntimeError, match="injected diagnostics failure"):
        diagnostics_page._diagnostic_result(
            object(),
            ctx,
            deep=True,
            force=True,
        )

    assert (
        diagnostics_page.st.session_state["diagnostics_result_cache"]
        is old
    )


def test_diagnostics_page_prominently_renders_snapshot_mode_time_and_age():
    source = (
        Path(__file__).resolve().parents[1]
        / "rank42"
        / "ui_pages"
        / "diagnostics_page.py"
    ).read_text(encoding="utf-8")

    assert "def _render_snapshot_banner" in source
    assert "**{mode_label} snapshot**" in source
    assert "generated_at" in source
    assert "age_label" in source
    assert "**STALE**" in source
    assert "**Fresh**" in source
    assert 'force=bool(deep or refresh)' in source
    assert 'st.session_state.pop("diagnostics_result_cache"' not in source
    assert "st.rerun()" not in source[
        source.index("def page"):source.index("def page") + 5000
    ]
