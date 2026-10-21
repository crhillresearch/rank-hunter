"""Manifest-driven policy helpers for core Auto Search.

Families may provide search hints without owning a custom scheduler. Core keeps
all proof boundaries: policy changes discovery order/budgets only.
"""
from __future__ import annotations


def auto_search_policy(plugin, variant=None):
    policy = dict((getattr(plugin, "manifest", {}) or {}).get("auto_search") or {})
    if variant is not None:
        policy.update(dict((getattr(variant, "manifest", {}) or {}).get("auto_search") or {}))
    return policy


def baseline_certificate_timeout(policy, fallback):
    value = policy.get("baseline_certificate_timeout")
    if value is None:
        return int(fallback)
    return max(int(fallback), int(value))


def point_search_mode(policy, rigorous_lower):
    """Return exact discovery geometry requested at the current lower bound.

    integral_minimal_until_rank means denominator=1 on a global minimal
    integral model, then exact map-back to the stored family curve. The legacy
    native_integral_until_rank spelling is accepted for compatibility.
    """
    threshold = policy.get("integral_minimal_until_rank")
    if threshold is None:
        threshold = policy.get("native_integral_until_rank")
    lower = int(rigorous_lower or 0)
    if threshold is not None and lower < int(threshold):
        return {
            "id_suffix": "integral",
            "native_only": True,
            "minimal_model": True,
            "denominator_low": 1,
            "denominator_high": 1,
            "description": (
                f"global-minimal integral x until rigorous rank >= {int(threshold)}"
            ),
        }
    return {
        "id_suffix": "rational",
        "native_only": False,
        "minimal_model": False,
        "denominator_low": None,
        "denominator_high": None,
        "description": "general exact affine rational x search",
    }


def default_target_rank(policy, fallback=20):
    value = policy.get("target_rank")
    return int(value) if value is not None else int(fallback)


def tier_should_run(
    tier_id,
    *,
    rank_order,
    deep_keep,
    lower,
    baseline_lower,
    target_rank,
    growth,
    novel_points,
    previous_growth,
):
    """Return whether one Auto Search tier has earned more compute."""
    rank_order = int(rank_order)
    deep_keep = int(deep_keep)
    lower = int(lower)
    baseline_lower = int(baseline_lower)
    target_rank = int(target_rank)
    growth = max(0, int(growth))
    novel_points = max(0, int(novel_points))
    previous_growth = max(0, int(previous_growth))
    remaining = max(0, target_rank - lower)

    if tier_id == "scout":
        return True
    if tier_id == "structural":
        return (
            rank_order == 0
            or (0 < rank_order <= deep_keep)
            or novel_points > 0
            or growth > 0
        )
    if tier_id == "deep":
        return (
            growth > 0
            or novel_points >= 2
            or (0 < rank_order <= min(deep_keep, 4))
            or (rank_order == 0 and novel_points > 0)
        )
    if tier_id == "priority":
        return (
            growth >= 3
            or (previous_growth > 0 and growth >= 1)
            or (growth > 0 and remaining <= 2)
        )
    return False


def tier_chart_budget(tier, *, growth):
    """Scale the most expensive chart tier to demonstrated rank growth."""
    budget = int(tier["charts"])
    if str(tier["id"]) != "priority":
        return budget
    growth = max(0, int(growth))
    if growth <= 1:
        return min(budget, 32)
    if growth == 2:
        return min(budget, 64)
    return budget

def classical_covering_policy(policy):
    """Return manifest-driven classical 2-descent escalation settings.

    This is discovery policy only. Returned points still require exact
    independence certification before they can raise a rigorous lower bound.
    """
    enabled = bool(policy.get("classical_covering_enabled", False))
    return {
        "enabled": enabled,
        "engine": str(policy.get("classical_covering_engine", "simon_known")),
        "min_rank": max(0, int(policy.get("classical_covering_min_rank", 17))),
        "top_fresh": max(0, int(policy.get("classical_covering_top_fresh", 2))),
        "timeout": max(1, int(policy.get("classical_covering_timeout", 600))),
        "lim1": max(1, int(policy.get("classical_covering_lim1", 5))),
        "lim3": max(1, int(policy.get("classical_covering_lim3", 120))),
        "limbigprime": max(
            0, int(policy.get("classical_covering_limbigprime", 0))
        ),
        "first_limit": max(1, int(policy.get("classical_covering_first_limit", 20))),
        "second_limit": max(1, int(policy.get("classical_covering_second_limit", 10))),
        "n_aux": int(policy.get("classical_covering_n_aux", 33)),
    }


def classical_covering_should_run(
    policy,
    *,
    rank_order,
    lower,
    target_rank,
    growth,
    created_by_campaign,
):
    """Spend classical covering time only on genuinely record-relevant fibers."""
    cfg = classical_covering_policy(policy)
    if not cfg["enabled"]:
        return False
    lower = int(lower or 0)
    if lower >= int(target_rank) or lower < int(cfg["min_rank"]):
        return False
    if int(growth or 0) > 0:
        return True
    if not created_by_campaign:
        return True
    rank_order = int(rank_order or 0)
    return 0 < rank_order <= int(cfg["top_fresh"])



def pointed_quartic_policy(policy):
    """Return manifest-driven exact point-centred quartic escalation settings."""
    heights = policy.get("pointed_quartic_heights", (10000, 1000000))
    if isinstance(heights, int):
        heights = (heights,)
    return {
        "enabled": bool(policy.get("pointed_quartic_enabled", False)),
        "min_rank": max(1, int(policy.get("pointed_quartic_min_rank", 17))),
        "top_fresh": max(0, int(policy.get("pointed_quartic_top_fresh", 2))),
        "anchors": max(1, int(policy.get("pointed_quartic_anchors", 24))),
        "pool_size": max(1, int(policy.get("pointed_quartic_pool_size", 256))),
        "rounds": max(1, int(policy.get("pointed_quartic_rounds", 2))),
        "deep_keep": max(1, int(policy.get("pointed_quartic_deep_keep", 8))),
        "timeout": max(1, int(policy.get("pointed_quartic_timeout", 8))),
        "reduce_timeout": max(0, int(policy.get("pointed_quartic_reduce_timeout", 20))),
        "heights": tuple(sorted({max(1, int(x)) for x in heights})),
    }


def pointed_quartic_should_run(
    policy,
    *,
    rank_order,
    lower,
    target_rank,
    growth,
    created_by_campaign,
):
    """Spend pointed-quartic time only on record-relevant high-rank fibers."""
    cfg = pointed_quartic_policy(policy)
    if not cfg["enabled"]:
        return False
    lower = int(lower or 0)
    if lower >= int(target_rank) or lower < int(cfg["min_rank"]):
        return False
    if int(growth or 0) > 0:
        return True
    if not created_by_campaign:
        return True
    rank_order = int(rank_order or 0)
    return 0 < rank_order <= int(cfg["top_fresh"])



def rigorous_goal_reached(lower, target_rank, *, stop_on_target=True):
    """Whether a campaign should stop at its configured rigorous lower-bound goal."""
    if not stop_on_target:
        return False
    return int(lower or 0) >= int(target_rank)


def discard_fresh_curve_for_retention(
    *,
    created_by_campaign,
    retention_floor,
    rigorous_lower,
    status,
    growth,
    exact_rank,
):
    """Return whether a fresh campaign curve should leave the Curves inventory.

    This is an inventory decision only. Campaign trial history remains durable.
    Preexisting curves are never discarded here.
    """
    if not created_by_campaign:
        return False
    floor = max(0, int(retention_floor or 0))
    lower = int(rigorous_lower or 0)
    if floor:
        return lower < floor
    return (
        str(status) == "exhausted"
        and int(growth or 0) == 0
        and exact_rank is None
    )
