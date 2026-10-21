from rank42.db import connect, get_curve, upsert_curve, update_curve
from rank42.submission import icarm_submission_payload, icarm_submission_text


def test_icarm_packet_uses_rigorous_witnesses(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        cid = upsert_curve(db, family="play", parameter="A=14, B=1")
        update_curve(
            db, cid,
            a_invariants_json='["0","0","0","14","1"]',
            descent_lower=3,
            exact_rank=3,
            generators_json='[["0","1"],["1","4"],["4","11"]]',
            bad_primes_json='[2, 7, 157]',
            status="exact",
        )
        row = get_curve(db, cid)
        p = icarm_submission_payload(row)
        assert p["rank_lower_bound"] == 3
        assert p["exact_rank"] == 3
        assert len(p["points"]) == 3
        text = icarm_submission_text(row)
        assert "0, 0, 0, 14, 1" in text
        assert "4, 11" in text
    finally:
        db.close()
