"""Validate one exact legacy-rank tier, skipping already certified rows."""
from __future__ import annotations

import argparse
import json
import sqlite3

from rank42.db import connect, now
from rank42.exact_lb import ExactCertificateTimeout, run_exact_certificate
from rank42.rank_evidence import apply_reduced_rank_state, record_rank_evidence


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser(description="Certify one legacy recovery rank tier")
    ap.add_argument("--work", required=True)
    ap.add_argument("--legacy-lower", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--max-halvings", type=int, default=40)
    args = ap.parse_args()

    db = connect(args.work)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT m.*, c.a_invariants_json, c.generators_json
        FROM legacy_recovery_map m
        JOIN curves c ON c.id=m.curve_id
        WHERE m.model_valid=1
          AND m.generator_count>0
          AND m.legacy_lower=?
          AND m.validation_status!='certified'
        ORDER BY m.old_curve_id
        """,
        (args.legacy_lower,),
    ).fetchall()

    out = {"legacy_lower": args.legacy_lower, "attempted": 0, "certified": 0,
           "timeout": 0, "failed": 0, "inconclusive": 0, "results": []}
    for row in rows:
        out["attempted"] += 1
        model = _loads(row["a_invariants_json"], [])
        points = [p for p in _loads(row["generators_json"], []) if isinstance(p, list) and len(p) >= 2]
        status = "inconclusive"
        payload = {}
        try:
            result = run_exact_certificate(
                model, points, timeout=args.timeout, max_halvings=args.max_halvings
            )
            if result.get("independent"):
                lower = int(result["rank_lower_bound"])
                record_rank_evidence(
                    db,
                    curve_id=int(row["curve_id"]),
                    model=model,
                    data={
                        "engine": "exact_quadratic_character",
                        "evidence_type": "certified_subgroup",
                        "status": "completed",
                        "rigorous": True,
                        "rigorous_lower": lower,
                        "rigorous_upper": None,
                        "points_found": points,
                        "options": {
                            "legacy_recovery": True,
                            "old_curve_id": int(row["old_curve_id"]),
                            "max_halvings": args.max_halvings,
                        },
                        "elapsed_seconds": result.get("runtime_seconds"),
                    },
                )
                state = apply_reduced_rank_state(db, int(row["curve_id"]))
                payload = {"certificate": result, "state": state}
                status = "certified"
                out["certified"] += 1
            else:
                payload = {"certificate": result}
                out["inconclusive"] += 1
        except ExactCertificateTimeout as exc:
            status = "timeout"
            payload = {"error": str(exc)}
            out["timeout"] += 1
        except Exception as exc:
            status = "failed"
            payload = {"error": str(exc)}
            out["failed"] += 1

        db.execute(
            """
            UPDATE legacy_recovery_map
            SET validation_status=?, validation_json=?, updated_at=?
            WHERE old_db=? AND old_curve_id=?
            """,
            (
                status,
                json.dumps(payload, sort_keys=True, default=str),
                now(),
                row["old_db"],
                row["old_curve_id"],
            ),
        )
        db.commit()
        out["results"].append({
            "curve_id": int(row["curve_id"]),
            "old_curve_id": int(row["old_curve_id"]),
            "legacy_lower": row["legacy_lower"],
            "status": status,
        })

    db.close()
    print(json.dumps(out, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
