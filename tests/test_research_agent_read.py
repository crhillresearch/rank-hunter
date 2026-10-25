from __future__ import annotations

import json

import pytest

from rank42.db import connect, now, upsert_curve, update_curve
from rank42.research_agent_read import run_query
from rank42 import research_agent
from rank42 import research_agent_families
from rank42.plugins import Plugin


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


def test_agent_answers_family_rank_curve_id_and_stored_point_count(tmp_path):
    db = connect(tmp_path / "rank42.db")
    curve_id = upsert_curve(db, family="Mestre/Fermigier sextuple, generic rank >=11", parameter="7/3")
    update_curve(db, curve_id, descent_lower=19)
    for x, verified in [("1", 1), ("2", 1), ("3", 0)]:
        db.execute(
            """INSERT INTO points(curve_id,x,y,source,exact_verified,created_at,updated_at)
               VALUES(?,?,?,'test',?,?,?)""",
            (curve_id, x, "1", verified, now(), now()),
        )
    db.commit()
    question = "My rank 19 curve under the Mestre rank-11 family: how many points and what curve id?"
    answer = research_agent.quick_response(db, tmp_path, question)
    assert f"Curve #{curve_id}" in answer["reply"]
    assert "3 stored rational points, 2 exactly verified" in answer["reply"]
    assert "certified rank ≥19" in answer["reply"]
    assert answer["curve_id"] == curve_id

    update_curve(db, curve_id, descent_lower=16)
    missing = research_agent.quick_response(db, tmp_path, question)
    assert "no curve recorded at certified rank ≥19" in missing["reply"]
    assert f"#{curve_id} (3 stored, 2 verified rational points)" in missing["reply"]
    db.close()


def test_agent_finds_curve_with_most_verified_rational_points(tmp_path):
    db = connect(tmp_path / "rank42.db")
    leader = upsert_curve(db, family="Example family", parameter="5/2")
    other = upsert_curve(db, family="Other family", parameter="7")
    for curve_id, x, verified, independent in [
        (leader, "1", 1, 1), (leader, "2", 1, 0),
        (other, "1", 1, 0), (other, "2", 0, 0), (other, "3", 0, 0),
    ]:
        db.execute(
            """INSERT INTO points(curve_id,x,y,source,exact_verified,
               rigorous_independent,created_at,updated_at)
               VALUES(?,?,?,'test',?,?,?,?)""",
            (curve_id, x, "1", verified, independent, now(), now()),
        )
    db.commit()
    for question in ("What curve in the db has the most rat points?",
                     "Which curve has the most rational points?"):
        answer = research_agent.quick_response(db, tmp_path, question)
        assert answer["curve_id"] == leader
        assert f"Curve #{leader}" in answer["reply"]
        assert "2 (2 stored)" in answer["reply"]
        assert "1 of those points" in answer["reply"]
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


def test_agent_reads_installed_rank18_family_equation_and_followup(monkeypatch, tmp_path):
    root = tmp_path / "plugins" / "families"
    family_dir = root / "elkies_rank18_2026"
    family_dir.mkdir(parents=True)
    base_dir = root / "elkies_rank17_2026"
    base_dir.mkdir()
    (base_dir / "family.json").write_text(json.dumps({
        "a_invariants": ["0", "0", "0", "-t", "t+1"],
    }), encoding="utf-8")
    plugin = Plugin(
        id="elkies_rank18_2026", name="Elkies Rank-18 First Cover",
        version="0.1.1", plugin_type="family", root=family_dir,
        family_spec="module:rank18_family.py", adapter_path=None,
        capabilities=frozenset(), manifest={
            "enabled_by_default": True,
            "verified_generic_rank_lower": 18,
            "provenance": {
                "published_cover": "u^2=4225*t^2+38636*t+289444",
                "parameterization": "t=(289444-r^2)/(130*r-38636), u=65*t+r",
            },
        },
    )
    monkeypatch.setattr(research_agent_families, "discover_plugins", lambda *_args: [plugin])
    db = connect(tmp_path / "rank42.db")
    question = "Can you show me the equation for the Elkies Rank-18 family that is installed?"
    first = research_agent.quick_response(db, tmp_path, question)
    followup = research_agent.quick_response(db, tmp_path, "It's a family plugin", history=[
        {"role": "user", "text": question},
    ])
    assert first is not None and followup is not None
    assert first["reply"] == followup["reply"]
    assert "A(t) = -t" in first["reply"]
    assert "B(t) = t+1" in first["reply"]
    assert "4225t^2+38636t+289444" in first["reply"]
    assert "lower bound of 18" in first["reply"]
    db.close()
