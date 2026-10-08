import argparse
import json
from pathlib import Path

from sage.all import QQ, EllipticCurve

from rank42.mathutil import curve_summary, height_screen, try_saturation
from rank42.exact_lb import ExactCertificateFailure, ExactCertificateTimeout, run_exact_certificate


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate_json")
    ap.add_argument("--precision", type=int, default=256)
    ap.add_argument("--saturate-to", type=int, default=50)
    ap.add_argument(
        "--fast", action="store_true",
        help="exact point membership only; skip conductor/factorization, heights and saturation",
    )
    ap.add_argument("--skip-summary", action="store_true",
                    help="skip minimal-model/discriminant/conductor/root-number summary")
    ap.add_argument("--skip-height", action="store_true",
                    help="skip the Neron-Tate height matrix")
    ap.add_argument(
        "--exact-certificate", action="store_true",
        help="attempt a rigorous Cremona/Brumer quadratic-character lower-bound certificate",
    )
    ap.add_argument("--certificate-timeout", type=int, default=120)
    ap.add_argument("--max-halvings", type=int, default=40)
    return ap.parse_args()


def parse_q(x):
    return QQ(str(x))


def main():
    args = parse_args()
    data = json.loads(Path(args.candidate_json).read_text())

    ainvs = [parse_q(x) for x in data["a_invariants"]]
    if len(ainvs) != 5:
        raise SystemExit("a_invariants must have five entries")

    E = EllipticCurve(QQ, ainvs)
    if E.discriminant() == 0:
        raise SystemExit("singular curve")

    raw_points = data.get("points", [])
    print(f"[exact] verifying {len(raw_points)} point(s) on the input curve...")
    points = []
    for i, xy in enumerate(raw_points, 1):
        if len(xy) != 2:
            raise SystemExit(f"point {i} must be [x,y]")
        x, y = map(parse_q, xy)
        try:
            P = E(x, y)
        except Exception as exc:
            raise SystemExit(f"point {i} is not on E: {xy}: {exc}")
        points.append(P)
        if i % 10 == 0 or i == len(raw_points):
            print(f"[exact] {i}/{len(raw_points)} verified")

    if args.fast:
        result = {
            "input_a_invariants": [str(x) for x in E.a_invariants()],
            "input_points_verified_exactly": len(points),
            "fast_mode": True,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    summary = None
    if not args.skip_summary:
        print("[summary] computing minimal model / discriminant factors / conductor / root number...")
        summary = curve_summary(E)

    screen = None
    if not args.skip_height:
        print(f"[height] computing {len(points)}x{len(points)} height matrix at {args.precision} bits...")
        screen = height_screen(E, points, precision=args.precision)

    exact_certificate = {"attempted": False}
    if args.exact_certificate and points:
        print(
            f"[exact-lb] attempting exact Cremona/Brumer certificate for {len(points)} point(s) "
            f"with timeout {args.certificate_timeout}s..."
        )
        try:
            exact_certificate = run_exact_certificate(
                [str(x) for x in E.a_invariants()],
                [[str(P[0]), str(P[1])] for P in points],
                timeout=args.certificate_timeout,
                max_halvings=args.max_halvings,
            )
            exact_certificate["attempted"] = True
        except ExactCertificateTimeout as exc:
            exact_certificate = {"attempted": True, "status": "timeout", "error": str(exc)}
        except ExactCertificateFailure as exc:
            exact_certificate = {"attempted": True, "status": "error", "error": str(exc)}

    saturation = {"attempted": False}
    if int(args.saturate_to) > 0 and points:
        print(f"[saturation] testing through p <= {args.saturate_to}...")
        saturation = try_saturation(E, points, max_prime=args.saturate_to)

    result = {
        "input_a_invariants": [str(x) for x in E.a_invariants()],
        "input_points_verified_exactly": len(points),
        "minimal_model": summary,
        "height_independence_screen": screen,
        "exact_lower_bound_certificate": exact_certificate,
        "saturation": saturation,
    }

    print(json.dumps(result, indent=2, sort_keys=True))

    if exact_certificate.get("independent"):
        print(
            f"\n*** RIGOROUS LOWER BOUND: rank >= {exact_certificate['rank_lower_bound']} "
            "(exact quadratic-character certificate) ***"
        )
    elif (
        len(points) >= 42
        and screen is not None
        and screen["positive_definite_screen"]
    ):
        print("\n*** RANK >= 42 WITNESS SET PRESENT (numerical screen only; not yet rigorous) ***")


if __name__ == "__main__":
    main()
