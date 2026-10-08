import argparse
import json
from pathlib import Path

from sage.all import QQ, ZZ, EllipticCurve, prime_divisors

from rank42.db import connect, get_curve, upsert_curve, update_curve, log_event
from rank42.mathutil import height_screen
from rank42.model_points import map_points_exact
from rank42.points import upsert_point

def parse_args():
    ap = argparse.ArgumentParser(description="Import exact curve/point material into rank42.db without trusting external rank claims as proof")
    ap.add_argument("json_file")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--family", default="imported")
    ap.add_argument("--parameter", default=None)
    ap.add_argument("--exact-rank", type=int, default=None, help="external exact-rank claim to retain as reference provenance only")
    ap.add_argument("--precision", type=int, default=512)
    return ap.parse_args()

def import_verified_result(
    db,
    data,
    *,
    family="imported",
    parameter=None,
    claimed_exact_rank=None,
    precision=512,
    source="import_result",
):
    """Import exact curve/point material without promoting unproved rank claims."""
    E = EllipticCurve(QQ, [QQ(str(x)) for x in data["a_invariants"]])
    if E.discriminant() == 0:
        raise ValueError("singular curve")

    source_points = [
        E(QQ(str(x)), QQ(str(y)))
        for x, y in data.get("points", [])
    ]
    Em = E.global_minimal_model()
    points = map_points_exact(E, Em, source_points)
    screen = height_screen(Em, points, precision=int(precision))
    bad = [int(p) for p in prime_divisors(abs(ZZ(Em.discriminant())))]

    claimed = claimed_exact_rank
    if claimed is None:
        claimed = data.get("known_exact_rank")
    claimed = None if claimed is None else int(claimed)
    if claimed is not None and claimed < 0:
        raise ValueError("claimed exact rank must be non-negative")

    parameter = str(
        parameter
        if parameter is not None
        else data.get("source_parameter", "imported")
    )
    curve_id = upsert_curve(db, family=str(family), parameter=parameter, score=None)
    before = get_curve(db, curve_id)
    stored_model = [str(x) for x in Em.a_invariants()]
    if before is not None and before["a_invariants_json"]:
        try:
            existing_model = [str(x) for x in json.loads(before["a_invariants_json"])]
        except Exception:
            existing_model = None
        if existing_model is not None and existing_model != stored_model:
            raise ValueError(
                "import key collides with a different stored minimal model"
            )
    fields = {
        "a_invariants_json": json.dumps(stored_model),
        "conductor": str(Em.conductor()),
        "discriminant": str(Em.discriminant()),
        "bad_primes_json": json.dumps(bad),
        "root_number": int(Em.root_number()),
        "regulator": str(data.get("regulator_mwrank", screen["determinant"])),
        "error": None,
    }
    if before is not None and str(before["status"] or "") in {"", "new", "imported"}:
        fields["status"] = "imported"
    update_curve(db, curve_id, **fields)

    for index, P in enumerate(points):
        upsert_point(
            db,
            curve_id=curve_id,
            x=P[0],
            y=P[1],
            source=str(source),
            role="candidate_extra",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref="import_result:exact-point",
            metadata={
                "import_index": index,
                "source_model_a_invariants": [str(x) for x in E.a_invariants()],
                "stored_model_a_invariants": [str(x) for x in Em.a_invariants()],
                "height_screen_positive": bool(screen.get("positive_definite_screen")),
                "claimed_exact_rank": claimed,
                "claim_is_proof": False,
            },
        )

    claim_text = "none" if claimed is None else str(claimed)
    log_event(
        db,
        curve_id,
        "info",
        (
            f"Imported {len(points)} exact rational points; "
            f"external_exact_rank_claim={claim_text}; source={source}"
        ),
    )
    return {
        "curve_id": int(curve_id),
        "point_count": len(points),
        "claimed_exact_rank": claimed,
        "height_screen_positive": bool(screen.get("positive_definite_screen")),
        "stored_a_invariants": stored_model,
    }


def main():
    args = parse_args()
    path = Path(args.json_file)
    data = json.loads(path.read_text())

    db = connect(args.db)
    try:
        try:
            result = import_verified_result(
                db,
                data,
                family=args.family,
                parameter=args.parameter or str(data.get("source_parameter", path.stem)),
                claimed_exact_rank=args.exact_rank,
                precision=args.precision,
                source=f"import_result:{path.name}",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        db.close()

    print(f"Imported curve #{result['curve_id']}")
    print("a-invariants =", result["stored_a_invariants"])
    print("exact rational points imported =", result["point_count"])
    print("height screen positive =", result["height_screen_positive"])
    print("claimed exact rank (reference only) =", result["claimed_exact_rank"])
    print("rigorous rank promotion = none")
    print("database =", args.db)

if __name__ == "__main__":
    main()
