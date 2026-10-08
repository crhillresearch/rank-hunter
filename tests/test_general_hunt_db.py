from rank42.db import connect


def test_general_hunt_trial_table_is_additive(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "general_hunt_trials" in names
        cols = {row[1] for row in db.execute("PRAGMA table_info(general_hunt_trials)")}
        assert {"trial_key", "screened_rank", "rigorous_lower", "curve_id"} <= cols
    finally:
        db.close()
