"""Bounded presentation-only geometry for the Curves workbench."""
from __future__ import annotations

import json
import math
from fractions import Fraction


def _finite_float(value):
    try:
        result = float(Fraction(str(value)))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def parse_a_invariants(value):
    """Return five finite real a-invariants suitable for plotting, or None."""
    try:
        raw = json.loads(value) if isinstance(value, str) else list(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, list) or len(raw) != 5:
        return None
    parsed = [_finite_float(item) for item in raw]
    return None if any(item is None for item in parsed) else tuple(parsed)


def _point_rows(points, *, max_points=120):
    parsed = []
    for point in points or ():
        x = _finite_float(point.get("x"))
        y = _finite_float(point.get("y"))
        if x is None or y is None:
            continue
        parsed.append(
            {
                "id": point.get("id"),
                "x": x,
                "y": y,
                "rigorous": bool(point.get("rigorous_independent")),
                "source": str(point.get("source") or ""),
            }
        )
    parsed.sort(key=lambda item: (0 if item["rigorous"] else 1, abs(item["x"]), int(item["id"] or 0)))
    return parsed[: max(1, int(max_points))]


def _x_window(points):
    usable = [
        point["x"]
        for point in sorted(points, key=lambda item: abs(item["x"]))[:24]
        if math.isfinite(point["x"]) and abs(point["x"]) <= 1e90
    ]
    if not usable:
        return -10.0, 10.0
    low = min(usable)
    high = max(usable)
    spread = max(high - low, 1.0)
    magnitude = max(abs(low), abs(high), 1.0)
    margin = max(2.5, 0.18 * magnitude, 0.25 * spread)
    low -= margin
    high += margin
    if not math.isfinite(low) or not math.isfinite(high) or low >= high:
        return -10.0, 10.0
    return low, high


def curve_portrait_data(a_invariants, points, *, samples=520, max_points=120):
    """Build bounded real-locus + exact-point data for Vega-Lite.

    This is presentation-only. It does not search for points or compute rank,
    independence, heights, or any other scientific evidence.
    """
    ainvs = parse_a_invariants(a_invariants)
    if ainvs is None:
        return None

    parsed_points = _point_rows(points, max_points=max_points)
    x_low, x_high = _x_window(parsed_points)
    a1, a2, a3, a4, a6 = ainvs
    sample_count = max(80, min(int(samples), 1200))
    step = (x_high - x_low) / float(sample_count - 1)

    rows = []
    segment = 0
    in_real = False
    curve_samples = 0
    for index in range(sample_count):
        x = x_low + step * index
        try:
            rhs = x * x * x + a2 * x * x + a4 * x + a6
            linear = a1 * x + a3
            disc = linear * linear + 4.0 * rhs
        except OverflowError:
            disc = -1.0
        if not math.isfinite(disc) or disc < 0.0:
            if in_real:
                segment += 1
                in_real = False
            continue
        root = math.sqrt(max(0.0, disc))
        upper = (-linear + root) / 2.0
        lower = (-linear - root) / 2.0
        if not (math.isfinite(upper) and math.isfinite(lower)):
            continue
        in_real = True
        rows.append(
            {
                "kind": "curve",
                "x": x,
                "y": upper,
                "series": f"{segment}:upper",
                "status": "",
                "point_id": None,
                "source": "",
            }
        )
        rows.append(
            {
                "kind": "curve",
                "x": x,
                "y": lower,
                "series": f"{segment}:lower",
                "status": "",
                "point_id": None,
                "source": "",
            }
        )
        curve_samples += 1

    shown_points = 0
    for point in parsed_points:
        if not (x_low <= point["x"] <= x_high):
            continue
        rows.append(
            {
                "kind": "point",
                "x": point["x"],
                "y": point["y"],
                "series": "",
                "status": "Rigorous witness" if point["rigorous"] else "Exact point",
                "point_id": point["id"],
                "source": point["source"],
            }
        )
        shown_points += 1

    if curve_samples == 0:
        return None
    return {
        "rows": rows,
        "x_domain": [x_low, x_high],
        "points_available": len(parsed_points),
        "points_shown": shown_points,
        "samples": curve_samples,
    }
