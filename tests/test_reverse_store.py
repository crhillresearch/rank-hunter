from rank42.db import connect
from rank42.reverse_store import get_report, list_reports, report_payload, store_report


def sample_report():
    return {
        "report_key": "abc123",
        "subject": {"type": "external", "source": "icarm", "source_id": "302", "label": "ICARM #302", "rank_lower_bound": 31},
        "a_invariants": ["1", "1", "1", "-2", "3"],
        "parameters": {"prime_bounds": [100, 200]},
        "exact": {"point_structure": {"point_count": 31}},
        "heuristic": {"nagao": {"100": {"score": 1.2, "primes_used": 20}}},
        "numerical": {"status": "not_run"},
        "comparison": {},
        "status": "complete",
    }


def test_reverse_report_schema_is_additive_and_round_trips(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        row = store_report(db, sample_report())
        assert int(row["rank_lower_bound"]) == 31
        rows = list_reports(db)
        assert len(rows) == 1
        loaded = report_payload(get_report(db, rows[0]["id"]))
        assert loaded["subject"]["source_id"] == "302"
        assert loaded["numerical"]["status"] == "not_run"
    finally:
        db.close()


def test_reverse_report_never_changes_local_incumbent(tmp_path):
    from rank42.db import best_lower_bound, upsert_curve

    db = connect(tmp_path / "rank42.db")
    try:
        upsert_curve(db, family="local", parameter="demo", generic_lower=15)
        before = best_lower_bound(db)
        store_report(db, sample_report())
        after = best_lower_bound(db)
        assert before == 15
        assert after == before
    finally:
        db.close()
