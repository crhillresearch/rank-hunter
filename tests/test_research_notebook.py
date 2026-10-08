import inspect

import pytest

from rank42.db import connect
from rank42.manage_store import (
    NOTEBOOK_ENTRY_TYPES,
    NOTEBOOK_HANDOFF_STATES,
    NOTEBOOK_LINK_KINDS,
    add_campaign_note,
    campaign_handoff_markdown,
    campaign_notebook_entries,
    campaign_notes,
    create_campaign,
    ensure_manage_schema,
)
from rank42.ui_pages import campaigns_page


def _campaign(db, name="Notebook fixture"):
    ensure_manage_schema(db)
    return create_campaign(
        db,
        name=name,
        objective="Study a research hypothesis without mutating scientific truth.",
        target_rank=9,
    )


def test_legacy_note_defaults_to_plain_included_notebook_entry(tmp_path):
    db = connect(tmp_path / "legacy-note.db")
    try:
        campaign_id = _campaign(db)
        note_id = add_campaign_note(db, campaign_id, "legacy-style call")

        raw = campaign_notes(db, campaign_id, limit=10)[0]
        entries = campaign_notebook_entries(db, campaign_id, limit=10)

        assert int(raw["id"]) == note_id
        assert raw["entry_type"] == "note"
        assert raw["handoff_state"] == "include"
        assert entries[0]["entry_type"] == "note"
        assert entries[0]["handoff_state"] == "include"
        assert entries[0]["links"] == []
    finally:
        db.close()


def test_typed_notebook_entry_and_links_round_trip(tmp_path):
    db = connect(tmp_path / "typed-notebook.db")
    try:
        campaign_id = _campaign(db)
        note_id = add_campaign_note(
            db,
            campaign_id,
            "Prime 41 appears unusually often in this selected cohort.",
            entry_type="observation",
            handoff_state="include",
            links=[
                {"kind": "curve", "id": 123},
                {"kind": "candidate_pool", "id": 8},
                {"kind": "pipeline_run", "id": 591},
                {"kind": "job", "id": 602},
                {"kind": "landscape_analysis", "id": 7},
            ],
        )

        entry = campaign_notebook_entries(db, campaign_id, limit=10)[0]

        assert entry["id"] == note_id
        assert entry["entry_type"] == "observation"
        assert entry["handoff_state"] == "include"
        assert entry["created_at"]
        assert entry["body"].startswith("Prime 41")
        assert entry["links"] == [
            {"kind": "curve", "id": 123},
            {"kind": "candidate_pool", "id": 8},
            {"kind": "pipeline_run", "id": 591},
            {"kind": "job", "id": 602},
            {"kind": "landscape_analysis", "id": 7},
        ]
    finally:
        db.close()


def test_notebook_contract_rejects_invalid_semantics(tmp_path):
    db = connect(tmp_path / "invalid-notebook.db")
    try:
        campaign_id = _campaign(db)

        assert "hypothesis" in NOTEBOOK_ENTRY_TYPES
        assert "failed_approach" in NOTEBOOK_ENTRY_TYPES
        assert "next_experiment" in NOTEBOOK_ENTRY_TYPES
        assert set(NOTEBOOK_HANDOFF_STATES) == {"include", "private", "resolved"}
        assert "landscape_analysis" in NOTEBOOK_LINK_KINDS

        with pytest.raises(ValueError, match="entry type"):
            add_campaign_note(
                db,
                campaign_id,
                "bad type",
                entry_type="proof",
            )
        with pytest.raises(ValueError, match="handoff state"):
            add_campaign_note(
                db,
                campaign_id,
                "bad handoff",
                handoff_state="publish",
            )
        with pytest.raises(ValueError, match="link kind"):
            add_campaign_note(
                db,
                campaign_id,
                "bad link",
                links=[{"kind": "rank_evidence", "id": 1}],
            )
        with pytest.raises(ValueError, match="positive"):
            add_campaign_note(
                db,
                campaign_id,
                "bad link id",
                links=[{"kind": "curve", "id": 0}],
            )
    finally:
        db.close()


def test_handoff_selectively_includes_notebook_entries(tmp_path):
    db = connect(tmp_path / "handoff-notebook.db")
    try:
        campaign_id = _campaign(db)
        add_campaign_note(
            db,
            campaign_id,
            "Include this hypothesis.",
            entry_type="hypothesis",
            handoff_state="include",
            links=[{"kind": "curve", "id": 77}],
        )
        add_campaign_note(
            db,
            campaign_id,
            "Private scratch only.",
            entry_type="note",
            handoff_state="private",
        )
        add_campaign_note(
            db,
            campaign_id,
            "Resolved old direction.",
            entry_type="failed_approach",
            handoff_state="resolved",
        )

        handoff = campaign_handoff_markdown(db, campaign_id)

        assert "## Research notebook" in handoff
        assert "Include this hypothesis." in handoff
        assert "Hypothesis" in handoff
        assert "Curve #77" in handoff
        assert "Private scratch only." not in handoff
        assert "Resolved old direction." not in handoff
    finally:
        db.close()


def test_notebook_write_does_not_mutate_rank_evidence(tmp_path):
    db = connect(tmp_path / "notebook-science-boundary.db")
    try:
        campaign_id = _campaign(db)
        before = db.execute("SELECT COUNT(*) FROM rank_evidence").fetchone()[0]

        add_campaign_note(
            db,
            campaign_id,
            "This is a human hypothesis, not scientific evidence.",
            entry_type="hypothesis",
            handoff_state="include",
        )

        after = db.execute("SELECT COUNT(*) FROM rank_evidence").fetchone()[0]
        assert after == before
    finally:
        db.close()


def test_campaign_ui_exposes_research_notebook_and_legacy_notes_tab_alias():
    detail = inspect.getsource(campaigns_page._render_campaign_detail)
    notes = inspect.getsource(campaigns_page._notes)

    assert '"Notebook"' in detail
    assert '"Notes"' in detail
    assert 'current == "Notes"' in detail
    assert '"#### Research Notebook"' in notes
    assert "NOTEBOOK_ENTRY_TYPES" in notes
    assert "NOTEBOOK_HANDOFF_STATES" in notes
    assert '"Linked object"' in notes
    assert "campaign_notebook_entries(" in notes
    assert "add_campaign_note(" in notes
