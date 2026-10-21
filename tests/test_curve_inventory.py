from rank42.curve_inventory import icarm_action_state, inventory_rows
from rank42.db import connect, get_curve, now, upsert_curve, update_curve


def _curve(db, lower=3, *, bad_primes=False):
    cid = upsert_curve(db, family="demo", parameter="t=5")
    fields = dict(
        a_invariants_json='["0","0","0","-1","1"]',
        descent_lower=lower,
        generators_json='[["0","1"],["1","1"],["2","3"],["3","5"]]',
        status="strong_done",
    )
    if bad_primes:
        fields["bad_primes_json"] = '[2,37]'
    update_curve(db, cid, **fields)
    return cid


def _snapshot(db):
    ts = now()
    db.execute(
        "INSERT INTO external_catalogs(source,source_url,curve_count,best_rank_lower,fetched_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("icarm", "https://elliptic-rank.icarm.cloud/database.json", 1, 20, ts, ts),
    )
    db.commit()
    return ts


def test_inventory_preserves_lower_bound_vs_exact_rank(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        cid = _curve(db, 3)
        rows = inventory_rows(db)
        item = next(x for x in rows if x["ID"] == cid)
        assert item["Rank"] == ">= 3"
        update_curve(db, cid, exact_rank=3, status="exact")
        item = next(x for x in inventory_rows(db) if x["ID"] == cid)
        assert item["Rank"] == "= 3"
    finally:
        db.close()


def test_icarm_create_requires_current_no_match_and_bad_primes(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        cid = _curve(db, 3)
        row = get_curve(db, cid)
        state = icarm_action_state(db, row, None)
        assert state["state"] == "needs_check"
        ts = _snapshot(db)
        check = {"status": "not_found_synced", "checked_at": ts}
        state = icarm_action_state(db, row, check)
        assert state["state"] == "needs_metadata"
        assert state["label"] == "COMPUTE BAD PRIMES"
        update_curve(db, cid, bad_primes_json='[2,37]')
        state = icarm_action_state(db, get_curve(db, cid), check)
        assert state["state"] == "ready_create"
        assert state["label"] == "SUBMIT TO ICARM"
    finally:
        db.close()


def test_icarm_improve_only_when_local_witness_is_stronger(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        cid = _curve(db, 3, bad_primes=True)
        row = get_curve(db, cid)
        ts = _snapshot(db)
        known = {"status": "known", "source_label": "99", "source_rank": 2, "checked_at": ts}
        state = icarm_action_state(db, row, known)
        assert state["state"] == "ready_improve"
        assert ">= 3" in state["label"]
        known["source_rank"] = 3
        state = icarm_action_state(db, row, known)
        assert state["state"] == "already_current"
    finally:
        db.close()
