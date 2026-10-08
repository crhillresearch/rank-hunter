"""Build and persist a screened Mordell--Weil lattice for one stored curve.

The expensive object is the Neron--Tate height-pairing matrix. We compute it
once, persist it, and reuse it for downstream geometry inspection and
half-coset prioritization. Positive definiteness is a numerical independence
screen, not by itself a formal proof.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from sage.all import QQ, EllipticCurve

from rank42.db import connect, get_curve
from rank42.family_loader import load_family, resolve_family_spec
from rank42.lattice_store import list_lattices, store_lattice
from rank42.points import rigorous_witness_basis


def _point_json(P):
    return [str(P[0]), str(P[1])]


def _matrix_json(M):
    return [[str(M[i, j]) for j in range(M.ncols())] for i in range(M.nrows())]


def _load_stored_curve(row):
    if not row["a_invariants_json"]:
        raise SystemExit(f"curve #{row['id']} has no stored a-invariants")
    return EllipticCurve(QQ, [QQ(str(x)) for x in json.loads(row["a_invariants_json"])])


def _load_points_file(E, path):
    data = json.loads(Path(path).read_text())
    raw = data.get("points", data) if isinstance(data, dict) else data
    out = []
    for i, xy in enumerate(raw, 1):
        try:
            out.append(E(QQ(str(xy[0])), QQ(str(xy[1]))))
        except Exception as exc:
            raise SystemExit(f"point {i} from {path} is not on stored curve: {exc}")
    return out


def _generic_points_on_stored_model(row, E, family_spec):
    family = load_family(family_spec, need_sections=True)
    t = QQ(str(row["parameter"]))
    Ef = family.curve(t)
    if Ef is None:
        raise SystemExit(f"family {family_spec} is singular/undefined at t={t}")
    points = family.generic_section_points(t)
    if list(Ef.a_invariants()) == list(E.a_invariants()):
        return points
    try:
        iso = Ef.isomorphism_to(E)
    except Exception as exc:
        raise SystemExit(
            "specialized family curve is not isomorphic to the stored curve; "
            f"cannot transport generic sections: {exc}"
        )
    return [iso(P) for P in points]


def _signless_point_key(P):
    Q = -P
    return min(
        (str(P[0]), str(P[1])),
        (str(Q[0]), str(Q[1])),
    )


def _point_ledger_points(db, row, E, point_ids):
    """Load the authoritative rigorous witness basis plus selected exact points."""
    basis, required, complete = rigorous_witness_basis(db, int(row["id"]), E)
    if not complete:
        raise SystemExit(
            f"curve #{row['id']} has rigorous lower ≥{required}, but only "
            f"{len(basis)} replayable witness point(s); repair the witness basis first"
        )

    entries = []
    seen = set()
    for index, P in enumerate(basis, 1):
        key = _signless_point_key(P)
        if key in seen:
            continue
        seen.add(key)
        entries.append((P, f"rigorous_basis:{index}"))

    selected_ids = []
    skipped_duplicate_ids = []
    for raw_id in point_ids or []:
        point_id = int(raw_id)
        rec = db.execute(
            "SELECT * FROM points WHERE id=? AND curve_id=?",
            (point_id, int(row["id"])),
        ).fetchone()
        if rec is None:
            raise SystemExit(
                f"point #{point_id} does not belong to curve #{row['id']}"
            )
        if not int(rec["exact_verified"] or 0):
            raise SystemExit(
                f"point #{point_id} is not exact-verified and cannot enter MW Geometry"
            )
        try:
            P = E(QQ(str(rec["x"])), QQ(str(rec["y"])))
        except Exception as exc:
            raise SystemExit(
                f"point #{point_id} cannot be reconstructed on curve #{row['id']}: {exc}"
            ) from exc
        key = _signless_point_key(P)
        if key in seen:
            skipped_duplicate_ids.append(point_id)
            continue
        seen.add(key)
        entries.append((P, f"point:{point_id}"))
        selected_ids.append(point_id)

    return [P for P, _ in entries], {
        "rigorous_basis_count": len(basis),
        "rigorous_basis_required": int(required),
        "rigorous_basis_complete": bool(complete),
        "selected_point_ids": selected_ids,
        "skipped_duplicate_point_ids": skipped_duplicate_ids,
        "input_labels": [label for _, label in entries],
    }


def _dedupe(points):
    seen = set()
    out = []
    for P in points:
        if P.is_zero():
            continue
        Q = -P
        key = min((QQ(P[0]), QQ(P[1])), (QQ(Q[0]), QQ(Q[1])))
        if key in seen:
            continue
        seen.add(key)
        out.append(P)
    return out


MARKER = "RANK42_LATTICE_RESULT="


class LatticeTimeout(RuntimeError):
    pass


class LatticeFailure(RuntimeError):
    pass


def run_lattice_build(
    a_invariants,
    points,
    *,
    precision=256,
    lll_reduce=True,
    timeout=300,
):
    """Run height-pairing/LLL construction in an isolated subprocess."""
    payload = {
        "a_invariants": [str(x) for x in a_invariants],
        "points": [[str(P[0]), str(P[1])] for P in points],
        "precision_bits": max(64, int(precision)),
        "lll_reduce": bool(lll_reduce),
    }
    started = time.monotonic()
    try:
        cp = subprocess.run(
            [sys.executable, "-m", "rank42.lattice_worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout)),
        )
    except subprocess.TimeoutExpired as exc:
        raise LatticeTimeout(
            f"height-lattice worker exceeded {int(timeout)}s"
        ) from exc

    elapsed = time.monotonic() - started
    result = None
    for line in reversed((cp.stdout or "").splitlines()):
        if line.startswith(MARKER):
            try:
                result = json.loads(line[len(MARKER):])
            except json.JSONDecodeError:
                result = None
            break

    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:]
    )
    if result is None:
        raise LatticeFailure(
            f"height-lattice worker returned no result "
            f"(exit={cp.returncode}); tail:\n{tail}"
        )
    if result.get("status") != "completed":
        raise LatticeFailure(str(result.get("error") or tail))
    if cp.returncode != 0:
        raise LatticeFailure(
            f"height-lattice worker exited {cp.returncode}; tail:\n{tail}"
        )
    result["runtime_seconds"] = float(
        result.get("runtime_seconds") or elapsed
    )
    return result


def build_lattice(E, points, *, precision, lll_reduce=True):
    points = _dedupe(points)
    if not points:
        raise ValueError("lattice basis is empty")

    print(f"[height] computing {len(points)}x{len(points)} pairing matrix at {precision} bits...")
    H = E.height_pairing_matrix(points, precision=int(precision))
    input_points = list(points)
    input_gram = _matrix_json(H)
    input_eigs = H.eigenvalues()
    input_det = H.det()

    eigs = list(input_eigs)
    mineig = min(eigs)
    positive = bool(mineig > 0)
    transform = None

    if positive and lll_reduce and len(points) > 1:
        print("[lll] reducing the point basis with the cached height matrix...")
        try:
            reduced_points, U = E.lll_reduce(points, height_matrix=H)
            # Sage documents Q_j = sum_i U[i,j] P_i, hence G_Q = U^T G_P U.
            H = U.transpose() * H * U
            points = list(reduced_points)
            transform = [[str(U[i, j]) for j in range(U.ncols())] for i in range(U.nrows())]
            eigs = H.eigenvalues()
            mineig = min(eigs)
        except Exception as exc:
            print(f"[lll] WARNING: reduction failed; keeping original basis: {exc}")

    det = H.det()
    gram = _matrix_json(H)
    maxeig = max(eigs)
    return {
        "points": points,
        "gram": gram,
        "determinant": str(det),
        "min_eigenvalue": str(mineig),
        "max_eigenvalue": str(maxeig),
        "eigenvalues": [str(value) for value in eigs],
        "positive_definite_screen": bool(mineig > 0),
        "lll_transform": transform,
        "input_points": input_points,
        "input_gram": input_gram,
        "input_determinant": str(input_det),
        "input_eigenvalues": [str(value) for value in input_eigs],
    }


def parse_args():
    ap = argparse.ArgumentParser(description="Build/persist a Mordell--Weil height lattice.")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--curve-id", type=int)
    ap.add_argument(
        "--source", choices=("generic", "stored-generators", "points-json", "point-ledger"),
        default="generic",
    )
    ap.add_argument("--family", default=None, help="family spec used with --source generic")
    ap.add_argument("--points-json", help="JSON point list used with --source points-json")
    ap.add_argument("--point-id", type=int, action="append", default=[], help="exact Point Ledger candidate included with --source point-ledger")
    ap.add_argument("--precision", type=int, default=256)
    ap.add_argument("--no-lll", action="store_true", help="keep the input point basis instead of height-LLL reduction")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    if args.list:
        rows = list_lattices(db, curve_id=args.curve_id, limit=args.limit)
        if not rows:
            print("no Mordell-Weil lattices")
            return
        print("MORDELL-WEIL LATTICES")
        print("=" * 78)
        for r in rows:
            print(
                f"#{r['id']:<4} curve={r['curve_id']:<5} rank={r['basis_count']:<3} "
                f"pd={r['positive_definite_screen']} prec={r['precision_bits']} "
                f"source={r['source']}"
            )
        return

    if args.curve_id is None:
        raise SystemExit("--curve-id is required unless --list is used")
    row = get_curve(db, args.curve_id)
    if row is None:
        raise SystemExit(f"curve #{args.curve_id} not found")
    E = _load_stored_curve(row)

    point_ledger_meta = {}
    if args.source == "generic":
        args.family = resolve_family_spec(args.family)
        points = _generic_points_on_stored_model(row, E, args.family)
        family_spec = args.family
    elif args.source == "stored-generators":
        raw = json.loads(row["generators_json"] or "[]")
        points = [E(QQ(str(x)), QQ(str(y))) for x, y in raw]
        family_spec = None
    elif args.source == "point-ledger":
        points, point_ledger_meta = _point_ledger_points(
            db,
            row,
            E,
            args.point_id,
        )
        family_spec = None
    else:
        if not args.points_json:
            raise SystemExit("--points-json is required with --source points-json")
        points = _load_points_file(E, args.points_json)
        family_spec = None

    print("RANK HUNTER MORDELL-WEIL LATTICE")
    print("=" * 62)
    print("curve id             =", row["id"])
    print("parameter            =", row["parameter"])
    print("source               =", args.source)
    if family_spec:
        print("family spec          =", family_spec)
    print("input points         =", len(points))

    result = build_lattice(E, points, precision=args.precision, lll_reduce=not args.no_lll)
    basis = [_point_json(P) for P in result["points"]]
    input_basis = [_point_json(P) for P in result["input_points"]]
    stored = store_lattice(
        db,
        curve_id=row["id"],
        source=args.source,
        family_spec=family_spec,
        parameter=row["parameter"],
        basis=basis,
        gram=result["gram"],
        precision_bits=args.precision,
        determinant=result["determinant"],
        min_eigenvalue=result["min_eigenvalue"],
        positive_definite_screen=result["positive_definite_screen"],
        metadata={
            "a_invariants": json.loads(row["a_invariants_json"]),
            "eigenvalues": result["eigenvalues"],
            "max_eigenvalue": result["max_eigenvalue"],
            "lll_reduced": bool(result.get("lll_transform")),
            "lll_transform": result.get("lll_transform"),
            "input_basis": input_basis,
            "input_gram": result["input_gram"],
            "input_determinant": result["input_determinant"],
            "input_eigenvalues": result["input_eigenvalues"],
            "point_ledger": point_ledger_meta or None,
        },
    )
    print("lattice id           =", stored["id"])
    print("basis count          =", stored["basis_count"])
    print("positive definite    =", bool(stored["positive_definite_screen"]))
    print("determinant          =", stored["determinant"])
    print("min eigenvalue       =", stored["min_eigenvalue"])
    print("max eigenvalue       =", result["max_eigenvalue"])
    if not stored["positive_definite_screen"]:
        print("WARNING: basis failed numerical positive-definite height screen")


if __name__ == "__main__":
    main()
