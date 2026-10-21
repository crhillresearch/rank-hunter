"""Deterministic shared Analysis Planner.

The planner is read-only. It consumes one curve_analysis_snapshot()-compatible
snapshot and returns an ordered research plan. It never launches work or writes
scientific state.
"""
from __future__ import annotations


PLANNER_VERSION = 2


def _action(
    *,
    kind,
    title,
    why,
    priority,
    research_goal,
    recommended_method,
    prerequisites=None,
    prior_attempt_state=None,
    exhausted=False,
    page=None,
    tab=None,
    family_pool_id=None,
    legacy_path_kind=None,
    role="immediate",
):
    rec = {
        "kind": str(kind),
        "title": str(title),
        "why": str(why),
        "reason": str(why),
        "priority": int(priority),
        "research_goal": str(research_goal),
        "prerequisites": list(prerequisites or []),
        "prior_attempt_state": dict(prior_attempt_state or {}),
        "recommended_method": str(recommended_method),
        "exhausted": bool(exhausted),
        "role": str(role),
        "destination": {
            "page": page,
            "tab": tab,
        },
    }
    if page is not None:
        rec["page"] = str(page)
    if tab is not None:
        rec["tab"] = str(tab)
    if family_pool_id is not None:
        rec["family_pool_id"] = family_pool_id
    if legacy_path_kind is not None:
        rec["legacy_path_kind"] = str(legacy_path_kind)
    return rec


def analysis_plan(snapshot, *, target_rank=None):
    """Return one ordered, proof-safe research plan for a curve snapshot."""
    state = snapshot["state"]
    lower = int(state.get("rigorous_lower") or 0)
    upper = state.get("rigorous_upper")
    exact = state.get("exact_rank")
    inconsistent = bool(state.get("inconsistent"))
    actionable = len(snapshot.get("actionable_points") or [])
    novel = len(snapshot.get("numerical_novel_points") or [])
    witnesses = len(snapshot.get("witnesses") or [])
    witness_gap = int(snapshot.get("witness_gap") or 0)
    lattices = len(snapshot.get("lattices") or [])
    evidence_rows = len(snapshot.get("evidence") or [])
    evidence_timeouts = int(snapshot.get("evidence_timeouts") or 0)
    quartic_searches = len(snapshot.get("quartic_searches") or [])
    pipeline_rows = len(snapshot.get("pipeline_candidates") or [])
    family_section_lower = snapshot.get("family_section_lower")
    extra_directions = snapshot.get("extra_directions_lower")
    generic_rank_exact = snapshot.get("generic_rank_exact")
    rank_jump_lower = snapshot.get("rank_jump_lower")
    rank_jump_status = snapshot.get("rank_jump_status")
    family_pool_id = snapshot.get("family_pool_id")
    method_history = dict(snapshot.get("method_history") or {})
    upper_bound_history = dict(method_history.get("upper_bound") or {})
    curve = snapshot.get("curve")
    curve_id = None if curve is None else int(curve["id"])

    target = None if target_rank is None else int(target_rank)
    actions = []

    if target is not None and curve_id is not None and not inconsistent and lower >= target:
        actions.append(_action(
            kind="campaign_target_review",
            title="Review the target-reaching curve",
            why=(
                f"Curve #{curve_id} has rigorous rank ≥{lower}, meeting the campaign "
                f"target ≥{target}. Inspect its witnesses and evidence before marking "
                "the campaign complete."
            ),
            priority=120,
            research_goal="Confirm the campaign target result before completion.",
            recommended_method="Review the curve record and rigorous evidence.",
            prerequisites=["consistent rigorous rank state"],
            prior_attempt_state={
                "target_rank": target,
                "rigorous_lower": lower,
                "rank_evidence_rows": evidence_rows,
            },
            page="Curves",
        ))
    elif (
        target is not None
        and curve_id is not None
        and not inconsistent
        and exact is not None
        and int(exact) < target
    ):
        actions.append(_action(
            kind="campaign_search",
            title="Continue the campaign search",
            why=(
                f"Curve #{curve_id} has exact rank {int(exact)}, below the campaign "
                f"target ≥{target}. Its rank cannot increase with more proof work, "
                "so reuse or adjust the campaign's search strategy."
            ),
            priority=115,
            research_goal="Find another campaign curve capable of reaching the target.",
            recommended_method="Resume, retry, or adjust a Campaign Pipeline.",
            prerequisites=["current leading curve has exact rank below target"],
            prior_attempt_state={
                "target_rank": target,
                "exact_rank": int(exact),
                "pipeline_candidate_rows": pipeline_rows,
            },
            page="Pipelines",
        ))

    if inconsistent:
        actions.append(_action(
            kind="evidence",
            title="Resolve conflicting rank evidence",
            why=(
                "Stored rigorous upper and lower bounds are inconsistent. Review the "
                "evidence ledger before doing more arithmetic."
            ),
            priority=110,
            research_goal="Restore a consistent rigorous rank state.",
            recommended_method="Review and reconcile the rank-evidence ledger.",
            prerequisites=["stored contradictory rigorous bounds"],
            prior_attempt_state={
                "rank_evidence_rows": evidence_rows,
                "evidence_timeouts": evidence_timeouts,
            },
            page="Descent",
        ))

    if witness_gap > 0:
        actions.append(_action(
            kind="independence",
            title="Repair the replayable witness basis",
            why=(
                f"The rigorous lower bound is ≥{lower}, but the point ledger is "
                f"missing {witness_gap} rigorous witness point(s)."
            ),
            priority=100,
            research_goal="Make the rigorous lower bound replayable from stored witnesses.",
            recommended_method="Certify/store missing independent witness points.",
            prerequisites=["rigorous lower bound exceeds replayable witness count"],
            prior_attempt_state={
                "rigorous_witnesses": witnesses,
                "witness_gap": witness_gap,
            },
            page="Independence",
        ))

    if actionable:
        actions.append(_action(
            kind="independence",
            title=f"Certify {actionable} unresolved exact point(s)",
            why=(
                f"{novel} are numerically novel. Exact independence testing is the fastest "
                "path to a higher rigorous lower bound from material already found."
            ),
            priority=95,
            research_goal="Resolve stored exact candidate points into dependent/independent state.",
            recommended_method="Run exact independence certification.",
            prerequisites=["exact unresolved candidate points are stored"],
            prior_attempt_state={
                "actionable_points": actionable,
                "numerical_novel_points": novel,
            },
            page="Independence",
        ))

    if not lattices and witnesses >= 2:
        actions.append(_action(
            kind="lattice",
            title="Compute Mordell–Weil lattice geometry",
            why=(
                "A rigorous witness basis exists, but no stored height lattice has been "
                "computed. The lattice can reveal weak directions and better search geometry."
            ),
            priority=65,
            research_goal="Understand the geometry of the known Mordell–Weil subgroup.",
            recommended_method="Compute the Mordell–Weil height lattice.",
            prerequisites=["at least two rigorous witness points"],
            prior_attempt_state={
                "rigorous_witnesses": witnesses,
                "stored_lattices": lattices,
            },
            page="MW Geometry",
        ))

    if exact is None and upper is None:
        rank_why = (
            f"Rank is currently only known to be ≥{lower}. Use this when the research "
            "goal is classification or exact rank."
        )
        upper_exhausted = bool(
            upper_bound_history.get("all_exhausted_at_budget")
        )
        if upper_exhausted:
            rank_why += (
                " Equal-or-stronger attempts already exist for every tracked "
                "upper-bound method at the current planning budgets; review the "
                "history or deliberately raise a budget before repeating them."
            )
        elif upper_bound_history.get("stronger_budget_available"):
            rank_why += (
                " Earlier weaker attempts exist, but a materially stronger "
                "planning budget remains available for at least one method."
            )
        actions.append(_action(
            kind="descent",
            title="Try a rigorous upper bound",
            why=rank_why,
            priority=50,
            research_goal="Determine whether the current lower bound is the exact rank.",
            recommended_method=(
                "Review prior proof attempts or deliberately raise a method budget."
                if upper_exhausted
                else "Run a rigorous upper-bound / rank-proof method."
            ),
            prerequisites=["exact rank is not known", "no rigorous upper bound is stored"],
            prior_attempt_state={
                "rank_evidence_rows": evidence_rows,
                "evidence_timeouts": evidence_timeouts,
                "method_history": upper_bound_history,
            },
            exhausted=upper_exhausted,
            page="Descent",
            legacy_path_kind="determine_rank",
        ))
    elif exact is None:
        rank_why = (
            f"The current rigorous interval is {lower}–{int(upper)}. Close the interval "
            "if exact rank matters."
        )
        actions.append(_action(
            kind="descent",
            title="Close the rigorous rank interval",
            why=rank_why,
            priority=50,
            research_goal="Determine the exact rank.",
            recommended_method="Strengthen lower/upper evidence until the interval closes.",
            prerequisites=["exact rank is not known"],
            prior_attempt_state={
                "rigorous_lower": lower,
                "rigorous_upper": int(upper),
                "rank_evidence_rows": evidence_rows,
            },
            page="Descent",
            legacy_path_kind="determine_rank",
            role="strategic",
        ))

    if exact is None and not actionable and quartic_searches:
        actions.append(_action(
            kind="quartics",
            title="Revisit persisted quartic geometry",
            why=(
                f"{quartic_searches} persisted quartic search artifact(s) already exist for "
                "this curve. Review timeouts, successful models, mapped points, and untried "
                "height/denominator regions before starting a fresh target search."
            ),
            priority=80,
            research_goal="Extract more value from already-constructed quartic search geometry.",
            recommended_method="Open Quartics and retry, widen, change denominator band, or vary anchors.",
            prerequisites=["persisted quartic search history", "no unresolved exact point is waiting"],
            prior_attempt_state={
                "quartic_searches": quartic_searches,
                "pipeline_candidate_rows": pipeline_rows,
            },
            page="Quartics",
        ))

    if exact is None:
        actions.append(_action(
            kind="search",
            title="Hunt the next independent point",
            why=(
                f"Current rigorous lower bound is ≥{lower}. Target this exact curve with "
                "family-aware geometry or a Builder pipeline."
            ),
            priority=45 if actionable else 75,
            research_goal="Increase the rigorous Mordell–Weil lower bound.",
            recommended_method="Target the curve for a genuinely new independent point.",
            prerequisites=["rank is not already exact"],
            prior_attempt_state={
                "quartic_searches": quartic_searches,
                "pipeline_candidate_rows": pipeline_rows,
            },
            page="Target",
            legacy_path_kind="hunt_generator",
        ))
    elif not actions:
        actions.append(_action(
            kind="evidence",
            title="Review the exact-rank evidence",
            why=(
                f"Rank {int(exact)} is already exact from matching rigorous bounds. "
                "The useful next step is inspection/export rather than another rank-growth search."
            ),
            priority=30,
            research_goal="Inspect or export the completed rank proof.",
            recommended_method="Review the stored rigorous evidence.",
            prerequisites=["exact rank is already known"],
            prior_attempt_state={"exact_rank": int(exact)},
            page="Descent",
            exhausted=True,
        ))

    if family_section_lower is not None:
        surplus_text = (
            f"at least {int(extra_directions)} extra independent direction(s)"
            if extra_directions is not None
            else "an unresolved specialization surplus"
        )
        if str(rank_jump_status or "") == "family_generic_rank_conflict":
            jump_title = "Resolve generic-rank evidence conflict"
            jump_why = (
                f"The certified family-section subgroup has rank {int(family_section_lower)}, "
                f"but the stored family generic-rank evidence is inconsistent. Resolve that "
                "family-level conflict before making a formal specialization rank-jump claim."
            )
            jump_method = "Review the family-evidence certificates and conflicting generic-rank bounds."
        elif generic_rank_exact is not None and rank_jump_lower is not None and int(rank_jump_lower) > 0:
            jump_title = "Study the certified rank jump"
            jump_why = (
                f"The family generic rank is rigorously exact at {int(generic_rank_exact)}, "
                f"while this specialization has rigorous rank ≥{lower}. Therefore the "
                f"specialization rank jump is rigorously at least {int(rank_jump_lower)}. "
                "Investigate the arithmetic or geometric mechanism creating the extra directions."
            )
            jump_method = "Compare the extra rigorous directions with family structure and related fibers."
        elif generic_rank_exact is not None:
            jump_title = "Study specialization structure"
            jump_why = (
                f"The family generic rank is rigorously exact at {int(generic_rank_exact)}. "
                f"This fiber currently has rigorous rank ≥{lower}, so no positive rank jump is "
                "yet certified beyond that exact generic rank."
            )
            jump_method = "Strengthen the specialization lower bound or compare related fibers."
        else:
            jump_title = "Investigate the apparent rank jump"
            jump_why = (
                f"The certified family-section subgroup has rank {int(family_section_lower)}; "
                f"this fiber has {surplus_text} beyond it. Determine whether the family's "
                "generic rank is known exactly and what creates the extra directions."
            )
            jump_method = "Compare the fiber with the certified family subgroup."

        actions.append(_action(
            kind="study_jump",
            title=jump_title,
            why=jump_why,
            priority=35,
            research_goal="Explain specialization-specific Mordell–Weil directions.",
            recommended_method=jump_method,
            prerequisites=["certified family-section subgroup baseline"],
            prior_attempt_state={
                "family_section_lower": int(family_section_lower),
                "extra_directions_lower": extra_directions,
                "generic_rank_exact": generic_rank_exact,
                "rank_jump_lower": rank_jump_lower,
                "rank_jump_status": rank_jump_status,
            },
            tab="Rank Jump",
            legacy_path_kind="study_jump",
            role="strategic",
        ))
    else:
        actions.append(_action(
            kind="study_jump",
            title="Establish the family subgroup baseline",
            why=(
                "No certified family-section subgroup is stored for this fiber, so Rank Hunter "
                "cannot yet separate ordinary family directions from specialization-specific ones."
            ),
            priority=30,
            research_goal="Establish the family-section subgroup baseline.",
            recommended_method="Review/certify the family-section subgroup.",
            prerequisites=[],
            prior_attempt_state={"family_section_lower": None},
            tab="Rank Jump",
            legacy_path_kind="study_jump",
            role="strategic",
        ))

    actions.append(_action(
        kind="search_related",
        title="Search related fibers",
        why=(
            "An exceptional fiber is often more valuable as a clue to a subfamily or parameter "
            "condition than as an isolated curve. Return to the family search and look for repeats."
        ),
        priority=20,
        research_goal="Test whether the phenomenon repeats across related fibers.",
        recommended_method="Search the source family/candidate pool for neighboring or related fibers.",
        prerequisites=[],
        prior_attempt_state={"family_pool_id": family_pool_id},
        page="Search",
        family_pool_id=family_pool_id,
        legacy_path_kind="search_related",
        role="strategic",
    ))

    actions.sort(key=lambda rec: (-int(rec["priority"]), rec["title"]))
    return {
        "planner_version": PLANNER_VERSION,
        "curve_id": curve_id,
        "target_rank": target,
        "actions": actions,
        "primary": actions[0] if actions else None,
    }


def planner_research_paths(plan):
    """Return strategic path cards from one shared plan."""
    by_legacy = {}
    for action in plan.get("actions") or []:
        legacy = action.get("legacy_path_kind")
        if legacy and legacy not in by_legacy:
            by_legacy[legacy] = action

    ordered = []
    for legacy in (
        "determine_rank",
        "hunt_generator",
        "study_jump",
        "search_related",
    ):
        action = by_legacy.get(legacy)
        if action is None:
            continue
        legacy_title = {
            "determine_rank": "Determine the actual rank",
            "hunt_generator": "Hunt another independent point",
        }.get(legacy, action["title"])
        rec = {
            "kind": legacy,
            "title": legacy_title,
            "why": action["why"],
        }
        for key in ("page", "tab", "family_pool_id"):
            if key in action:
                rec[key] = action[key]
        ordered.append(rec)
    return ordered


def planner_immediate_actions(plan):
    """Return executable next-action records from one shared plan."""
    return [
        action
        for action in plan.get("actions") or []
        if str(action.get("role") or "immediate") == "immediate"
    ]
