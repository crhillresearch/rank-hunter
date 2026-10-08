"""Cumulative research-evidence depth for retained curves.

Evidence depth is deliberately stricter than a maximum of independent flags.
A curve advances only through a continuous chain of completed stages.  This
keeps later saturation work from implying prerequisites that are not actually
persisted.
"""
from __future__ import annotations


EVIDENCE_DEPTH_STAGES = (
    (0, "none", "None"),
    (1, "points", "Points"),
    (2, "mw", "Lattices"),
    (3, "descent", "Selmer"),
    (4, "saturation", "Bounded Saturation"),
    (5, "full", "Exact Rank"),
)


def deepest_completed_stage(*, independent_points, mw_lattice, descent, saturation, exact_rank):
    """Return the deepest continuous completed evidence stage, from 0 through 5.

    Stage 4 means completed bounded saturation evidence, not a proof of global
    saturation. Stage 5 means stages 1-4 are present and an exact rank is
    persisted. Exact rank by itself never skips missing prerequisites.
    """
    if not independent_points:
        return 0
    if not mw_lattice:
        return 1
    if not descent:
        return 2
    if not saturation:
        return 3
    if exact_rank is None:
        return 4
    return 5


def reached_stage_counts(deepest_counts):
    """Return cumulative totals from mutually exclusive deepest-stage counts.

    None remains the count of curves with no completed evidence stage.
    Every positive depth counts curves whose deepest continuous stage is at
    least that depth, so later stages remain visible in prerequisite totals.
    """
    deepest = {
        int(depth): int(deepest_counts.get(depth, 0) or 0)
        for depth, _key, _label in EVIDENCE_DEPTH_STAGES
    }
    reached = {0: deepest[0]}
    running = 0
    for depth, _key, _label in reversed(EVIDENCE_DEPTH_STAGES[1:]):
        running += deepest[depth]
        reached[depth] = running
    return reached


def evidence_depth_counts(db):
    """Count every retained curve exactly once at its deepest completed stage."""
    rows = db.execute(
        """
        SELECT
            cv.id,
            cv.exact_rank,
            CASE WHEN EXISTS (
                SELECT 1 FROM points p
                WHERE p.curve_id=cv.id AND p.rigorous_independent=1
            ) THEN 1 ELSE 0 END AS has_independent_points,
            CASE WHEN EXISTS (
                SELECT 1 FROM mw_lattices mw
                WHERE mw.curve_id=cv.id AND mw.basis_count > 0
            ) THEN 1 ELSE 0 END AS has_mw_lattice,
            CASE WHEN EXISTS (
                SELECT 1 FROM rank_evidence re
                WHERE re.curve_id=cv.id
                  AND re.evidence_type='descent'
                  AND re.rigorous=1
                  AND re.status='completed'
            ) THEN 1 ELSE 0 END AS has_descent,
            CASE WHEN EXISTS (
                SELECT 1 FROM rank_evidence re
                WHERE re.curve_id=cv.id
                  AND re.evidence_type='saturation'
                  AND re.rigorous=1
                  AND re.status='completed'
            ) THEN 1 ELSE 0 END AS has_saturation
        FROM curves cv
        """
    ).fetchall()

    counts = {depth: 0 for depth, _, _ in EVIDENCE_DEPTH_STAGES}
    for row in rows:
        depth = deepest_completed_stage(
            independent_points=bool(row["has_independent_points"]),
            mw_lattice=bool(row["has_mw_lattice"]),
            descent=bool(row["has_descent"]),
            saturation=bool(row["has_saturation"]),
            exact_rank=row["exact_rank"],
        )
        counts[depth] += 1
    return counts
