"""Read-only, job-scoped hunt findings. Logs describe this run; SQLite holds current evidence."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from rank42.ui_results import (
    _RE_CANDIDATE,
    _RE_CANDIDATE_DONE,
    _RE_EXTRA_HIT,
    _RE_MAPPED,
    _RE_MESTRE_HIT,
    _RE_MESTRE_MAPPED,
    _RE_NATIVE,
)


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _during_run(created, job):
    created = _time(created)
    start = _time(job["started_at"] or job["created_at"])
    end = _time(job["finished_at"])
    return bool(created and start and start <= created and (end is None or created <= end))


def _ids(job):
    try:
        value = json.loads(job["metadata_json"] or "{}").get("candidate_ids") or []
    except (ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [int(item) for item in value[:10000] if isinstance(item, int) and item > 0]


def _rows_by_id(db, table, ids):
    found = {}
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        if batch:
            placeholders = ",".join("?" for _ in batch)
            found.update({int(row["id"]): row for row in db.execute(
                f"SELECT * FROM {table} WHERE id IN ({placeholders})", batch
            ).fetchall()})
    return found


def hunt_findings(db, job, log):
    """Return per-candidate observations; never infer proof from a screen or a hit."""
    candidate_ids = _ids(job)
    candidates = _rows_by_id(db, "candidates", candidate_ids)
    markers = list(_RE_CANDIDATE.finditer(log))
    blocks = {}
    for position, marker in enumerate(markers):
        index = int(marker.group(1))
        block = log[marker.end():markers[position + 1].start() if position + 1 < len(markers) else len(log)]
        blocks[index] = {
            "curve_id": int(marker.group(3)),
            "parameter": marker.group(4),
            "native_hits": sum(int(m.group(1)) for m in _RE_NATIVE.finditer(block)),
            "mapped_extras": sum(int(m.group(1)) for m in _RE_MAPPED.finditer(block))
            + sum(int(m.group(1)) for m in _RE_MESTRE_MAPPED.finditer(block)),
            "rank_growth_signal": bool(_RE_EXTRA_HIT.search(block) or _RE_MESTRE_HIT.search(block)),
            "completed": bool(_RE_CANDIDATE_DONE.search(block)),
        }
    count = max(len(candidate_ids), max(blocks, default=0))
    curve_ids = sorted({int(candidate["curve_id"]) for candidate in candidates.values() if candidate["curve_id"] is not None}
                       | {block["curve_id"] for block in blocks.values()})
    curves = _rows_by_id(db, "curves", curve_ids)
    result = []
    for index in range(1, count + 1):
        candidate_id = candidate_ids[index - 1] if index <= len(candidate_ids) else None
        candidate = candidates.get(candidate_id)
        block = blocks.get(index)
        if candidate is None and block is None:
            continue
        curve_id = block["curve_id"] if block else candidate["curve_id"]
        curve = curves.get(int(curve_id)) if curve_id is not None else None
        rank = None
        if curve is not None:
            bounds = [curve[key] for key in ("exact_rank", "descent_lower", "generic_lower") if curve[key] is not None]
            rank = max(map(int, bounds)) if bounds else None
        result.append({
            "position": index,
            "candidate_id": candidate_id,
            "parameter": (candidate["parameter"] if candidate else None) or (block["parameter"] if block else None),
            "curve_id": curve_id,
            "new_curve": _during_run(curve["created_at"], job) if curve is not None else False,
            "rigorous_lower_now": rank,
            "native_hits": block["native_hits"] if block else None,
            "mapped_extras": block["mapped_extras"] if block else None,
            "rank_growth_signal": block["rank_growth_signal"] if block else False,
            "completed": block["completed"] if block else None,
        })
    return result


def recorded_native_x(db, job, curve_id):
    """Distinct native x recorded during the run, including structural/repeated hits."""
    start = job["started_at"] or job["created_at"]
    if not start:
        return []
    sql = """
        SELECT qp.metadata_json
        FROM quartic_searches qs
        JOIN quartic_points qp ON qp.search_id=qs.id
        WHERE qs.curve_id=? AND qp.created_at>=?
    """
    args = [int(curve_id), start]
    if job["finished_at"]:
        sql += " AND qp.created_at<=?"
        args.append(job["finished_at"])
    seen = set()
    for row in db.execute(sql, args):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if metadata.get("inverse_map_verified_exactly") and metadata.get("native_x") is not None:
            seen.add(str(metadata["native_x"]))
    return sorted(seen)


_KNOWN_X_SCRIPT = """
import importlib.util, json, sys
from pathlib import Path
source = Path(sys.argv[1])
sys.path.insert(0, str(source.parent))
spec = importlib.util.spec_from_file_location('_rank42_hunt_family', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
parameters = json.loads(sys.argv[2])
if callable(getattr(module, 'native_quartic_known_x', None)):
    values = [[str(x) for x in module.native_quartic_known_x(parameter)] for parameter in parameters]
else:
    values = [[str(point[0]) for point in module.native_quartic_known_points(parameter)] for parameter in parameters]
print('RANK42_KNOWN_X=' + json.dumps(values))
"""


@lru_cache(maxsize=512)
def _known_x_batch(family_path, fingerprint, parameters, science_python):
    result = subprocess.run(
        [science_python, "-c", _KNOWN_X_SCRIPT, family_path, json.dumps(parameters)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    marker = next((line.removeprefix("RANK42_KNOWN_X=") for line in reversed(result.stdout.splitlines())
                   if line.startswith("RANK42_KNOWN_X=")), None)
    return [set(values) for values in json.loads(marker)] if marker is not None else None


def _matching_family_source(job):
    metadata = json.loads(job["metadata_json"] or "{}")
    expected_hash = metadata.get("installed_family_sha256")
    family_path = Path(metadata["adapter_file"]).with_name("family.py")
    if not expected_hash or not family_path.is_file():
        return None
    actual_hash = hashlib.sha256(family_path.read_bytes()).hexdigest()
    return (str(family_path), actual_hash) if actual_hash == expected_hash else None


def reconstructed_new_native_x(db, job, curve_id, parameter, expected_count, science_python=None):
    """Recover individual new x only when the original family source and log agree."""
    observed = recorded_native_x(db, job, curve_id)
    if expected_count is None or parameter is None:
        return None, observed
    try:
        source = _matching_family_source(job)
        if source is None:
            return None, observed
        known = _known_x_batch(*source, (str(parameter),), science_python or sys.executable)
        if not known:
            return None, observed
        recovered = sorted(set(observed) - known[0])
        if len(recovered) != int(expected_count):
            return None, observed
        return recovered, observed
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return None, observed


def reconstructed_job_native_x(db, job, findings, science_python=None):
    """Batch-recover new native x for a whole run; report how many rows verified."""
    eligible = [f for f in findings if f["curve_id"] is not None and f["parameter"] is not None
                and f["native_hits"] is not None]
    try:
        source = _matching_family_source(job)
        if source is None:
            return [], 0, len(eligible)
        known_sets = _known_x_batch(*source, tuple(str(f["parameter"]) for f in eligible), science_python or sys.executable)
        if known_sets is None or len(known_sets) != len(eligible):
            return [], 0, len(eligible)
        rows = []
        verified = 0
        for finding, known_x in zip(eligible, known_sets):
            observed = recorded_native_x(db, job, finding["curve_id"])
            recovered = sorted(set(observed) - known_x)
            if len(recovered) != int(finding["native_hits"]):
                continue
            verified += 1
            rows.extend({
                "candidate #": finding["position"],
                "parameter": finding["parameter"],
                "curve": finding["curve_id"],
                "new native x": x,
            } for x in recovered)
        return rows, verified, len(eligible)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return [], 0, len(eligible)
