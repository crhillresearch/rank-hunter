from __future__ import annotations

import json

import pytest

from rank42.db import connect, upsert_curve, update_curve
from rank42.research_agent_read import run_query
from rank42 import research_agent


def test_read_query_allows_bounded_curve_facts_and_blocks_private_columns(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    curve_id = upsert_curve(db, family="test family", parameter="1/2")
    update_curve(db, curve_id, descent_lower=3)
    db.close()

    result = run_query(path, "SELECT id,family,descent_lower FROM curves ORDER BY id DESC LIMIT 5")
    assert result["rows"] == [{"id": str(curve_id), "family": "test family", "descent_lower": "3"}]

    with pytest.raises(ValueError, match="rejected"):
        run_query(path, "SELECT generators_json FROM curves")
    with pytest.raises(ValueError, match="bounded SELECT"):
        run_query(path, "UPDATE curves SET descent_lower=99")
    with pytest.raises(ValueError, match="bounded SELECT"):
        run_query(path, "SELECT id FROM curves; DELETE FROM curves")

    check = connect(path)
    assert check.execute("SELECT descent_lower FROM curves WHERE id=?", (curve_id,)).fetchone()[0] == 3
    check.close()


def test_chat_receives_read_only_query_result(monkeypatch, tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    upsert_curve(db, family="sample", parameter="1")
    prompts = []
    monkeypatch.setattr(research_agent, "plan_query", lambda *_a, **_k: "SELECT COUNT(*) AS total FROM curves")

    def fake_stream(_root, prompt, **_kwargs):
        prompts.append(prompt)
        return iter(["There is one stored curve."])

    monkeypatch.setattr(research_agent, "stream_text", fake_stream)
    answer = "".join(research_agent.stream_answer(
        db, tmp_path, page="Curves", message="How many local curves do I have?",
        history=[], db_path=path,
    ))
    assert answer == "There is one stored curve."
    assert '"total": "1"' in prompts[0]
    db.close()


def test_icarm_torsion_json_can_be_filtered_read_only(tmp_path):
    path = tmp_path / "rank42.db"
    db = connect(path)
    db.execute(
        "INSERT INTO external_catalogs(source,source_url,fetched_at,updated_at) VALUES('icarm','https://example.test','now','now')"
    )
    db.execute(
        """INSERT INTO external_curves(source,source_id,a_invariants_json,rank_lower_bound,
           points_json,raw_json,imported_at,updated_at)
           VALUES('icarm','42','[]',22,'[]',?,'now','now')""",
        (json.dumps({"torsion": [2]}),),
    )
    db.commit()
    db.close()
    result = run_query(
        path,
        "SELECT source_id,rank_lower_bound FROM external_curves "
        "WHERE source='icarm' AND json_extract(raw_json,'$.torsion')='[2]' LIMIT 10",
    )
    assert result["rows"] == [{"source_id": "42", "rank_lower_bound": "22"}]
