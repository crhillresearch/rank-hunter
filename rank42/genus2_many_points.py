"""Persistent, exact rational-point hunts on saved genus-two equations.

This ledger counts points on C(Q), not divisor classes or Jacobian rank.
Completed denominator bands are reused only at the same numerator height.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from math import isqrt
from time import monotonic

from rank42.db import now
from rank42.genus2_pipeline import audit_model
from rank42.ratpoints import (
    RatpointsError, evaluate_polynomial, run_ratpoints, vendored_executable,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS genus2_rational_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_fingerprint_sha256 TEXT NOT NULL,
    x TEXT NOT NULL,
    y TEXT NOT NULL,
    source TEXT NOT NULL,
    first_run_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(model_fingerprint_sha256,x,y)
);
CREATE INDEX IF NOT EXISTS idx_genus2_rational_points_model
    ON genus2_rational_points(model_fingerprint_sha256);
CREATE TABLE IF NOT EXISTS genus2_point_search_bands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_fingerprint_sha256 TEXT NOT NULL,
    height INTEGER NOT NULL,
    denominator_low INTEGER NOT NULL,
    denominator_high INTEGER NOT NULL,
    backend TEXT NOT NULL,
    status TEXT NOT NULL,
    new_points INTEGER NOT NULL DEFAULT 0,
    runtime_seconds REAL,
    engine_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    run_id INTEGER,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(model_fingerprint_sha256,height,denominator_low,denominator_high)
);
CREATE INDEX IF NOT EXISTS idx_genus2_point_search_bands_model
    ON genus2_point_search_bands(model_fingerprint_sha256,status);
"""


def ensure_schema(db):
    rows = db.execute(
        """SELECT name FROM sqlite_master WHERE type='table'
           AND name IN ('genus2_rational_points','genus2_point_search_bands')"""
    ).fetchall()
    if {row[0] for row in rows} != {"genus2_rational_points", "genus2_point_search_bands"}:
        db.executescript(SCHEMA)
        db.commit()


def _model(values):
    audit = audit_model(values)
    if audit.get("status") != "completed":
        raise ValueError(f"invalid genus-two equation: {audit.get('reason')}")
    return audit


def _finite_points_count(db, fingerprint):
    return int(db.execute(
        "SELECT COUNT(*) FROM genus2_rational_points WHERE model_fingerprint_sha256=?",
        (fingerprint,),
    ).fetchone()[0])


def point_census(db, values, *, recheck=False):
    """Return a lower bound from exact, distinct rational points."""
    audit = _model(values)
    ensure_schema(db)
    fingerprint = audit["model_fingerprint_sha256"]
    if recheck:
        rows = db.execute(
            "SELECT x,y FROM genus2_rational_points WHERE model_fingerprint_sha256=?",
            (fingerprint,),
        ).fetchall()
        for row in rows:
            x, y = Fraction(row["x"]), Fraction(row["y"])
            if y * y != evaluate_polynomial(audit["coefficients_ascending"], x):
                raise ArithmeticError(f"stored genus-two point ({x},{y}) fails its equation")
    finite = _finite_points_count(db, fingerprint)
    infinity = (1 if audit["infinity_rationality"] == "one_rational_point" else
                2 if audit["infinity_rationality"] == "two_rational_points" else 0)
    bands = db.execute(
        """SELECT COUNT(*),COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0)
           FROM genus2_point_search_bands WHERE model_fingerprint_sha256=?""",
        (fingerprint,),
    ).fetchone()
    return {
        "status": "completed",
        "model_fingerprint_sha256": fingerprint,
        "finite_points": finite,
        "rational_points_at_infinity": infinity,
        "verified_point_lower_bound": finite + infinity,
        "bands_attempted": int(bands[0]),
        "bands_completed": int(bands[1]),
        "claim_boundary": "verified rational points found; not the complete rational-point set or Jacobian rank",
    }


def model_point_counts(db, models):
    """Read the persistent witness counts for a Models inventory in one query."""
    ensure_schema(db)
    finite_by_fingerprint = {
        str(row["model_fingerprint_sha256"]): int(row["n"])
        for row in db.execute(
            """SELECT model_fingerprint_sha256,COUNT(*) AS n
               FROM genus2_rational_points GROUP BY model_fingerprint_sha256"""
        )
    }
    counts = {}
    for model in models:
        coefficients = json.loads(model["coefficients_json"])
        if len(coefficients) == 6:
            infinity = 1
        else:
            lead = Fraction(str(coefficients[-1]))
            infinity = 2 if (lead > 0 and
                             isqrt(lead.numerator) ** 2 == lead.numerator and
                             isqrt(lead.denominator) ** 2 == lead.denominator) else 0
        counts[int(model["id"])] = finite_by_fingerprint.get(
            str(model["model_fingerprint_sha256"]), 0
        ) + infinity
    return counts


def stored_points(db, values, *, limit=1000):
    """Return exact witnesses for downstream divisor and quotient stages."""
    audit = _model(values)
    ensure_schema(db)
    return [
        [str(row["x"]), str(row["y"])]
        for row in db.execute(
            """SELECT x,y FROM genus2_rational_points
               WHERE model_fingerprint_sha256=? ORDER BY id LIMIT ?""",
            (audit["model_fingerprint_sha256"], max(1, int(limit))),
        )
    ]


def _insert_orbit(db, fingerprint, coefficients, point, *, run_id):
    x, y = Fraction(str(point.x)), Fraction(str(point.y))
    even_model = all(Fraction(str(coefficients[i])) == 0 for i in (1, 3, 5))
    xs = {x, -x} if even_model else {x}
    ys = {y, -y}
    inserted = 0
    for xx in xs:
        for yy in ys:
            if yy * yy != evaluate_polynomial(coefficients, xx):
                raise ArithmeticError("point symmetry failed exact genus-two verification")
            cursor = db.execute(
                """INSERT OR IGNORE INTO genus2_rational_points
                   (model_fingerprint_sha256,x,y,source,first_run_id,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (fingerprint, str(xx), str(yy), "ratpoints+verified_symmetry",
                 run_id, now()),
            )
            inserted += int(cursor.rowcount > 0)
    return inserted


def search_point_bands(db, values, *, height=10000, band_width=500,
                       bands_per_run=20, timeout=900, backend="CPU", run_id=None,
                       goal_points=649):
    """Search the next uncovered denominator bands and checkpoint each one."""
    audit = _model(values)
    height, band_width = int(height), int(band_width)
    bands_per_run, timeout, goal_points = int(bands_per_run), int(timeout), int(goal_points)
    backend = str(backend).upper()
    if not (1 <= height <= 1_000_000_000_000 and 1 <= band_width <= height
            and 1 <= bands_per_run <= 1000 and 1 <= timeout <= 86400
            and 1 <= goal_points <= 1000000):
        raise ValueError("point-hunt budgets are outside supported ranges")
    if backend not in {"CPU", "GPU"}:
        raise ValueError("point-hunt backend must be CPU or GPU")
    ensure_schema(db)
    fingerprint = audit["model_fingerprint_sha256"]
    coefficients = audit["coefficients_ascending"]
    infinity_points = (1 if audit["infinity_rationality"] == "one_rational_point" else
                       2 if audit["infinity_rationality"] == "two_rational_points" else 0)
    attempted, completed, new_points = 0, 0, 0
    last_error = None
    started_monotonic = monotonic()
    print(
        f"[genus2 points] scanning x-height ≤{height:,}; "
        f"up to {bands_per_run} new denominator bands of width {band_width:,}",
        flush=True,
    )
    for low in range(1, height + 1, band_width):
        if _finite_points_count(db, fingerprint) + infinity_points >= goal_points:
            break
        if attempted >= bands_per_run:
            break
        high = min(height, low + band_width - 1)
        prior = db.execute(
            """SELECT status,started_at FROM genus2_point_search_bands
               WHERE model_fingerprint_sha256=? AND height=?
                 AND denominator_low=? AND denominator_high=?""",
            (fingerprint, height, low, high),
        ).fetchone()
        if prior is not None and prior["status"] == "completed":
            continue
        if prior is not None and prior["status"] == "running" and prior["started_at"]:
            try:
                lease_started = datetime.fromisoformat(prior["started_at"])
                if datetime.now(timezone.utc) - lease_started < timedelta(seconds=timeout + 600):
                    continue
            except (TypeError, ValueError):
                pass
        started_at = now()
        if prior is None:
            claim = db.execute(
                """INSERT OR IGNORE INTO genus2_point_search_bands
                   (model_fingerprint_sha256,height,denominator_low,denominator_high,
                    backend,status,run_id,started_at)
                   VALUES(?,?,?,?,?,'running',?,?)""",
                (fingerprint, height, low, high, backend, run_id, started_at),
            )
        else:
            claim = db.execute(
                """UPDATE genus2_point_search_bands
                   SET backend=?,status='running',run_id=?,started_at=?,error=NULL
                   WHERE model_fingerprint_sha256=? AND height=?
                     AND denominator_low=? AND denominator_high=?
                     AND status=? AND started_at IS ?""",
                (backend, run_id, started_at, fingerprint, height, low, high,
                 prior["status"], prior["started_at"]),
            )
        db.commit()
        if claim.rowcount != 1:
            continue
        attempted += 1
        try:
            result = run_ratpoints(
                coefficients, height,
                executable=vendored_executable("GPU") if backend == "GPU" else None,
                timeout=timeout, denominator_low=low, denominator_high=high,
            )
            found = 0
            for point in result["points"]:
                found += _insert_orbit(db, fingerprint, coefficients, point, run_id=run_id)
            db.execute(
                """UPDATE genus2_point_search_bands SET status='completed',new_points=?,
                   runtime_seconds=?,engine_json=?,error=NULL,finished_at=?
                   WHERE model_fingerprint_sha256=? AND height=?
                     AND denominator_low=? AND denominator_high=?""",
                (found, float(result.get("runtime") or 0),
                 json.dumps(result.get("engine_provenance") or {}, sort_keys=True),
                 now(), fingerprint, height, low, high),
            )
            db.commit()
            new_points += found
            completed += 1
            if found:
                print(
                    f"[genus2 points] denominators {low:,}–{high:,}: "
                    f"+{found} verified · total={_finite_points_count(db, fingerprint) + infinity_points}",
                    flush=True,
                )
        except (RatpointsError, OSError, ValueError, ArithmeticError) as exc:
            db.execute(
                """UPDATE genus2_point_search_bands SET status='failed',error=?,finished_at=?
                   WHERE model_fingerprint_sha256=? AND height=?
                     AND denominator_low=? AND denominator_high=?""",
                (str(exc), now(), fingerprint, height, low, high),
            )
            db.commit()
            last_error = str(exc)
            print(f"[genus2 points] band failed: {last_error}", flush=True)
            break
    census = point_census(db, coefficients)
    completed_bands = [
        (int(row["denominator_low"]), int(row["denominator_high"]))
        for row in db.execute(
            """SELECT denominator_low,denominator_high FROM genus2_point_search_bands
               WHERE model_fingerprint_sha256=? AND height=? AND status='completed'""",
            (fingerprint, height),
        ) if (int(row["denominator_low"]) - 1) % band_width == 0
        and int(row["denominator_high"]) == min(height, int(row["denominator_low"]) + band_width - 1)
    ]
    remaining = (height + band_width - 1) // band_width - len(completed_bands)
    covered_to = 0
    for low, high in sorted(completed_bands):
        if low != covered_to + 1:
            break
        covered_to = high
    elapsed = monotonic() - started_monotonic
    print(
        f"[genus2 points] finished {completed} band(s) in {elapsed:.1f}s; "
        f"covered denominators 1–{covered_to:,} at x-height {height:,}; "
        f"{census['verified_point_lower_bound']} total verified points",
        flush=True,
    )
    return {
        **census,
        "status": "completed" if last_error is None else "inconclusive",
        "height": height, "band_width": band_width,
        "bands_this_run": completed, "new_points": new_points,
        "remaining_bands_at_height": max(0, remaining),
        "covered_denominators_through": covered_to,
        "elapsed_seconds": round(elapsed, 3),
        "goal_points": goal_points,
        "point_goal_hit": census["verified_point_lower_bound"] >= goal_points,
        "error": last_error,
    }
