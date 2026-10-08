"""Read-only curve-analysis workspace helpers.

The Analyze cockpit is intentionally evidence-first: opening the page must not
launch Sage, PARI, mwrank, ratpoints, or any other expensive worker.  This
module only summarizes durable database state and emits deterministic next
actions.
"""
from __future__ import annotations

import json

from rank42.analysis_planner import (
    analysis_plan,
    planner_immediate_actions,
    planner_research_paths,
)
from rank42.analysis_method_history import (
    upper_bound_method_history,
    upper_bound_method_history_map,
)
from rank42.analysis_case_store import (
    analysis_case_map_readonly,
    case_priority_label,
)
from rank42.curve_arithmetic_state import curve_arithmetic_state_map
from rank42.curve_research_state import get_curve_research_state, list_curve_research_states
from rank42.family_evidence import family_evidence_state_for_curve
from rank42.manage_store import (
    campaign_curve_ids_readonly,
    current_campaign_readonly,
)


ACTIONABLE_POINT_STATUSES = {"unknown", "numerical_novel", "inconclusive"}


def _table_exists(db, name):
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone()
    return row is not None


def _loads(value, default):
    try:
        out = json.loads(value or "")
    except Exception:
        return default
    return out



def _table_has_column(db, table, column):
    if not _table_exists(db, table):
        return False
    return any(
        str(row["name"]) == str(column)
        for row in db.execute(f"PRAGMA table_info({table})").fetchall()
    )


def _active_campaign_case_ids(db):
    """Return selected active Campaign plus retained curves, without mutation."""
    campaign = current_campaign_readonly(db)
    if campaign is None:
        return None, set()
    return (
        campaign,
        set(campaign_curve_ids_readonly(db, int(campaign["id"]))),
    )



def _point_attention_map(db):
    rows = db.execute(
        """SELECT curve_id,
                  SUM(CASE
                        WHEN COALESCE(exact_verified,0)=1
                         AND COALESCE(rigorous_independent,0)=1
                        THEN 1 ELSE 0 END) AS witnesses,
                  SUM(CASE
                        WHEN COALESCE(exact_verified,0)=1
                         AND COALESCE(rigorous_independent,0)=0
                         AND COALESCE(independence_status,'unknown')
                             IN ('unknown','numerical_novel','inconclusive')
                        THEN 1 ELSE 0 END) AS actionable,
                  SUM(CASE
                        WHEN COALESCE(exact_verified,0)=1
                         AND COALESCE(independence_status,'')='numerical_novel'
                        THEN 1 ELSE 0 END) AS numerical_novel,
                  SUM(CASE
                        WHEN COALESCE(hard_flag,0)=1
                         AND COALESCE(exact_verified,0)=1
                         AND COALESCE(rigorous_independent,0)=0
                         AND COALESCE(independence_status,'unknown')!='dependent'
                        THEN 1 ELSE 0 END) AS hard
           FROM points
           GROUP BY curve_id"""
    ).fetchall()
    return {
        int(row["curve_id"]): {
            "witnesses": int(row["witnesses"] or 0),
            "actionable": int(row["actionable"] or 0),
            "numerical_novel": int(row["numerical_novel"] or 0),
            "hard": int(row["hard"] or 0),
        }
        for row in rows
    }


def _evidence_activity_map(db):
    rows = db.execute(
        """SELECT curve_id,COUNT(*) AS evidence_rows,
                  SUM(CASE
                        WHEN COALESCE(timed_out,0)=1 OR status='timeout'
                        THEN 1 ELSE 0 END) AS timeouts,
                  MAX(created_at) AS last_attempt_at
           FROM rank_evidence
           GROUP BY curve_id"""
    ).fetchall()
    return {
        int(row["curve_id"]): {
            "evidence_rows": int(row["evidence_rows"] or 0),
            "timeouts": int(row["timeouts"] or 0),
            "last_attempt_at": row["last_attempt_at"],
        }
        for row in rows
    }


def _count_map(db, table, *, curve_column="curve_id"):
    if not _table_exists(db, table):
        return {}
    rows = db.execute(
        f"""SELECT {curve_column} AS curve_id,COUNT(*) AS n
            FROM {table}
            WHERE {curve_column} IS NOT NULL
            GROUP BY {curve_column}"""
    ).fetchall()
    return {int(row["curve_id"]): int(row["n"] or 0) for row in rows}


def research_inbox(db, *, limit=100):
    """Return a read-only researcher queue derived from durable state.

    The queue is attention-oriented rather than a passive Curve inventory:
    active Campaign cases first, then unresolved/recent work, then resolved
    backlog. Scientific rank truth comes only from Curve Research State.
    """
    states = list_curve_research_states(db)
    campaign, campaign_curve_ids = _active_campaign_case_ids(db)
    campaign_id = None if campaign is None else int(campaign["id"])
    campaign_name = None if campaign is None else str(campaign["name"])
    campaign_target = (
        None
        if campaign is None or campaign["target_rank"] is None
        else int(campaign["target_rank"])
    )

    cases = analysis_case_map_readonly(
        db,
        [int(state["curve_id"]) for state in states],
    )
    campaign_curve_rows = [
        state["curve"]
        for state in states
        if int(state["curve_id"]) in campaign_curve_ids
    ]
    campaign_upper_history = upper_bound_method_history_map(
        db,
        campaign_curve_rows,
    )
    points = _point_attention_map(db)
    evidence = _evidence_activity_map(db)
    lattices = _count_map(db, "mw_lattices")
    quartics = _count_map(db, "quartic_searches")
    pipeline = _count_map(db, "search_pipeline_candidates")

    rows = []
    for research_state in states:
        curve = research_state["curve"]
        curve_id = int(research_state["curve_id"])
        workflow = cases.get(curve_id)
        workflow_status = None if workflow is None else str(workflow["workflow_status"])
        workflow_priority = 100 if workflow is None else int(workflow["priority"])
        lower = int(research_state["rigorous_lower"] or 0)
        upper = research_state["rigorous_upper"]
        exact = research_state["exact_rank"]
        inconsistent = bool(research_state["rank_inconsistent"])
        p = points.get(
            curve_id,
            {"witnesses": 0, "actionable": 0, "numerical_novel": 0, "hard": 0},
        )
        e = evidence.get(
            curve_id,
            {"evidence_rows": 0, "timeouts": 0, "last_attempt_at": None},
        )
        witness_gap = max(0, lower - int(p["witnesses"]))
        baseline = None if curve["generic_lower"] is None else int(curve["generic_lower"])
        extra = None if baseline is None else max(0, lower - baseline)
        in_campaign = curve_id in campaign_curve_ids
        campaign_gap = (
            None
            if not in_campaign or campaign_target is None or inconsistent
            else max(0, campaign_target - lower)
        )

        lightweight = {
            "curve": curve,
            "state": {
                "rigorous_lower": lower,
                "rigorous_upper": upper,
                "exact_rank": exact,
                "inconsistent": inconsistent,
            },
            "actionable_points": range(int(p["actionable"])),
            "numerical_novel_points": range(int(p["numerical_novel"])),
            "witnesses": range(int(p["witnesses"])),
            "witness_gap": witness_gap,
            "lattices": range(int(lattices.get(curve_id, 0))),
            "evidence": range(int(e["evidence_rows"])),
            "evidence_timeouts": int(e["timeouts"]),
            "quartic_searches": range(int(quartics.get(curve_id, 0))),
            "pipeline_candidates": range(int(pipeline.get(curve_id, 0))),
            "family_section_lower": baseline,
            "extra_directions_lower": extra,
            "family_pool_id": None,
            "method_history": {
                "upper_bound": campaign_upper_history.get(curve_id, {}),
            },
        }
        plan = analysis_plan(
            lightweight,
            target_rank=campaign_target if in_campaign else None,
        )
        actions = planner_immediate_actions(plan)
        primary = actions[0] if actions else None

        needs_attention = bool(
            inconsistent
            or exact is None
            or int(p["actionable"]) > 0
            or int(p["hard"]) > 0
            or witness_gap > 0
            or (extra is not None and extra > 0)
            or (campaign_gap is not None and campaign_gap > 0)
        )
        queue_bucket = (
            "active_campaign"
            if in_campaign and needs_attention
            else "recent_unresolved"
            if needs_attention
            else "backlog"
        )
        tier = {
            "active_campaign": 0,
            "recent_unresolved": 1,
            "backlog": 2,
        }[queue_bucket]
        activity_at = max(
            str(curve["updated_at"] or ""),
            str(e["last_attempt_at"] or ""),
        )

        reasons = []
        if inconsistent:
            reasons.append("rank conflict")
        if witness_gap:
            reasons.append(f"witness gap {witness_gap}")
        if p["actionable"]:
            reasons.append(f"{p['actionable']} unresolved point(s)")
        if p["hard"]:
            reasons.append(f"{p['hard']} hard case(s)")
        if extra:
            reasons.append(f"≥{extra} extra direction(s)")
        if in_campaign:
            reasons.append(f"active campaign: {campaign_name}")

        rows.append({
            "id": curve_id,
            "curve_id": curve_id,
            "family": str(curve["family"]),
            "parameter": str(curve["parameter"]),
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": exact,
            "rank_inconsistent": inconsistent,
            "family_section_lower": baseline,
            "extra_directions_lower": extra,
            "unresolved_exact_points": int(p["actionable"]),
            "numerical_novel_points": int(p["numerical_novel"]),
            "hard_cases": int(p["hard"]),
            "rigorous_witnesses": int(p["witnesses"]),
            "witness_gap": witness_gap,
            "last_attempt_at": e["last_attempt_at"],
            "last_activity_at": activity_at,
            "next_action": None if primary is None else str(primary["title"]),
            "next_action_page": None if primary is None else primary.get("page"),
            "next_action_exhausted": bool(primary and primary.get("exhausted")),
            "attention_reasons": reasons,
            "needs_attention": needs_attention,
            "active_campaign": in_campaign,
            "active_campaign_id": campaign_id if in_campaign else None,
            "active_campaign_name": campaign_name if in_campaign else None,
            "campaign_target_rank": campaign_target if in_campaign else None,
            "campaign_target_gap": campaign_gap,
            "campaign_rank_growth_lower": (
                extra if in_campaign else None
            ),
            "campaign_followup_owner": (
                None if not in_campaign or primary is None else primary.get("page")
            ),
            "campaign_followup_exhausted": bool(
                in_campaign and primary and primary.get("exhausted")
            ),
            "campaign_needs_rank_proof": bool(
                in_campaign
                and exact is None
                and not inconsistent
                and (upper is None or int(upper) != lower)
            ),
            "campaign_proof_methods_exhausted": bool(
                in_campaign
                and campaign_upper_history.get(curve_id, {}).get(
                    "all_exhausted_at_budget"
                )
            ),
            "campaign_stronger_proof_budget_available": bool(
                in_campaign
                and campaign_upper_history.get(curve_id, {}).get(
                    "stronger_budget_available"
                )
            ),
            "queue_bucket": queue_bucket,
            "workflow_case_id": None if workflow is None else int(workflow["id"]),
            "workflow_status": workflow_status,
            "workflow_priority": workflow_priority,
            "workflow_priority_label": (
                "—" if workflow is None else case_priority_label(workflow_priority)
            ),
            "workflow_goal": "" if workflow is None else str(workflow["research_goal"] or ""),
            "workflow_reason": "" if workflow is None else str(workflow["reason"] or ""),
            "workflow_note": "" if workflow is None else str(workflow["note"] or ""),
            "workflow_campaign_id": (
                None
                if workflow is None or workflow["campaign_id"] is None
                else int(workflow["campaign_id"])
            ),
            "workflow_updated_at": None if workflow is None else workflow["updated_at"],
            "_workflow_order": (
                0
                if workflow_status == "open"
                else 2
                if workflow_status == "deferred"
                else 3
                if workflow_status == "resolved"
                else 1
            ),
            "_tier": tier,
        })

    # Derived scientific/campaign attention tiers remain primary. Human workflow
    # state only orders cases *within* the same derived tier.
    grouped = []
    for tier in (0, 1, 2):
        chunk = [rec for rec in rows if int(rec["_tier"]) == tier]
        chunk.sort(
            key=lambda rec: (
                str(rec["last_activity_at"] or ""),
                int(rec["rigorous_lower"]),
                int(rec["curve_id"]),
            ),
            reverse=True,
        )
        chunk.sort(key=lambda rec: int(rec["workflow_priority"]), reverse=True)
        chunk.sort(key=lambda rec: int(rec["_workflow_order"]))
        grouped.extend(chunk)
    for rec in grouped:
        rec.pop("_tier", None)
        rec.pop("_workflow_order", None)
    return grouped[: max(1, int(limit))]


def research_inbox_default_curve_id(inbox, *, explicit_analyze_curve_id=None, semantic_curve_id=None):
    """Choose the Inbox default without overriding an explicit Analyze-local choice.

    Fresh Analyze visits follow the audit workflow: active Campaign unresolved
    curves first, then recent unresolved work, then the global backlog.
    """
    rows = list(inbox or [])
    if not rows:
        return None
    ids = {int(row["curve_id"]) for row in rows}

    try:
        explicit = int(explicit_analyze_curve_id)
    except (TypeError, ValueError):
        explicit = None
    if explicit in ids:
        return explicit

    campaign_rows = [
        row for row in rows
        if str(row.get("queue_bucket") or "") == "active_campaign"
    ]
    if campaign_rows:
        return int(campaign_rows[0]["curve_id"])

    try:
        semantic = int(semantic_curve_id)
    except (TypeError, ValueError):
        semantic = None
    if semantic in ids:
        return semantic

    unresolved = [
        row for row in rows
        if str(row.get("queue_bucket") or "") == "recent_unresolved"
    ]
    if unresolved:
        return int(unresolved[0]["curve_id"])
    return int(rows[0]["curve_id"])


def case_board(snapshot):
    """Return the selected case's actionable, non-duplicative Work Center state."""
    plan = analysis_plan(snapshot)
    actions = planner_immediate_actions(plan)
    state = snapshot["state"]
    upper_history = dict(
        (snapshot.get("method_history") or {}).get("upper_bound") or {}
    )
    hard_cases = sum(
        bool(int(rec["hard_flag"] or 0))
        and bool(int(rec["exact_verified"] or 0))
        and not bool(int(rec["rigorous_independent"] or 0))
        and str(rec["independence_status"] or "unknown") != "dependent"
        for rec in snapshot.get("points") or []
    )
    latest_evidence = snapshot["evidence"][0] if snapshot.get("evidence") else None
    return {
        "curve_id": int(snapshot["curve"]["id"]),
        "rank": {
            "rigorous_lower": int(state.get("rigorous_lower") or 0),
            "rigorous_upper": state.get("rigorous_upper"),
            "exact_rank": state.get("exact_rank"),
            "conditional_analytic_upper": state.get("conditional_analytic_upper"),
            "inconsistent": bool(state.get("inconsistent")),
        },
        "subgroup": {
            "rigorous_witnesses": len(snapshot.get("witnesses") or []),
            "witness_gap": int(snapshot.get("witness_gap") or 0),
            "family_section_lower": snapshot.get("family_section_lower"),
            "extra_directions_lower": snapshot.get("extra_directions_lower"),
        },
        "material": {
            "unresolved_exact_points": len(snapshot.get("actionable_points") or []),
            "numerical_novel_points": len(snapshot.get("numerical_novel_points") or []),
            "dependent_points": len(snapshot.get("dependent_points") or []),
            "hard_cases": int(hard_cases),
        },
        "attempts": {
            "rank_evidence_rows": len(snapshot.get("evidence") or []),
            "evidence_timeouts": int(snapshot.get("evidence_timeouts") or 0),
            "last_evidence_id": None if latest_evidence is None else int(latest_evidence["id"]),
            "last_evidence_at": None if latest_evidence is None else latest_evidence["created_at"],
            "upper_bound_history": upper_history,
        },
        "actions": actions,
        "primary_action": actions[0] if actions else None,
        "planner_version": int(plan["planner_version"]),
    }


def curve_analysis_snapshot(db, curve_id):
    research_state = get_curve_research_state(db, int(curve_id))
    curve = research_state["curve"]
    state = {
        "curve_id": int(research_state["curve_id"]),
        "rigorous_lower": int(research_state["rigorous_lower"]),
        "rigorous_upper": research_state["rigorous_upper"],
        "exact_rank": research_state["exact_rank"],
        "conditional_analytic_upper": research_state["conditional_analytic_upper"],
        "numerical_rank_signal": research_state["numerical_rank_signal"],
        "inconsistent": bool(research_state["rank_inconsistent"]),
    }
    points = db.execute(
        """SELECT * FROM points
           WHERE curve_id=?
           ORDER BY rigorous_independent DESC, exact_verified DESC, id DESC""",
        (int(curve_id),),
    ).fetchall()

    exact_points = [p for p in points if int(p["exact_verified"] or 0)]
    witnesses = [p for p in exact_points if int(p["rigorous_independent"] or 0)]
    candidates = [p for p in exact_points if not int(p["rigorous_independent"] or 0)]
    actionable = [
        p for p in candidates
        if str(p["independence_status"] or "unknown") in ACTIONABLE_POINT_STATUSES
    ]
    numerical_novel = [
        p for p in candidates
        if str(p["independence_status"] or "") == "numerical_novel"
    ]
    dependent = [
        p for p in candidates
        if str(p["independence_status"] or "") == "dependent"
    ]

    evidence = db.execute(
        """SELECT * FROM rank_evidence
           WHERE curve_id=?
           ORDER BY id DESC LIMIT 250""",
        (int(curve_id),),
    ).fetchall()
    evidence_timeouts = sum(
        bool(int(r["timed_out"] or 0) or str(r["status"] or "") == "timeout")
        for r in evidence
    )
    upper_bound_history = upper_bound_method_history(
        db,
        curve_id=int(curve_id),
        curve_row=curve,
    )

    lattices = []
    if _table_exists(db, "mw_lattices"):
        lattices = db.execute(
            """SELECT * FROM mw_lattices
               WHERE curve_id=? ORDER BY id DESC LIMIT 50""",
            (int(curve_id),),
        ).fetchall()

    quartics = []
    if _table_exists(db, "quartic_searches"):
        quartics = db.execute(
            """SELECT * FROM quartic_searches
               WHERE curve_id=? ORDER BY id DESC LIMIT 200""",
            (int(curve_id),),
        ).fetchall()

    pipeline_rows = []
    if _table_exists(db, "search_pipeline_candidates"):
        pipeline_rows = db.execute(
            """SELECT c.*, r.pipeline_name, r.status AS run_status,
                      r.started_at AS run_started_at, r.finished_at AS run_finished_at
               FROM search_pipeline_candidates c
               JOIN search_pipeline_runs r ON r.id=c.run_id
               WHERE c.curve_id=?
               ORDER BY c.id DESC LIMIT 200""",
            (int(curve_id),),
        ).fetchall()

    events = db.execute(
        """SELECT * FROM events
           WHERE curve_id=? ORDER BY id DESC LIMIT 100""",
        (int(curve_id),),
    ).fetchall()

    rigorous_lower = int(state["rigorous_lower"] or 0)
    witness_gap = max(0, rigorous_lower - len(witnesses))

    family_section_lower = (
        None if curve["generic_lower"] is None else int(curve["generic_lower"])
    )
    extra_directions_lower = (
        None
        if family_section_lower is None
        else max(0, rigorous_lower - family_section_lower)
    )
    family_generic_state = family_evidence_state_for_curve(db, curve)
    generic_rank_exact = family_generic_state["exact_generic_rank"]
    rank_jump_lower = (
        None
        if generic_rank_exact is None
        else max(0, rigorous_lower - int(generic_rank_exact))
    )
    rank_jump_status = (
        "family_generic_rank_conflict"
        if family_generic_state["rank_inconsistent"]
        else "certified_rank_jump_lower"
        if rank_jump_lower is not None and rank_jump_lower > 0
        else "generic_rank_exact_known_no_certified_jump"
        if generic_rank_exact is not None
        else str(family_generic_state["reason"])
    )
    extra_witnesses = [
        p for p in witnesses
        if str(p["role"] or "") != "generic_section"
    ]

    family_states = list_curve_research_states(
        db,
        family=str(curve["family"]),
    )
    family_arithmetic = curve_arithmetic_state_map(
        db,
        [int(rec["curve_id"]) for rec in family_states],
    )
    arithmetic = family_arithmetic[int(curve_id)]
    family_total = len(family_states)
    family_best_lower = max(
        (int(rec["rigorous_lower"]) for rec in family_states),
        default=rigorous_lower,
    )
    family_at_or_above = sum(
        int(rec["rigorous_lower"]) >= rigorous_lower
        for rec in family_states
    )

    family_rows = []
    for rec in family_states[:100]:
        row = dict(rec["curve"])
        row["rigorous_lower"] = int(rec["rigorous_lower"])
        row["rigorous_upper"] = rec["rigorous_upper"]
        row["exact_rank"] = rec["exact_rank"]
        row["rank_inconsistent"] = bool(rec["rank_inconsistent"])
        family_rows.append(row)
    family_peers = [rec for rec in family_rows if int(rec["id"]) != int(curve_id)]

    family_jumps = []
    for rec in family_states[:100]:
        curve_row = rec["curve"]
        baseline = None if curve_row["generic_lower"] is None else int(curve_row["generic_lower"])
        lower = int(rec["rigorous_lower"])
        family_jumps.append({
            "curve_id": int(rec["curve_id"]),
            "parameter": str(rec["parameter"]),
            "rigorous_lower": lower,
            "family_section_lower": baseline,
            "extra_directions_lower": None if baseline is None else max(0, lower - baseline),
            "score": rec["score"],
            "root_number": family_arithmetic[int(rec["curve_id"])]["root_number"],
            "exact_rank": rec["exact_rank"],
        })

    family_pool_id = None
    if _table_exists(db, "candidate_pools") and _table_exists(db, "candidates"):
        pool = db.execute(
            """SELECT cp.id
               FROM candidates c
               JOIN candidate_pools cp ON cp.id=c.pool_id
               WHERE c.curve_id=?
               ORDER BY c.id DESC LIMIT 1""",
            (int(curve_id),),
        ).fetchone()
        if pool is not None:
            family_pool_id = int(pool["id"])
        elif curve["plugin_id"]:
            pool = db.execute(
                """SELECT id
                   FROM candidate_pools
                   WHERE plugin_id=?
                     AND (? IS NULL OR family_spec=?)
                   ORDER BY id DESC LIMIT 1""",
                (
                    str(curve["plugin_id"]),
                    curve["family_spec"],
                    curve["family_spec"],
                ),
            ).fetchone()
            if pool is not None:
                family_pool_id = int(pool["id"])

    return {
        "curve": curve,
        "state": state,
        "arithmetic": arithmetic,
        "points": points,
        "exact_points": exact_points,
        "witnesses": witnesses,
        "candidate_points": candidates,
        "actionable_points": actionable,
        "numerical_novel_points": numerical_novel,
        "dependent_points": dependent,
        "witness_gap": witness_gap,
        "family_section_lower": family_section_lower,
        "extra_directions_lower": extra_directions_lower,
        "family_generic_evidence": family_generic_state,
        "generic_rank_exact": generic_rank_exact,
        "rank_jump_lower": rank_jump_lower,
        "rank_jump_status": rank_jump_status,
        "extra_witnesses": extra_witnesses,
        "family_peers": family_peers,
        "family_jumps": family_jumps,
        "family_total": family_total,
        "family_best_lower": family_best_lower,
        "family_at_or_above": family_at_or_above,
        "family_pool_id": family_pool_id,
        "evidence": evidence,
        "evidence_timeouts": evidence_timeouts,
        "method_history": {
            "upper_bound": upper_bound_history,
        },
        "lattices": lattices,
        "quartic_searches": quartics,
        "pipeline_candidates": pipeline_rows,
        "events": events,
    }


def next_useful_actions(snapshot):
    """Compatibility wrapper over the shared Analysis Planner."""
    return planner_immediate_actions(analysis_plan(snapshot))


def researcher_paths(snapshot):
    """Compatibility wrapper over the shared Analysis Planner."""
    return planner_research_paths(analysis_plan(snapshot))

