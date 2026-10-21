"""Reverse engineer exceptional elliptic curves without changing proof claims.

The workflow intentionally separates four evidence classes:

* exact: rational arithmetic, Weierstrass invariants, exact on-curve checks;
* source provenance: rigorous lower bounds imported from the source catalog;
* heuristic: Nagao/Mestre-style finite-field scores;
* numerical: optional Neron--Tate height-pairing screens behind a hard timeout.

No result from this module updates ``curves.exact_rank`` or any local rigorous
rank field.  Its purpose is comparative research: learn which measurable
features distinguish known exceptional curves from the search population.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from fractions import Fraction

from rank42.catalog import get_external_curve
from rank42.db import connect, get_curve, proven_lower
from rank42.reverse_store import list_reports, report_payload, store_report

HEIGHT_MARKER = "RANK42_HEIGHT_JSON="
SMALL_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97)


def Q(value):
    return Fraction(str(value))


def normalize_ainvs(ainvs):
    vals = tuple(Q(x) for x in ainvs)
    if len(vals) == 2:
        vals = (Fraction(0), Fraction(0), Fraction(0), vals[0], vals[1])
    if len(vals) != 5:
        raise ValueError("a-invariants must have length 2 or 5")
    return vals


def _qstr(x: Fraction):
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


def exact_weierstrass_invariants(ainvs):
    a1, a2, a3, a4, a6 = normalize_ainvs(ainvs)
    b2 = a1 * a1 + 4 * a2
    b4 = 2 * a4 + a1 * a3
    b6 = a3 * a3 + 4 * a6
    b8 = a1 * a1 * a6 + 4 * a2 * a6 - a1 * a3 * a4 + a2 * a3 * a3 - a4 * a4
    c4 = b2 * b2 - 24 * b4
    c6 = -(b2 ** 3) + 36 * b2 * b4 - 216 * b6
    disc = -(b2 * b2 * b8) - 8 * (b4 ** 3) - 27 * (b6 ** 2) + 9 * b2 * b4 * b6
    if disc == 0:
        raise ValueError("singular Weierstrass model")
    j = c4 ** 3 / disc
    return {
        "b2": _qstr(b2), "b4": _qstr(b4), "b6": _qstr(b6), "b8": _qstr(b8),
        "c4": _qstr(c4), "c6": _qstr(c6), "discriminant": _qstr(disc), "j": _qstr(j),
        "discriminant_sign": 1 if disc > 0 else -1,
    }


def point_on_curve(ainvs, xy):
    a1, a2, a3, a4, a6 = normalize_ainvs(ainvs)
    x, y = Q(xy[0]), Q(xy[1])
    return y * y + a1 * x * y + a3 * y == x ** 3 + a2 * x * x + a4 * x + a6


def _bitlen(n):
    return int(abs(int(n))).bit_length()


def _summary_int(values):
    vals = [int(v) for v in values]
    if not vals:
        return {"min": None, "median": None, "max": None}
    return {
        "min": min(vals),
        "median": float(statistics.median(vals)),
        "max": max(vals),
    }


def _gcd_many(values):
    g = 0
    for value in values:
        g = math.gcd(g, abs(int(value)))
    return g


def point_structure(ainvs, points):
    normalized = [(Q(x), Q(y)) for x, y in points]
    verified = [point_on_curve(ainvs, (x, y)) for x, y in normalized]
    x_integral = [x.denominator == 1 for x, _ in normalized]
    y_integral = [y.denominator == 1 for _, y in normalized]
    x_denoms = [x.denominator for x, _ in normalized]
    y_denoms = [y.denominator for _, y in normalized]
    integral_xs = sorted(int(x) for (x, _), flag in zip(normalized, x_integral) if flag)
    gaps = [b - a for a, b in zip(integral_xs, integral_xs[1:]) if b != a]
    small_support = {
        str(p): sum(1 for d in x_denoms if d % p == 0)
        for p in SMALL_PRIMES if any(d % p == 0 for d in x_denoms)
    }
    residues = {}
    if integral_xs:
        for p in (2, 3, 5, 7, 11, 13):
            residues[str(p)] = len({x % p for x in integral_xs})
    n = len(points)
    return {
        "point_count": n,
        "exactly_verified_count": sum(bool(v) for v in verified),
        "all_points_exactly_verified": bool(n == sum(bool(v) for v in verified)),
        "integral_x_count": sum(x_integral),
        "integral_y_count": sum(y_integral),
        "integral_xy_count": sum(a and b for a, b in zip(x_integral, y_integral)),
        "integral_x_fraction": (sum(x_integral) / n) if n else None,
        "unique_x_denominators": len(set(x_denoms)) if n else 0,
        "x_denominator_bits": _summary_int(_bitlen(d) for d in x_denoms),
        "y_denominator_bits": _summary_int(_bitlen(d) for d in y_denoms),
        "x_numerator_bits": _summary_int(_bitlen(x.numerator) for x, _ in normalized),
        "y_numerator_bits": _summary_int(_bitlen(y.numerator) for _, y in normalized),
        "x_denominator_small_prime_support": small_support,
        "integral_x_gap_gcd": _gcd_many(gaps) if gaps else None,
        "integral_x_residue_occupancy": residues,
        "x_denominators": [str(d) for d in sorted(set(x_denoms))[:64]],
    }


def _parse_json(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def load_subject(db, *, external=None, local_curve_id=None):
    if bool(external) == bool(local_curve_id is not None):
        raise ValueError("choose exactly one of --external or --local")
    if external:
        if ":" not in external:
            raise ValueError("--external must be SOURCE:ID, for example icarm:302")
        source, source_id = external.split(":", 1)
        row = get_external_curve(db, source, source_id)
        if row is None:
            raise ValueError(f"external curve {source}:{source_id} not found; sync the catalog first")
        ainvs = _parse_json(row["a_invariants_json"], [])
        points = _parse_json(row["points_json"], [])
        bad_primes = _parse_json(row["bad_primes_json"], [])
        return {
            "subject": {
                "type": "external", "source": source, "source_id": str(source_id),
                "curve_id": None,
                "label": f"{source.upper()} #{source_id}",
                "rank_lower_bound": int(row["rank_lower_bound"] or 0),
                "proof_provenance": row["proof_method"],
                "source_url": row["source_url"],
                "submitter": row["submitter"],
            },
            "a_invariants": [str(x) for x in ainvs],
            "points": [[str(x), str(y)] for x, y in points],
            "catalog": {
                "conductor": row["conductor"], "discriminant": row["discriminant"],
                "bad_primes": [str(x) for x in bad_primes], "regulator": row["regulator"],
                "naive_height": row["naive_height"], "faltings_height": row["faltings_height"],
                "commentary": row["commentary"],
            },
            "external_row": row,
        }
    row = get_curve(db, int(local_curve_id))
    if row is None:
        raise ValueError(f"local curve #{local_curve_id} not found")
    ainvs = _parse_json(row["a_invariants_json"], [])
    if not ainvs:
        raise ValueError(f"local curve #{local_curve_id} has no stored a-invariants")
    points = _parse_json(row["generators_json"], [])
    bad_primes = _parse_json(row["bad_primes_json"], [])
    return {
        "subject": {
            "type": "local", "source": None, "source_id": None,
            "curve_id": int(local_curve_id), "label": f"Local curve #{local_curve_id}",
            "rank_lower_bound": int(proven_lower(row)), "proof_provenance": "Rank Hunter stored rigorous lower bound",
            "family": row["family"], "parameter": row["parameter"],
        },
        "a_invariants": [str(x) for x in ainvs],
        "points": [[str(x), str(y)] for x, y in points],
        "catalog": {
            "conductor": row["conductor"], "discriminant": row["discriminant"],
            "bad_primes": [str(x) for x in bad_primes], "regulator": row["regulator"],
        },
        "external_row": None,
    }


def parse_bounds(text):
    vals = sorted(set(int(x.strip()) for x in str(text).split(",") if x.strip()))
    if not vals or min(vals) <= 3:
        raise ValueError("prime bounds must be comma-separated integers > 3")
    if max(vals) > 5000:
        raise ValueError("reverse-engineering prime bounds are capped at 5000")
    return vals


def score_nagao(ainvs, bounds):
    from rank42.catalog_score import score_fixed_curve
    started = time.monotonic()
    scores = score_fixed_curve(ainvs, bounds)
    runtime = time.monotonic() - started
    return {
        str(B): {
            "score": float(scores[B]["score"]),
            "primes_used": int(scores[B]["primes_used"]),
        }
        for B in bounds
    }, runtime


def score_percentiles(db, scores):
    result = {}
    for B_text, info in scores.items():
        B = int(B_text)
        rows = db.execute(
            """
            SELECT s.score FROM external_curve_scores s
            JOIN external_curves c ON c.source=s.source AND c.source_id=s.source_id
            WHERE c.active=1 AND s.prime_bound=?
            """,
            (B,),
        ).fetchall()
        vals = [float(r["score"]) for r in rows]
        if not vals:
            result[B_text] = {"population": 0, "percentile": None}
            continue
        score = float(info["score"])
        leq = sum(v <= score for v in vals)
        result[B_text] = {"population": len(vals), "percentile": 100.0 * leq / len(vals)}
    return result


def run_height_screen(ainvs, points, *, precision=192, timeout=60):
    if not points:
        return {"status": "not_run", "reason": "no stored witness points"}
    payload = {"a_invariants": ainvs, "points": points, "precision": int(precision)}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "rank42.height_worker"],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=float(timeout), check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout", "timeout_seconds": int(timeout),
            "evidence_class": "numerical_screen_only",
        }
    marker_line = None
    for line in (proc.stdout or "").splitlines()[::-1]:
        if line.startswith(HEIGHT_MARKER):
            marker_line = line[len(HEIGHT_MARKER):]
            break
    if marker_line is None:
        return {
            "status": "error", "returncode": int(proc.returncode),
            "error": (proc.stderr or proc.stdout or "height worker produced no result")[-2000:],
            "evidence_class": "numerical_screen_only",
        }
    data = json.loads(marker_line)
    if data.get("status") == "error":
        return {**data, "evidence_class": "numerical_screen_only"}
    gram = data.get("gram") or []
    diag = []
    for i, row in enumerate(gram):
        try:
            diag.append(float(row[i]))
        except Exception:
            pass
    return {
        "status": "complete",
        "precision_bits": int(data.get("precision_bits") or precision),
        "count": int(data.get("count") or 0),
        "determinant": data.get("determinant"),
        "min_eigenvalue": data.get("min_eigenvalue"),
        "positive_definite_screen": bool(data.get("positive_definite_screen")),
        "canonical_height_diagonal": diag,
        "canonical_height_summary": {
            "min": min(diag) if diag else None,
            "median": statistics.median(diag) if diag else None,
            "max": max(diag) if diag else None,
        },
        "gram": gram,
        "timeout_seconds": int(timeout),
        "evidence_class": "numerical_screen_only",
    }


def _bad_prime_features(catalog):
    vals = []
    for x in catalog.get("bad_primes") or []:
        try:
            vals.append(int(str(x)))
        except Exception:
            pass
    vals = sorted(set(vals))
    return {
        "bad_primes": [str(x) for x in vals],
        "bad_prime_count": len(vals),
        "bad_primes_le_100": [x for x in vals if x <= 100],
        "bad_primes_le_100_count": sum(x <= 100 for x in vals),
    }


def _clues(point_info, bad_info, heuristic):
    clues = []
    f = point_info.get("integral_x_fraction")
    if f is not None and f >= 0.75:
        clues.append({"signal": "integral-point-rich", "basis": f"{100*f:.1f}% of stored witness x-coordinates are integral", "status": "structural_observation"})
    n = int(point_info.get("point_count") or 0)
    ud = int(point_info.get("unique_x_denominators") or 0)
    if n >= 8 and ud <= max(3, n // 4):
        clues.append({"signal": "denominator-concentrated", "basis": f"{ud} distinct x-denominators across {n} witnesses", "status": "structural_observation"})
    small_bad = int(bad_info.get("bad_primes_le_100_count") or 0)
    if small_bad >= 8:
        clues.append({"signal": "small-prime-loaded-discriminant", "basis": f"{small_bad} catalog bad primes are <= 100", "status": "structural_observation"})
    for B, pct in (heuristic.get("percentiles") or {}).items():
        p = pct.get("percentile")
        if p is not None and p >= 90:
            clues.append({"signal": f"high-Nagao-B{B}", "basis": f"score is at the {p:.1f} percentile of stored external calibration scores", "status": "heuristic_only"})
    return clues


def _fingerprint_distance(a, b):
    ah = (a.get("heuristic") or {}).get("nagao") or {}
    bh = (b.get("heuristic") or {}).get("nagao") or {}
    common = sorted(set(ah).intersection(bh), key=int)
    score_terms = []
    for B in common:
        av, bv = float(ah[B]["score"]), float(bh[B]["score"])
        scale = max(1.0, abs(av), abs(bv))
        score_terms.append(abs(av - bv) / scale)
    ap = (a.get("exact") or {}).get("point_structure") or {}
    bp = (b.get("exact") or {}).get("point_structure") or {}
    af = ap.get("integral_x_fraction")
    bf = bp.get("integral_x_fraction")
    point_term = abs(float(af) - float(bf)) if af is not None and bf is not None else 0.5
    abad = set((a.get("exact") or {}).get("bad_prime_features", {}).get("bad_primes_le_100") or [])
    bbad = set((b.get("exact") or {}).get("bad_prime_features", {}).get("bad_primes_le_100") or [])
    union = abad | bbad
    bad_term = 1.0 - (len(abad & bbad) / len(union)) if union else 0.0
    terms = score_terms + [point_term, bad_term]
    return sum(terms) / len(terms), {
        "common_nagao_bounds": common,
        "integral_x_fraction_gap": point_term,
        "small_bad_prime_jaccard": 1.0 - bad_term,
    }


def nearest_stored_reports(db, report, *, limit=5):
    out = []
    for row in list_reports(db, limit=500):
        other = report_payload(row)
        if not other or other.get("report_key") == report.get("report_key"):
            continue
        distance, detail = _fingerprint_distance(report, other)
        out.append({
            "report_id": int(row["id"]), "label": row["label"],
            "rank_lower_bound": int(row["rank_lower_bound"] or 0),
            "distance": float(distance), **detail,
        })
    out.sort(key=lambda x: (x["distance"], -x["rank_lower_bound"], x["label"]))
    return out[: int(limit)]


def build_report(db, subject_data, *, bounds, heights=False, precision=192, height_timeout=60):
    subject = subject_data["subject"]
    ainvs = subject_data["a_invariants"]
    points = subject_data["points"]
    catalog = subject_data["catalog"]

    inv = exact_weierstrass_invariants(ainvs)
    pstruct = point_structure(ainvs, points)
    bad_info = _bad_prime_features(catalog)
    exact = {
        "weierstrass_invariants": inv,
        "point_structure": pstruct,
        "bad_prime_features": bad_info,
        "catalog_metadata": catalog,
        "evidence_class": "exact_or_source_metadata",
    }

    nagao, score_runtime = score_nagao(ainvs, bounds)
    heuristic = {
        "nagao": nagao,
        "score_runtime_seconds": score_runtime,
        "percentiles": score_percentiles(db, nagao),
        "evidence_class": "heuristic_only",
        "warning": "Nagao/Mestre-style scores rank candidates; they do not prove rank.",
    }

    numerical = (
        run_height_screen(ainvs, points, precision=precision, timeout=height_timeout)
        if heights else
        {"status": "not_run", "evidence_class": "numerical_screen_only", "reason": "height screen disabled"}
    )

    key_payload = {
        "subject": subject, "ainvs": ainvs, "points": points, "bounds": list(bounds),
        "heights": bool(heights), "precision": int(precision) if heights else None,
    }
    report_key = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()
    report = {
        "schema_version": "rank-hunter-reverse-v1",
        "report_key": report_key,
        "subject": subject,
        "a_invariants": ainvs,
        "parameters": {
            "prime_bounds": list(bounds), "height_screen": bool(heights),
            "height_precision_bits": int(precision), "height_timeout_seconds": int(height_timeout),
        },
        "exact": exact,
        "heuristic": heuristic,
        "numerical": numerical,
        "comparison": {},
        "claim_ledger": [
            {"claim": f"source/stored rank >= {int(subject.get('rank_lower_bound') or 0)}", "class": "rigorous_provenance", "note": subject.get("proof_provenance")},
            {"claim": f"{pstruct['exactly_verified_count']}/{pstruct['point_count']} stored witness points verified on this model", "class": "exact"},
            {"claim": "Nagao finite-field fingerprint", "class": "heuristic_only"},
            {"claim": "canonical-height Gram fingerprint", "class": "numerical_screen_only" if heights else "not_run"},
        ],
        "status": "complete",
    }
    report["clues"] = _clues(pstruct, bad_info, heuristic)
    report["comparison"]["nearest_stored_fingerprints"] = nearest_stored_reports(db, report)
    return report


def parse_args():
    ap = argparse.ArgumentParser(description="Reverse engineer an exceptional elliptic curve")
    ap.add_argument("--db", default="rank42.db")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--external", help="external catalog curve as SOURCE:ID, e.g. icarm:302")
    g.add_argument("--local", type=int, help="local curves.id")
    ap.add_argument("--bounds", default="100,200,500", help="Nagao prime bounds")
    ap.add_argument("--heights", action="store_true", help="run numerical Neron--Tate Gram screen")
    ap.add_argument("--precision", type=int, default=192)
    ap.add_argument("--height-timeout", type=int, default=60)
    ap.add_argument("--json", action="store_true", help="print full JSON report")
    return ap.parse_args()


def main():
    args = parse_args()
    bounds = parse_bounds(args.bounds)
    db = connect(args.db)
    try:
        data = load_subject(db, external=args.external, local_curve_id=args.local)
        print("RANK HUNTER CURVE REVERSE ENGINEERING")
        print("=" * 72)
        print("subject              =", data["subject"]["label"])
        print("rigorous lower       =", data["subject"].get("rank_lower_bound", 0))
        print("stored witnesses     =", len(data["points"]))
        print("Nagao bounds         =", ",".join(map(str, bounds)))
        print("height screen        =", "enabled (numerical only)" if args.heights else "disabled")
        report = build_report(
            db, data, bounds=bounds, heights=args.heights,
            precision=args.precision, height_timeout=args.height_timeout,
        )
        stored = store_report(db, report)
        ps = report["exact"]["point_structure"]
        print("exact points verified=", f"{ps['exactly_verified_count']}/{ps['point_count']}")
        print("integral witness x   =", f"{ps['integral_x_count']}/{ps['point_count']}")
        for B in bounds:
            info = report["heuristic"]["nagao"][str(B)]
            pct = report["heuristic"]["percentiles"][str(B)].get("percentile")
            pct_text = f" percentile={pct:.1f}" if pct is not None else ""
            print(f"Nagao B={B:<4}       = {info['score']:.6f} ({info['primes_used']} primes){pct_text}")
        if args.heights:
            h = report["numerical"]
            print("height status        =", h.get("status"))
            print("height PD screen     =", h.get("positive_definite_screen"))
        print("report id            =", int(stored["id"]))
        print("NOTE: exact/source, heuristic, and numerical evidence remain separate; this workflow never promotes rank.")
        if args.json:
            print("RANK42_REVERSE_REPORT=" + json.dumps(report, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
