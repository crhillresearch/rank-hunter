"""Read-only Hall of Fame projection for retained Curves."""
from __future__ import annotations

from collections import defaultdict


UNKNOWN_TORSION = "Unknown"


def _torsion_group(row):
    label = str(row.get("torsion_label") or "").strip()
    return label or UNKNOWN_TORSION


def hall_of_fame_groups(rows, *, top_per_group=5):
    """Group retained curves by stored exact torsion and rigorous rank state."""
    grouped = defaultdict(list)
    limit = max(1, int(top_per_group))
    for row in rows or ():
        grouped[_torsion_group(row)].append(dict(row))

    out = []
    for torsion, group_rows in grouped.items():
        group_rows.sort(
            key=lambda row: (
                -int(row.get("rigorous_lower") or 0),
                -(float(row["score"]) if row.get("score") is not None else float("-inf")),
                -int(row["id"]),
            )
        )
        champions = group_rows[:limit]
        out.append(
            {
                "torsion": torsion,
                "best_rigorous_lower": int(champions[0].get("rigorous_lower") or 0),
                "curve_count": len(group_rows),
                "rows": champions,
            }
        )

    out.sort(
        key=lambda group: (
            group["torsion"] == UNKNOWN_TORSION,
            -int(group["best_rigorous_lower"]),
            str(group["torsion"]),
        )
    )
    return out



def hall_of_fame_summary(rows):
    """Return Hall of Fame hero statistics from authoritative local state."""
    records = [dict(row) for row in (rows or ())]

    rigorous_rows = [
        row for row in records
        if int(row.get("rigorous_lower") or 0) > 0
    ]
    exact_rows = [
        row for row in records
        if row.get("exact_rank") is not None
    ]

    torsion_counts = {}
    for row in rigorous_rows:
        label = str(row.get("torsion_label") or "").strip()
        if not label:
            continue
        torsion_counts[label] = torsion_counts.get(label, 0) + 1

    most_common = None
    if torsion_counts:
        label, count = sorted(
            torsion_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )[0]
        most_common = {"torsion": label, "count": int(count)}

    return {
        "best_rigorous_lower": (
            max(int(row.get("rigorous_lower") or 0) for row in rigorous_rows)
            if rigorous_rows
            else None
        ),
        "best_exact_rank": (
            max(int(row["exact_rank"]) for row in exact_rows)
            if exact_rows
            else None
        ),
        "rigorous_curve_count": len(rigorous_rows),
        "exact_curve_count": len(exact_rows),
        "torsion_group_count": len(torsion_counts),
        "most_common_torsion": most_common,
    }
