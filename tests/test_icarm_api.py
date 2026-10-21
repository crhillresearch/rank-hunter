import json
import os
from types import SimpleNamespace

from rank42.db import connect, get_curve, now, upsert_curve, update_curve
from rank42.icarm_api import (
    compute_missing_bad_primes,
    delete_local_token,
    read_token,
    submit_curve,
    token_source,
    write_token,
)
from rank42.submission import certified_witness_lower, icarm_api_body, stored_bad_primes


def _certified_curve(db):
    cid = upsert_curve(db, family="Mestre/Fermigier sextuple, generic rank >=11", parameter="954")
    update_curve(
        db,
        cid,
        a_invariants_json='["1","0","0","-10","20"]',
        descent_lower=3,
        generators_json='[["1","2"],["3/4","5/8"],["7","11"]]',
        bad_primes_json='[2,5,13]',
        status="strong_done",
    )
    return get_curve(db, cid)


def test_icarm_token_is_outside_db_and_env_overrides(tmp_path):
    path = write_token(tmp_path, "erank_local_secret")
    assert path.read_text().strip() == "erank_local_secret"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert read_token(tmp_path, environ={}) == "erank_local_secret"
    assert token_source(tmp_path, environ={}) == "local file"
    assert read_token(tmp_path, environ={"ICARM_API_TOKEN": "erank_env_secret"}) == "erank_env_secret"
    assert token_source(tmp_path, environ={"ICARM_API_TOKEN": "erank_env_secret"}) == "environment"
    assert delete_local_token(tmp_path) is True
    assert delete_local_token(tmp_path) is False


def test_api_body_uses_explicit_certified_witness_lower(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        row = _certified_curve(db)
        assert certified_witness_lower(row) == 3
        body = icarm_api_body(row, commentary="test")
        assert body == {
            "ainvs": ["1", "0", "0", "-10", "20"],
            "points": [["1", "2"], ["3/4", "5/8"], ["7", "11"]],
            "primes": ["2", "5", "13"],
            "commentary": "test",
        }
    finally:
        db.close()


def test_generic_lower_alone_is_not_submission_ready(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        cid = upsert_curve(db, family="demo", parameter="1")
        update_curve(
            db,
            cid,
            a_invariants_json='["0","0","0","-1","1"]',
            generic_lower=2,
            generators_json='[["0","1"],["1","1"]]',
        )
        row = get_curve(db, cid)
        assert certified_witness_lower(row) == 0
        try:
            icarm_api_body(row)
        except ValueError as exc:
            assert "exact witness-backed" in str(exc)
        else:
            raise AssertionError("generic_lower alone must not authorize ICARM submission")
    finally:
        db.close()


class _FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_submit_curve_posts_documented_body_without_persisting_token(tmp_path):
    db = connect(tmp_path / "rank42.db")
    captured = {}
    try:
        row = _certified_curve(db)

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "ok": True,
                    "canonical": {"key": "demo-key"},
                    "independence": {"independent": True, "rankLowerBound": 3},
                    "leaderboard": {"status": "created", "rank": 3},
                }
            )

        result = submit_curve(
            db,
            row,
            project_root=tmp_path,
            token="erank_test_secret",
            commentary="from test",
            timeout=7,
            opener=opener,
        )
        assert result["leaderboard"]["status"] == "created"
        assert captured["authorization"] == "Bearer erank_test_secret"
        assert captured["body"]["ainvs"] == ["1", "0", "0", "-10", "20"]
        assert len(captured["body"]["points"]) == 3
        assert captured["body"]["commentary"] == "from test"
        assert "erank_test_secret" not in (tmp_path / "rank42.db").read_bytes().decode("latin1", errors="ignore")
        event = db.execute("SELECT message FROM events ORDER BY id DESC LIMIT 1").fetchone()
        assert "ICARM API submission created" in event["message"]
    finally:
        db.close()


def test_api_body_refuses_to_silently_omit_missing_bad_primes(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        row = _certified_curve(db)
        update_curve(db, int(row["id"]), bad_primes_json=None)
        row = get_curve(db, int(row["id"]))
        assert stored_bad_primes(row) is None
        try:
            icarm_api_body(row)
        except ValueError as exc:
            assert "missing bad-prime metadata" in str(exc)
        else:
            raise AssertionError("actual ICARM payload must refuse missing bad primes")
        preview = icarm_api_body(row, require_bad_primes=False)
        assert "primes" not in preview
    finally:
        db.close()


def test_compute_missing_bad_primes_uses_science_backend_and_persists(tmp_path):
    db = connect(tmp_path / "rank42.db")
    captured = {}
    try:
        row = _certified_curve(db)
        cid = int(row["id"])
        update_curve(db, cid, bad_primes_json=None)
        row = get_curve(db, cid)

        def runner(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["timeout"] = kwargs.get("timeout")
            update_curve(db, cid, bad_primes_json='[2,3,17]', discriminant='-816')
            return SimpleNamespace(returncode=0, stdout='RANK42_CURVE_METADATA_RESULT={}', stderr='')

        refreshed = compute_missing_bad_primes(
            db, row, project_root=tmp_path, science_command=["sage-python"], timeout=41, runner=runner
        )
        assert stored_bad_primes(refreshed) == [2, 3, 17]
        assert captured["cmd"][0] == "sage-python"
        assert "rank42.curve_metadata" in captured["cmd"]
        assert captured["timeout"] == 41
        body = icarm_api_body(refreshed)
        assert body["primes"] == ["2", "3", "17"]
    finally:
        db.close()
