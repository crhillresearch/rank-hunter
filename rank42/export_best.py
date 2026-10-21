import argparse
import json
from pathlib import Path

from rank42.db import connect, incumbent, proven_lower

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--out", default="best-candidate.json")
    return ap.parse_args()

def main():
    args = parse_args()
    db = connect(args.db)
    row = incumbent(db)
    if row is None:
        raise SystemExit("database has no ranked curves")

    points = json.loads(row["generators_json"] or "[]")
    data = {
        "a_invariants": json.loads(row["a_invariants_json"]),
        "points": points,
        "bad_primes": json.loads(row["bad_primes_json"] or "[]"),
        "known_exact_rank": row["exact_rank"],
        "known_lower_bound": proven_lower(row),
        "source_parameter": row["parameter"],
        "family": row["family"],
        "regulator_mwrank": row["regulator"],
    }
    Path(args.out).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"Exported curve #{row['id']} to {args.out}")

if __name__ == "__main__":
    main()
