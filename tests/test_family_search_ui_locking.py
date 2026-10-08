import inspect
import sqlite3

from rank42 import candidates
from rank42.db import connect
from rank42.ui_pages import candidate_pools_page, family_search
from rank42.ui_pages.common import setting


def test_family_search_uses_shared_setting_resolver():
    assert family_search.setting is setting


def test_family_search_does_not_own_pool_reconciliation():
    source = inspect.getsource(family_search.render)
    module_source = inspect.getsource(family_search)

    assert "Pool maintenance" not in source
    assert "Reconcile pool state" not in source
    assert "_reconcile_pool_for_ui" not in module_source
    assert "reconcile_pool" not in module_source


def test_candidate_pools_reconcile_defers_quickly_when_database_is_locked(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA busy_timeout=30000")

    def locked(_db, _pool_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(candidate_pools_page, "reconcile_pool", locked)
    assert candidate_pools_page._reconcile_pool_for_ui(db, 7) is False
    assert int(db.execute("PRAGMA busy_timeout").fetchone()[0]) == 30000
    db.close()


def test_candidate_pools_reconcile_restores_timeout_after_success(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA busy_timeout=30000")
    calls = []

    monkeypatch.setattr(
        candidate_pools_page,
        "reconcile_pool",
        lambda _db, pool_id: calls.append(int(pool_id)),
    )
    assert candidate_pools_page._reconcile_pool_for_ui(db, 11) is True
    assert calls == [11]
    assert int(db.execute("PRAGMA busy_timeout").fetchone()[0]) == 30000
    db.close()


def test_candidate_resume_lookup_has_covering_index(tmp_path):
    db = connect(tmp_path / "rank42.db")
    indexes = {
        str(row["name"])
        for row in db.execute("PRAGMA index_list(candidates)").fetchall()
    }
    assert "idx_candidates_pool_status_rank" in indexes


def test_candidate_reconcile_worker_reuses_loaded_candidate_metadata():
    source = inspect.getsource(candidates._reconcile_candidate_rows)

    assert 'json.loads(rec["metadata_json"] or "{}")' in source
    assert 'current = str(rec["status"] or "unsearched")' in source
    assert "SELECT metadata_json FROM candidates WHERE id=?" not in source
    assert "SELECT status FROM candidates WHERE id=?" not in source
