from __future__ import annotations

import json

from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42 import research_agent


def _db():
    db = connect(":memory:")
    first = upsert_curve(db, family="test", parameter="1")
    second = upsert_curve(db, family="test", parameter="2")
    return db, first, second


def test_model_cannot_redirect_curve_action(monkeypatch, tmp_path):
    db, first, second = _db()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_args, **_kwargs: {
        "reply": "I'll do it", "action": "calculate_log_n", "curve_id": second,
    })
    proposal = research_agent.propose(db, tmp_path, page="Curves",
                                      message=f"Tell me about curve #{first}", history=[])
    assert proposal["action"] == "none"
    assert proposal["curve_id"] is None


def test_explicit_log_n_request_uses_requested_curve(monkeypatch, tmp_path):
    db, first, _second = _db()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_args, **_kwargs: {
        "reply": "I'll calculate it", "action": "none", "curve_id": None,
    })
    proposal = research_agent.propose(db, tmp_path, page="Curves",
                                      message=f"Calculate log N for curve #{first}", history=[])
    assert proposal["action"] == "calculate_log_n"
    assert proposal["curve_id"] == first
    assert get_curve(db, first)["conductor"] is None  # Proposal never writes.


def test_single_curve_calculation_and_audit(monkeypatch, tmp_path):
    db, first, second = _db()

    def fake_backfill(connection, *, curve_ids, limit, timeout_seconds):
        assert curve_ids == [first]
        assert limit == 1
        assert timeout_seconds == 20
        update_curve(connection, first, conductor="11")

    monkeypatch.setattr(research_agent, "backfill_conductors", fake_backfill)
    answer = research_agent.calculate_log_n(db, tmp_path, first)
    assert "log N = 2.39789527" in answer
    assert get_curve(db, second)["conductor"] is None
    audit = (tmp_path / ".rank42-ui" / "agent-actions.jsonl").read_text().splitlines()
    assert [json.loads(line)["outcome"] for line in audit] == ["started", "complete"]


def test_best_curve_icarm_answer_uses_size_placement_without_gemini(monkeypatch, tmp_path):
    db, first, second = _db()
    update_curve(db, first, descent_lower=27, conductor="1000", discriminant="5000")
    update_curve(db, second, descent_lower=2, conductor="11")
    db.execute(
        "INSERT INTO external_catalogs(source,source_url,fetched_at,updated_at) VALUES('icarm','https://example.test','2026-10-23T12:00:00+00:00','now')"
    )
    for index in range(11):
        db.execute(
            """INSERT INTO external_curves(source,source_id,a_invariants_json,
               rank_lower_bound,points_json,conductor,discriminant,raw_json,
               imported_at,updated_at)
               VALUES('icarm',?,'[]',27,'[]',?,?,'{}','now','now')""",
            (str(index), str(100 + index), str(500 + index)),
        )
    db.commit()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Gemini called")))
    result = research_agent.propose(
        db, tmp_path, page="Dashboard", message="Is my best curve submittable to ICARM leaderboard?", history=[]
    )
    assert "No overall top-10 size placement" in result["reply"]
    assert "#12 overall" in result["reply"]
    assert "Torsion is not stored" in result["reply"]


def test_latest_discovery_uses_recent_certified_curve(monkeypatch, tmp_path):
    db, first, second = _db()
    update_curve(db, first, descent_lower=27)
    update_curve(db, second, descent_lower=2)
    db.execute("UPDATE curves SET created_at='2026-10-23T12:00:00+00:00' WHERE id=?", (first,))
    db.execute("UPDATE curves SET created_at='2026-10-23T13:00:00+00:00' WHERE id=?", (second,))
    db.commit()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Gemini called")))
    result = research_agent.propose(
        db, tmp_path, page="Dashboard", message="What is the last curve I found?", history=[]
    )
    assert f"#{second}" in result["reply"]
    assert "rank ≥2" in result["reply"]


def test_icarm_torsion_route_can_qualify_when_overall_does_not(monkeypatch, tmp_path):
    db, first, _second = _db()
    db.execute("ALTER TABLE curves ADD COLUMN torsion_label TEXT")
    update_curve(db, first, descent_lower=27, conductor="1000")
    db.execute("UPDATE curves SET torsion_label='C8' WHERE id=?", (first,))
    db.execute(
        "INSERT INTO external_catalogs(source,source_url,fetched_at,updated_at) VALUES('icarm','https://example.test','2026-10-23T12:00:00+00:00','now')"
    )
    for index in range(11):
        db.execute(
            """INSERT INTO external_curves(source,source_id,a_invariants_json,
               rank_lower_bound,points_json,conductor,raw_json,imported_at,updated_at)
               VALUES('icarm',?,'[]',27,'[]',?,?,'now','now')""",
            (str(index), str(100 + index), json.dumps({"torsion": [8] if index == 0 else []})),
        )
    db.commit()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Gemini called")))
    result = research_agent.propose(
        db, tmp_path, page="Dashboard", message=f"Is curve #{first} on the ICARM leaderboard threshold?", history=[]
    )
    assert "Yes" in result["reply"]
    assert "#12 overall, #2 in torsion" in result["reply"]


def test_torsion_comparison_reports_only_confirmed_local_curves(monkeypatch, tmp_path):
    db, first, second = _db()
    db.execute("ALTER TABLE curves ADD COLUMN torsion_label TEXT")
    update_curve(db, first, descent_lower=4)
    update_curve(db, second, descent_lower=5)
    db.execute("UPDATE curves SET torsion_label='C2 × C4' WHERE id=?", (first,))
    db.commit()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Gemini called")))
    result = research_agent.quick_response(db, tmp_path, "compare my best two curves for Z/2Z x Z/4Z")
    assert "only one stored curve" in result["reply"]
    assert f"#{first}" in result["reply"]
    assert f"#{second}" not in result["reply"]


def test_torsion_comparison_reports_both_ranked_curves(tmp_path):
    db, first, second = _db()
    db.execute("ALTER TABLE curves ADD COLUMN torsion_label TEXT")
    update_curve(db, first, descent_lower=3)
    update_curve(db, second, descent_lower=2)
    db.execute("UPDATE curves SET torsion_label='C2 × C8' WHERE id IN (?,?)", (first, second))
    db.commit()
    result = research_agent.quick_response(db, tmp_path, "compare my best two curves for Z/2Z x Z/8Z")
    assert f"#{first}" in result["reply"]
    assert f"#{second}" in result["reply"]
    assert "leads by 1" in result["reply"]


def test_rank_breakthrough_advice_uses_confirmed_curve_without_ai(monkeypatch, tmp_path):
    db, first, second = _db()
    db.execute("ALTER TABLE curves ADD COLUMN torsion_label TEXT")
    update_curve(db, first, descent_lower=27)
    update_curve(db, second, descent_lower=32)
    db.execute("UPDATE curves SET torsion_label='Trivial' WHERE id=?", (first,))
    db.commit()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("AI called")))
    result = research_agent.quick_response(db, tmp_path, "best method to break >=27 in trivial?")
    assert f"curve #{first}" in result["reply"]
    assert f"#{second}" not in result["reply"]
    assert "≥28" in result["reply"]
    assert "Exact Independence" in result["reply"]


def test_icarm_group_leader_and_correction_use_synced_catalog(monkeypatch, tmp_path):
    db, _first, _second = _db()
    db.execute(
        "INSERT INTO external_catalogs(source,source_url,fetched_at,updated_at) VALUES('icarm','https://example.test','2026-10-23T12:00:00+00:00','now')"
    )
    for source_id, rank, conductor, faltings, torsion in [
        ("8", 22, "100", "10.0", [2]),
        ("9", 22, "100", "11.0", [2]),
        ("10", 25, "10", "1.0", [4]),
    ]:
        db.execute(
            """INSERT INTO external_curves(source,source_id,a_invariants_json,
               rank_lower_bound,points_json,conductor,faltings_height,raw_json,
               imported_at,updated_at)
               VALUES('icarm',?,'[]',?,'[]',?,?,?,'now','now')""",
            (source_id, rank, conductor, faltings, json.dumps({"torsion": torsion})),
        )
    db.commit()
    monkeypatch.setattr(research_agent, "generate_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Gemini called")))
    question = "What is the best C2 curve on ICARM?"
    result = research_agent.quick_response(db, tmp_path, question)
    assert "ICARM #8" in result["reply"]
    assert "rank ≥22" in result["reply"]
    assert "2 curves share" in result["reply"]
    followup = research_agent.quick_response(
        db, tmp_path, "yes it does",
        history=[{"role": "user", "text": question},
                 {"role": "assistant", "text": "Rank Hunter has no record."}],
    )
    assert "ICARM #8" in followup["reply"]
