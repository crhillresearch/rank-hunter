"""Legacy/interchange JSONL helpers for Rank Hunter candidate pools.

Rank Hunter v0.8 stores authoritative candidate pools in SQLite.  These helpers
remain for reproducible JSONL import/export and compatibility with older CLI
workflows; they are not the application's primary candidate state.
"""

from __future__ import annotations

from pathlib import Path
import math


CANDIDATE_DIRNAME = "candidates"


def _log_conductor(value):
    try:
        n = int(str(value))
        return math.log(n) if n > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def candidate_directory(project_root: str | Path) -> Path:
    path = Path(project_root).resolve() / CANDIDATE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_candidate_jsonls(project_root: str | Path) -> list[Path]:
    """Return project candidate pools, newest first, then by name."""
    root = candidate_directory(project_root)
    paths = [p for p in root.glob("*.jsonl") if p.is_file()]
    return sorted(paths, key=lambda p: (p.stat().st_mtime_ns, p.name.lower()), reverse=True)


def candidate_output_path(project_root: str | Path, filename: str) -> Path:
    """Resolve a user-supplied output *name* inside ``candidates/``.

    Directory components are intentionally discarded.  This keeps generated
    pools in the managed candidate directory and prevents accidental writes to
    unrelated project files.
    """
    name = Path(str(filename).strip()).name
    if not name:
        raise ValueError("candidate output filename is empty")
    if not name.lower().endswith(".jsonl"):
        name += ".jsonl"
    return candidate_directory(project_root) / name


def candidate_reference(project_root: str | Path, path: str | Path) -> str:
    """Return a portable project-relative reference for a managed candidate."""
    root = Path(project_root).resolve()
    p = Path(path).resolve()
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def load_candidate_jsonl(path: str | Path) -> list[dict]:
    """Load and score-sort one managed candidate pool."""
    import json

    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"candidate line {line_no} is not a JSON object")
            rows.append(value)
    rows.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
    return rows


def candidate_pool_records(db, path: str | Path) -> list[dict]:
    """Compatibility view joining an exported/legacy JSONL pool to curve state.

    New v0.8 UI workflows use :mod:`rank42.candidates` and SQLite pool IDs.
    This function is retained only for older scripts and imported JSONL files.
    """
    from rank42.short_weierstrass import candidate_state, normalize_candidate_parameter

    rows = load_candidate_jsonl(path)
    out = []
    for nagao_rank, row in enumerate(rows, 1):
        parameter = normalize_candidate_parameter(row)
        family = row.get("family")
        db_row = None
        if family is not None and parameter is not None:
            db_row = db.execute(
                """
                SELECT id,status,generic_lower,quick_upper,descent_lower,descent_upper,
                       exact_rank,certain,conductor,updated_at
                FROM curves WHERE family=? AND parameter=?
                """,
                (str(family), str(parameter)),
            ).fetchone()
        record = {
            "Nagao rank": int(nagao_rank),
            "t": parameter if parameter is not None else row.get("t"),
            "a": row.get("a"),
            "b": row.get("b"),
            "Nagao score": float(row.get("score", 0.0)),
            "prime bound": row.get("prime_bound"),
            "prime terms": row.get("prime_terms"),
            "State": candidate_state(db_row),
            "curve id": int(db_row["id"]) if db_row is not None else None,
            "DB status": db_row["status"] if db_row is not None else None,
            "generic lower": db_row["generic_lower"] if db_row is not None else None,
            "quick upper": db_row["quick_upper"] if db_row is not None else None,
            "proven lower": (
                max(int(db_row["descent_lower"] or 0), int(db_row["exact_rank"] or 0), int(db_row["generic_lower"] or 0))
                if db_row is not None else None
            ),
            "log N": _log_conductor(db_row["conductor"]) if db_row is not None else None,
            "updated": db_row["updated_at"] if db_row is not None else None,
        }
        out.append(record)
    return out
