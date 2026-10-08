from pathlib import Path

from rank42.candidate_files import (
    candidate_directory,
    candidate_output_path,
    candidate_reference,
    list_candidate_jsonls,
)


def test_candidate_outputs_are_forced_into_managed_folder(tmp_path):
    out = candidate_output_path(tmp_path, "../outside/scout")
    assert out == tmp_path / "candidates" / "scout.jsonl"
    assert out.parent == candidate_directory(tmp_path)


def test_candidate_listing_is_jsonl_only_and_portable(tmp_path):
    folder = candidate_directory(tmp_path)
    a = folder / "a.jsonl"
    b = folder / "b.jsonl"
    a.write_text('{"t":"1"}\n')
    b.write_text('{"t":"2"}\n')
    (folder / "ignore.txt").write_text("x")
    names = {p.name for p in list_candidate_jsonls(tmp_path)}
    assert names == {"a.jsonl", "b.jsonl"}
    assert candidate_reference(tmp_path, a) == "candidates/a.jsonl"


def test_candidate_pool_records_overlay_database_state(tmp_path):
    from rank42.candidate_files import candidate_pool_records
    from rank42.db import connect, upsert_curve, update_curve

    folder = candidate_directory(tmp_path)
    pool = folder / "pool.jsonl"
    pool.write_text(
        '\n'.join([
            '{"family":"Kihara 2001, generic rank >=14","a":1,"b":2,"t":"1/2","score":9.5,"prime_bound":500}',
            '{"family":"Kihara 2001, generic rank >=14","a":2,"b":3,"t":"2/3","score":8.5,"prime_bound":500}',
            '{"family":"Kihara 2001, generic rank >=14","a":3,"b":4,"t":"3/4","score":7.5,"prime_bound":500}'
        ]) + '\n'
    )
    db = connect(tmp_path / "rank42.db")
    try:
        c2 = upsert_curve(db, family="Kihara 2001, generic rank >=14", parameter="2/3", score=8.5)
        update_curve(db, c2, status="extra_done")
        c3 = upsert_curve(db, family="Kihara 2001, generic rank >=14", parameter="3/4", score=7.5)
        update_curve(db, c3, status="pruned", quick_upper=14)
        rows = candidate_pool_records(db, pool)
    finally:
        db.close()

    assert [r["Nagao rank"] for r in rows] == [1, 2, 3]
    assert [r["State"] for r in rows] == ["Unsearched", "Searched", "Pruned"]
    assert rows[0]["Nagao score"] == 9.5
