"""Sage-backed arithmetic metadata enrichment for stored curves.

This helper deliberately leaves the stored Weierstrass model and witness
coordinates unchanged.  It computes global-minimal-model invariants only as
metadata, so existing exact point provenance remains valid on the original
stored model.
"""

from __future__ import annotations

import argparse
import json

from sage.all import EllipticCurve, QQ, ZZ, prime_divisors

from rank42.curve_size_metrics import compute_curve_size_metrics, store_curve_size_metrics
from rank42.db import connect, get_curve, log_event, update_curve


def _minimal_model_and_core(row):
    try:
        ainvs_raw = json.loads(row["a_invariants_json"] or "[]")
    except Exception as exc:
        raise ValueError("curve has malformed a-invariants metadata") from exc
    if len(ainvs_raw) != 5:
        raise ValueError("curve has no five-term Weierstrass model stored")

    ainvs = [QQ(str(x)) for x in ainvs_raw]
    E = EllipticCurve(QQ, ainvs)
    if E.discriminant() == 0:
        raise ValueError("stored curve model is singular")

    Em = E.global_minimal_model()
    disc = ZZ(Em.discriminant())
    bad = [int(p) for p in prime_divisors(abs(disc))]
    core = {
        "discriminant": str(disc),
        "bad_primes_json": json.dumps(bad),
    }
    try:
        core["root_number"] = int(Em.root_number())
    except Exception:
        pass
    return Em, core


def compute_core_metadata(row) -> dict:
    """Compute exact minimal discriminant/bad-prime/root-number metadata only."""
    _Em, core = _minimal_model_and_core(row)
    return core


def compute_metadata(row) -> dict:
    """Compute the complete ICARM-preparation arithmetic payload."""
    Em, out = _minimal_model_and_core(row)
    out = {
        **out,
        **compute_curve_size_metrics(Em),
    }
    try:
        out["conductor"] = str(Em.conductor())
    except Exception:
        pass
    return out


def enrich_curve_core(db, curve_id: int) -> dict:
    """Persist only cheap/core minimal-model arithmetic metadata."""
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    metadata = compute_core_metadata(row)
    fields = {}
    if row["discriminant"] in (None, "") and metadata.get("discriminant") is not None:
        fields["discriminant"] = metadata["discriminant"]
    if row["bad_primes_json"] in (None, "") and metadata.get("bad_primes_json") is not None:
        fields["bad_primes_json"] = metadata["bad_primes_json"]
    if row["root_number"] is None and metadata.get("root_number") is not None:
        fields["root_number"] = metadata["root_number"]
    if fields:
        update_curve(db, int(curve_id), **fields)
    bad = json.loads(metadata["bad_primes_json"])
    log_event(
        db,
        int(curve_id),
        "info",
        "Computed bounded core arithmetic metadata; bad primes="
        + ",".join(str(p) for p in bad),
    )
    return {
        "curve_id": int(curve_id),
        "bad_primes": bad,
        "discriminant": metadata.get("discriminant"),
        "root_number": metadata.get("root_number"),
    }


def enrich_curve(db, curve_id: int) -> dict:
    row = get_curve(db, int(curve_id))
    if row is None:
        raise ValueError(f"curve #{curve_id} not found")
    metadata = compute_metadata(row)
    # update_curve deliberately owns only the historical curve-row columns; the
    # two archimedean size metrics live in their additive sidecar table.
    update_curve(db, int(curve_id), **metadata)
    store_curve_size_metrics(db, int(curve_id), metadata)
    bad = json.loads(metadata["bad_primes_json"])
    log_event(
        db,
        int(curve_id),
        "info",
        "Computed global-minimal arithmetic metadata and size metrics; bad primes="
        + ",".join(str(p) for p in bad),
    )
    return {
        "curve_id": int(curve_id),
        "bad_primes": bad,
        "discriminant": metadata.get("discriminant"),
        "conductor": metadata.get("conductor"),
        "root_number": metadata.get("root_number"),
        "naive_height": metadata.get("naive_height"),
        "faltings_height": metadata.get("faltings_height"),
    }


def parse_args():
    ap = argparse.ArgumentParser(description="Compute global-minimal arithmetic metadata for one Rank Hunter curve")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument(
        "--core-only",
        action="store_true",
        help="compute/persist discriminant, bad primes, and root number only",
    )
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        if args.core_only:
            result = enrich_curve_core(db, int(args.id))
            marker = "RANK42_CURVE_CORE_RESULT="
        else:
            result = enrich_curve(db, int(args.id))
            marker = "RANK42_CURVE_METADATA_RESULT="
        print(marker + json.dumps(result, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
