"""Construct an exact linear elliptic pencil through a stored curve.

Up to two stored rational points remain constant sections. The deformation
changes Weierstrass coefficients linearly in T and specializes exactly to the
source curve at T=0. It does not claim the source curve determines a unique
pencil or that its full Mordell–Weil group extends generically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import EllipticCurve, FractionField, PolynomialRing, QQ

from rank42.db import connect_existing, get_curve
from rank42.formula_family import FormulaFamily
from rank42.pencil_registry import register_pencil
from rank42.plugins import read_plugin


def construct_curve_pencil(db, *, project_root, curve_id, point_ids):
    curve_id = int(curve_id)
    point_ids = tuple(dict.fromkeys(int(value) for value in point_ids))
    if not 0 <= len(point_ids) <= 2:
        raise ValueError("choose at most two exact points")
    source = get_curve(db, curve_id)
    if source is None or not source["a_invariants_json"]:
        raise ValueError(f"curve #{curve_id} has no exact Weierstrass model")
    invariants = [QQ(value) for value in json.loads(source["a_invariants_json"])]
    if len(invariants) != 5:
        raise ValueError("the source curve must have five Weierstrass invariants")
    original = EllipticCurve(QQ, invariants)
    if original.discriminant() == 0:
        raise ValueError("the source model is singular")
    rows = []
    if point_ids:
        placeholders = ",".join("?" for _ in point_ids)
        rows = db.execute(
            f"SELECT id,x,y,rigorous_independent FROM points WHERE curve_id=? "
            f"AND exact_verified=1 AND id IN ({placeholders})",
            (curve_id, *point_ids),
        ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    if len(by_id) != len(point_ids):
        raise ValueError("all selected points must be exact-verified on the source curve")
    points = []
    for point_id in point_ids:
        row = by_id[point_id]
        point = original(QQ(row["x"]), QQ(row["y"]))
        if point.is_zero():
            raise ValueError("the point at infinity cannot be used as a section")
        points.append(point)
    if len(points) == 2 and points[0][0] == points[1][0]:
        raise ValueError("choose points with distinct x-coordinates")

    ring = PolynomialRing(QQ, "T")
    T = ring.gen()
    field = FractionField(ring)
    deformations = []
    if not points:
        deformations = [
            (0, 0, 0, 1, 1),
            (0, 1, 0, 0, 1),
        ]
    elif len(points) == 1:
        x, y = QQ(points[0][0]), QQ(points[0][1])
        deformations = [
            (0, 0, 0, 1, -x),
            (0, 1, 0, 0, -(x * x)),
            (1, 0, 0, 0, x * y),
        ]
    else:
        x1, x2 = (QQ(point[0]) for point in points)
        y1, y2 = (QQ(point[1]) for point in points)
        a4_from_a1 = (x1 * y1 - x2 * y2) / (x1 - x2)
        a4_from_a3 = (y1 - y2) / (x1 - x2)
        deformations = [
            (0, 1, 0, -(x1 + x2), x1 * x2),
            (1, 0, 0, a4_from_a1, x1 * y1 - x1 * a4_from_a1),
            (0, 0, 1, a4_from_a3, y1 - x1 * a4_from_a3),
        ]

    coefficients = None
    family = None
    chosen_deformation = None
    for delta in deformations:
        proposed = [field(base) + field(QQ(step)) * T
                    for base, step in zip(invariants, delta)]
        candidate = EllipticCurve(field, proposed)
        if candidate.discriminant() == 0 or candidate.j_invariant() == field(original.j_invariant()):
            continue
        for point in points:
            candidate(field(QQ(point[0])), field(QQ(point[1])))
        coefficients = proposed
        family = candidate
        chosen_deformation = delta
        break
    if family is None or coefficients is None or chosen_deformation is None:
        raise ArithmeticError("no non-isotrivial point-preserving coefficient pencil was found")
    deformation = "; ".join(
        f"{name}(T)={name}+({QQ(step)})T"
        for name, step in zip(("a1", "a2", "a3", "a4", "a6"), chosen_deformation)
        if step
    )
    if [QQ(value.numerator()(0) / value.denominator()(0))
            for value in coefficients] != invariants:
        raise ArithmeticError("specialization at T=0 did not reproduce the source model")

    root = Path(project_root).resolve()
    identity = hashlib.sha256(
        json.dumps({"curve_id": curve_id, "point_ids": point_ids,
                    "invariants": [str(value) for value in invariants]},
                   sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    plugin_id = f"curve_pencil_{curve_id}_{identity}"
    plugin_root = root / "plugins" / "families" / plugin_id
    plugin_root.mkdir(parents=True, exist_ok=True)
    family_path = plugin_root / "family.json"
    manifest_path = plugin_root / "plugin.json"
    family_record = {
        "name": f"Curve #{curve_id} · curve-seeded pencil",
        "parameter": "T",
        "generic_rank": None,
        "a_invariants": [str(value) for value in coefficients],
        "sections": [
            {"label": f"Curve #{curve_id} point #{point_id}",
             "x": str(QQ(point[0])), "y": str(QQ(point[1])),
             "source_curve_id": curve_id, "source_point_id": point_id}
            for point_id, point in zip(point_ids, points)
        ],
    }
    manifest = {
        "schema_version": 1,
        "plugin_type": "family",
        "id": plugin_id,
        "name": family_record["name"],
        "version": "0.1.0",
        "icon": ":material/edit:",
        "description": (
            f"Exact non-isotrivial linear Weierstrass pencil through curve #{curve_id}; "
            f"{len(points)} constant rational section(s), with source fiber T=0."
        ),
        "enabled_by_default": True,
        "generic_rank_claim_state": "sections_verified",
        "validation_parameter": "0",
        "family": {"kind": "json", "file": "family.json"},
        "capabilities": ["candidate_generation"],
        "candidate_defaults": {
            "a_min": -200, "a_max": 200, "b_min": 1, "b_max": 50,
            "top": 200, "engine": "sieve",
        },
    }
    FormulaFamily(family_record).validate_symbolically()
    family_path.write_text(json.dumps(family_record, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    read_plugin(plugin_root)
    register_pencil(
        db, plugin_id=plugin_id, pencil_key="seed-fiber",
        label=family_record["name"], status="constructed",
        definition={
            "family_spec": f"json:{family_path}",
            "model_path": str(family_path),
            "source_curve_id": curve_id,
            "source_point_ids": list(point_ids),
            "source_parameter": "T=0",
            "construction": "linear Weierstrass coefficient pencil",
            "deformation": deformation,
            "verification": {
                "source_fiber_equal": True,
                "sections_on_generic_curve": len(points),
                "generic_discriminant_nonzero": True,
                "j_nonconstant": True,
            },
            "rank_claim": None,
        },
        curve_ids=[curve_id],
    )
    db.commit()
    return {
        "plugin_id": plugin_id,
        "family_spec": f"json:{family_path}",
        "source_curve_id": curve_id,
        "source_point_ids": list(point_ids),
        "generic_sections_verified": len(points),
        "rank_claim": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Construct a curve-seeded elliptic pencil")
    parser.add_argument("--db", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--curve-id", required=True, type=int)
    parser.add_argument("--point-id", type=int, action="append", default=[])
    args = parser.parse_args()
    db = connect_existing(args.db)
    try:
        print(json.dumps(construct_curve_pencil(
            db, project_root=args.project_root, curve_id=args.curve_id,
            point_ids=args.point_id,
        ), sort_keys=True), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
