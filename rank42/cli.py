"""Small machine-friendly Rank Hunter CLI dispatcher."""
from __future__ import annotations

import argparse
import json

from rank42.db import connect
from rank42.rank_pipeline import run_rank_bounds_for_curve
from rank42.saturation import saturate_curve, saturate_curve_ladder

MARKER = "RANK42_RANK_RESULT="


def _rank_bounds_parser(sub):
    ap = sub.add_parser("rank-bounds", help="run the PARI-first rigorous rank-bounds pipeline")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--engine", choices=["auto", "pari", "mwrank"], default="auto")
    ap.add_argument("--timeout", type=int, default=300, help="PARI/selected-engine hard timeout")
    ap.add_argument("--mwrank-timeout", type=int, default=300)
    ap.add_argument("--pari-stack-max-gib", type=int, default=4, help="maximum PARI stack size in GiB after overflow")
    ap.add_argument("--escalate", action="store_true", help="run bounded mwrank after a completed PARI gap")
    ap.add_argument("--force", action="store_true", help="ignore compatible cached evidence")
    ap.set_defaults(command="rank-bounds")


def _saturation_parser(sub):
    ap = sub.add_parser("saturate", help="saturate the stored rigorous point basis without full mwrank descent")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int, required=True)
    ap.add_argument("--min-prime", type=int, default=2)
    ap.add_argument("--max-prime", type=int, default=100)
    ap.add_argument(
        "--mode",
        choices=["range", "ladder"],
        default="range",
        help="one bounded Sage saturation or sequential prime-by-prime saturation",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="hard timeout for range mode; per-prime timeout for ladder mode",
    )
    ap.set_defaults(command="saturate")


def parse_args():
    ap = argparse.ArgumentParser(prog="rank42")
    sub = ap.add_subparsers(dest="command", required=True)
    _rank_bounds_parser(sub)
    _saturation_parser(sub)
    return ap.parse_args()


def main():
    args = parse_args()
    if args.command == "rank-bounds":
        db = connect(args.db)
        try:
            result = run_rank_bounds_for_curve(
                db, args.curve_id, engine=args.engine, timeout=args.timeout,
                mwrank_timeout=args.mwrank_timeout, escalate=args.escalate, force=args.force,
                pari_stack_max_gib=args.pari_stack_max_gib,
            )
        finally:
            db.close()
        print(MARKER + json.dumps(result, sort_keys=True))
    elif args.command == "saturate":
        db = connect(args.db)
        try:
            runner = (
                saturate_curve_ladder
                if args.mode == "ladder"
                else saturate_curve
            )
            result = runner(
                db,
                args.curve_id,
                min_prime=args.min_prime,
                max_prime=args.max_prime,
                timeout=args.timeout,
                source=f"cli_{args.mode}",
            )
        finally:
            db.close()
        print("RANK42_SATURATION=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
