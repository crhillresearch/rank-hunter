import argparse
import heapq
import json
import math
import pickle
import random
import re
from pathlib import Path

from rank42.family_loader import (
    family_cache_key,
    family_cache_keys,
    family_generic_rank,
    load_family,
    resolve_family_spec,
)
from rank42.nagao import (
    extend_score_tables,
    iter_fixed_denominator_sieve,
    load_score_tables,
    score_cache_is_complete,
    save_score_tables,
    score_rational,
)


def parse_csv_ints(text):
    vals = [int(x.strip()) for x in str(text).split(",") if x.strip()]
    if not vals:
        raise argparse.ArgumentTypeError("expected comma-separated integers")
    return vals


def parse_args():
    ap = argparse.ArgumentParser(
        description="Search rational specializations with scalar or staged Nagao sieving."
    )
    ap.add_argument("--family", default=None,
                    help="family alias/module, or json:/path/to/family.json")
    ap.add_argument("--a-min", type=int, required=True)
    ap.add_argument("--a-max", type=int, required=True)
    ap.add_argument("--b-min", type=int, default=1)
    ap.add_argument("--b-max", type=int, required=True)
    ap.add_argument("--prime-bound", type=int, default=1000,
                    help="single-stage bound retained for backward compatibility")
    ap.add_argument("--stage-bounds", type=parse_csv_ints, default=None,
                    help="ascending prime bounds, e.g. 1000,5000,20000")
    ap.add_argument("--stage-keeps", type=parse_csv_ints, default=None,
                    help="candidates retained after each stage; last defaults to --top")
    ap.add_argument("--engine", choices=["sieve", "scalar", "sampled"], default="sieve")
    ap.add_argument(
        "--sample-count",
        type=int,
        default=200000,
        help="bounded rational pairs for the sampled engine",
    )
    ap.add_argument("--sample-seed", type=int, default=42)
    ap.add_argument("--block-size", type=int, default=1 << 18)
    ap.add_argument("--top", type=int, default=1000)
    ap.add_argument("--cache-dir", default=".rank42-cache")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", required=True)
    return ap.parse_args()


def normalize_stages(args):
    bounds = args.stage_bounds or [args.prime_bound]
    if sorted(bounds) != bounds or len(set(bounds)) != len(bounds):
        raise SystemExit("--stage-bounds must be strictly increasing")

    if args.stage_keeps is None:
        if len(bounds) == 1:
            keeps = [args.top]
        else:
            # Keep a generous stage-0 pool without requiring extra flags.
            keeps = [max(args.top * 20, args.top)]
            for i in range(1, len(bounds) - 1):
                keeps.append(max(args.top * 5, args.top))
            keeps.append(args.top)
    else:
        keeps = args.stage_keeps
        if len(keeps) != len(bounds):
            raise SystemExit("--stage-keeps must have the same length as --stage-bounds")
        if any(k <= 0 for k in keeps):
            raise SystemExit("--stage-keeps values must be positive")
        if any(b > a for a, b in zip(keeps, keeps[1:])):
            raise SystemExit("--stage-keeps must be nonincreasing; discarded candidates cannot be recovered")
        if keeps[-1] < args.top:
            raise SystemExit("final --stage-keeps value must be >= --top")
    return bounds, keeps


def cache_path(cache_dir, key, bound):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
    return Path(cache_dir) / f"nagao-{safe}-p{int(bound)}.pickle"


def checkpoint_cache_path(cache_dir, key, bound):
    return cache_path(cache_dir, key, bound).with_suffix(".checkpoint.pickle")


_CACHE_BOUND_RE = re.compile(r"-p(?P<bound>[0-9]+)(?:\.checkpoint)?\.pickle$")


def _cache_bound(path):
    match = _CACHE_BOUND_RE.search(Path(path).name)
    return int(match.group("bound")) if match else None


def _cache_metadata_matches(meta, family, family_spec, filename_bound, *, complete=True):
    if not isinstance(meta, dict):
        return False
    if complete != score_cache_is_complete(meta):
        return False
    if meta.get("family_spec") is not None and str(meta["family_spec"]) != str(family_spec):
        return False
    if meta.get("family") is not None and str(meta["family"]) != str(family.name()):
        return False
    declared_bound = meta.get("prime_bound")
    if declared_bound is not None:
        try:
            if int(declared_bound) != int(filename_bound):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _load_cache_candidates(cache_dir, key, bound, family, family_spec, *, checkpoints=False):
    """Load compatible cache candidates at or below ``bound``.

    Normal caches and checkpoints use separate filename suffixes.  The
    ``complete`` metadata check is still applied so a manually misplaced or
    partially written file cannot be promoted by filename alone.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
    root = Path(cache_dir)
    suffix = ".checkpoint.pickle" if checkpoints else ".pickle"
    best = None
    paths = sorted(
        root.glob(f"nagao-{safe}-p*{suffix}"),
        key=lambda path: _cache_bound(path) or -1,
        reverse=True,
    )
    for path in paths:
        is_checkpoint_path = path.name.endswith(".checkpoint.pickle")
        if is_checkpoint_path != checkpoints:
            continue
        cache_bound = _cache_bound(path)
        if cache_bound is None or cache_bound > int(bound):
            continue
        try:
            tables, meta = load_score_tables(path)
        except (OSError, EOFError, ValueError, pickle.UnpicklingError):
            continue
        if not _cache_metadata_matches(
            meta, family, family_spec, cache_bound, complete=not checkpoints
        ):
            continue
        candidate = (cache_bound, len(tables), path, tables, meta)
        if not checkpoints:
            # Paths are visited from the largest bound down, so the first
            # compatible complete cache is the only one that can be useful.
            return [candidate]
        if best is None or (candidate[1], candidate[0]) > (best[1], best[0]):
            best = candidate
    return [best] if best is not None else []


def get_tables(family, family_spec, bound, current, args):
    key = family_cache_key(family_spec, family)
    compatible_keys = (key,) + tuple(
        candidate for candidate in family_cache_keys(family_spec, family)
        if candidate != key
    )
    bound = int(bound)
    current = dict(current or {})
    source = None

    if not args.no_cache:
        complete = []
        checkpoints = []
        for candidate_key in compatible_keys:
            complete.extend(_load_cache_candidates(
                args.cache_dir, candidate_key, bound, family, family_spec, checkpoints=False
            ))
            checkpoints.extend(_load_cache_candidates(
                args.cache_dir, candidate_key, bound, family, family_spec, checkpoints=True
            ))
        exact = [candidate for candidate in complete if candidate[0] == bound]
        if exact:
            _, _, source, loaded, _ = max(exact, key=lambda item: item[1])
            current.update(loaded)
            print(f"[cache] loaded {len(current)} prime tables from {source}")
            return current

        sources = [(len(current), -1, 0, None, current, None)]
        sources.extend((count, cache_bound, 1, path, tables, meta)
                       for cache_bound, count, path, tables, meta in complete)
        sources.extend((count, cache_bound, 0, path, tables, meta)
                       for cache_bound, count, path, tables, meta in checkpoints)
        _, _, source_complete, source, loaded, meta = max(
            sources, key=lambda item: (item[0], item[1], item[2])
        )
        if source is not None:
            current.update(loaded)
            if not source_complete:
                print(
                    f"[checkpoint] resuming {len(current)}/{meta.get('target_table_count', '?')} "
                    f"tables from {source}"
                )
            else:
                print(
                    f"[cache] loaded {len(current)} prime tables from {source}; "
                    f"extending to p < {bound}"
                )

    checkpoint = None if args.no_cache else checkpoint_cache_path(args.cache_dir, key, bound)
    print(f"[precompute] extending prime tables to p < {bound}")
    tables = extend_score_tables(
        family,
        bound,
        current,
        progress=True,
        checkpoint_path=checkpoint,
        checkpoint_metadata={
            "family": family.name(),
            "family_spec": family_spec,
            "cache_key": key,
        },
    )
    print(f"[precompute] {len(tables)} prime tables ready")
    if not args.no_cache:
        path = cache_path(args.cache_dir, key, bound)
        save_score_tables(
            path,
            tables,
            metadata={
                "family": family.name(),
                "family_spec": family_spec,
                "prime_bound": bound,
                "complete": True,
                "table_count": len(tables),
                "cache_key": key,
            },
        )
        print(f"[cache] saved {path}")
        if checkpoint is not None:
            try:
                checkpoint.unlink()
            except FileNotFoundError:
                pass
    return tables


def heap_push_best(heap, keep, rec):
    key = rec["score"]
    item = (key, rec["a"], rec["b"], rec)
    if len(heap) < keep:
        heapq.heappush(heap, item)
    elif item[:3] > heap[0][:3]:
        heapq.heapreplace(heap, item)


def family_parameter_symmetry(family):
    """Return an optional exact parameter-orbit symmetry declared by a family."""
    value = getattr(family, "parameter_symmetry", None)
    return str(value).strip().lower() if value is not None else None


def canonical_candidate_pair(family, a, b):
    """Canonicalize a reduced rational candidate without changing its orbit."""
    a = int(a)
    b = int(b)
    if family_parameter_symmetry(family) == "sign" and a < 0:
        a = -a
    return a, b


def sign_duplicate_in_requested_range(args, a):
    """True when negative ``a`` has its positive orbit mate in the same scan."""
    a = int(a)
    return a < 0 and int(args.a_min) <= -a <= int(args.a_max)


def stage0_scalar(args, tables, keep, family):
    heap = []
    tested = 0
    sign_symmetry = family_parameter_symmetry(family) == "sign"
    for b in range(args.b_min, args.b_max + 1):
        for a in range(args.a_min, args.a_max + 1):
            if math.gcd(a, b) != 1:
                continue
            if sign_symmetry and sign_duplicate_in_requested_range(args, a):
                continue
            score, used = score_rational(a, b, tables)
            tested += 1
            ca, cb = canonical_candidate_pair(family, a, b)
            heap_push_best(heap, keep, {
                "family": family.name(), "a": ca, "b": cb,
                "score": score, "prime_terms": used,
            })
    return [x[3] for x in sorted(heap, reverse=True)], tested




def _denominator_shells(b_min, b_max):
    """Inclusive logarithmic denominator shells covering [b_min,b_max]."""
    b_min, b_max = int(b_min), int(b_max)
    if b_min <= 0 or b_min > b_max:
        return []
    edges = {b_min, b_max + 1}
    power = 1
    while power <= b_max:
        if b_min < power <= b_max:
            edges.add(power)
        power *= 10
    ordered = sorted(edges)
    shells = []
    for low, stop in zip(ordered, ordered[1:]):
        high = min(b_max, stop - 1)
        if low <= high:
            shells.append((low, high))
    return shells


def stage0_sampled(args, tables, keep, family):
    """Deterministically sample reduced rational pairs across log denominator shells.

    This exists for high-baseline record hunts where denominator reach matters
    more than exhaustive rectangular enumeration.  The sampled points are exact
    rationals; only their Nagao ordering is heuristic.
    """
    sample_count = max(1, int(getattr(args, "sample_count", 200000) or 200000))
    seed = int(getattr(args, "sample_seed", 42) or 42)
    rng = random.Random(seed)
    shells = _denominator_shells(args.b_min, args.b_max)
    if not shells:
        return [], 0

    heap = []
    tested = 0
    seen = set()
    sign_symmetry = family_parameter_symmetry(family) == "sign"
    attempts = 0
    max_attempts = max(sample_count * 20, sample_count + 1000)

    # Seed each shell at its boundaries so every requested scale is represented.
    seeds = []
    for low, high in shells:
        seeds.extend((low, high))
    for b in seeds:
        if tested >= sample_count:
            break
        a = rng.randint(int(args.a_min), int(args.a_max))
        if math.gcd(a, b) != 1:
            continue
        ca, cb = canonical_candidate_pair(family, a, b)
        key = (ca, cb)
        if key in seen:
            continue
        seen.add(key)
        score, used = score_rational(ca, cb, tables)
        tested += 1
        heap_push_best(heap, keep, {
            "family": family.name(), "a": ca, "b": cb,
            "score": float(score), "prime_terms": int(used),
            "sampled": True,
        })

    while tested < sample_count and attempts < max_attempts:
        attempts += 1
        shell_index = attempts % len(shells)
        low, high = shells[shell_index]
        b = rng.randint(low, high)
        a = rng.randint(int(args.a_min), int(args.a_max))
        if math.gcd(a, b) != 1:
            continue
        if sign_symmetry and sign_duplicate_in_requested_range(args, a):
            a = abs(a)
        ca, cb = canonical_candidate_pair(family, a, b)
        key = (ca, cb)
        if key in seen:
            continue
        seen.add(key)
        score, used = score_rational(ca, cb, tables)
        tested += 1
        heap_push_best(heap, keep, {
            "family": family.name(), "a": ca, "b": cb,
            "score": float(score), "prime_terms": int(used),
            "sampled": True,
        })
        if tested % max(1, sample_count // 20) == 0:
            floor = heap[0][0] if heap else float("-inf")
            print(
                f"[sampled] tested={tested:,}/{sample_count:,} "
                f"shells={len(shells)} keep-floor={floor:.6f}"
            )

    return [x[3] for x in sorted(heap, reverse=True)], tested


def top_block_indices(aa, scores, keep):
    """Indices of the exact best ``keep`` entries by (score, a).

    We partition on score in NumPy, then resolve only the cutoff tie by the
    numerator.  This avoids sending every sieved numerator through Python's
    heap while preserving the same ordering as ``heap_push_best`` for a fixed
    denominator.
    """
    import numpy as np

    n = len(scores)
    take = min(int(keep), n)
    if take <= 0:
        return np.empty(0, dtype=np.int64)
    if take == n:
        return np.arange(n, dtype=np.int64)

    kth = np.partition(scores, n - take)[n - take]
    above = np.flatnonzero(scores > kth)
    tied = np.flatnonzero(scores == kth)
    need = take - len(above)
    if need < len(tied):
        # heap_push_best breaks score ties by larger numerator (then b).
        order = np.argsort(aa[tied], kind="stable")
        tied = tied[order[-need:]] if need else tied[:0]
    return np.concatenate((above, tied))


def stage0_sieve(args, tables, keep, family):
    import numpy as np

    heap = []
    tested = 0
    sign_symmetry = family_parameter_symmetry(family) == "sign"
    span = max(1, args.b_max - args.b_min + 1)
    for bi, b in enumerate(range(args.b_min, args.b_max + 1), 1):
        for aa, scores, used in iter_fixed_denominator_sieve(
            args.a_min, args.a_max, b, tables, block_size=args.block_size
        ):
            if sign_symmetry and len(aa):
                # Keep one representative when both signs are present in the
                # requested interval.  If only a negative representative was
                # requested, retain it but write the canonical positive orbit
                # representative to the candidate record.
                mate = -aa
                duplicate = (aa < 0) & (mate >= int(args.a_min)) & (mate <= int(args.a_max))
                keep_mask = ~duplicate
                aa = aa[keep_mask]
                scores = scores[keep_mask]
                used = used[keep_mask]
                aa = np.abs(aa)
            tested += len(aa)
            if not len(aa):
                continue
            idx = top_block_indices(aa, scores, keep)
            for j in idx.tolist():
                ca, cb = canonical_candidate_pair(family, int(aa[j]), int(b))
                heap_push_best(heap, keep, {
                    "family": family.name(), "a": ca, "b": cb,
                    "score": float(scores[j]), "prime_terms": int(used[j]),
                })
        if bi % max(1, span // 20) == 0 or bi == span:
            floor = heap[0][0] if heap else float("-inf")
            print(f"[sieve] b={b} tested={tested:,} keep-floor={floor:.6f}")
    return [x[3] for x in sorted(heap, reverse=True)], tested


def rescore_stage(rows, tables, keep, bound):
    heap = []
    for rec in rows:
        score, used = score_rational(int(rec["a"]), int(rec["b"]), tables)
        out = dict(rec)
        out["score"] = float(score)
        out["prime_terms"] = int(used)
        out["prime_bound"] = int(bound)
        heap_push_best(heap, keep, out)
    return [x[3] for x in sorted(heap, reverse=True)]


def main():
    args = parse_args()
    if args.b_min <= 0:
        raise SystemExit("positive denominators are required")
    if args.a_min > args.a_max or args.b_min > args.b_max:
        raise SystemExit("bad search range")
    if args.top <= 0:
        raise SystemExit("--top must be positive")

    bounds, keeps = normalize_stages(args)
    args.family = resolve_family_spec(args.family)
    family = load_family(args.family)
    print(f"[family] {family.name()}")
    gr = family_generic_rank(family)
    if gr is not None:
        print(f"[family] operational generic lower bound {gr}")
    symmetry = family_parameter_symmetry(family)
    if symmetry == "sign":
        print("[symmetry] canonicalizing exact parameter orbit t ~ -t")
    print(f"[engine] {args.engine}")
    print(f"[stages] {list(zip(bounds, keeps))}")

    tables = {}
    tables = get_tables(family, args.family, bounds[0], tables, args)
    if args.engine == "sieve":
        rows, tested = stage0_sieve(args, tables, keeps[0], family)
    elif args.engine == "sampled":
        rows, tested = stage0_sampled(args, tables, keeps[0], family)
    else:
        rows, tested = stage0_scalar(args, tables, keeps[0], family)
    for rec in rows:
        rec["prime_bound"] = bounds[0]
    print(f"[stage 1/{len(bounds)}] retained {len(rows)} from {tested:,}")

    for idx, bound in enumerate(bounds[1:], 1):
        tables = get_tables(family, args.family, bound, tables, args)
        before = len(rows)
        rows = rescore_stage(rows, tables, keeps[idx], bound)
        print(f"[stage {idx+1}/{len(bounds)}] rescored {before}; retained {len(rows)}")

    rows = sorted(rows, key=lambda r: (r["score"], r["a"], r["b"]), reverse=True)[: args.top]
    out = Path(args.out)
    with out.open("w") as f:
        for rec in rows:
            rec = dict(rec)
            rec["t"] = f"{rec['a']}/{rec['b']}"
            rec["family_spec"] = args.family
            rec["search_engine"] = args.engine
            rec["stage_bounds"] = bounds
            if symmetry:
                rec["parameter_symmetry"] = symmetry
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    print(f"[done] wrote {len(rows)} candidates to {out}")
    if rows:
        r = rows[0]
        print("[best]", (r["score"], r["a"], r["b"], r["prime_terms"]))


if __name__ == "__main__":
    main()
