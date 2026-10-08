"""Human-only Analysis case workflow state.

Scientific claims remain owned by Curve Research State, Point Ledger, Rank
Evidence, lattices, and search artifacts.  This store only tracks what the
researcher wants to do next with a retained curve.
"""
from __future__ import annotations

from rank42.ui_store import now


CASE_WORKFLOW_STATUSES = ("open", "deferred", "resolved")
CASE_PRIORITY_MIN = 0
CASE_PRIORITY_MAX = 300
CASE_PRIORITY_PRESETS = {
    "Low": 50,
    "Normal": 100,
    "High": 200,
    "Critical": 300,
}
CASE_RESEARCH_GOALS = (
    "",
    "Close the rigorous rank interval",
    "Resolve candidate point independence",
    "Find another independent point",
    "Understand Mordell–Weil geometry",
    "Push quartic / covering geometry",
    "Explain family / specialization structure",
    "Compare related fibers",
    "Other",
)


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def analysis_case_schema_ready(db):
    return _table_exists(db, "analysis_cases")


def get_analysis_case(db, curve_id):
    if not analysis_case_schema_ready(db):
        return None
    return db.execute(
        "SELECT * FROM analysis_cases WHERE curve_id=?",
        (int(curve_id),),
    ).fetchone()


def analysis_case_map_readonly(db, curve_ids=None):
    """Return current human case state without creating/migrating schema."""
    if not analysis_case_schema_ready(db):
        return {}
    if curve_ids is None:
        rows = db.execute("SELECT * FROM analysis_cases").fetchall()
        return {int(row["curve_id"]): row for row in rows}

    ids = sorted({int(value) for value in curve_ids})
    if not ids:
        return {}

    out = {}
    for offset in range(0, len(ids), 800):
        chunk = ids[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT * FROM analysis_cases WHERE curve_id IN ({placeholders})",
            chunk,
        ).fetchall()
        out.update({int(row["curve_id"]): row for row in rows})
    return out


def list_analysis_cases(db, *, workflow_status=None, limit=1000):
    if not analysis_case_schema_ready(db):
        return []
    sql = "SELECT * FROM analysis_cases"
    vals = []
    if workflow_status is not None:
        status = str(workflow_status)
        if status not in CASE_WORKFLOW_STATUSES:
            raise ValueError(f"unsupported Analysis case workflow status {status!r}")
        sql += " WHERE workflow_status=?"
        vals.append(status)
    sql += (
        " ORDER BY CASE workflow_status "
        "WHEN 'open' THEN 0 WHEN 'deferred' THEN 1 ELSE 2 END,"
        "priority DESC,updated_at DESC,id DESC LIMIT ?"
    )
    vals.append(max(1, int(limit)))
    return db.execute(sql, vals).fetchall()


def _clean_text(value, *, limit):
    text = str(value or "").strip()
    return text[: int(limit)]


def save_analysis_case(
    db,
    *,
    curve_id,
    campaign_id=None,
    priority=100,
    workflow_status="open",
    research_goal="",
    reason="",
    note="",
):
    """Create/update one curve's human workflow state.

    This API deliberately has no rank/proof/point-result parameters.
    """
    if not analysis_case_schema_ready(db):
        raise RuntimeError(
            "Analysis case schema is not ready; run the baseline UI migration first"
        )

    curve_id = int(curve_id)
    curve = db.execute("SELECT id FROM curves WHERE id=?", (curve_id,)).fetchone()
    if curve is None:
        raise ValueError(f"curve #{curve_id} not found")

    if campaign_id in (None, ""):
        campaign_id = None
    else:
        campaign_id = int(campaign_id)
        campaign = db.execute(
            "SELECT id FROM research_campaigns WHERE id=?",
            (campaign_id,),
        ).fetchone()
        if campaign is None:
            raise ValueError(f"campaign #{campaign_id} not found")

    priority = int(priority)
    if priority < CASE_PRIORITY_MIN or priority > CASE_PRIORITY_MAX:
        raise ValueError(
            f"Analysis case priority must be between "
            f"{CASE_PRIORITY_MIN} and {CASE_PRIORITY_MAX}"
        )

    workflow_status = str(workflow_status)
    if workflow_status not in CASE_WORKFLOW_STATUSES:
        raise ValueError(
            f"unsupported Analysis case workflow status {workflow_status!r}"
        )

    research_goal = _clean_text(research_goal, limit=240)
    reason = _clean_text(reason, limit=1000)
    note = _clean_text(note, limit=8000)
    ts = now()

    db.execute(
        """INSERT INTO analysis_cases(
               curve_id,campaign_id,priority,workflow_status,research_goal,
               reason,note,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(curve_id) DO UPDATE SET
               campaign_id=excluded.campaign_id,
               priority=excluded.priority,
               workflow_status=excluded.workflow_status,
               research_goal=excluded.research_goal,
               reason=excluded.reason,
               note=excluded.note,
               updated_at=excluded.updated_at""",
        (
            curve_id,
            campaign_id,
            priority,
            workflow_status,
            research_goal,
            reason,
            note,
            ts,
            ts,
        ),
    )
    db.commit()
    return get_analysis_case(db, curve_id)


def case_priority_label(priority):
    value = int(priority)
    if value >= 250:
        return "Critical"
    if value >= 150:
        return "High"
    if value <= 75:
        return "Low"
    return "Normal"
