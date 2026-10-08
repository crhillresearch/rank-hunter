"""Shared Dashboard rank/research summaries.

These helpers consume authoritative Curve Research State so Dashboard surfaces do
not re-derive exact/lower/interval semantics from compatibility columns.
"""
from __future__ import annotations

from rank42.curve_research_state import list_curve_research_states

_SQLITE_ID_CHUNK = 800


def _states(db, states=None):
    return list_curve_research_states(db) if states is None else list(states)


def _chunks(values, size=_SQLITE_ID_CHUNK):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def dashboard_rank_summary(db, *, states=None) -> dict:
    states = _states(db, states)
    exact_values = [
        int(state["exact_rank"])
        for state in states
        if state["exact_rank"] is not None and not state["rank_inconsistent"]
    ]
    best_exact = max(exact_values) if exact_values else None
    best_rigorous = max(
        (int(state["rigorous_lower"]) for state in states if not state["rank_inconsistent"]),
        default=0,
    ) or None

    high_rank_floor = max(8, int(best_exact or 0))
    specialization_lowers = [
        int(state["specialization_rigorous_lower"])
        for state in states
        if not state["rank_inconsistent"]
    ]
    high_rank_hits = sum(value >= high_rank_floor for value in specialization_lowers)
    high_rank_plus_2 = sum(value >= high_rank_floor + 2 for value in specialization_lowers)
    high_rank_plus_4 = sum(value >= high_rank_floor + 4 for value in specialization_lowers)

    curve_count = len(states)
    exact_count = len(exact_values)
    exact_pct = 0.0 if curve_count == 0 else 100.0 * exact_count / curve_count
    return {
        "best_rigorous": best_rigorous,
        "best_exact": best_exact,
        "curve_count": curve_count,
        "exact_count": exact_count,
        "exact_pct": exact_pct,
        "high_rank_floor": high_rank_floor,
        "high_rank_hits": high_rank_hits,
        "high_rank_plus_2": high_rank_plus_2,
        "high_rank_plus_4": high_rank_plus_4,
    }


def dashboard_rank_certification(db, *, states=None) -> dict:
    states = _states(db, states)
    counts = {
        "exact": 0,
        "lower": 0,
        "interval": 0,
        "none": 0,
        "conflict": 0,
    }
    for state in states:
        if state["rank_inconsistent"]:
            counts["conflict"] += 1
            continue
        if state["exact_rank"] is not None:
            counts["exact"] += 1
            continue
        lower = int(state["rigorous_lower"] or 0)
        upper = state["rigorous_upper"]
        if lower > 0 and upper is not None and int(upper) > lower:
            counts["interval"] += 1
        elif lower > 0:
            counts["lower"] += 1
        else:
            counts["none"] += 1
    return {
        "total": len(states),
        "counts": counts,
    }

def dashboard_frontier_rows(db, *, states=None) -> list[dict]:
    """Authoritative specialization-rank rows for the local research frontier."""
    states = _states(db, states)
    eligible = [
        state
        for state in states
        if not state["rank_inconsistent"]
        and int(state["specialization_rigorous_lower"] or 0) > 0
    ]
    point_counts = {}
    ids = [int(state["curve_id"]) for state in eligible]
    for chunk in _chunks(ids):
        marks = ",".join("?" for _ in chunk)
        for row in db.execute(
            f"""SELECT curve_id, COUNT(*) AS independent_points
                FROM points
                WHERE rigorous_independent=1 AND curve_id IN ({marks})
                GROUP BY curve_id""",
            chunk,
        ).fetchall():
            point_counts[int(row["curve_id"])] = int(row["independent_points"] or 0)

    rows = []
    for state in eligible:
        specialization_lower = int(state["specialization_rigorous_lower"] or 0)
        curve = state["curve"]
        rows.append(
            {
                "id": int(state["curve_id"]),
                "family": state["family"],
                "parameter": state["parameter"],
                "exact_rank": state["exact_rank"],
                "rigorous_lower": specialization_lower,
                "rigorous_upper": state["rigorous_upper"],
                "updated_at": curve.get("updated_at"),
                "independent_points": point_counts.get(int(state["curve_id"]), 0),
            }
        )
    rows.sort(
        key=lambda row: (
            str(row["updated_at"] or ""),
            int(row["id"]),
        ),
        reverse=True,
    )
    return rows


def dashboard_exact_rank_distribution(db, *, states=None) -> list[dict]:
    """Count authoritative exact specialization ranks for Dashboard charts."""
    counts = {}
    for state in _states(db, states):
        if state["rank_inconsistent"] or state["exact_rank"] is None:
            continue
        rank = int(state["exact_rank"])
        counts[rank] = counts.get(rank, 0) + 1
    return [
        {"rank_value": rank, "curve_count": counts[rank]}
        for rank in sorted(counts)
    ]

