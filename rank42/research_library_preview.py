"""Read-only previews for researcher Library artifacts.

Preview parsers inspect immutable external artifacts only. They do not write
normalized projections, rank42.db, Candidate Pools, Curves, Points, or proof
state.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

from rank42.research_library import library_dir, load_research_library


DEFAULT_PREVIEW_ROWS = 25
DEFAULT_TEXT_CHARS = 12000
DEFAULT_MAX_BYTES = 25 * 1024 * 1024

_TEXT_SUFFIXES = {
    ".txt", ".log", ".md", ".rst", ".sage", ".py", ".m", ".gp",
    ".out", ".dat", ".tex",
}


def _artifact_record(project_root, library_id, artifact_id):
    manifest = load_research_library(project_root, library_id)
    wanted = str(artifact_id)
    for artifact in manifest["artifacts"]:
        if str(artifact.get("id") or "") == wanted:
            return artifact
    raise KeyError(f"research library artifact {artifact_id!r} not found")


def _verified_bytes(project_root, library_id, artifact, *, max_bytes):
    expected = str(artifact.get("sha256") or "")
    stored_path = Path(str(artifact.get("stored_path") or ""))
    if (
        not expected
        or stored_path.is_absolute()
        or stored_path.parts != ("originals", expected)
    ):
        raise ValueError("research library artifact stored path is invalid")

    path = library_dir(project_root, library_id) / stored_path
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size > int(max_bytes):
        return None, size

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise ValueError("research library artifact hash verification failed")
    return raw, size


def _decode_text(raw):
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _csv_preview(raw, *, suffix, row_limit):
    text, encoding = _decode_text(raw)
    sample = text[:8192]
    default_delimiter = "\t" if suffix == ".tsv" else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = default_delimiter

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for index, row in enumerate(reader):
        if index >= int(row_limit) + 1:
            break
        rows.append([str(value) for value in row])

    if not rows:
        return {
            "parser": "delimited_text",
            "rows": [],
            "columns": [],
            "meta": {
                "encoding": encoding,
                "delimiter": delimiter,
                "header": False,
            },
        }

    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = True

    width = max(len(row) for row in rows)
    if has_header:
        raw_columns = rows[0]
        columns = []
        seen = set()
        for index in range(width):
            label = (
                raw_columns[index].strip()
                if index < len(raw_columns) and raw_columns[index].strip()
                else f"column_{index + 1}"
            )
            base = label
            counter = 2
            while label in seen:
                label = f"{base}_{counter}"
                counter += 1
            seen.add(label)
            columns.append(label)
        data_rows = rows[1:]
    else:
        columns = [f"column_{index + 1}" for index in range(width)]
        data_rows = rows

    records = []
    for row in data_rows[: int(row_limit)]:
        padded = list(row) + [""] * (width - len(row))
        records.append(dict(zip(columns, padded)))

    return {
        "parser": "delimited_text",
        "rows": records,
        "columns": columns,
        "meta": {
            "encoding": encoding,
            "delimiter": "\\t" if delimiter == "\t" else delimiter,
            "header": bool(has_header),
        },
    }


def _json_rows(value, *, row_limit):
    if isinstance(value, list):
        preview = value[: int(row_limit)]
        if all(isinstance(item, dict) for item in preview):
            columns = []
            seen = set()
            for item in preview:
                for key in item:
                    key = str(key)
                    if key not in seen:
                        seen.add(key)
                        columns.append(key)
            return preview, columns
        return [{"value": item} for item in preview], ["value"]

    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, list) and child and all(
                isinstance(item, dict) for item in child[: int(row_limit)]
            ):
                rows, columns = _json_rows(child, row_limit=row_limit)
                return rows, columns
        return [value], [str(key) for key in value]

    return [{"value": value}], ["value"]


def _json_preview(raw, *, row_limit):
    text, encoding = _decode_text(raw)
    value = json.loads(text)
    rows, columns = _json_rows(value, row_limit=row_limit)
    shape = (
        "object"
        if isinstance(value, dict)
        else "array"
        if isinstance(value, list)
        else type(value).__name__
    )
    return {
        "parser": "json",
        "rows": rows,
        "columns": columns,
        "meta": {
            "encoding": encoding,
            "root_type": shape,
            "root_count": len(value) if isinstance(value, (dict, list)) else 1,
        },
    }


def _jsonl_preview(raw, *, row_limit):
    text, encoding = _decode_text(raw)
    rows = []
    errors = []
    nonblank = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        nonblank += 1
        if len(rows) >= int(row_limit):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: {exc.msg}")
            continue
        rows.append(value if isinstance(value, dict) else {"value": value})

    columns = []
    seen = set()
    for row in rows:
        for key in row:
            key = str(key)
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return {
        "parser": "jsonl",
        "rows": rows,
        "columns": columns,
        "meta": {
            "encoding": encoding,
            "nonblank_lines": nonblank,
            "parse_errors": errors[:10],
        },
    }


_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XLSX_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _xlsx_shared_strings(zf):
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in root.findall(f"{{{_XLSX_MAIN_NS}}}si"):
        parts = [
            node.text or ""
            for node in item.iter(f"{{{_XLSX_MAIN_NS}}}t")
        ]
        values.append("".join(parts))
    return values


def _xlsx_sheet_targets(zf):
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib.get("Id"): rel.attrib.get("Target")
        for rel in rels.findall(f"{{{_XLSX_PACKAGE_REL_NS}}}Relationship")
    }
    sheets = []
    sheets_node = workbook.find(f"{{{_XLSX_MAIN_NS}}}sheets")
    if sheets_node is None:
        return sheets
    for sheet in sheets_node.findall(f"{{{_XLSX_MAIN_NS}}}sheet"):
        rel_id = sheet.attrib.get(f"{{{_XLSX_REL_NS}}}id")
        target = targets.get(rel_id)
        if not target:
            continue
        if target.startswith("/"):
            archive_path = target.lstrip("/")
        else:
            archive_path = str(Path("xl") / target)
        sheets.append((str(sheet.attrib.get("name") or "Sheet"), archive_path))
    return sheets


def _xlsx_col_index(cell_ref):
    match = re.match(r"([A-Z]+)", str(cell_ref or "").upper())
    if not match:
        return None
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - 64)
    return value - 1


def _xlsx_cell_value(cell, shared):
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(
            node.text or ""
            for node in cell.iter(f"{{{_XLSX_MAIN_NS}}}t")
        )
    value_node = cell.find(f"{{{_XLSX_MAIN_NS}}}v")
    if value_node is None:
        return ""
    value = value_node.text or ""
    if cell_type == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _xlsx_preview(raw, *, row_limit):
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        sheets = _xlsx_sheet_targets(zf)
        if not sheets:
            raise ValueError("XLSX workbook has no readable sheets")
        shared = _xlsx_shared_strings(zf)
        sheet_name, sheet_path = sheets[0]
        root = ET.fromstring(zf.read(sheet_path))

        matrix = []
        max_width = 0
        for row_node in root.iter(f"{{{_XLSX_MAIN_NS}}}row"):
            values = {}
            for cell in row_node.findall(f"{{{_XLSX_MAIN_NS}}}c"):
                column = _xlsx_col_index(cell.attrib.get("r"))
                if column is None:
                    continue
                values[column] = _xlsx_cell_value(cell, shared)
                max_width = max(max_width, column + 1)
            row = [values.get(index, "") for index in range(max_width)]
            matrix.append(row)
            if len(matrix) >= int(row_limit):
                break

    columns = [f"column_{index + 1}" for index in range(max_width)]
    records = []
    for row in matrix:
        padded = list(row) + [""] * (max_width - len(row))
        records.append(dict(zip(columns, padded)))
    return {
        "parser": "xlsx",
        "rows": records,
        "columns": columns,
        "meta": {
            "sheets": [name for name, _path in sheets],
            "preview_sheet": sheet_name,
        },
    }


def _text_preview(raw, *, text_limit):
    text, encoding = _decode_text(raw)
    clipped = text[: int(text_limit)]
    return {
        "parser": "text",
        "text": clipped,
        "meta": {
            "encoding": encoding,
            "characters": len(text),
            "lines": len(text.splitlines()),
            "truncated": len(clipped) < len(text),
        },
    }


def structured_research_artifact_rows(
    project_root,
    library_id,
    artifact_id,
    *,
    row_limit,
    max_bytes,
):
    """Return bounded structured rows after immutable-byte verification.

    This is the read side used by external Library projections. Text and other
    unstructured artifacts are intentionally rejected here.
    """

    artifact = _artifact_record(project_root, library_id, artifact_id)
    raw, size = _verified_bytes(
        project_root,
        library_id,
        artifact,
        max_bytes=max_bytes,
    )
    if raw is None:
        raise ValueError(
            f"artifact size {int(size)} exceeds projection limit {int(max_bytes)}"
        )

    suffix = Path(str(artifact["original_name"])).suffix.lower()
    limit = max(1, int(row_limit))
    if suffix in {".csv", ".tsv"}:
        parsed = _csv_preview(raw, suffix=suffix, row_limit=limit)
    elif suffix == ".json":
        parsed = _json_preview(raw, row_limit=limit)
    elif suffix in {".jsonl", ".ndjson"}:
        parsed = _jsonl_preview(raw, row_limit=limit)
    elif suffix == ".xlsx":
        parsed = _xlsx_preview(raw, row_limit=limit)
    else:
        raise ValueError("artifact has no structured projection parser")

    parsed = dict(parsed)
    parsed["size_bytes"] = int(size)
    return parsed


def preview_research_artifact(
    project_root,
    library_id,
    artifact_id,
    *,
    row_limit=DEFAULT_PREVIEW_ROWS,
    text_limit=DEFAULT_TEXT_CHARS,
    max_bytes=DEFAULT_MAX_BYTES,
):
    """Return a bounded, read-only preview for one immutable artifact."""

    artifact = _artifact_record(project_root, library_id, artifact_id)
    base = {
        "artifact_id": str(artifact["id"]),
        "original_name": str(artifact["original_name"]),
        "category": str(artifact["category"]),
        "media_type": str(artifact.get("media_type") or ""),
        "status": "ok",
        "rows": [],
        "columns": [],
        "text": None,
        "meta": {},
    }

    try:
        raw, size = _verified_bytes(
            project_root,
            library_id,
            artifact,
            max_bytes=max_bytes,
        )
        if raw is None:
            base.update({
                "status": "too_large",
                "parser": "none",
                "meta": {
                    "size_bytes": int(size),
                    "max_preview_bytes": int(max_bytes),
                },
            })
            return base

        suffix = Path(base["original_name"]).suffix.lower()
        media_type = base["media_type"].lower()
        if suffix in {".csv", ".tsv"}:
            parsed = _csv_preview(raw, suffix=suffix, row_limit=row_limit)
        elif suffix == ".json":
            parsed = _json_preview(raw, row_limit=row_limit)
        elif suffix in {".jsonl", ".ndjson"}:
            parsed = _jsonl_preview(raw, row_limit=row_limit)
        elif suffix == ".xlsx":
            parsed = _xlsx_preview(raw, row_limit=row_limit)
        elif suffix in _TEXT_SUFFIXES or media_type.startswith("text/"):
            parsed = _text_preview(raw, text_limit=text_limit)
        else:
            base.update({
                "status": "unsupported",
                "parser": "none",
                "meta": {"size_bytes": int(size)},
            })
            return base

        base.update(parsed)
        base["meta"] = {
            "size_bytes": int(size),
            **dict(parsed.get("meta") or {}),
        }
        return base
    except Exception as exc:
        base.update({
            "status": "error",
            "parser": "none",
            "meta": {
                "error": f"{type(exc).__name__}: {exc}",
            },
        })
        return base
