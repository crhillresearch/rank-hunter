import json

import pytest

from rank42.curve_import import import_curve
from rank42.db import connect
from rank42.research_library import (
    add_research_artifact,
    create_research_library,
    save_research_library_mapping_draft,
)
from rank42.research_library_curve_import import import_projection_curves
from rank42.research_library_projection import build_research_projection


_MODEL_ROLES = {
    "a1": "a1",
    "a2": "a2",
    "a3": "a3",
    "a4": "a4",
    "a6": "a6",
    "family": "family_label",
    "parameter": "parameter",
    "rank": "rank_claim",
    "x": "x",
    "y": "y",
    "notes": "notes",
}


def _library(
    tmp_path,
    *,
    data=None,
    roles=None,
):
    library = create_research_library(tmp_path, name="Legacy Curves")
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="curves.csv",
        data=data or (
            b"a1,a2,a3,a4,a6,family,parameter,rank,x,y,notes\n"
            b"0,0,0,-1,0,Library family,t1,31,0,0,first\n"
            b"0,0,0,-2,0,Library family,t2,30,0,0,second\n"
        ),
        category="curve_candidate_data",
        media_type="text/csv",
    )
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=[
            "a1", "a2", "a3", "a4", "a6",
            "family", "parameter", "rank", "x", "y", "notes",
        ],
        roles=roles or _MODEL_ROLES,
    )
    build_research_projection(tmp_path, library["id"], artifact["id"])
    return library, artifact


def test_library_curve_handoff_creates_models_but_not_rank_or_points(tmp_path):
    library, artifact = _library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        result = import_projection_curves(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
        )

        assert result["processed"] == 2
        assert result["created"] == 2
        assert result["matched_existing"] == 0

        curves = db.execute(
            "SELECT * FROM curves ORDER BY parameter"
        ).fetchall()
        assert [row["parameter"] for row in curves] == ["t1", "t2"]
        assert [row["family"] for row in curves] == [
            "Library family",
            "Library family",
        ]
        assert json.loads(curves[0]["a_invariants_json"]) == [
            "0", "0", "0", "-1", "0",
        ]
        assert json.loads(curves[1]["a_invariants_json"]) == [
            "0", "0", "0", "-2", "0",
        ]
        assert all(row["status"] == "imported" for row in curves)
        assert all(row["generic_lower"] is None for row in curves)
        assert all(row["descent_lower"] is None for row in curves)
        assert all(row["descent_upper"] is None for row in curves)
        assert all(row["exact_rank"] is None for row in curves)

        # Mapped x/y are intentionally ignored by the Curve/model handoff.
        assert db.execute("SELECT COUNT(*) FROM points").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM rank_evidence").fetchone()[0] == 0

        event = db.execute(
            """
            SELECT message
            FROM events
            WHERE curve_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(curves[0]["id"]),),
        ).fetchone()["message"]
        assert "Manual external curve import" in event
        assert "external_rank_claim=rank=31" in event
        assert f"research_library:{library['id']}:artifact:{artifact['id']}:record:1" in event
        assert "research_library_curve_import" in event
        assert "mapping_sha256" in event
    finally:
        db.close()


def test_library_curve_handoff_respects_projection_query(tmp_path):
    library, artifact = _library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        result = import_projection_curves(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
            query="first",
        )
        assert result["processed"] == 1
        assert result["created"] == 1
        rows = db.execute("SELECT family,parameter FROM curves").fetchall()
        assert [(row["family"], row["parameter"]) for row in rows] == [
            ("Library family", "t1")
        ]
    finally:
        db.close()


def test_library_curve_handoff_requires_all_five_model_roles(tmp_path):
    roles = dict(_MODEL_ROLES)
    roles.pop("a6")
    library, artifact = _library(tmp_path, roles=roles)
    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="requires mapped"):
            import_projection_curves(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
            )
        assert db.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0
    finally:
        db.close()


def test_library_curve_handoff_refuses_stale_projection(tmp_path):
    library, artifact = _library(tmp_path)
    changed_roles = dict(_MODEL_ROLES)
    changed_roles["rank"] = "exact_rank_claim"
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=[
            "a1", "a2", "a3", "a4", "a6",
            "family", "parameter", "rank", "x", "y", "notes",
        ],
        roles=changed_roles,
    )

    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="stale"):
            import_projection_curves(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
            )
        assert db.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0
    finally:
        db.close()


def test_library_curve_handoff_singular_row_aborts_entire_batch(tmp_path):
    library, artifact = _library(
        tmp_path,
        data=(
            b"a1,a2,a3,a4,a6,family,parameter,rank,x,y,notes\n"
            b"0,0,0,-1,0,Atomic family,good,8,0,0,good\n"
            b"0,0,0,0,0,Atomic family,singular,99,0,0,bad\n"
        ),
    )
    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="curve record 2.*singular"):
            import_projection_curves(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
            )
        assert db.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0
    finally:
        db.close()


def test_library_curve_handoff_key_conflict_aborts_before_new_curve(tmp_path):
    library, artifact = _library(
        tmp_path,
        data=(
            b"a1,a2,a3,a4,a6,family,parameter,rank,x,y,notes\n"
            b"0,0,0,-3,0,Conflict family,new-one,8,0,0,new\n"
            b"0,0,0,-2,0,Conflict family,same-key,9,0,0,conflict\n"
        ),
    )
    db = connect(tmp_path / "rank42.db")
    try:
        existing_id, _ = import_curve(
            db,
            a_invariants=[0, 0, 0, -1, 0],
            family="Conflict family",
            parameter="same-key",
        )

        with pytest.raises(ValueError, match="different stored model"):
            import_projection_curves(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
            )

        rows = db.execute(
            "SELECT id,family,parameter FROM curves ORDER BY id"
        ).fetchall()
        assert len(rows) == 1
        assert int(rows[0]["id"]) == int(existing_id)
        assert rows[0]["parameter"] == "same-key"
        assert db.execute(
            "SELECT COUNT(*) FROM curves WHERE parameter='new-one'"
        ).fetchone()[0] == 0
    finally:
        db.close()
