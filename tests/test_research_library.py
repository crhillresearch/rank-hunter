from pathlib import Path

import pytest

from rank42.research_library import (
    LIBRARY_SCHEMA_VERSION,
    add_research_artifact,
    create_research_library,
    delete_research_library,
    library_dir,
    list_research_libraries,
    load_research_library,
    mapped_preview_rows,
    research_library_artifacts,
    research_library_mapping_draft,
    save_research_library_mapping_draft,
)


def test_research_library_vault_is_external_and_preserves_original_bytes(tmp_path):
    sentinel = tmp_path / "rank42.db"
    sentinel.write_bytes(b"authoritative-core-sentinel")

    library = create_research_library(
        tmp_path,
        name="Old Elkies experiments",
        description="Legacy material",
        plugin_id="elkies-demo",
    )
    assert library["schema_version"] == LIBRARY_SCHEMA_VERSION
    assert library["plugin_id"] == "elkies-demo"
    assert library_dir(tmp_path, library["id"]).parent == tmp_path / ".rank42-libraries"

    payload = b"rank,parameter\n18,562/193\n"
    artifact, created = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="../legacy/results.csv",
        data=payload,
        category="tabular_corpus",
        media_type="text/csv",
    )
    assert created is True
    assert artifact["original_name"] == "results.csv"
    assert artifact["category"] == "tabular_corpus"

    stored = library_dir(tmp_path, library["id"]) / artifact["stored_path"]
    assert stored.read_bytes() == payload
    assert sentinel.read_bytes() == b"authoritative-core-sentinel"

    duplicate, created_again = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="copy.csv",
        data=payload,
        category="unknown_raw",
    )
    assert created_again is False
    assert duplicate["sha256"] == artifact["sha256"]
    assert len(research_library_artifacts(tmp_path, library["id"])) == 1
    assert stored.read_bytes() == payload
    assert sentinel.read_bytes() == b"authoritative-core-sentinel"


def test_research_library_manifest_round_trip_and_listing(tmp_path):
    first = create_research_library(tmp_path, name="First")
    second = create_research_library(
        tmp_path,
        name="Second",
        description="notes",
    )

    loaded = load_research_library(tmp_path, second["id"])
    assert loaded["name"] == "Second"
    assert loaded["description"] == "notes"

    listed = list_research_libraries(tmp_path)
    assert {row["id"] for row in listed} == {first["id"], second["id"]}
    assert all(Path(tmp_path / ".rank42-libraries" / row["id"]).is_dir() for row in listed)


def test_research_library_rejects_unknown_artifact_category(tmp_path):
    library = create_research_library(tmp_path, name="Safety")
    with pytest.raises(ValueError, match="artifact category"):
        add_research_artifact(
            tmp_path,
            library["id"],
            original_name="claim.txt",
            data=b"rank = 99",
            category="rigorous_rank_truth",
        )


def test_mapping_draft_is_descriptive_manifest_metadata_only(tmp_path):
    sentinel = tmp_path / "rank42.db"
    sentinel.write_bytes(b"core-db-unchanged")

    library = create_research_library(tmp_path, name="Mappings")
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="claims.csv",
        data=b"t,rank,notes\n1/2,31,old claim\n",
        category="tabular_corpus",
    )

    draft = save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=["t", "rank", "notes"],
        roles={
            "t": "parameter",
            "rank": "rank_claim",
            "notes": "notes",
        },
    )

    assert draft["roles"] == {
        "t": "parameter",
        "rank": "rank_claim",
        "notes": "notes",
    }
    assert research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
    )["roles"]["rank"] == "rank_claim"
    assert mapped_preview_rows(
        [{"t": "1/2", "rank": "31", "notes": "old claim"}],
        draft,
    ) == [
        {
            "parameter": "1/2",
            "rank_claim": "31",
            "notes": "old claim",
        }
    ]

    manifest = load_research_library(tmp_path, library["id"])
    assert artifact["id"] in manifest["mapping_drafts"]
    assert sentinel.read_bytes() == b"core-db-unchanged"


def test_mapping_draft_rejects_duplicate_semantic_role(tmp_path):
    library = create_research_library(tmp_path, name="Mapping Safety")
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="points.csv",
        data=b"x,y\n1,2\n",
        category="points",
    )

    with pytest.raises(ValueError, match="may be assigned only once"):
        save_research_library_mapping_draft(
            tmp_path,
            library["id"],
            artifact["id"],
            parser="delimited_text",
            columns=["x", "y"],
            roles={"x": "parameter", "y": "parameter"},
        )


def test_mapping_draft_rejects_unknown_scientific_role(tmp_path):
    library = create_research_library(tmp_path, name="Mapping Safety")
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="claims.csv",
        data=b"rank\n31\n",
        category="tabular_corpus",
    )

    with pytest.raises(ValueError, match="unsupported Library mapping role"):
        save_research_library_mapping_draft(
            tmp_path,
            library["id"],
            artifact["id"],
            parser="delimited_text",
            columns=["rank"],
            roles={"rank": "rigorous_rank"},
        )


def test_delete_research_library_requires_exact_name_and_stays_scoped(tmp_path):
    keep = create_research_library(tmp_path, name="Keep Me")
    doomed = create_research_library(tmp_path, name="Delete Me")
    add_research_artifact(
        tmp_path,
        doomed["id"],
        original_name="notes.txt",
        data=b"legacy material",
        category="notes_reference",
    )

    with pytest.raises(ValueError, match="confirmation"):
        delete_research_library(
            tmp_path,
            doomed["id"],
            expected_name="wrong",
        )

    doomed_dir = library_dir(tmp_path, doomed["id"])
    keep_dir = library_dir(tmp_path, keep["id"])
    assert doomed_dir.is_dir()
    assert keep_dir.is_dir()

    assert delete_research_library(
        tmp_path,
        doomed["id"],
        expected_name="Delete Me",
    ) is True

    assert not doomed_dir.exists()
    assert keep_dir.is_dir()
    assert load_research_library(tmp_path, keep["id"])["name"] == "Keep Me"


def test_delete_research_library_refuses_symlink_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "library.json").write_text(
        '{"schema_version":1,"id":"evil","name":"Evil","artifacts":[]}',
        encoding="utf-8",
    )
    root = tmp_path / ".rank42-libraries"
    root.mkdir()
    link = root / "evil"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="symlinked"):
        delete_research_library(
            tmp_path,
            "evil",
            expected_name="Evil",
        )

    assert outside.is_dir()
    assert (outside / "library.json").is_file()
