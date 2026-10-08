import json

import pytest

from rank42.db import connect, upsert_curve
from rank42.research_library import (
    add_research_artifact,
    create_research_library,
    save_research_library_mapping_draft,
)
from rank42.research_library_point_import import import_projection_points
from rank42.research_library_projection import build_research_projection


class _FakePoint:
    def __init__(self, x, y):
        self._xy = (str(x), str(y))

    def is_zero(self):
        return False

    def __getitem__(self, index):
        return self._xy[index]


def _point_factory(x, y):
    if str(x) == "bad":
        raise ValueError("not on curve")
    return _FakePoint(x, y)


def _setup_library(tmp_path, *, map_xy=True):
    library = create_research_library(
        tmp_path,
        name="Legacy Points",
        plugin_id=None,
    )
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="points.csv",
        data=(
            b"x,y,rank,notes\n"
            b"1/2,3/4,31,first\n"
            b"bad,5/6,99,reject\n"
            b"1/2,3/4,31,duplicate\n"
        ),
        category="points",
        media_type="text/csv",
    )
    roles = {
        "rank": "rank_claim",
        "notes": "notes",
    }
    if map_xy:
        roles.update({"x": "x", "y": "y"})
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=["x", "y", "rank", "notes"],
        roles=roles,
    )
    build_research_projection(tmp_path, library["id"], artifact["id"])
    return library, artifact


def _curve(db):
    return upsert_curve(
        db,
        family="Demo",
        parameter="1",
        a_invariants_json=json.dumps(["0", "0", "0", "-1", "0"]),
    )


def test_exact_library_point_handoff_persists_only_valid_points(tmp_path):
    library, artifact = _setup_library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        result = import_projection_points(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
            curve_id=curve_id,
            point_factory=_point_factory,
        )

        assert result["processed"] == 3
        assert result["exact_verified"] == 2
        assert result["rejected"] == 1

        points = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(points) == 1
        point = points[0]
        assert point["x"] == "1/2"
        assert point["y"] == "3/4"
        assert int(point["exact_verified"]) == 1
        assert int(point["rigorous_independent"]) == 0
        assert point["independence_status"] == "unknown"
        assert point["role"] == "candidate"

        metadata = json.loads(point["metadata_json"])
        provenance = metadata["library_point_import"]
        assert provenance["library_id"] == library["id"]
        assert provenance["artifact_id"] == artifact["id"]
        assert provenance["claims"] == {"rank_claim": "31"}
        assert metadata["validation"] == "exact_on_stored_curve_over_QQ"

        discoveries = db.execute(
            "SELECT * FROM point_discoveries WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(discoveries) == 3
        assert [row["outcome"] for row in discoveries].count("exact_point_verified") == 2
        assert [row["outcome"] for row in discoveries].count("rejected_exact_point") == 1
        rejected = next(row for row in discoveries if row["outcome"] == "rejected_exact_point")
        assert int(rejected["exact_verified"]) == 0
        assert rejected["point_id"] is None
        assert rejected["error_class"] == "ValueError"

        assert db.execute("SELECT COUNT(*) FROM rank_evidence").fetchone()[0] == 0
        curve = db.execute("SELECT * FROM curves WHERE id=?", (curve_id,)).fetchone()
        assert curve["exact_rank"] is None
        assert curve["descent_lower"] is None
    finally:
        db.close()


def test_library_point_handoff_respects_projection_query(tmp_path):
    library, artifact = _setup_library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        result = import_projection_points(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
            curve_id=curve_id,
            query="first",
            point_factory=_point_factory,
        )
        assert result["processed"] == 1
        assert result["exact_verified"] == 1
        assert result["rejected"] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM point_discoveries WHERE curve_id=?",
            (curve_id,),
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_library_point_handoff_requires_x_and_y_mapping(tmp_path):
    library, artifact = _setup_library(tmp_path, map_xy=False)
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        with pytest.raises(ValueError, match="mapped x and y"):
            import_projection_points(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
                curve_id=curve_id,
                point_factory=_point_factory,
            )
        assert db.execute("SELECT COUNT(*) FROM points").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM point_discoveries").fetchone()[0] == 0
    finally:
        db.close()


def test_library_point_handoff_refuses_stale_projection(tmp_path):
    library, artifact = _setup_library(tmp_path)
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=["x", "y", "rank", "notes"],
        roles={"x": "x", "y": "y", "rank": "exact_rank_claim"},
    )
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        with pytest.raises(ValueError, match="stale"):
            import_projection_points(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
                curve_id=curve_id,
                point_factory=_point_factory,
            )
        assert db.execute("SELECT COUNT(*) FROM points").fetchone()[0] == 0
    finally:
        db.close()
