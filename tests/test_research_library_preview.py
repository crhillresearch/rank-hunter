import io
import json
import zipfile

from rank42.research_library import (
    add_research_artifact,
    create_research_library,
    library_dir,
)
from rank42.research_library_preview import preview_research_artifact


def _library(tmp_path):
    return create_research_library(tmp_path, name="Preview Test")


def _minimal_xlsx_bytes():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Curves" sheetId="1" r:id="rId1"/></sheets>
            </workbook>""",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="inlineStr"><is><t>rank</t></is></c>
                  <c r="B1" t="inlineStr"><is><t>parameter</t></is></c>
                </row>
                <row r="2">
                  <c r="A2"><v>18</v></c>
                  <c r="B2" t="inlineStr"><is><t>562/193</t></is></c>
                </row>
              </sheetData>
            </worksheet>""",
        )
    return out.getvalue()


def test_csv_preview_is_bounded_and_does_not_promote_claims(tmp_path):
    library = _library(tmp_path)
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="claims.csv",
        data=b"rank,parameter\n31,1/2\n30,2/3\n",
        category="tabular_corpus",
        media_type="text/csv",
    )

    preview = preview_research_artifact(
        tmp_path,
        library["id"],
        artifact["id"],
        row_limit=1,
    )

    assert preview["status"] == "ok"
    assert preview["parser"] == "delimited_text"
    assert preview["columns"] == ["rank", "parameter"]
    assert preview["rows"] == [{"rank": "31", "parameter": "1/2"}]
    assert preview["meta"]["size_bytes"] > 0


def test_jsonl_preview_reports_parse_errors_without_failing_library(tmp_path):
    library = _library(tmp_path)
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="old-search.jsonl",
        data=b'{"t":"1/2","rank":18}\nnot-json\n{"t":"2/3","rank":19}\n',
        category="logs_output",
    )

    preview = preview_research_artifact(
        tmp_path,
        library["id"],
        artifact["id"],
    )

    assert preview["status"] == "ok"
    assert preview["parser"] == "jsonl"
    assert [row["t"] for row in preview["rows"]] == ["1/2", "2/3"]
    assert len(preview["meta"]["parse_errors"]) == 1
    assert preview["meta"]["parse_errors"][0].startswith("line 2:")


def test_xlsx_preview_uses_stdlib_and_lists_sheet(tmp_path):
    library = _library(tmp_path)
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="legacy.xlsx",
        data=_minimal_xlsx_bytes(),
        category="tabular_corpus",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    preview = preview_research_artifact(
        tmp_path,
        library["id"],
        artifact["id"],
    )

    assert preview["status"] == "ok"
    assert preview["parser"] == "xlsx"
    assert preview["meta"]["sheets"] == ["Curves"]
    assert preview["meta"]["preview_sheet"] == "Curves"
    assert preview["rows"][0] == {"column_1": "rank", "column_2": "parameter"}
    assert preview["rows"][1] == {"column_1": "18", "column_2": "562/193"}


def test_preview_refuses_tampered_or_oversized_originals(tmp_path):
    library = _library(tmp_path)
    artifact, _ = add_research_artifact(
        tmp_path,
        library["id"],
        original_name="notes.txt",
        data=b"original notes",
        category="notes_reference",
    )
    stored = library_dir(tmp_path, library["id"]) / artifact["stored_path"]

    oversized = preview_research_artifact(
        tmp_path,
        library["id"],
        artifact["id"],
        max_bytes=4,
    )
    assert oversized["status"] == "too_large"

    stored.write_bytes(b"tampered")
    tampered = preview_research_artifact(
        tmp_path,
        library["id"],
        artifact["id"],
    )
    assert tampered["status"] == "error"
    assert "hash verification failed" in tampered["meta"]["error"]
