import sys

from rank42.catalog import import_icarm_payload
from rank42.db import connect, upsert_curve
from rank42 import status


def test_status_separates_external_reference_from_local_lower_bound(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "rank42.db"
    db = connect(db_path)
    try:
        upsert_curve(db, family="local", parameter="1", generic_lower=15)
        import_icarm_payload(
            db,
            {
                "count": 1,
                "curves": [
                    {
                        "id": 273,
                        "curve_key": "demo",
                        "ainvs": ["0", "0", "1", "-1", "0"],
                        "rank_lower_bound": 30,
                        "points": [["0", "0"]],
                    }
                ],
            },
        )
    finally:
        db.close()
    monkeypatch.setattr(sys, "argv", ["rank42.status", "--db", str(db_path), "--events", "0"])
    status.main()
    out = capsys.readouterr().out
    assert "RANK HUNTER" in out
    assert "Target" not in out
    assert "Best proven lower      15" in out
    assert "External ICARM ref     rank >= 30  (external reference only)" in out
