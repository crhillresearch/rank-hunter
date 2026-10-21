from decimal import Decimal

import pytest

from rank42 import lmfdb_sql


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_sql_many_uses_one_parameterized_batch_and_normalizes(monkeypatch):
    conn = FakeConnection([
        ("88024.a1", "88024a1", [Decimal(0), Decimal(0), Decimal(0), Decimal(14), Decimal(1)], 88024, 3),
        ("123.a1", "123a1", [Decimal(0), Decimal(0), Decimal(1), Decimal(-1), Decimal(0)], 123, 1),
    ])
    monkeypatch.setattr(lmfdb_sql, "_load_driver", lambda: ("psycopg", object()))
    monkeypatch.setattr(lmfdb_sql, "_connect", lambda *args, **kwargs: conn)

    grouped, meta = lmfdb_sql.fetch_lmfdb_sql_many([88024, 123, 88024], timeout=2)

    assert sorted(grouped) == [123, 88024]
    assert grouped[88024][0]["ainvs"] == [0, 0, 0, 14, 1]
    assert grouped[88024][0]["rank"] == 3
    assert meta["transport"] == "sql"
    assert meta["requested_conductors"] == 2
    query_calls = [call for call in conn.cur.calls if "FROM ec_curvedata" in call[0]]
    assert len(query_calls) == 1
    assert "conductor = ANY(%s)" in query_calls[0][0]
    assert query_calls[0][1] == ([123, 88024],)
    assert conn.closed is True


def test_missing_driver_has_actionable_message(monkeypatch):
    def missing(name, *args, **kwargs):
        raise ImportError(name)

    monkeypatch.setattr("builtins.__import__", missing)
    with pytest.raises(lmfdb_sql.LMFDBSQLUnavailable, match="install-catalog-sql"):
        lmfdb_sql._load_driver()
