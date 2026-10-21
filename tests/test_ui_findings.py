import json
import hashlib
import sqlite3

from rank42.ui_findings import hunt_findings, recorded_native_x, reconstructed_job_native_x, reconstructed_new_native_x
from rank42.ui_results import summarize_job


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE candidates(id INTEGER PRIMARY KEY, parameter TEXT, curve_id INTEGER);
        CREATE TABLE curves(id INTEGER PRIMARY KEY, created_at TEXT, exact_rank INTEGER,
                            descent_lower INTEGER, generic_lower INTEGER);
        CREATE TABLE quartic_searches(id INTEGER PRIMARY KEY, curve_id INTEGER);
        CREATE TABLE quartic_points(search_id INTEGER, created_at TEXT, metadata_json TEXT);
    """)
    return db


def _job():
    return {
        "id": 225, "kind": "family_search", "label": "overnight", "status": "succeeded",
        "exit_code": 0, "metadata_json": json.dumps({"candidate_ids": [1, 2]}),
        "created_at": "2026-09-22T23:00:00+00:00",
        "started_at": "2026-09-23T00:00:00+00:00",
        "finished_at": "2026-09-23T01:00:00+00:00",
    }


def test_hunt_recap_keeps_fibers_curves_and_proof_separate():
    db = _db()
    db.executemany("INSERT INTO candidates VALUES (?,?,?)", [(1, "5/2", 10), (2, "7/3", 11)])
    db.executemany("INSERT INTO curves VALUES (?,?,?,?,?)", [
        (10, "2026-09-22T12:00:00+00:00", None, 12, None),
        (11, "2026-09-23T00:30:00+00:00", None, None, None),
    ])
    log = """[1/2] curve #10 t=5/2 score=9.0
    NEW NATIVE QUARTIC HIT(S): 3 new x-fibre(s)
    mapped unique points outside listed basis = 6
    [mestre hit] curve #10 rigorous_lower=12 points=12
[candidate done] 1/2
[2/2] curve #11 t=7/3 score=8.0
    NEW NATIVE QUARTIC HIT(S): 2 new x-fibre(s)
[candidate done] 2/2
RANK42_MESTRE_SEARCH_RESULT={"new_native_fibers":5,"rank_growth_curves":1}
"""
    findings = hunt_findings(db, _job(), log)
    assert len(findings) == 2
    assert [f["native_hits"] for f in findings] == [3, 2]
    assert [f["new_curve"] for f in findings] == [False, True]
    assert [f["rigorous_lower_now"] for f in findings] == [12, None]
    assert [f["rank_growth_signal"] for f in findings] == [True, False]
    assert [f["completed"] for f in findings] == [True, True]
    summary = summarize_job(_job(), log)
    assert summary["rank_growth_curves"] == 1
    assert summary["result_status"] == "RANK GROWTH"


def test_native_coordinates_are_deduplicated_and_limited_to_job_time():
    db = _db()
    db.executemany("INSERT INTO quartic_searches VALUES (?,?)", [(1, 10), (2, 20)])
    def point(search_id, when, x, verified=True):
        return search_id, when, json.dumps({"native_x": x, "inverse_map_verified_exactly": verified})
    db.executemany("INSERT INTO quartic_points VALUES (?,?,?)", [
        point(1, "2026-09-22T23:59:59+00:00", "old"),
        point(1, "2026-09-23T00:01:00+00:00", "1/2"),
        point(1, "2026-09-23T00:02:00+00:00", "1/2"),
        point(1, "2026-09-23T00:03:00+00:00", "3/2", False),
        point(2, "2026-09-23T00:04:00+00:00", "other curve"),
        point(1, "2026-09-23T01:01:00+00:00", "later"),
    ])
    assert recorded_native_x(db, _job(), 10) == ["1/2"]


def test_missing_candidate_metadata_still_uses_job_log_not_other_candidates():
    db = _db()
    db.execute("INSERT INTO curves VALUES (?,?,?,?,?)", (5, "2026-09-22T00:00:00+00:00", None, None, None))
    job = {**_job(), "metadata_json": "{}"}
    findings = hunt_findings(db, job, "[1/1] curve #5 t=9 score=1\n[candidate done] 1/1\n")
    assert len(findings) == 1
    assert findings[0]["candidate_id"] is None
    assert findings[0]["curve_id"] == 5


def test_reconstructs_only_with_matching_family_source_and_log_count(tmp_path):
    family = tmp_path / "family.py"
    family.write_text("def native_quartic_known_x(t):\n    return ['0']\n")
    adapter = tmp_path / "adapter.py"
    fingerprint = hashlib.sha256(family.read_bytes()).hexdigest()
    job = {**_job(), "metadata_json": json.dumps({
        "adapter_file": str(adapter), "installed_family_sha256": fingerprint,
    })}
    db = _db()
    db.execute("INSERT INTO quartic_searches VALUES (?,?)", (1, 10))
    db.executemany("INSERT INTO quartic_points VALUES (?,?,?)", [
        (1, "2026-09-23T00:10:00+00:00", json.dumps({"native_x": x, "inverse_map_verified_exactly": True}))
        for x in ("0", "3/2", "3/2")
    ])
    assert reconstructed_new_native_x(db, job, 10, "5/2", 1) == (["3/2"], ["0", "3/2"])
    rows, verified, total = reconstructed_job_native_x(db, job, [{
        "position": 1, "curve_id": 10, "parameter": "5/2", "native_hits": 1,
    }])
    assert (rows, verified, total) == ([{
        "candidate #": 1, "parameter": "5/2", "curve": 10, "new native x": "3/2",
    }], 1, 1)
    assert reconstructed_new_native_x(db, job, 10, "5/2", 2)[0] is None
    stale_job = {**job, "metadata_json": json.dumps({
        "adapter_file": str(adapter), "installed_family_sha256": "0" * 64,
    })}
    assert reconstructed_new_native_x(db, stale_job, 10, "5/2", 1)[0] is None
