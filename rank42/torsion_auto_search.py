"""Torsion-target Auto Hunter orchestration.

A torsion campaign is a parent Auto Hunter campaign that cycles through enabled
family/variant providers. Provider suitability is discovered from optional
manifest hints or an exact torsion computation at the variant validation
specialization. Every actual candidate is still checked exactly by
rank42.auto_search before rank-search compute is spent.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sage.all import QQ

from rank42.auto_search_policy import rigorous_goal_reached
from rank42.auto_search_state import (
    campaign_progress,
    child_campaign,
    create_campaign,
    ensure_auto_search_schema,
    get_campaign,
    list_campaigns,
    update_campaign,
)
from rank42.db import connect, now
from rank42.family_loader import load_family
from rank42.plugins import (
    Plugin,
    discover_plugins,
    is_enabled,
    variant_torsion_groups,
    variant_torsion_provider_role,
    variant_torsion_record,
    variant_value,
)
from rank42.torsion import canonical_torsion_label, compute_torsion_data


MARKER = "RANK42_TORSION_AUTO_SEARCH_RESULT="
PROVIDER_DISCOVERY_REVISION = 2


def parse_args():
    ap = argparse.ArgumentParser(description="Auto Hunter by exact rational torsion group")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--campaign-id", type=int)
    ap.add_argument("--torsion-group", required=True)
    ap.add_argument("--target-rank", type=int, default=20)
    ap.add_argument("--retention-floor", type=int, default=0)
    ap.add_argument(
        "--stop-on-target",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    ap.add_argument("--pool-size", type=int, default=50)
    ap.add_argument("--deep-keep", type=int, default=12)
    ap.add_argument("--upper-timeout", type=int, default=8)
    ap.add_argument("--final-upper-timeout", type=int, default=30)
    ap.add_argument("--certificate-timeout", type=int, default=120)
    ap.add_argument("--exact-candidates", type=int, default=48)
    ap.add_argument(
        "--geometry-first",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="run each torsion provider through the Geometry pipeline",
    )
    ap.add_argument("--geometry-keep", type=int, default=48)
    ap.add_argument("--geometry-triage-timeout", type=int, default=2)
    ap.add_argument("--geometry-timeout", type=int, default=180)
    ap.add_argument("--geometry-stage-bounds", default="523,1979")
    ap.add_argument("--geometry-stage-keeps", default="")
    ap.add_argument(
        "--include-secondary-providers",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="after direct prescribed-torsion providers, also try unrelated family providers",
    )
    ap.add_argument("--ratpoints", required=True)
    return ap.parse_args()


def _campaign_config(row):
    try:
        return json.loads(row["config_json"] or "{}")
    except Exception:
        return {}


def _rank_hint(plugin, variant):
    for value in (
        variant.verified_generic_rank_lower,
        variant.historical_generic_rank_lower,
        variant.generic_rank,
        plugin.verified_generic_rank_lower,
        plugin.historical_generic_rank_lower,
        plugin.generic_rank,
    ):
        if value is not None:
            return int(value)
    return 0


def _provider_torsion(plugin, variant):
    """Exact-check one provider's validation specialization.

    Manifest torsion metadata is routing metadata, not proof.  Even canonical
    providers are checked exactly before the scheduler trusts them.
    """
    declared = variant_torsion_groups(plugin, variant)
    family = load_family(variant.family_spec)
    parameter = variant_value(plugin, variant, "validation_parameter", "1")
    E = family.curve(QQ(str(parameter)))
    if E is None:
        return (), "validation-singular"
    data = compute_torsion_data(E)
    exact = str(data["torsion_label"])
    source = "manifest+validation-specialization" if declared else "validation-specialization"
    return (exact,), source


def discover_providers(project_root, db, target, *, include_secondary=False):
    """Return enabled prescribed-torsion providers in research priority order.

    Primary providers explicitly declare the selected torsion group.  Canonical
    universal parameterizations run first, then torsion-specific subfamilies.
    Unrelated families are excluded by default because waiting for exceptional
    torsion growth is an extremely sparse record-hunting strategy; users may
    opt into those secondary providers after the direct lane is exhausted.
    """
    providers = []
    for plugin in discover_plugins(project_root):
        if not isinstance(plugin, Plugin):
            continue
        if plugin.plugin_type != "family" or "candidate_generation" not in plugin.capabilities:
            continue
        if not is_enabled(db, plugin):
            continue
        for variant in plugin.variants:
            declared = variant_torsion_groups(plugin, variant)
            role = variant_torsion_provider_role(plugin, variant)
            direct = bool(declared and target in declared)
            if declared and target not in declared:
                continue
            if not direct and not include_secondary:
                continue

            try:
                groups, source = _provider_torsion(plugin, variant)
            except Exception as exc:
                print(
                    f"[torsion:auto] provider preflight skipped "
                    f"{plugin.id}/{variant.id}: {exc!r}",
                    flush=True,
                )
                continue

            exact_validation_match = target in groups
            if direct and not exact_validation_match:
                print(
                    f"[torsion:auto] declared provider rejected "
                    f"{plugin.id}/{variant.id}: validation torsion={groups!r}, "
                    f"target={target}",
                    flush=True,
                )
                continue

            if role == "canonical_universal" and direct:
                priority = 0
                lane = "canonical-universal"
            elif role == "prescribed_subfamily" and direct:
                priority = 1
                lane = "prescribed-subfamily"
            elif direct:
                priority = 2
                lane = "declared-prescribed"
            elif exact_validation_match:
                priority = 10
                lane = "secondary-validation-match"
            else:
                priority = 20
                lane = "secondary-exact-filter"

            record = variant_torsion_record(plugin, variant)
            providers.append(
                {
                    "plugin_id": plugin.id,
                    "plugin_version": plugin.version,
                    "plugin_name": plugin.name,
                    "variant_id": variant.id,
                    "variant_name": variant.name,
                    "family_spec": variant.family_spec,
                    "family_name": variant.curve_family_name or plugin.name,
                    "provider_key": f"{plugin.id}:{variant.id}",
                    "rank_hint": _rank_hint(plugin, variant),
                    "torsion_source": source,
                    "torsion_priority": priority,
                    "provider_lane": lane,
                    "torsion_provider_role": role,
                    "record_rank_lower": (
                        int(record["rank_lower"]) if record is not None else None
                    ),
                }
            )
    providers.sort(
        key=lambda rec: (
            int(rec["torsion_priority"]),
            -int(rec["rank_hint"]),
            str(rec["plugin_name"]).lower(),
            str(rec["variant_name"]).lower(),
        )
    )
    return providers


def _child_command(args, child, provider):
    common = [
        "--project-root",
        str(Path(args.project_root).resolve()),
        "--db",
        str(Path(args.db).resolve()),
        "--campaign-id",
        str(int(child["id"])),
        "--plugin",
        str(provider["plugin_id"]),
        "--variant",
        str(provider["variant_id"]),
        "--target-rank",
        str(int(args.target_rank)),
        "--torsion-group",
        str(args.torsion_group),
        "--retention-floor",
        str(int(args.retention_floor)),
        "--pool-size",
        str(int(args.pool_size)),
        "--deep-keep",
        str(int(args.deep_keep)),
        "--upper-timeout",
        str(int(args.upper_timeout)),
        "--final-upper-timeout",
        str(int(args.final_upper_timeout)),
        "--certificate-timeout",
        str(int(args.certificate_timeout)),
        "--exact-candidates",
        str(int(args.exact_candidates)),
        "--ratpoints",
        str(args.ratpoints),
        "--stop-on-target" if args.stop_on_target else "--no-stop-on-target",
    ]
    if args.geometry_first:
        command = [
            sys.executable,
            "-m",
            "rank42.geometry_search",
            *common,
            "--geometry-keep",
            str(int(args.geometry_keep)),
            "--geometry-triage-timeout",
            str(int(args.geometry_triage_timeout)),
            "--geometry-timeout",
            str(int(args.geometry_timeout)),
            "--stage-bounds",
            str(args.geometry_stage_bounds),
        ]
        keeps = str(args.geometry_stage_keeps or "").strip()
        if keeps:
            command += ["--stage-keeps", keeps]
        return command
    return [sys.executable, "-m", "rank42.auto_search", *common]


def _child_for_provider(db, parent_id, provider, args):
    key = str(provider["provider_key"])
    existing = child_campaign(db, parent_id, key)
    if existing is not None:
        return existing
    cid = create_campaign(
        db,
        plugin_id=provider["plugin_id"],
        plugin_version=provider["plugin_version"],
        variant_id=provider["variant_id"],
        family_spec=provider["family_spec"],
        family_name=provider["family_name"],
        target_rank=args.target_rank,
        search_mode="geometry_torsion_provider" if args.geometry_first else "torsion_provider",
        torsion_group=args.torsion_group,
        retention_floor=args.retention_floor,
        stop_on_target=args.stop_on_target,
        parent_campaign_id=parent_id,
        provider_key=key,
        config={
            "parent_campaign_id": int(parent_id),
            "provider_key": key,
            "torsion_group": args.torsion_group,
            "retention_floor": int(args.retention_floor),
            "stop_on_target": bool(args.stop_on_target),
            "pool_size": int(args.pool_size),
            "deep_keep": int(args.deep_keep),
            "upper_timeout": int(args.upper_timeout),
            "final_upper_timeout": int(args.final_upper_timeout),
            "certificate_timeout": int(args.certificate_timeout),
            "exact_candidates": int(args.exact_candidates),
            "ratpoints_backend": "configured",
            "geometry_first": bool(args.geometry_first),
            "geometry_keep": int(args.geometry_keep),
            "geometry_triage_timeout": int(args.geometry_triage_timeout),
            "geometry_timeout": int(args.geometry_timeout),
            "geometry_stage_bounds": str(args.geometry_stage_bounds),
            "geometry_stage_keeps": str(args.geometry_stage_keeps or ""),
        },
    )
    return get_campaign(db, cid)


def _rollup_parent(db, parent_id, providers):
    provider_keys = {str(rec["provider_key"]) for rec in providers}
    children = [
        row
        for row in list_campaigns(
            db,
            limit=max(1000, len(provider_keys) + 50),
            parent_campaign_id=parent_id,
        )
        if str(row["provider_key"] or "") in provider_keys
    ]
    child_states = []
    for child in children:
        progress = campaign_progress(db, int(child["id"]))
        # Recompute persisted provider rank state from eligible trials so an old
        # torsion-mismatch row can never poison a resumed parent campaign.
        update_campaign(
            db,
            int(child["id"]),
            candidates_done=progress["done"],
            exact_count=progress["exact_count"],
            exhausted_count=progress["exhausted_count"],
            target_hit_count=progress["target_hit_count"],
            torsion_mismatch_count=progress["torsion_mismatch_count"],
            best_lower=progress["best_lower"],
            best_curve_id=progress["best_curve_id"],
        )
        child_states.append((child, progress))

    best = max((int(progress["best_lower"] or 0) for _, progress in child_states), default=0)
    best_curve_id = None
    for _, progress in child_states:
        if int(progress["best_lower"] or 0) == best and progress["best_curve_id"] is not None:
            best_curve_id = int(progress["best_curve_id"])
            break

    terminal = {"completed", "target_hit", "failed"}
    fields = {
        "candidates_total": len(provider_keys),
        "candidates_done": sum(str(child["status"]) in terminal for child, _ in child_states),
        "exact_count": sum(int(progress["exact_count"] or 0) for _, progress in child_states),
        "exhausted_count": sum(int(progress["exhausted_count"] or 0) for _, progress in child_states),
        "target_hit_count": sum(int(progress["target_hit_count"] or 0) for _, progress in child_states),
        "torsion_mismatch_count": sum(int(progress["torsion_mismatch_count"] or 0) for _, progress in child_states),
        "best_lower": int(best),
        "best_curve_id": best_curve_id,
    }
    update_campaign(db, parent_id, **fields)
    return fields


def main():
    args = parse_args()
    if args.campaign_id is None:
        raise SystemExit(
            "legacy torsion Auto is resume-only; start new work from the Pipeline Auto page"
        )
    args.torsion_group = canonical_torsion_label(args.torsion_group)
    if args.target_rank <= 0 or args.pool_size <= 0 or args.deep_keep < 0:
        raise SystemExit("target rank/pool size must be positive and deep-keep nonnegative")
    if args.retention_floor < 0 or args.retention_floor > args.target_rank:
        raise SystemExit("retention floor must satisfy 0 <= floor <= target rank")
    if args.geometry_keep <= 0 or args.geometry_triage_timeout < 0 or args.geometry_timeout <= 0:
        raise SystemExit("invalid geometry-search limits")

    project = Path(args.project_root).resolve()
    db_path = Path(args.db).resolve()
    db = connect(db_path)
    ensure_auto_search_schema(db)

    parent_id = args.campaign_id
    if parent_id is None:
        parent_id = create_campaign(
            db,
            plugin_id="__torsion__",
            plugin_version=None,
            variant_id=args.torsion_group,
            family_spec=f"torsion:{args.torsion_group}",
            family_name=f"Torsion {args.torsion_group}",
            target_rank=args.target_rank,
            search_mode="geometry_torsion" if args.geometry_first else "torsion",
            torsion_group=args.torsion_group,
            retention_floor=args.retention_floor,
            stop_on_target=args.stop_on_target,
            config={
                "torsion_group": args.torsion_group,
                "retention_floor": int(args.retention_floor),
                "stop_on_target": bool(args.stop_on_target),
                "pool_size": int(args.pool_size),
                "deep_keep": int(args.deep_keep),
                "upper_timeout": int(args.upper_timeout),
                "final_upper_timeout": int(args.final_upper_timeout),
                "certificate_timeout": int(args.certificate_timeout),
                "exact_candidates": int(args.exact_candidates),
                "include_secondary_providers": bool(args.include_secondary_providers),
                "provider_discovery_revision": PROVIDER_DISCOVERY_REVISION,
                "geometry_first": bool(args.geometry_first),
                "geometry_keep": int(args.geometry_keep),
                "geometry_triage_timeout": int(args.geometry_triage_timeout),
                "geometry_timeout": int(args.geometry_timeout),
                "geometry_stage_bounds": str(args.geometry_stage_bounds),
                "geometry_stage_keeps": str(args.geometry_stage_keeps or ""),
            },
        )
    parent = get_campaign(db, parent_id)
    if parent is None:
        raise SystemExit(f"Auto Hunter campaign #{parent_id} not found")
    expected_mode = "geometry_torsion" if args.geometry_first else "torsion"
    if str(parent["search_mode"]) != expected_mode:
        raise SystemExit(
            f"campaign #{parent_id} has search mode {parent['search_mode']!r}, "
            f"expected {expected_mode!r}"
        )

    config = _campaign_config(parent)
    providers = list(config.get("providers") or [])
    cached_revision = int(config.get("provider_discovery_revision") or 0)
    cached_secondary = bool(config.get("include_secondary_providers", False))
    cached_geometry = bool(config.get("geometry_first", False))
    if (
        not providers
        or cached_revision != PROVIDER_DISCOVERY_REVISION
        or cached_secondary != bool(args.include_secondary_providers)
        or cached_geometry != bool(args.geometry_first)
    ):
        providers = discover_providers(
            project,
            db,
            args.torsion_group,
            include_secondary=bool(args.include_secondary_providers),
        )
        config["providers"] = providers
        config["provider_discovery_revision"] = PROVIDER_DISCOVERY_REVISION
        config["include_secondary_providers"] = bool(args.include_secondary_providers)
        config["geometry_first"] = bool(args.geometry_first)
        update_campaign(db, parent_id, config_json=json.dumps(config, sort_keys=True))
    if not providers:
        update_campaign(
            db,
            parent_id,
            status="failed",
            error=(
                f"No enabled prescribed-torsion provider matched {args.torsion_group}"
                if not args.include_secondary_providers
                else f"No enabled candidate-generation provider matched {args.torsion_group}"
            ),
            finished_at=now(),
        )
        raise SystemExit(
            (
                f"No enabled prescribed-torsion provider matched {args.torsion_group}; "
                "install/enable a direct torsion-family plugin or opt into secondary providers."
            )
            if not args.include_secondary_providers
            else f"No enabled candidate-generation provider matched {args.torsion_group}"
        )

    update_campaign(
        db,
        parent_id,
        status="running",
        started_at=parent["started_at"] or now(),
        finished_at=None,
        error=None,
        candidates_total=len(providers),
    )

    print(
        "RANK HUNTER TORSION GEOMETRY SEARCH"
        if args.geometry_first
        else "RANK HUNTER TORSION AUTO HUNTER",
        flush=True,
    )
    print("=" * 72, flush=True)
    print(f"campaign = #{parent_id}", flush=True)
    print(f"torsion = exact E(Q)_tors {args.torsion_group}", flush=True)
    print(f"target = rigorous rank >= {args.target_rank}", flush=True)
    print(f"retention floor = rigorous rank >= {args.retention_floor}", flush=True)
    print(f"stop on target = {bool(args.stop_on_target)}", flush=True)
    print(f"providers = {len(providers)}", flush=True)
    print(f"secondary providers = {bool(args.include_secondary_providers)}", flush=True)
    print(f"geometry pipeline = {bool(args.geometry_first)}", flush=True)
    for index, provider in enumerate(providers, 1):
        print(
            f"  {index:>2}. {provider['plugin_name']} / {provider['variant_name']} "
            f"[{provider['provider_lane']}] "
            f"(rank hint {provider['rank_hint']}, torsion via {provider['torsion_source']})",
            flush=True,
        )

    failures = []
    goal_met = False
    should_stop = False
    try:
        initial_rollup = _rollup_parent(db, parent_id, providers)
        goal_met = int(initial_rollup["best_lower"] or 0) >= int(args.target_rank)
        should_stop = rigorous_goal_reached(
            initial_rollup["best_lower"],
            args.target_rank,
            stop_on_target=args.stop_on_target,
        )
        if should_stop:
            print(
                f"[torsion:auto] resume goal already satisfied at rigorous rank >= "
                f"{initial_rollup['best_lower']}; no provider work needed",
                flush=True,
            )

        for index, provider in enumerate(providers, 1):
            if should_stop:
                break
            child = _child_for_provider(db, parent_id, provider, args)
            child_status = str(child["status"])
            if child_status in {"completed", "target_hit"}:
                print(
                    f"[torsion:auto] resume-skip provider {index}/{len(providers)} "
                    f"{provider['provider_key']} status={child_status}",
                    flush=True,
                )
            else:
                print(
                    f"[torsion:auto] provider {index}/{len(providers)} "
                    f"{provider['plugin_name']} / {provider['variant_name']}",
                    flush=True,
                )
                rc = subprocess.run(_child_command(args, child, provider), check=False).returncode
                child = get_campaign(db, int(child["id"]))
                if rc:
                    failures.append(f"{provider['provider_key']} exit={rc}")
                    if child is not None and str(child["status"]) not in {"paused", "killed", "failed"}:
                        update_campaign(
                            db,
                            int(child["id"]),
                            status="failed",
                            error=f"provider subprocess exit={rc}",
                            finished_at=now(),
                        )

            rollup = _rollup_parent(db, parent_id, providers)
            goal_met = int(rollup["best_lower"] or 0) >= int(args.target_rank)
            should_stop = rigorous_goal_reached(
                rollup["best_lower"],
                args.target_rank,
                stop_on_target=args.stop_on_target,
            )
            if should_stop:
                print(
                    f"[torsion:auto] goal reached at rigorous rank >= {rollup['best_lower']}; "
                    "stopping provider cycle",
                    flush=True,
                )
                break

        rollup = _rollup_parent(db, parent_id, providers)
        goal_met = int(rollup["best_lower"] or 0) >= int(args.target_rank)
        should_stop = rigorous_goal_reached(
            rollup["best_lower"],
            args.target_rank,
            stop_on_target=args.stop_on_target,
        )
        update_campaign(
            db,
            parent_id,
            status="target_hit" if should_stop else "completed",
            finished_at=now(),
            error="; ".join(failures) if failures else None,
        )
        result = {
            "campaign_id": int(parent_id),
            "torsion_group": args.torsion_group,
            "providers": len(providers),
            "providers_done": int(rollup["candidates_done"]),
            "best_lower": int(rollup["best_lower"]),
            "best_curve_id": rollup.get("best_curve_id"),
            "target_hits": int(rollup["target_hit_count"]),
            "torsion_mismatches": int(rollup["torsion_mismatch_count"]),
            "goal_met": bool(goal_met),
            "stopped_on_target": bool(should_stop),
            "include_secondary_providers": bool(args.include_secondary_providers),
            "geometry_first": bool(args.geometry_first),
            "provider_failures": failures,
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
    except BaseException as exc:
        latest = get_campaign(db, parent_id)
        status = str(latest["status"] if latest is not None else "")
        if status not in {"paused", "killed"}:
            update_campaign(
                db,
                parent_id,
                status="failed",
                error=str(exc),
                finished_at=now(),
            )
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
