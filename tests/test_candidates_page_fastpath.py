import inspect
import sqlite3

from rank42 import candidates
from rank42.db import connect
from rank42.ui_pages import candidate_generate_page, candidate_pools_page, candidates_page


def test_candidates_use_one_canonical_shell_with_generate_and_pools_subpages():
    generate = inspect.getsource(candidate_generate_page.page)
    pools = inspect.getsource(candidate_pools_page.page)
    shell = inspect.getsource(candidates_page.page)

    assert "st.tabs(" not in generate
    assert "st.tabs(" not in pools
    assert '"Generate Candidates"' in generate
    assert '"Candidate Pools"' in pools
    assert "_render_import_export(db, ctx)" in pools
    assert '["Browse", "Import & Export"]' not in pools
    assert 'key="candidate-pools-tabs"' not in pools
    assert '"Import/Export"' in pools
    assert 'icon=":material/file_export:"' in pools
    assert '"Back to Pools"' in pools
    assert 'icon=":material/arrow_back:"' in pools
    assert 'st.session_state["candidate_pools_view"] = "Import & Export"' in pools
    assert 'if current == "Import & Export":' in pools
    assert 'title(\n        "Candidates",' in shell
    assert 'icon="scatter_plot"' in shell
    assert '["Generate", "Pools"]' in shell
    assert 'key="candidates-main-tabs"' in shell
    assert 'variant="line"' in shell
    assert 'variant="page"' not in shell
    assert 'width="content"' in shell
    assert 'st.session_state["candidates_tab"] = choice' in shell
    assert "candidate_pools_page.page(db, ctx, embedded=True)" in shell
    assert "candidate_generate_page.page(db, ctx, embedded=True)" in shell


def test_candidate_pools_uses_export_subpage_instead_of_subtabs():
    pools = inspect.getsource(candidate_pools_page.page)

    assert '["Browse", "Import & Export"]' not in pools
    assert 'key="candidate-pools-tabs"' not in pools
    assert '"Import/Export"' in pools
    assert 'icon=":material/file_export:"' in pools
    assert 'st.session_state["candidate_pools_view"] = "Import & Export"' in pools
    assert '"Back to Pools"' in pools
    assert 'icon=":material/arrow_back:"' in pools
    assert 'st.session_state["candidate_pools_view"] = "Browse"' in pools
    assert 'title_col, transfer_col = st.columns(' in pools
    assert '[5.4, 1.2]' in pools
    assert 'vertical_alignment="top"' in pools
    assert "with title_col:" in pools
    assert "section_title(" in pools
    assert "'Stored pools'" in pools
    assert "with transfer_col:" in pools
    assert 'width="stretch"' in pools


def test_candidates_page_never_reconciles_pool_during_render():
    source = inspect.getsource(candidate_pools_page.page)

    assert "reconcile_pool(db,selected['id'])" not in source
    assert "'Reconcile pool state'" in source
    button_pos = source.index("'Reconcile pool state'")
    reconcile_pos = source.index("_reconcile_pool_for_ui", button_pos)
    assert button_pos < reconcile_pos


def test_candidate_pools_browse_is_unboxed_and_transfer_is_separate():
    source = inspect.getsource(candidate_pools_page.page)

    assert "with st.container(border=True):" not in source
    assert "'Stored pools'" in source
    assert '"Import/Export"' in source
    transfer_pos = source.index('if current == "Import & Export":')
    pools_pos = source.index("pools=list_pools(db,limit=200)", transfer_pos)
    assert "_render_import_export(db, ctx)" in source[transfer_pos:pools_pos]


def test_candidates_pool_preview_is_bounded_by_default():
    source = inspect.getsource(candidate_pools_page.page)

    assert '"Preview rows"' in source
    assert "[100, 250, 500, 1000]" in source
    assert "index=1" in source
    assert "limit=int(preview_limit)" in source
    assert "limit=1000" not in source


def test_candidates_export_is_explicit_not_render_time_work():
    source = inspect.getsource(candidate_pools_page._render_import_export)

    prepare_pos = source.index("'Prepare JSONL export'")
    export_pos = source.index("export_pool_jsonl(", prepare_pos)
    assert prepare_pos < export_pos


def test_candidates_manual_reconcile_defers_quickly_when_locked(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA busy_timeout=30000")

    def locked(_db, _pool_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(candidate_pools_page, "reconcile_pool", locked)
    assert candidate_pools_page._reconcile_pool_for_ui(db, 7) is False
    assert int(db.execute("PRAGMA busy_timeout").fetchone()[0]) == 30000
    db.close()


def test_candidate_hot_path_indexes_exist(tmp_path):
    db = connect(tmp_path / "rank42.db")
    indexes = {
        str(row["name"])
        for row in db.execute("PRAGMA index_list(candidates)").fetchall()
    }
    assert "idx_candidates_pool_status_rank" in indexes
    assert "idx_candidates_pool_rank" in indexes


def test_candidate_reconcile_worker_reuses_loaded_metadata_and_status():
    source = inspect.getsource(candidates._reconcile_candidate_rows)

    assert 'json.loads(rec["metadata_json"] or "{}")' in source
    assert 'current = str(rec["status"] or "unsearched")' in source
    assert "SELECT metadata_json FROM candidates WHERE id=?" not in source
    assert "SELECT status FROM candidates WHERE id=?" not in source


def test_targeted_reconcile_does_not_scan_unrelated_candidates(tmp_path):
    from rank42.candidates import create_pool, replace_pool_rows, reconcile_candidates
    from rank42.db import upsert_curve, update_curve

    db = connect(tmp_path / "rank42.db")
    pool = create_pool(
        db,
        name="targeted",
        plugin_id="demo",
        family_spec="demo.family",
    )
    replace_pool_rows(
        db,
        pool["id"],
        [
            {"t": "1", "score": 2.0, "family_name": "Demo"},
            {"t": "2", "score": 1.0, "family_name": "Demo"},
        ],
    )
    first, second = db.execute(
        "SELECT id,parameter FROM candidates WHERE pool_id=? ORDER BY rank_order",
        (int(pool["id"]),),
    ).fetchall()
    for parameter in ("1", "2"):
        curve_id = upsert_curve(
            db,
            family="Demo",
            parameter=parameter,
            score=0.0,
        )
        update_curve(db, curve_id, status="quick_done")

    reconcile_candidates(
        db,
        int(pool["id"]),
        [int(first["id"])],
        infer_searched=False,
    )

    rows = db.execute(
        "SELECT id,curve_id,status FROM candidates WHERE pool_id=? ORDER BY rank_order",
        (int(pool["id"]),),
    ).fetchall()
    assert rows[0]["curve_id"] is not None
    assert rows[0]["status"] == "unsearched"
    assert rows[1]["curve_id"] is None
    assert rows[1]["status"] == "unsearched"
