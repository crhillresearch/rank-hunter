"""Persistence helpers for quartic / extra-point searches."""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from rank42.db import now


def _frac_str(x):
    return str(Fraction(str(x)))


def canonical_search_payload(
    *, curve_id, coefficients, height_bound, denominator_low=None,
    denominator_high=None, extra_args=None,
):
    return {
        "curve_id": int(curve_id) if curve_id is not None else None,
        "coefficients": [_frac_str(x) for x in coefficients],
        "height_bound": int(height_bound),
        "denominator_low": int(denominator_low) if denominator_low is not None else None,
        "denominator_high": int(denominator_high) if denominator_high is not None else None,
        "extra_args": [str(x) for x in (extra_args or [])],
    }


def search_key(**kwargs):
    raw = json.dumps(canonical_search_payload(**kwargs), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def create_or_get_search(
    db,
    *,
    curve_id=None,
    family=None,
    parameter=None,
    hole_label=None,
    coefficients,
    integer_coefficients,
    y_scale,
    degree,
    height_bound,
    denominator_low=None,
    denominator_high=None,
    extra_args=None,
    metadata=None,
):
    key = search_key(
        curve_id=curve_id,
        coefficients=coefficients,
        height_bound=height_bound,
        denominator_low=denominator_low,
        denominator_high=denominator_high,
        extra_args=extra_args,
    )
    ts = now()
    db.execute(
        """
        INSERT OR IGNORE INTO quartic_searches(
            search_key, curve_id, family, parameter, hole_label,
            polynomial_json, integer_polynomial_json, y_scale, degree,
            height_bound, denominator_low, denominator_high, options_json,
            metadata_json, status, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            key, curve_id, family, parameter, hole_label,
            json.dumps([_frac_str(x) for x in coefficients]),
            json.dumps([str(int(x)) for x in integer_coefficients]),
            str(int(y_scale)), int(degree), int(height_bound),
            int(denominator_low) if denominator_low is not None else None,
            int(denominator_high) if denominator_high is not None else None,
            json.dumps([str(x) for x in (extra_args or [])]),
            json.dumps(dict(metadata or {}), sort_keys=True),
            "new", ts, ts,
        ),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM quartic_searches WHERE search_key=?", (key,)
    ).fetchone()


def mark_running(db, search_id, *, executable):
    ts = now()
    db.execute(
        """
        UPDATE quartic_searches
        SET status='running', ratpoints_executable=?, started_at=?,
            updated_at=?, error=NULL
        WHERE id=?
        """,
        (executable, ts, ts, search_id),
    )
    db.commit()


def finish_search(db, search_id, *, status, runtime=None, error=None, point_count=None):
    if status not in {"done", "timeout", "error", "locally_obstructed"}:
        raise ValueError(f"invalid quartic terminal status: {status}")
    ts = now()
    db.execute(
        """
        UPDATE quartic_searches
        SET status=?, runtime=?, error=?, point_count=COALESCE(?, point_count),
            finished_at=?, updated_at=?
        WHERE id=?
        """,
        (status, runtime, error, point_count, ts, ts, search_id),
    )
    db.commit()


def store_points(db, search_id, points):
    ts = now()
    for P in points:
        db.execute(
            """
            INSERT OR IGNORE INTO quartic_points(
                search_id, x, y, projective_json, exact_verified, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                search_id, str(P.x), str(P.y),
                json.dumps([
                    str(P.projective_x), str(P.projective_y), str(P.projective_z)
                ]),
                1, ts,
            ),
        )
    db.commit()


def list_searches(db, *, status=None, limit=50):
    sql = "SELECT * FROM quartic_searches"
    vals = []
    if status is not None:
        sql += " WHERE status=?"
        vals.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    vals.append(int(limit))
    return db.execute(sql, vals).fetchall()



def record_map_back_failures(db, search_id, failures, *, sample_limit=16):
    """Persist bounded, deduplicated point-map failure diagnostics."""
    failures = [dict(item) for item in (failures or []) if isinstance(item, dict)]
    if not failures:
        return {"count": 0, "samples": []}
    row = db.execute(
        "SELECT metadata_json FROM quartic_searches WHERE id=?",
        (int(search_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown quartic search id {int(search_id)}")
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except Exception:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    samples = [
        dict(item)
        for item in (metadata.get("map_back_failures") or [])
        if isinstance(item, dict)
    ]
    keys = {
        str(item.get("failure_key") or "")
        for item in samples
        if item.get("failure_key")
    }
    count = int(metadata.get("map_back_failure_count") or 0) + len(failures)
    for failure in failures:
        canonical = dict(failure)
        canonical.pop("failure_key", None)
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(raw.encode()).hexdigest()
        if key in keys:
            continue
        keys.add(key)
        if len(samples) < max(1, int(sample_limit)):
            canonical["failure_key"] = key
            samples.append(canonical)

    metadata["map_back_failure_count"] = count
    metadata["map_back_failures"] = samples
    metadata["map_back_failure_samples_truncated"] = count > len(samples)
    db.execute(
        "UPDATE quartic_searches SET metadata_json=?, updated_at=? WHERE id=?",
        (json.dumps(metadata, sort_keys=True), now(), int(search_id)),
    )
    db.commit()
    return {
        "count": count,
        "samples": samples,
        "samples_truncated": count > len(samples),
    }
