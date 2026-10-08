"""Filesystem-backed researcher Libraries.

Researcher Libraries are external research material. They deliberately live
outside rank42.db and preserve original artifact bytes immutably. Normalized
projections and scientific promotion are separate, explicit later workflows.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
import uuid


LIBRARY_SCHEMA_VERSION = 1
LIBRARY_CATEGORIES = (
    "notes_reference",
    "tabular_corpus",
    "curve_candidate_data",
    "points",
    "certificate_proof",
    "logs_output",
    "code_script",
    "unknown_raw",
)
LIBRARY_CATEGORY_LABELS = {
    "notes_reference": "Notes / reference",
    "tabular_corpus": "Tabular corpus",
    "curve_candidate_data": "Curve / candidate data",
    "points": "Points",
    "certificate_proof": "Certificate / proof artifact",
    "logs_output": "Logs / output",
    "code_script": "Code / script",
    "unknown_raw": "Unknown / raw",
}

LIBRARY_MAPPING_ROLES = (
    "parameter",
    "family_label",
    "curve_label",
    "a1",
    "a2",
    "a3",
    "a4",
    "a6",
    "x",
    "y",
    "rank_claim",
    "rank_lower_claim",
    "rank_upper_claim",
    "exact_rank_claim",
    "source",
    "notes",
)
LIBRARY_MAPPING_ROLE_LABELS = {
    "parameter": "Parameter",
    "family_label": "Family label",
    "curve_label": "Curve label",
    "a1": "a1",
    "a2": "a2",
    "a3": "a3",
    "a4": "a4",
    "a6": "a6",
    "x": "Point x",
    "y": "Point y",
    "rank_claim": "Rank claim",
    "rank_lower_claim": "Rank lower-bound claim",
    "rank_upper_claim": "Rank upper-bound claim",
    "exact_rank_claim": "Exact-rank claim",
    "source": "Source / provenance",
    "notes": "Notes",
}
_LIBRARY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def _now():
    return datetime.now(timezone.utc).isoformat()


def library_root(project_root):
    return Path(project_root).resolve() / ".rank42-libraries"


def _safe_library_id(value):
    library_id = str(value or "").strip().lower()
    if not _LIBRARY_ID_RE.fullmatch(library_id):
        raise ValueError(f"invalid research library id {value!r}")
    return library_id


def library_dir(project_root, library_id):
    library_id = _safe_library_id(library_id)
    root = library_root(project_root)
    return root / library_id


def library_manifest_path(project_root, library_id):
    return library_dir(project_root, library_id) / "library.json"


def _atomic_json_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _library_slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug[:48] or "library"


def create_research_library(
    project_root,
    *,
    name,
    description="",
    plugin_id=None,
):
    name = str(name or "").strip()
    if not name:
        raise ValueError("library name is required")
    slug = _library_slug(name)
    library_id = f"{slug}-{uuid.uuid4().hex[:8]}"
    ts = _now()
    manifest = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "id": library_id,
        "name": name,
        "description": str(description or "").strip(),
        "plugin_id": str(plugin_id).strip() if plugin_id else None,
        "created_at": ts,
        "updated_at": ts,
        "artifacts": [],
        "mapping_drafts": {},
    }
    directory = library_dir(project_root, library_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "originals").mkdir()
    _atomic_json_write(directory / "library.json", manifest)
    return manifest


def load_research_library(project_root, library_id):
    path = library_manifest_path(project_root, library_id)
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("research library manifest must be a JSON object")
    if int(payload.get("schema_version") or 0) != LIBRARY_SCHEMA_VERSION:
        raise ValueError(
            "unsupported research library schema_version "
            f"{payload.get('schema_version')!r}; expected {LIBRARY_SCHEMA_VERSION}"
        )
    if str(payload.get("id") or "") != _safe_library_id(library_id):
        raise ValueError("research library manifest id does not match its directory")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("research library artifacts must be a list")
    mapping_drafts = payload.get("mapping_drafts", {})
    if not isinstance(mapping_drafts, dict):
        raise ValueError("research library mapping_drafts must be an object")
    payload["mapping_drafts"] = mapping_drafts
    return payload


def list_research_libraries(project_root):
    root = library_root(project_root)
    if not root.is_dir():
        return []
    libraries = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "library.json").is_file():
            continue
        try:
            manifest = load_research_library(project_root, child.name)
        except Exception:
            continue
        libraries.append(manifest)
    libraries.sort(
        key=lambda row: (
            str(row.get("updated_at") or ""),
            str(row.get("name") or ""),
        ),
        reverse=True,
    )
    return libraries


def _artifact_category(category):
    category = str(category or "").strip()
    if category not in LIBRARY_CATEGORIES:
        raise ValueError(
            "artifact category must be one of: " + ", ".join(LIBRARY_CATEGORIES)
        )
    return category


def add_research_artifact(
    project_root,
    library_id,
    *,
    original_name,
    data,
    category="unknown_raw",
    media_type=None,
):
    category = _artifact_category(category)
    raw = bytes(data)
    original_name = Path(str(original_name or "artifact")).name or "artifact"
    digest = hashlib.sha256(raw).hexdigest()
    manifest = load_research_library(project_root, library_id)

    for artifact in manifest["artifacts"]:
        if str(artifact.get("sha256") or "") == digest:
            return artifact, False

    originals = library_dir(project_root, library_id) / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    stored = originals / digest
    if stored.exists():
        if stored.read_bytes() != raw:
            raise ValueError("research library content hash collision")
    else:
        temp = originals / f".{digest}.tmp"
        temp.write_bytes(raw)
        temp.replace(stored)

    guessed_type = mimetypes.guess_type(original_name)[0]
    ts = _now()
    artifact = {
        "id": digest,
        "original_name": original_name,
        "sha256": digest,
        "size_bytes": len(raw),
        "media_type": str(media_type or guessed_type or "application/octet-stream"),
        "category": category,
        "stored_path": f"originals/{digest}",
        "added_at": ts,
    }
    manifest["artifacts"].append(artifact)
    manifest["updated_at"] = ts
    _atomic_json_write(library_manifest_path(project_root, library_id), manifest)
    return artifact, True


def research_library_artifacts(project_root, library_id):
    manifest = load_research_library(project_root, library_id)
    return list(manifest["artifacts"])


def delete_research_library(project_root, library_id, *, expected_name):
    """Permanently delete one researcher Library directory.

    The target must be a real direct child of .rank42-libraries and the caller
    must confirm the exact current Library name.
    """

    library_id = _safe_library_id(library_id)
    manifest = load_research_library(project_root, library_id)
    actual_name = str(manifest.get("name") or "")
    if str(expected_name or "") != actual_name:
        raise ValueError("Library name confirmation does not match")

    root = library_root(project_root).resolve()
    target = root / library_id
    if target.parent != root:
        raise ValueError("research Library delete target escaped Library root")
    if target.is_symlink():
        raise ValueError("refusing to delete symlinked research Library")
    if not target.is_dir():
        raise FileNotFoundError(target)

    manifest_path = target / "library.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("research Library manifest is missing or unsafe")

    shutil.rmtree(target)
    return True


def research_library_mapping_draft(project_root, library_id, artifact_id):
    manifest = load_research_library(project_root, library_id)
    draft = dict(manifest.get("mapping_drafts") or {}).get(str(artifact_id))
    return dict(draft) if isinstance(draft, dict) else None


def save_research_library_mapping_draft(
    project_root,
    library_id,
    artifact_id,
    *,
    parser,
    columns,
    roles,
):
    """Save descriptive field roles for one artifact without materializing data."""

    manifest = load_research_library(project_root, library_id)
    artifact_id = str(artifact_id)
    artifact_ids = {
        str(artifact.get("id") or "")
        for artifact in manifest["artifacts"]
    }
    if artifact_id not in artifact_ids:
        raise KeyError(f"research library artifact {artifact_id!r} not found")

    normalized_columns = [str(column) for column in columns]
    if not normalized_columns or len(set(normalized_columns)) != len(normalized_columns):
        raise ValueError("mapping draft columns must be nonempty and unique")

    normalized_roles = {}
    used_roles = set()
    for source_column, role in dict(roles or {}).items():
        source_column = str(source_column)
        role = str(role or "").strip()
        if source_column not in normalized_columns:
            raise ValueError(
                f"mapping draft references unavailable column {source_column!r}"
            )
        if not role:
            continue
        if role not in LIBRARY_MAPPING_ROLES:
            raise ValueError(f"unsupported Library mapping role {role!r}")
        if role in used_roles:
            raise ValueError(
                f"Library mapping role {role!r} may be assigned only once"
            )
        used_roles.add(role)
        normalized_roles[source_column] = role

    ts = _now()
    draft = {
        "artifact_id": artifact_id,
        "parser": str(parser or ""),
        "columns": normalized_columns,
        "roles": normalized_roles,
        "updated_at": ts,
    }
    manifest.setdefault("mapping_drafts", {})[artifact_id] = draft
    manifest["updated_at"] = ts
    _atomic_json_write(library_manifest_path(project_root, library_id), manifest)
    return dict(draft)


def mapped_preview_rows(rows, draft, *, limit=25):
    """Project preview rows through a saved draft; this remains display-only."""

    if not draft:
        return []
    roles = dict(draft.get("roles") or {})
    result = []
    for row in list(rows or [])[: max(0, int(limit))]:
        if not isinstance(row, dict):
            continue
        mapped = {}
        for source_column, role in roles.items():
            mapped[str(role)] = row.get(source_column)
        result.append(mapped)
    return result
