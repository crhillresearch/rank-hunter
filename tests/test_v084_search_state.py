import json

from rank42.candidates import (
    candidate_rows,
    create_pool,
    delete_pool,
    replace_pool_rows,
)
from rank42.db import connect, get_curve, upsert_curve
from rank42.ui_store import create_job, ensure_ui_schema, finalize_pool_search, update_job


def _pool_with_one(db, *, name='p', parameter='1'):
    ensure_ui_schema(db)
    pool = create_pool(db, name=name, plugin_id='plugin', family_spec='family')
    replace_pool_rows(db, pool['id'], [{'t': parameter, 'score': 1.0, 'family': 'Test Family'}])
    return pool


def test_candidate_pool_delete_cascades_queue_but_preserves_curves(tmp_path):
    db = connect(tmp_path / 'rank42.db')
    pool = _pool_with_one(db, name='delete-me')
    curve_id = upsert_curve(db, family='Test Family', parameter='1', descent_lower=2, status='proven_lower')
    candidate_id = int(candidate_rows(db, pool['id'])[0]['id'])
    db.execute('UPDATE candidates SET curve_id=? WHERE id=?', (curve_id, candidate_id))
    db.commit()

    assert delete_pool(db, pool['id']) is True
    assert db.execute('SELECT COUNT(*) n FROM candidates WHERE pool_id=?', (pool['id'],)).fetchone()['n'] == 0
    assert db.execute('SELECT * FROM candidate_pools WHERE id=?', (pool['id'],)).fetchone() is None
    assert get_curve(db, curve_id) is not None
    db.close()


def test_pool_finalize_discards_new_empty_zero_evidence_curve(tmp_path):
    db = connect(tmp_path / 'rank42.db')
    pool = _pool_with_one(db, name='zero-miss')
    candidate_id = int(candidate_rows(db, pool['id'])[0]['id'])
    log = tmp_path / 'job.log'
    jid = create_job(
        db, kind='family_search', label='search', command=['python'], cwd=tmp_path, log_path=log,
        metadata={'pool_id': int(pool['id']), 'candidate_ids': [candidate_id], 'offset': 0, 'count': 1},
    )
    update_job(db, jid, status='running', started_at='2026-08-26T00:00:00+00:00')
    curve_id = upsert_curve(db, family='Test Family', parameter='1', quick_upper=0, exact_rank=0, status='pruned')
    db.execute("UPDATE curves SET created_at='2026-08-26T00:00:01+00:00' WHERE id=?", (curve_id,))
    db.commit()
    log.write_text('[candidate done] 1/1\n')

    assert finalize_pool_search(db, jid) == 1
    assert get_curve(db, curve_id) is None
    rec = candidate_rows(db, pool['id'])[0]
    assert rec['status'] == 'searched'
    assert rec['curve_id'] is None
    meta = json.loads(rec['metadata_json'])
    assert meta['last_search']['rigorous_lower'] == 0
    assert meta['last_search']['zero_evidence_curve_discarded'] is True
    db.close()


def test_pool_finalize_preserves_positive_rank_curve(tmp_path):
    db = connect(tmp_path / 'rank42.db')
    pool = _pool_with_one(db, name='positive-hit')
    candidate_id = int(candidate_rows(db, pool['id'])[0]['id'])
    log = tmp_path / 'job.log'
    jid = create_job(
        db, kind='family_search', label='search', command=['python'], cwd=tmp_path, log_path=log,
        metadata={'pool_id': int(pool['id']), 'candidate_ids': [candidate_id], 'offset': 0, 'count': 1},
    )
    update_job(db, jid, status='running', started_at='2026-08-26T00:00:00+00:00')
    curve_id = upsert_curve(db, family='Test Family', parameter='1', descent_lower=3, status='proven_lower')
    db.execute("UPDATE curves SET created_at='2026-08-26T00:00:01+00:00' WHERE id=?", (curve_id,))
    db.commit()
    log.write_text('[candidate done] 1/1\n')

    finalize_pool_search(db, jid)
    assert get_curve(db, curve_id) is not None
    rec = candidate_rows(db, pool['id'])[0]
    assert rec['status'] == 'searched'
    assert int(rec['curve_id']) == curve_id
    db.close()


def test_pool_finalize_does_not_delete_preexisting_zero_curve(tmp_path):
    db = connect(tmp_path / 'rank42.db')
    curve_id = upsert_curve(db, family='Test Family', parameter='1', exact_rank=0, status='exact')
    db.execute("UPDATE curves SET created_at='2026-08-25T00:00:00+00:00' WHERE id=?", (curve_id,))
    db.commit()
    pool = _pool_with_one(db, name='old-zero')
    candidate_id = int(candidate_rows(db, pool['id'])[0]['id'])
    log = tmp_path / 'job.log'
    jid = create_job(
        db, kind='family_search', label='search', command=['python'], cwd=tmp_path, log_path=log,
        metadata={'pool_id': int(pool['id']), 'candidate_ids': [candidate_id], 'offset': 0, 'count': 1},
    )
    update_job(db, jid, status='running', started_at='2026-08-26T00:00:00+00:00')
    log.write_text('[candidate done] 1/1\n')

    finalize_pool_search(db, jid)
    assert get_curve(db, curve_id) is not None
    db.close()
