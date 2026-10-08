"""Authoritative read model for curve research/rank state.

UI pages and orchestration code should consume this module instead of rebuilding
rank truth from denormalized curves columns. Scientific rank semantics remain
owned by rank42.rank_evidence; this module supplies a convenient single/bulk
query projection around that reducer.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from rank42.rank_evidence import reduce_rank_records


_SQLITE_ID_CHUNK = 800


def _chunks(values: list[int], size: int = _SQLITE_ID_CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _curve_rows(
    db,
    *,
    curve_ids: Iterable[int] | None = None,
    family: str | None = None,
    compact: bool = False,
):
    ids = None if curve_ids is None else sorted({int(value) for value in curve_ids})
    if ids == []:
        return []

    select = (
        "id,family,parameter,score,status,"
        "exact_rank,descent_lower,generic_lower,descent_upper"
        if compact
        else "*"
    )

    if ids is None:
        sql = f"SELECT {select} FROM curves"
        args: list[object] = []
        if family is not None:
            sql += " WHERE family=?"
            args.append(str(family))
        return db.execute(sql, args).fetchall()

    rows = []
    for chunk in _chunks(ids):
        marks = ",".join("?" for _ in chunk)
        sql = f"SELECT {select} FROM curves WHERE id IN ({marks})"
        args: list[object] = list(chunk)
        if family is not None:
            sql += " AND family=?"
            args.append(str(family))
        rows.extend(db.execute(sql, args).fetchall())
    return rows


def _evidence_by_curve(
    db,
    curve_ids: Iterable[int],
    *,
    compact: bool = False,
):
    ids = sorted({int(value) for value in curve_ids})
    grouped = defaultdict(list)
    select = (
        "id,curve_id,rigorous,rigorous_lower,rigorous_upper,"
        "conditional_analytic_upper,conditional_mw_upper,"
        "numerical_rank_signal,status"
        if compact
        else "*"
    )
    for chunk in _chunks(ids):
        marks = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"""SELECT {select} FROM rank_evidence
                WHERE curve_id IN ({marks})
                ORDER BY curve_id, id DESC""",
            chunk,
        ).fetchall()
        for row in rows:
            grouped[int(row["curve_id"])].append(row)
    return grouped


def _project_curve(row, evidence_rows) -> dict:
    reduced = reduce_rank_records(row, evidence_rows)
    rigorous_count = sum(bool(int(rec["rigorous"] or 0)) for rec in evidence_rows)
    completed_rigorous_count = sum(
        bool(int(rec["rigorous"] or 0)) and str(rec["status"] or "") == "completed"
        for rec in evidence_rows
    )
    latest = evidence_rows[0] if evidence_rows else None
    return {
        "curve_id": int(row["id"]),
        "family": str(row["family"]),
        "parameter": str(row["parameter"]),
        "score": row["score"],
        "status": row["status"],
        "rigorous_lower": int(reduced["rigorous_lower"]),
        "specialization_rigorous_lower": int(reduced["specialization_rigorous_lower"]),
        "rigorous_upper": reduced["rigorous_upper"],
        "exact_rank": reduced["exact_rank"],
        "conditional_analytic_upper": reduced["conditional_analytic_upper"],
        "conditional_mw_upper": reduced["conditional_mw_upper"],
        "numerical_rank_signal": reduced["numerical_rank_signal"],
        "rank_inconsistent": bool(reduced["inconsistent"]),
        "rank_evidence_count": len(evidence_rows),
        "rigorous_evidence_count": rigorous_count,
        "completed_rigorous_evidence_count": completed_rigorous_count,
        "latest_rank_evidence_id": None if latest is None else int(latest["id"]),
        "curve": dict(row),
    }


def curve_rank_summary_map(
    db,
    curve_ids: Iterable[int],
) -> dict[int, dict]:
    """Return authoritative rank fields needed by bulk inventory views.

    This is algebraically equivalent to the rigorous lower/upper/exact/conflict
    portion of reduce_rank_records, while letting SQLite aggregate the evidence
    ledger without materializing every evidence payload in Python.
    """
    ids = sorted({int(value) for value in curve_ids})
    if not ids:
        return {}

    out = {}
    for chunk in _chunks(ids):
        marks = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"""
            WITH evidence AS (
                SELECT curve_id,
                       MAX(
                           CASE
                               WHEN rigorous=1 THEN rigorous_lower
                               ELSE NULL
                           END
                       ) AS evidence_lower,
                       MIN(
                           CASE
                               WHEN rigorous=1
                                AND status='completed'
                               THEN rigorous_upper
                               ELSE NULL
                           END
                       ) AS evidence_upper
                FROM rank_evidence
                WHERE curve_id IN ({marks})
                GROUP BY curve_id
            )
            SELECT c.id,
                   c.exact_rank,
                   c.descent_lower,
                   c.generic_lower,
                   c.descent_upper,
                   e.evidence_lower,
                   e.evidence_upper
            FROM curves c
            LEFT JOIN evidence e ON e.curve_id=c.id
            WHERE c.id IN ({marks})
            """,
            [*chunk, *chunk],
        ).fetchall()

        for row in rows:
            stored_lower = max(
                int(row["exact_rank"] or 0),
                int(row["descent_lower"] or 0),
                int(row["generic_lower"] or 0),
            )
            evidence_lower = (
                None
                if row["evidence_lower"] is None
                else int(row["evidence_lower"])
            )
            lower = max(
                stored_lower,
                0 if evidence_lower is None else evidence_lower,
            )

            upper_candidates = []
            if row["exact_rank"] is not None:
                upper_candidates.append(int(row["exact_rank"]))
            elif row["descent_upper"] is not None:
                upper_candidates.append(int(row["descent_upper"]))
            if row["evidence_upper"] is not None:
                upper_candidates.append(int(row["evidence_upper"]))
            upper = min(upper_candidates) if upper_candidates else None

            inconsistent = upper is not None and upper < lower
            exact = (
                lower
                if upper is not None and upper == lower and not inconsistent
                else None
            )
            out[int(row["id"])] = {
                "rigorous_lower": int(lower),
                "rigorous_upper": upper,
                "exact_rank": exact,
                "rank_inconsistent": bool(inconsistent),
            }
    return out


def get_curve_research_state(db, curve_id: int) -> dict:
    """Return authoritative research/rank state for one curve."""
    rows = _curve_rows(db, curve_ids=[int(curve_id)])
    if not rows:
        raise ValueError(f"curve #{int(curve_id)} not found")
    evidence = _evidence_by_curve(db, [int(curve_id)])
    return _project_curve(rows[0], evidence.get(int(curve_id), []))


def list_curve_research_states(
    db,
    *,
    curve_ids: Iterable[int] | None = None,
    family: str | None = None,
    minimum_lower: int | None = None,
    compact: bool = False,
) -> list[dict]:
    """Return authoritative curve states, strongest rigorous lower bound first.

    Filtering by minimum_lower is applied after evidence reduction so a curve
    cannot disappear merely because its compatibility columns lag behind the
    structured rank-evidence ledger.
    """
    rows = _curve_rows(
        db,
        curve_ids=curve_ids,
        family=family,
        compact=compact,
    )
    if not rows:
        return []
    ids = [int(row["id"]) for row in rows]
    evidence = _evidence_by_curve(db, ids, compact=compact)
    projected = [
        _project_curve(row, evidence.get(int(row["id"]), []))
        for row in rows
    ]
    if minimum_lower is not None:
        threshold = int(minimum_lower)
        projected = [
            state for state in projected
            if int(state["rigorous_lower"]) >= threshold
        ]
    projected.sort(
        key=lambda state: (
            -int(state["rigorous_lower"]),
            -(float(state["score"]) if state["score"] is not None else float("-inf")),
            -int(state["curve_id"]),
        )
    )
    return projected


def curve_research_state_map(
    db,
    *,
    curve_ids: Iterable[int] | None = None,
    family: str | None = None,
    compact: bool = False,
) -> dict[int, dict]:
    """Return the bulk projection keyed by curve id."""
    return {
        int(state["curve_id"]): state
        for state in list_curve_research_states(
            db,
            curve_ids=curve_ids,
            family=family,
            compact=compact,
        )
    }
