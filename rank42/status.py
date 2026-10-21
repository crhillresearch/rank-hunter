import argparse
import json

from rank42.catalog import catalog_status, external_best_lower_bound
from rank42.db import (
    connect,
    best_exact_rank,
    best_lower_bound,
    incumbent,
    stats,
    proven_lower,
    quartic_stats,
    lattice_stats,
)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--events", type=int, default=8)
    return ap.parse_args()

def main():
    args = parse_args()
    db = connect(args.db)
    s = stats(db)
    qs = quartic_stats(db)
    ls = lattice_stats(db)
    best_exact = best_exact_rank(db)
    best_lower = best_lower_bound(db)
    inc = incumbent(db)
    ext_status = catalog_status(db, "icarm")
    ext_best = external_best_lower_bound(db, "icarm") if ext_status is not None else 0

    print("RANK HUNTER")
    print("=" * 62)
    print(f"Best exact rank        {best_exact}")
    print(f"Best proven lower      {best_lower}")
    if ext_status is not None:
        print(f"External ICARM ref     rank >= {ext_best}  (external reference only)")
    print()
    print(f"Curves in database     {s.get('curves') or 0}")
    print(f"Pruned by upper bound  {s.get('pruned') or 0}")
    print(f"Promising              {s.get('promising') or 0}")
    print(f"Exact ranks            {s.get('exact_count') or 0}")
    print(f"Strong descents        {(s.get('strong_done') or 0) + (s.get('exact_count') or 0)}")
    print(f"Quick timeouts         {s.get('quick_timeout') or 0}")
    print(f"Strong timeouts        {s.get('strong_timeout') or 0}")
    print(f"Errors                 {s.get('errors') or 0}")
    print()
    print(f"Quartic searches       {qs.get('searches') or 0}")
    print(f"  running              {qs.get('running') or 0}")
    print(f"  done                 {qs.get('done') or 0}")
    print(f"  timeouts/errors      {(qs.get('timeouts') or 0) + (qs.get('errors') or 0)}")
    print(f"Quartic points stored  {qs.get('points') or 0}")
    print()
    print(f"MW lattices            {ls.get('lattices') or 0}")
    print(f"Half-lattice holes     {ls.get('holes') or 0}")
    print(f"Exact coverings        {ls.get('coverings') or 0}")
    print(f"Mapped extra points    {ls.get('extra_points') or 0}")
    print(f"Rank-growth screens    {ls.get('independent_screens') or 0}")

    if inc is not None:
        r = proven_lower(inc)
        print()
        print("BEST CURVE")
        print("-" * 62)
        print("curve id   =", inc["id"])
        print("family     =", inc["family"])
        print("parameter  =", inc["parameter"])
        print("rank >=    =", r)
        if inc["exact_rank"] is not None:
            print("exact rank =", inc["exact_rank"])
        if inc["score"] is not None:
            print("score      =", f"{inc['score']:.6f}")
        if inc["a_invariants_json"]:
            print("a-invs     =", json.loads(inc["a_invariants_json"]))


    print()
    print("RECENT EVENTS")
    print("-" * 62)
    rows = db.execute(
        """
        SELECT e.created_at, e.level, e.message, c.parameter
        FROM events e LEFT JOIN curves c ON c.id=e.curve_id
        ORDER BY e.id DESC LIMIT ?
        """,
        (args.events,),
    ).fetchall()
    for row in reversed(rows):
        stamp = row["created_at"].replace("T", " ")[:19]
        param = row["parameter"] or "-"
        print(f"{stamp}  {row['level']:<5}  t={param:<12} {row['message']}")

if __name__ == "__main__":
    main()
