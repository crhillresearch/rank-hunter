import json

import pytest

from rank42.db import connect
from rank42.research_library import (
    add_research_artifact,
    create_research_library,
    save_research_library_mapping_draft,
)
from rank42.research_library_projection import build_research_projection
from rank42.research_library_promote import (
    create_candidate_pool_from_research_projection,
)


def _write_family_plugin(project_root, plugin_id="demo-family"):
    root = project_root / "plugins" / plugin_id
    root.mkdir(parents=True)
    (root / "family.json").write_text("{}\n", encoding="utf-8")
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": plugin_id,
                "name": "Demo Family",
                "version": "1.0.0",
                "plugin_type": "family",
                "curve_family_name": "Demo Family Curves",
                "family": {
                    "kind": "json",
                    "file": "family.json",
                },
            }
        ),
        encoding="utf-8",
    )


def _projected_library(tmp_path, *, plugin_id="demo-family", include_parameter=True):
    library = create_research_library(
        tmp_path,
        name="Legacy Search",
        plugin_id=plugin_id,
    )
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="claims.csv",
        data=(
            b"parameter,rank,notes\n"
            b"1/2,31,first\n"
            b"2/3,30,second\n"
        ),
        category="curve_candidate_data",
        media_type="text/csv",
    )
    roles = {
        "rank": "rank_claim",
        "notes": "notes",
    }
    if include_parameter:
        roles["parameter"] = "parameter"
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=["parameter", "rank", "notes"],
        roles=roles,
    )
    build_research_projection(tmp_path, library["id"], artifact["id"])
    return library, artifact


def test_projection_handoff_creates_unsearched_pool_with_claim_provenance_only(tmp_path):
    _write_family_plugin(tmp_path)
    library, artifact = _projected_library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        pool = create_candidate_pool_from_research_projection(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
            plugin_id="demo-family",
            variant_id="default",
            pool_name="library-pool-test",
        )

        assert int(pool["candidate_count"]) == 2
        generation = json.loads(pool["generation_json"])
        assert generation["source"] == "research_library_projection"
        assert generation["variant_id"] == "default"
        assert generation["rank_claim_policy"] == "provenance_only"
        assert generation["selection_count"] == 2

        rows = db.execute(
            "SELECT * FROM candidates WHERE pool_id=? ORDER BY rank_order",
            (int(pool["id"]),),
        ).fetchall()
        assert [row["parameter"] for row in rows] == ["1/2", "2/3"]
        assert all(str(row["status"]) == "unsearched" for row in rows)
        assert all(float(row["score"]) == 0.0 for row in rows)
        assert all(row["curve_id"] is None for row in rows)

        first_meta = json.loads(rows[0]["metadata_json"])
        provenance = first_meta["library_projection"]
        assert first_meta["ranking_kind"] == "library_order"
        assert first_meta["ranking_value"] == 1
        assert provenance["library_id"] == library["id"]
        assert provenance["artifact_id"] == artifact["id"]
        assert provenance["record_index"] == 1
        assert provenance["claims"] == {"rank_claim": "31"}
        assert provenance["notes"] == "first"

        assert db.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM points").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM rank_evidence").fetchone()[0] == 0
    finally:
        db.close()


def test_projection_handoff_respects_current_projection_filter(tmp_path):
    _write_family_plugin(tmp_path)
    library, artifact = _projected_library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        pool = create_candidate_pool_from_research_projection(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
            plugin_id="demo-family",
            variant_id="default",
            query="31",
            pool_name="library-filtered-pool",
        )
        rows = db.execute(
            "SELECT parameter,metadata_json FROM candidates WHERE pool_id=?",
            (int(pool["id"]),),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["parameter"] == "1/2"
        assert json.loads(rows[0]["metadata_json"])["library_projection"]["claims"] == {
            "rank_claim": "31"
        }
    finally:
        db.close()


def test_projection_handoff_requires_mapped_parameter_before_pool_creation(tmp_path):
    _write_family_plugin(tmp_path)
    library, artifact = _projected_library(tmp_path, include_parameter=False)
    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="missing mapped parameter"):
            create_candidate_pool_from_research_projection(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
                plugin_id="demo-family",
                variant_id="default",
                pool_name="should-not-exist",
            )
        assert db.execute("SELECT COUNT(*) FROM candidate_pools").fetchone()[0] == 0
    finally:
        db.close()


def test_projection_handoff_enforces_library_plugin_association(tmp_path):
    _write_family_plugin(tmp_path)
    library, artifact = _projected_library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        with pytest.raises(ValueError, match="associated with plugin"):
            create_candidate_pool_from_research_projection(
                db,
                tmp_path,
                library_id=library["id"],
                artifact_id=artifact["id"],
                plugin_id="some-other-plugin",
                variant_id="default",
            )
        assert db.execute("SELECT COUNT(*) FROM candidate_pools").fetchone()[0] == 0
    finally:
        db.close()


def test_deleting_library_after_handoff_does_not_delete_candidate_pool(tmp_path):
    from rank42.research_library import delete_research_library

    _write_family_plugin(tmp_path)
    library, artifact = _projected_library(tmp_path)
    db = connect(tmp_path / "rank42.db")
    try:
        pool = create_candidate_pool_from_research_projection(
            db,
            tmp_path,
            library_id=library["id"],
            artifact_id=artifact["id"],
            plugin_id="demo-family",
            variant_id="default",
            pool_name="survives-library-delete",
        )

        delete_research_library(
            tmp_path,
            library["id"],
            expected_name="Legacy Search",
        )

        surviving = db.execute(
            "SELECT * FROM candidate_pools WHERE id=?",
            (int(pool["id"]),),
        ).fetchone()
        assert surviving is not None
        assert int(surviving["candidate_count"]) == 2
        assert db.execute(
            "SELECT COUNT(*) FROM candidates WHERE pool_id=?",
            (int(pool["id"]),),
        ).fetchone()[0] == 2
    finally:
        db.close()
