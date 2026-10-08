import argparse
import json
from pathlib import Path

from sage.all import QQ

from rank42.family_loader import load_family
from rank42.mathutil import (
    curve_summary,
    find_small_points,
    independent_subset_greedy,
)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--family", default="ek2020_z2_rank9", help="family alias/module, or json:/path/to/family.json")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--point-search-bound", type=int, default=10000)
    ap.add_argument("--out", required=True)
    return ap.parse_args()

def point_to_json(P):
    return [str(P[0]), str(P[1])]

def canonical_point_key(P):
    a = (QQ(P[0]), QQ(P[1]))
    Q = -P
    b = (QQ(Q[0]), QQ(Q[1]))
    return min(a, b)

def dedupe_points(points):
    seen = set()
    out = []
    for P in points:
        if P.is_zero():
            continue
        key = canonical_point_key(P)
        if key in seen:
            continue
        seen.add(key)
        out.append(P)
    return out

def main():
    args = parse_args()
    src = Path(args.input)
    out = Path(args.out)
    family = load_family(args.family, need_sections=True)

    rows = []
    with src.open() as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    declared = {r.get("family") for r in rows if r.get("family")}
    if declared and declared != {family.name()}:
        raise SystemExit(
            f"candidate file family {sorted(declared)!r} does not match loaded family {family.name()!r}"
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[:args.limit]

    with out.open("w") as fout:
        for i, row in enumerate(rows, 1):
            t = QQ(row["a"]) / QQ(row["b"])
            print(f"[{i}/{len(rows)}] t={t} score={row['score']:.6f}")

            E = family.curve(t)
            if E is None:
                print("    singular specialization; skipped")
                continue

            summary = curve_summary(E)

            generic = family.generic_section_points(t)
            generic_indep, generic_info = independent_subset_greedy(E, generic)

            small = find_small_points(E, args.point_search_bound)
            combined = dedupe_points(generic + small)
            indep, indep_info = independent_subset_greedy(E, combined)

            generic_keys = {canonical_point_key(P) for P in generic}
            extra = [P for P in small if canonical_point_key(P) not in generic_keys]

            result = dict(row)
            result["curve"] = summary
            result["generic_sections_specialized"] = [point_to_json(P) for P in generic]
            result["generic_independent_screen"] = generic_info
            result["small_points_found"] = [point_to_json(P) for P in small]
            result["extra_small_points"] = [point_to_json(P) for P in extra]
            result["independent_points_screened"] = [point_to_json(P) for P in indep]
            result["independence_screen"] = indep_info
            fout.write(json.dumps(result, sort_keys=True) + "\n")
            fout.flush()

            print(
                f"    generic_sections={len(generic)} "
                f"generic_independent={len(generic_indep)} "
                f"small_points={len(small)} "
                f"combined_independent={len(indep)} "
                f"conductor={summary.get('conductor')}"
            )

if __name__ == "__main__":
    main()
