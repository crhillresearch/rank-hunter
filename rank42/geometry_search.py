"""Geometry-first high-rank search orchestration.

This is deliberately a search scheduler, not a new rank theorem. Core Geometry
Search supplies family-independent exact geometry; family plugins may add
sections, native quartics, PGL2 charts, covering maps, Selmer gates, and other
specialized target-search machinery.

The default research order is:

1. cheap Mestre-Nagao sieve;
2. medium survivor rescore;
3. exact torsion / family-section baseline;
4. bounded rigorous arithmetic elimination;
5. global-minimal integral seed search;
6. optional plugin-native geometry;
7. point-centered quartics / known-point coverings;
8. affine exact-point fallback on the strongest shortlist;
9. rigorous independence certification.

Heuristic stages only choose where to spend time.  Rank promotion continues to
require Rank Hunter's ordinary exact evidence path.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from rank42.auto_search_state import (
    create_campaign,
    ensure_auto_search_schema,
    get_campaign,
    update_campaign,
)
from rank42.candidates import get_pool
from rank42.db import connect, now
from rank42.family_loader import load_family
from rank42.plugins import (
    get_plugin,
    get_variant,
    search_presets_for_variant,
    variant_candidate_defaults,
)
from rank42.torsion import canonical_torsion_label


MARKER = "RANK42_GEOMETRY_SEARCH_RESULT="


def _csv_ints(text):
    values = [int(x.strip()) for x in str(text).split(",") if x.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected comma-separated integers")
    return values


def parse_args():
    ap = argparse.ArgumentParser(description="Geometry-first elliptic-curve rank search")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--campaign-id", type=int)
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--variant")
    ap.add_argument("--target-rank", type=int, required=True)
    ap.add_argument("--torsion-group")
    ap.add_argument("--retention-floor", type=int, default=0)
    ap.add_argument("--stop-on-target", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--pool-size", type=int, default=250)
    ap.add_argument("--geometry-keep", type=int, default=48)
    ap.add_argument("--deep-keep", type=int, default=24)
    ap.add_argument("--stage-bounds", type=_csv_ints, default=[523, 1979])
    ap.add_argument("--stage-keeps", type=_csv_ints)
    ap.add_argument("--geometry-triage-timeout", type=int, default=2)
    ap.add_argument("--geometry-timeout", type=int, default=180)
    ap.add_argument("--upper-timeout", type=int, default=5)
    ap.add_argument("--final-upper-timeout", type=int, default=20)
    ap.add_argument("--certificate-timeout", type=int, default=120)
    ap.add_argument("--exact-candidates", type=int, default=48)
    ap.add_argument("--ratpoints", required=True)
    return ap.parse_args()


def _keeps(args):
    if args.stage_keeps:
        keeps = list(args.stage_keeps)
    else:
        keeps = [max(5000, int(args.pool_size) * 20), int(args.pool_size)]
    if len(keeps) != len(args.stage_bounds):
        raise SystemExit("--stage-keeps must match --stage-bounds")
    if any(k <= 0 for k in keeps) or any(a < b for a, b in zip(keeps, keeps[1:])):
        raise SystemExit("--stage-keeps must be positive and nonincreasing")
    if keeps[-1] < int(args.pool_size):
        raise SystemExit("final stage keep must be >= pool size")
    return keeps


def _candidate_defaults(plugin, variant):
    values = variant_candidate_defaults(plugin, variant)
    for preset in search_presets_for_variant(plugin, variant):
        if str(preset.get("id") or "") == "scan":
            values.update(dict(preset.get("candidate") or {}))
            break
    return values


def _generate_pool(args, db, campaign, plugin, variant):
    if campaign["pool_id"]:
        pool = get_pool(db, int(campaign["pool_id"]))
        if pool is not None:
            print(f"[geometry] resume pool #{int(pool['id'])} {pool['name']}", flush=True)
            return pool

    defaults = _candidate_defaults(plugin, variant)
    keeps = _keeps(args)
    pool_name = f"geometry-search-{int(campaign['id'])}-{plugin.id}-{variant.id}"
    command = [
        sys.executable,
        "-m",
        "rank42.candidate_generate",
        "--project-root",
        str(Path(args.project_root).resolve()),
        "--db",
        str(Path(args.db).resolve()),
        "--plugin",
        plugin.id,
        "--variant",
        variant.id,
        "--pool-name",
        pool_name,
        "--a-min",
        str(int(defaults.get("a_min", -3000))),
        "--a-max",
        str(int(defaults.get("a_max", 3000))),
        "--b-min",
        str(max(1, int(defaults.get("b_min", 1)))),
        "--b-max",
        str(max(1, int(defaults.get("b_max", 300)))),
        "--stage-bounds",
        ",".join(str(x) for x in args.stage_bounds),
        "--stage-keeps",
        ",".join(str(x) for x in keeps),
        "--engine",
        str(defaults.get("engine", "sieve")),
        "--top",
        str(int(args.pool_size)),
        "--corpus-policy",
        "off",
    ]
    print(
        "[geometry] stage 1/2: Mestre-Nagao sieve "
        f"p<{args.stage_bounds[0]} -> keep {keeps[0]}",
        flush=True,
    )
    print(
        "[geometry] stage 2/2: survivor rescore "
        f"p<{args.stage_bounds[-1]} -> keep {int(args.pool_size)}",
        flush=True,
    )
    rc = subprocess.run(command, check=False).returncode
    if rc:
        raise RuntimeError(f"geometry candidate generation failed with exit={rc}")

    pool = db.execute(
        "SELECT * FROM candidate_pools WHERE name=?",
        (pool_name,),
    ).fetchone()
    if pool is None:
        raise RuntimeError("candidate generation completed without a candidate pool")
    update_campaign(
        db,
        int(campaign["id"]),
        pool_id=int(pool["id"]),
        candidates_total=int(pool["candidate_count"] or 0),
    )
    return pool


def _auto_command(args, campaign, plugin, variant):
    command = [
        sys.executable,
        "-m",
        "rank42.auto_search",
        "--project-root",
        str(Path(args.project_root).resolve()),
        "--db",
        str(Path(args.db).resolve()),
        "--campaign-id",
        str(int(campaign["id"])),
        "--plugin",
        plugin.id,
        "--variant",
        variant.id,
        "--target-rank",
        str(int(args.target_rank)),
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
        "--geometry-first",
        "--geometry-keep",
        str(int(args.geometry_keep)),
        "--geometry-triage-timeout",
        str(int(args.geometry_triage_timeout)),
        "--geometry-timeout",
        str(int(args.geometry_timeout)),
        "--stop-on-target" if args.stop_on_target else "--no-stop-on-target",
    ]
    if args.torsion_group:
        command += ["--torsion-group", str(args.torsion_group)]
    return command


def main():
    args = parse_args()
    if args.target_rank <= 0 or args.pool_size <= 0:
        raise SystemExit("target rank and pool size must be positive")
    if args.geometry_keep <= 0 or args.geometry_keep > args.pool_size:
        raise SystemExit("geometry keep must satisfy 1 <= geometry keep <= pool size")
    if args.deep_keep < 0 or args.deep_keep > args.geometry_keep:
        raise SystemExit("deep keep must satisfy 0 <= deep keep <= geometry keep")
    if args.retention_floor < 0 or args.retention_floor > args.target_rank:
        raise SystemExit("retention floor must satisfy 0 <= floor <= target rank")
    if len(args.stage_bounds) != 2 or args.stage_bounds[0] >= args.stage_bounds[1]:
        raise SystemExit("Geometry Search currently requires two increasing Nagao bounds")
    if args.torsion_group:
        args.torsion_group = canonical_torsion_label(args.torsion_group)

    project = Path(args.project_root).resolve()
    db_path = Path(args.db).resolve()
    db = connect(db_path)
    ensure_auto_search_schema(db)
    plugin = get_plugin(project, args.plugin)
    variant = get_variant(plugin, args.variant)
    family = load_family(variant.family_spec)
    if "candidate_generation" not in plugin.capabilities:
        raise SystemExit(f"plugin {plugin.id} does not declare candidate_generation")
    # Geometry Search is a core strategy and works for every candidate-generating
    # family. A plugin target adapter is an optional accelerator, not a gate.

    campaign_id = args.campaign_id
    if campaign_id is None:
        campaign_id = create_campaign(
            db,
            plugin_id=plugin.id,
            plugin_version=plugin.version,
            variant_id=variant.id,
            family_spec=variant.family_spec,
            family_name=family.name(),
            target_rank=args.target_rank,
            search_mode="geometry_torsion_provider" if args.torsion_group else "geometry",
            torsion_group=args.torsion_group,
            retention_floor=args.retention_floor,
            stop_on_target=args.stop_on_target,
            config={
                "pipeline": "geometry-first-v1",
                "stage_bounds": list(args.stage_bounds),
                "stage_keeps": _keeps(args),
                "pool_size": int(args.pool_size),
                "geometry_keep": int(args.geometry_keep),
                "deep_keep": int(args.deep_keep),
                "geometry_triage_timeout": int(args.geometry_triage_timeout),
                "geometry_timeout": int(args.geometry_timeout),
                "upper_timeout": int(args.upper_timeout),
                "final_upper_timeout": int(args.final_upper_timeout),
                "certificate_timeout": int(args.certificate_timeout),
                "exact_candidates": int(args.exact_candidates),
                "stop_on_target": bool(args.stop_on_target),
            },
        )
    campaign = get_campaign(db, campaign_id)
    if campaign is None:
        raise SystemExit(f"Geometry Search campaign #{campaign_id} not found")

    update_campaign(
        db,
        campaign_id,
        status="running",
        started_at=campaign["started_at"] or now(),
        finished_at=None,
        error=None,
    )
    campaign = get_campaign(db, campaign_id)

    print("RANK HUNTER GEOMETRY SEARCH", flush=True)
    print("=" * 72, flush=True)
    print(f"campaign = #{campaign_id}", flush=True)
    print(f"geometry = {plugin.name} / {variant.name}", flush=True)
    print(f"target = rigorous rank >= {int(args.target_rank)}", flush=True)
    if args.torsion_group:
        print(f"torsion filter = exact E(Q)_tors {args.torsion_group}", flush=True)
    print(
        "pipeline = cheap Nagao -> medium Nagao -> exact baseline/torsion -> "
        "bounded PARI gate -> core geometry seed -> optional plugin geometry -> "
        "pointed quartic / covering -> generic exact fallback -> certificate",
        flush=True,
    )

    try:
        pool = _generate_pool(args, db, campaign, plugin, variant)
        campaign = get_campaign(db, campaign_id)
        print(
            f"[geometry] shortlist pool=#{int(pool['id'])} "
            f"candidates={int(pool['candidate_count'] or 0)}; "
            f"geometry keep={int(args.geometry_keep)}",
            flush=True,
        )
        rc = subprocess.run(
            _auto_command(args, campaign, plugin, variant),
            check=False,
        ).returncode
        if rc:
            raise RuntimeError(f"geometry exact-search phase failed with exit={rc}")
        final = get_campaign(db, campaign_id)
        result = {
            "campaign_id": int(campaign_id),
            "status": str(final["status"]),
            "best_lower": int(final["best_lower"] or 0),
            "best_curve_id": final["best_curve_id"],
            "pool_id": int(pool["id"]),
            "pool_candidates": int(pool["candidate_count"] or 0),
        }
        print(MARKER + json.dumps(result, sort_keys=True), flush=True)
    except BaseException as exc:
        latest = get_campaign(db, campaign_id)
        if latest is not None and str(latest["status"]) not in {"paused", "killed"}:
            update_campaign(
                db,
                campaign_id,
                status="failed",
                error=str(exc),
                finished_at=now(),
            )
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
