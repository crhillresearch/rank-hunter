from rank42.certify_curve import certify_stored_curve
from rank42.db import connect, get_curve, upsert_curve, update_curve


def test_certify_stored_curve_updates_exact_rank(monkeypatch, tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        cid = upsert_curve(db, family="demo", parameter="A=14, B=1")
        update_curve(
            db, cid,
            a_invariants_json='["0","0","0","14","1"]',
            descent_lower=3,
            generators_json='[["0","1"],["1","4"],["4","11"]]',
            status="proven_lower",
        )
        monkeypatch.setattr(
            "rank42.certify_curve.run_rank_certification",
            lambda ainvs, timeout: {"exact_rank": 3, "_runtime_seconds": 0.1},
        )
        out = certify_stored_curve(db, cid, timeout=5)
        assert out["exact_rank"] == 3
        row = get_curve(db, cid)
        assert row["exact_rank"] == 3
        assert row["status"] == "exact"
        assert row["certain"] == 1
    finally:
        db.close()
