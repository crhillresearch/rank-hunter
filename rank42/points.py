"""Unified rational-point ledger for Rank Hunter v0.8."""
from __future__ import annotations

import json

from rank42.curve_research_state import curve_research_state_map
from rank42.db import now


def upsert_point(db, *, curve_id, x, y, source, role="candidate", exact_verified=False,
                 independence_status="unknown", rigorous_independent=False, search_ref=None,
                 plugin_id=None, metadata=None):
    ts = now()
    db.execute(
        """
        INSERT INTO points(curve_id,x,y,source,role,exact_verified,independence_status,
                           rigorous_independent,search_ref,plugin_id,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(curve_id,x,y) DO UPDATE SET
            exact_verified=MAX(points.exact_verified, excluded.exact_verified),
            rigorous_independent=MAX(points.rigorous_independent, excluded.rigorous_independent),
            independence_status=CASE
                WHEN excluded.rigorous_independent=1 THEN 'rigorous_independent'
                WHEN points.rigorous_independent=1 THEN points.independence_status
                WHEN excluded.independence_status!='unknown' THEN excluded.independence_status
                ELSE points.independence_status END,
            role=CASE
                WHEN excluded.role IN ('generator','basis','rigorous_witness') THEN excluded.role
                ELSE points.role END,
            source=CASE WHEN points.source='' THEN excluded.source ELSE points.source END,
            search_ref=CASE
                WHEN points.rigorous_independent=1 AND points.search_ref IS NOT NULL
                    THEN points.search_ref
                ELSE COALESCE(excluded.search_ref,points.search_ref) END,
            plugin_id=COALESCE(excluded.plugin_id,points.plugin_id),
            metadata_json=CASE
                WHEN points.rigorous_independent=1
                     AND points.metadata_json IS NOT NULL
                     AND points.metadata_json NOT IN ('', '{}')
                    THEN points.metadata_json
                ELSE excluded.metadata_json END,
            updated_at=excluded.updated_at
        """,
        (
            int(curve_id), str(x), str(y), str(source), str(role),
            1 if exact_verified else 0, str(independence_status), 1 if rigorous_independent else 0,
            search_ref, plugin_id, json.dumps(metadata or {}, sort_keys=True), ts, ts,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM points WHERE curve_id=? AND x=? AND y=?", (int(curve_id), str(x), str(y))).fetchone()

    # Crossing into rigorous point evidence is a retained-research boundary.
    # Torsion enrichment is best-effort and never invalidates the point write.
    if rigorous_independent:
        try:
            from rank42.torsion import enrich_retained_curve_torsion
            enrich_retained_curve_torsion(db, int(curve_id))
        except Exception:
            pass
    return row


def record_point_discovery(
    db,
    *,
    curve_id,
    source,
    outcome,
    point_id=None,
    tier=None,
    search_ref=None,
    campaign_id=None,
    parameter=None,
    pipeline_run_id=None,
    pipeline_candidate_id=None,
    pipeline_stage_index=None,
    pipeline_stage_id=None,
    chart_index=None,
    center=None,
    scale=None,
    height=None,
    requested_denominator_low=None,
    requested_denominator_high=None,
    effective_denominator_low=None,
    effective_denominator_high=None,
    chart_x=None,
    chart_w=None,
    projective=None,
    stored_x=None,
    stored_y=None,
    exact_verified=False,
    error_class=None,
    error=None,
    metadata=None,
):
    """Append one immutable point-search observation.

    The canonical points row remains the current scientific point state.
    This ledger preserves every search geometry that rediscovered it, plus
    failed exact map-back observations that do not have a canonical point row.
    """
    ts = now()
    cur = db.execute(
        """
        INSERT INTO point_discoveries(
            curve_id,point_id,source,outcome,tier,search_ref,campaign_id,parameter,
            pipeline_run_id,pipeline_candidate_id,pipeline_stage_index,
            pipeline_stage_id,chart_index,center,scale,height,
            requested_denominator_low,requested_denominator_high,
            effective_denominator_low,effective_denominator_high,
            chart_x,chart_w,projective_json,stored_x,stored_y,exact_verified,
            error_class,error,metadata_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(curve_id),
            None if point_id is None else int(point_id),
            str(source),
            str(outcome),
            None if tier is None else str(tier),
            search_ref,
            None if campaign_id is None else int(campaign_id),
            None if parameter is None else str(parameter),
            None if pipeline_run_id is None else int(pipeline_run_id),
            None if pipeline_candidate_id is None else int(pipeline_candidate_id),
            None if pipeline_stage_index is None else int(pipeline_stage_index),
            None if pipeline_stage_id is None else str(pipeline_stage_id),
            None if chart_index is None else int(chart_index),
            None if center is None else str(center),
            None if scale is None else str(scale),
            None if height is None else int(height),
            None if requested_denominator_low is None else int(requested_denominator_low),
            None if requested_denominator_high is None else int(requested_denominator_high),
            None if effective_denominator_low is None else int(effective_denominator_low),
            None if effective_denominator_high is None else int(effective_denominator_high),
            None if chart_x is None else str(chart_x),
            None if chart_w is None else str(chart_w),
            None if projective is None else json.dumps(projective, sort_keys=True),
            None if stored_x is None else str(stored_x),
            None if stored_y is None else str(stored_y),
            1 if exact_verified else 0,
            None if error_class is None else str(error_class),
            None if error is None else str(error),
            json.dumps(metadata or {}, sort_keys=True),
            ts,
        ),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM point_discoveries WHERE id=?",
        (int(cur.lastrowid),),
    ).fetchone()


def list_point_discoveries(
    db,
    *,
    curve_id=None,
    point_id=None,
    outcome=None,
    limit=1000,
):
    """Return append-only point-search observations newest first."""
    sql = "SELECT * FROM point_discoveries WHERE 1=1"
    values = []
    if curve_id is not None:
        sql += " AND curve_id=?"
        values.append(int(curve_id))
    if point_id is not None:
        sql += " AND point_id=?"
        values.append(int(point_id))
    if outcome is not None:
        sql += " AND outcome=?"
        values.append(str(outcome))
    sql += " ORDER BY id DESC LIMIT ?"
    values.append(max(1, int(limit)))
    return db.execute(sql, values).fetchall()


def _enrich_point_rows_with_curve_state(db, rows):
    """Attach authoritative reduced curve rank state to point query rows.

    The legacy field names are preserved because Points UI code consumes them,
    but their values now come from Curve Research State rather than compatibility
    columns on curves.
    """
    rows = list(rows)
    if not rows:
        return []

    states = curve_research_state_map(
        db,
        curve_ids={int(row["curve_id"]) for row in rows},
    )
    out = []
    for record in rows:
        row = dict(record)
        state = states[int(row["curve_id"])]
        lower = int(state["rigorous_lower"] or 0)
        row["family"] = state["family"]
        row["parameter"] = state["parameter"]
        row["curve_exact_rank"] = state["exact_rank"]
        row["curve_descent_upper"] = state["rigorous_upper"]
        row["curve_rigorous_upper"] = state["rigorous_upper"]
        row["curve_rank_inconsistent"] = bool(state["rank_inconsistent"])
        row["rigorous_lower"] = (
            lower if lower > 0 or state["exact_rank"] is not None else None
        )
        out.append(row)
    return out


def list_points(db, *, curve_id=None, independence_status=None, exact_only=False, exact_verified=None, limit=1000):
    sql = "SELECT p.* FROM points p WHERE 1=1"
    vals=[]
    if curve_id is not None:
        sql += " AND p.curve_id=?"; vals.append(int(curve_id))
    if independence_status:
        sql += " AND p.independence_status=?"; vals.append(str(independence_status))
    if exact_verified is not None:
        sql += " AND p.exact_verified=?"
        vals.append(1 if exact_verified else 0)
    elif exact_only:
        sql += " AND p.exact_verified=1"
    sql += " ORDER BY p.rigorous_independent DESC,p.exact_verified DESC,p.id DESC LIMIT ?"
    vals.append(int(limit))
    return _enrich_point_rows_with_curve_state(db, db.execute(sql, vals).fetchall())


def list_actionable_points(db, *, curve_id=None, attention=None, limit=2000):
    """Return unresolved point work with filters applied before the final LIMIT.

    Rank priority is applied only after attaching authoritative Curve Research
    State, so stale compatibility columns cannot reorder the actionable queue.
    """
    sql = """
        SELECT p.*
        FROM points p
        WHERE (
            p.exact_verified=0
            OR (
                p.exact_verified=1
                AND p.rigorous_independent=0
                AND p.independence_status IN ('unknown','numerical_novel','inconclusive')
            )
        )
    """
    vals=[]
    if curve_id is not None:
        sql += " AND p.curve_id=?"
        vals.append(int(curve_id))

    attention = str(attention or "all")
    if attention == "promising":
        sql += " AND p.exact_verified=1 AND p.independence_status='numerical_novel'"
    elif attention == "unresolved":
        sql += " AND p.exact_verified=1 AND p.independence_status IN ('unknown','inconclusive')"
    elif attention == "needs_exact":
        sql += " AND p.exact_verified=0"

    rows = _enrich_point_rows_with_curve_state(db, db.execute(sql, vals).fetchall())

    def priority(row):
        if int(row["exact_verified"] or 0) and row["independence_status"] == "numerical_novel":
            return 0
        if int(row["exact_verified"] or 0) and row["independence_status"] == "inconclusive":
            return 1
        if int(row["exact_verified"] or 0) and row["independence_status"] == "unknown":
            return 2
        return 3

    rows.sort(
        key=lambda row: (
            priority(row),
            -int(row["rigorous_lower"] or 0),
            -int(row["id"]),
        )
    )
    return rows[:int(limit)]



def set_point_hard_flag(db, point_id, hard=True, *, reason=None):
    """Persist or clear a researcher-managed hard-case bookmark."""
    ts = now()
    db.execute(
        """
        UPDATE points
        SET hard_flag=?,
            hard_reason=?,
            hard_flagged_at=?,
            updated_at=?
        WHERE id=?
        """,
        (
            1 if hard else 0,
            str(reason) if hard and reason else None,
            ts if hard else None,
            ts,
            int(point_id),
        ),
    )
    db.commit()
    return db.execute("SELECT * FROM points WHERE id=?", (int(point_id),)).fetchone()


def list_hard_points(db, *, curve_id=None, limit=1000):
    """Return bookmarked unresolved exact points, newest flags first."""
    sql = """
        SELECT p.*
        FROM points p
        WHERE p.hard_flag=1
          AND p.exact_verified=1
          AND p.rigorous_independent=0
          AND p.independence_status!='dependent'
    """
    vals=[]
    if curve_id is not None:
        sql += " AND p.curve_id=?"
        vals.append(int(curve_id))
    sql += " ORDER BY p.hard_flagged_at DESC, p.id DESC LIMIT ?"
    vals.append(int(limit))
    return _enrich_point_rows_with_curve_state(db, db.execute(sql, vals).fetchall())

def point_counts(db):
    row = db.execute(
        """SELECT COUNT(*) total,
                  SUM(exact_verified=1) exact_verified,
                  SUM(independence_status='numerical_novel') numerical_novel,
                  SUM(rigorous_independent=1) rigorous_independent
           FROM points"""
    ).fetchone()
    return {k:int(row[k] or 0) for k in row.keys()}


def ingest_generator_json(db, curve_id, generators_json, *, source="stored_generators", rigorous=False):
    try:
        points=json.loads(generators_json or "[]")
    except Exception:
        return 0
    count=0
    for rec in points:
        if not isinstance(rec,(list,tuple)) or len(rec)<2:
            continue
        upsert_point(
            db, curve_id=curve_id, x=rec[0], y=rec[1], source=source,
            role="rigorous_witness" if rigorous else "generator", exact_verified=True,
            independence_status="rigorous_independent" if rigorous else "unknown",
            rigorous_independent=rigorous,
        )
        count+=1
    return count


def rigorous_witness_basis(db, curve_id, E):
    """Return the active exact witness basis supporting the curve lower bound.

    curves.generators_json is the active compatibility basis and may be
    replaced by exact saturation/preconditioning. Prefer that ordered basis
    when it contains enough exact points, then fall back to historical rigorous
    point-ledger witnesses. Every returned point is reconstructed on E
    exactly; no rank claim is inferred from coordinates alone.
    """
    from rank42.curve_research_state import get_curve_research_state
    from rank42.db import get_curve
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    state = get_curve_research_state(db, int(curve_id))
    required = int(state["rigorous_lower"])
    basis=[]; seen=set()

    def add_xy(x, y):
        try:
            P=E(x, y)
        except Exception:
            return False
        if P.is_zero():
            return False
        Q=-P
        key=min((str(P[0]),str(P[1])),(str(Q[0]),str(Q[1])))
        if key in seen:
            return False
        seen.add(key); basis.append(P)
        return True

    try:
        active=json.loads(row["generators_json"] or "[]")
    except Exception:
        active=[]
    for rec in active:
        if not isinstance(rec,(list,tuple)) or len(rec)<2:
            continue
        add_xy(rec[0], rec[1])
        if len(basis) >= required:
            return basis, required, True

    records = db.execute(
        """SELECT * FROM points
           WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1
           ORDER BY CASE role WHEN 'generic_section' THEN 0 WHEN 'rigorous_witness' THEN 1 WHEN 'basis' THEN 2 ELSE 3 END, id ASC""",
        (int(curve_id),),
    ).fetchall()
    for rec in records:
        add_xy(rec["x"], rec["y"])
        if len(basis) >= required:
            break
    return basis, required, len(basis) >= required
