from rank42.research_library import (
    add_research_artifact,
    create_research_library,
    save_research_library_mapping_draft,
)
from rank42.research_library_projection import (
    build_research_projection,
    count_research_projection_records,
    research_projection_inventory,
    research_projection_path,
    research_projection_status,
    search_research_projection,
)


def _csv_library(tmp_path):
    library = create_research_library(tmp_path, name="Projection Test")
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="claims.csv",
        data=(
            b"parameter,rank,notes\n"
            b"1/2,31,old record\n"
            b"2/3,30,second record\n"
        ),
        category="tabular_corpus",
        media_type="text/csv",
    )
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=["parameter", "rank", "notes"],
        roles={
            "parameter": "parameter",
            "rank": "rank_claim",
            "notes": "notes",
        },
    )
    return library, artifact


def test_external_projection_is_searchable_and_never_touches_core_db(tmp_path):
    sentinel = tmp_path / "rank42.db"
    sentinel.write_bytes(b"core-db-stays-unchanged")
    library, artifact = _csv_library(tmp_path)

    result = build_research_projection(
        tmp_path,
        library["id"],
        artifact["id"],
    )

    projection_path = research_projection_path(tmp_path, library["id"])
    assert projection_path.is_file()
    assert projection_path.parent == tmp_path / ".rank42-libraries" / library["id"]
    assert result["record_count"] == 2
    assert sentinel.read_bytes() == b"core-db-stays-unchanged"

    status = research_projection_status(
        tmp_path,
        library["id"],
        artifact["id"],
    )
    assert status["exists"] is True
    assert status["stale"] is False
    assert status["record_count"] == 2

    assert count_research_projection_records(
        tmp_path,
        library["id"],
        artifact_id=artifact["id"],
        query="31",
    ) == 1
    rows = search_research_projection(
        tmp_path,
        library["id"],
        artifact_id=artifact["id"],
        query="old record",
    )
    assert len(rows) == 1
    assert rows[0]["parameter"] == "1/2"
    assert rows[0]["rank_claim"] == "31"
    assert rows[0]["exact_rank_claim"] is None
    assert sentinel.read_bytes() == b"core-db-stays-unchanged"


def test_mapping_change_marks_projection_stale_until_rebuilt(tmp_path):
    library, artifact = _csv_library(tmp_path)
    build_research_projection(tmp_path, library["id"], artifact["id"])

    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="delimited_text",
        columns=["parameter", "rank", "notes"],
        roles={
            "parameter": "parameter",
            "rank": "exact_rank_claim",
            "notes": "notes",
        },
    )

    stale = research_projection_status(
        tmp_path,
        library["id"],
        artifact["id"],
    )
    assert stale["exists"] is True
    assert stale["stale"] is True

    build_research_projection(tmp_path, library["id"], artifact["id"])
    current = research_projection_status(
        tmp_path,
        library["id"],
        artifact["id"],
    )
    assert current["stale"] is False

    rows = search_research_projection(
        tmp_path,
        library["id"],
        artifact_id=artifact["id"],
        query="31",
    )
    assert rows[0]["rank_claim"] is None
    assert rows[0]["exact_rank_claim"] == "31"


def test_projection_refuses_malformed_jsonl_instead_of_dropping_lines(tmp_path):
    library = create_research_library(tmp_path, name="Malformed Projection")
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="mixed.jsonl",
        data=b'{"t":"1/2","rank":18}\nnot-json\n{"t":"2/3","rank":19}\n',
        category="curve_candidate_data",
    )
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="jsonl",
        columns=["t", "rank"],
        roles={"t": "parameter", "rank": "rank_claim"},
    )

    try:
        build_research_projection(tmp_path, library["id"], artifact["id"])
    except ValueError as exc:
        assert "refused malformed structured input" in str(exc)
        assert "line 2:" in str(exc)
    else:
        raise AssertionError("malformed JSONL must not be projected")

    assert research_projection_status(
        tmp_path,
        library["id"],
        artifact["id"],
    )["exists"] is False


def test_projection_refuses_parser_mismatch(tmp_path):
    library, artifact = _csv_library(tmp_path)
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        artifact["id"],
        parser="json",
        columns=["parameter", "rank", "notes"],
        roles={"parameter": "parameter"},
    )

    try:
        build_research_projection(tmp_path, library["id"], artifact["id"])
    except ValueError as exc:
        assert "does not match artifact parser" in str(exc)
    else:
        raise AssertionError("stale/mismatched parser mapping must be refused")


def test_library_wide_inventory_and_semantic_filters_exclude_stale_projection(tmp_path):
    library, claims = _csv_library(tmp_path)
    points, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="points.csv",
        data=b"x,y,notes\n1/2,3/4,point one\n5/6,7/8,point two\n",
        category="points",
        media_type="text/csv",
    )
    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        points["id"],
        parser="delimited_text",
        columns=["x", "y", "notes"],
        roles={"x": "x", "y": "y", "notes": "notes"},
    )
    build_research_projection(tmp_path, library["id"], claims["id"])
    build_research_projection(tmp_path, library["id"], points["id"])

    inventory = research_projection_inventory(tmp_path, library["id"])
    by_id = {row["artifact_id"]: row for row in inventory}
    assert by_id[claims["id"]]["projection_state"] == "current"
    assert by_id[claims["id"]]["record_count"] == 2
    assert by_id[claims["id"]]["mapping_roles"] == [
        "notes",
        "parameter",
        "rank_claim",
    ]
    assert by_id[points["id"]]["projection_state"] == "current"
    assert by_id[points["id"]]["category"] == "points"
    assert by_id[points["id"]]["record_count"] == 2

    current_ids = [
        row["artifact_id"]
        for row in inventory
        if row["projection_state"] == "current"
    ]
    assert count_research_projection_records(
        tmp_path,
        library["id"],
        artifact_ids=current_ids,
    ) == 4
    point_rows = search_research_projection(
        tmp_path,
        library["id"],
        artifact_ids=current_ids,
        roles=["x", "y"],
    )
    assert len(point_rows) == 2
    assert {row["x"] for row in point_rows} == {"1/2", "5/6"}
    assert all(row["raw_json"] for row in point_rows)

    rank_rows = search_research_projection(
        tmp_path,
        library["id"],
        artifact_ids=current_ids,
        roles=["rank_claim", "exact_rank_claim"],
        query="31",
    )
    assert len(rank_rows) == 1
    assert rank_rows[0]["artifact_id"] == claims["id"]
    assert rank_rows[0]["rank_claim"] == "31"

    save_research_library_mapping_draft(
        tmp_path,
        library["id"],
        claims["id"],
        parser="delimited_text",
        columns=["parameter", "rank", "notes"],
        roles={"parameter": "parameter", "rank": "exact_rank_claim"},
    )
    inventory = research_projection_inventory(tmp_path, library["id"])
    by_id = {row["artifact_id"]: row for row in inventory}
    assert by_id[claims["id"]]["projection_state"] == "stale"
    assert by_id[points["id"]]["projection_state"] == "current"

    current_ids = [
        row["artifact_id"]
        for row in inventory
        if row["projection_state"] == "current"
    ]
    assert current_ids == [points["id"]]
    assert count_research_projection_records(
        tmp_path,
        library["id"],
        artifact_ids=current_ids,
    ) == 2
    assert search_research_projection(
        tmp_path,
        library["id"],
        artifact_ids=current_ids,
        roles=["rank_claim", "exact_rank_claim"],
    ) == []


def test_library_wide_projection_filters_validate_role_and_empty_artifact_scope(tmp_path):
    library, artifact = _csv_library(tmp_path)
    build_research_projection(tmp_path, library["id"], artifact["id"])

    assert count_research_projection_records(
        tmp_path,
        library["id"],
        artifact_ids=[],
    ) == 0
    assert search_research_projection(
        tmp_path,
        library["id"],
        artifact_ids=[],
    ) == []

    try:
        search_research_projection(
            tmp_path,
            library["id"],
            roles=["rigorous_rank"],
        )
    except ValueError as exc:
        assert "unsupported projection role filter" in str(exc)
    else:
        raise AssertionError("browser role filter must reject unknown scientific roles")
