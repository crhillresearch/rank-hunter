"""Fixed-curve extension search for external high-rank witness sets.

This is intentionally separate from family discovery.  It takes a known curve
and its explicit witness subgroup from an external catalog, searches an exact
short Weierstrass representation with ratpoints, maps every hit back exactly,
and attempts a rigorous exact lower-bound certificate after appending a new
point.

A point is never called rank growth merely because it is not in the literal
witness list.  Only ``certified_extension`` means the exact certificate proved
that the augmented witness set is independent modulo torsion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time

from sage.all import QQ, EllipticCurve

from rank42.catalog import get_external_curve
from rank42.db import connect, now
from rank42.exact_lb import (
    ExactCertificateFailure,
    ExactCertificateTimeout,
    run_exact_certificate,
)
from rank42.fixed_curve import short_model_from_ainvs
from rank42.ratpoints import (
    RatpointsFailure,
    RatpointsNotFound,
    RatpointsTimeout,
    probe_version,
    run_ratpoints,
)


def parse_stages(text):
    vals = []
    for token in str(text).split(","):
        token = token.strip()
        if not token:
            continue
        value = int(token)
        if value <= 0:
            raise ValueError("height stages must be positive")
        vals.append(value)
    if not vals:
        raise ValueError("at least one height stage is required")
    return sorted(set(vals))


def _point_key(P):
    if P.is_zero():
        return ("0",)
    return (str(QQ(P[0])), str(QQ(P[1])))


def _search_key(source, source_id, stages, timeout, certificate_timeout, certificate_limit, curve_fingerprint, ratpoints_executable):
    raw = json.dumps(
        {
            "source": str(source),
            "source_id": str(source_id),
            "stages": [int(x) for x in stages],
            "timeout": int(timeout),
            "certificate_timeout": int(certificate_timeout),
            "certificate_limit": int(certificate_limit),
            "curve_fingerprint": str(curve_fingerprint),
            "ratpoints_executable": str(ratpoints_executable),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _create_search(
    db, *, source, source_id, stages, timeout, certificate_timeout,
    certificate_limit, curve_fingerprint, ratpoints_executable, force=False
):
    key = _search_key(
        source, source_id, stages, timeout, certificate_timeout, certificate_limit,
        curve_fingerprint, ratpoints_executable
    )
    row = db.execute(
        "SELECT * FROM external_curve_searches WHERE search_key=?", (key,)
    ).fetchone()
    if row is not None and not force:
        return row, False
    if row is not None and force:
        db.execute("DELETE FROM external_curve_searches WHERE id=?", (row["id"],))
        db.commit()
    ts = now()
    cur = db.execute(
        """
        INSERT INTO external_curve_searches(
            search_key,source,source_id,stages_json,timeout_seconds,status,
            points_found,metadata_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,'new',0,?,?,?)
        """,
        (
            key,
            str(source),
            str(source_id),
            json.dumps([int(x) for x in stages]),
            int(timeout),
            json.dumps(
                {
                    "certificate_timeout": int(certificate_timeout),
                    "certificate_limit": int(certificate_limit),
                    "curve_fingerprint": str(curve_fingerprint),
                },
                sort_keys=True,
            ),
            ts,
            ts,
        ),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM external_curve_searches WHERE id=?", (cur.lastrowid,)
    ).fetchone(), True


def _finish(db, search_id, *, status, points_found, certified_rank_lower=None, error=None, metadata=None):
    ts = now()
    db.execute(
        """
        UPDATE external_curve_searches
        SET status=?, points_found=?, certified_rank_lower=?, error=?,
            metadata_json=COALESCE(?,metadata_json), finished_at=?, updated_at=?
        WHERE id=?
        """,
        (
            status,
            int(points_found),
            certified_rank_lower,
            error,
            json.dumps(metadata, sort_keys=True) if metadata is not None else None,
            ts,
            ts,
            int(search_id),
        ),
    )
    db.commit()




def _search_metadata(*, stage_log, model, curve_fingerprint, certificate_attempts, certificate_limit, info):
    return {
        "stages": stage_log,
        "short_A": str(model.A),
        "short_B": str(model.B),
        "curve_fingerprint": str(curve_fingerprint),
        "certificate_attempts": int(certificate_attempts),
        "certificate_limit": int(certificate_limit),
        "ratpoints_version": info.get("version"),
    }

def _store_extra(
    db,
    *,
    search_id,
    P,
    short_xy,
    relation_status,
    certified_rank_lower=None,
    certificate=None,
    metadata=None,
):
    db.execute(
        """
        INSERT OR IGNORE INTO external_extra_points(
            search_id,x,y,short_x,short_y,exact_verified,relation_status,
            certified_rank_lower,certificate_json,metadata_json,created_at
        ) VALUES(?,?,?,?,?,1,?,?,?,?,?)
        """,
        (
            int(search_id),
            str(P[0]),
            str(P[1]),
            str(short_xy[0]),
            str(short_xy[1]),
            relation_status,
            certified_rank_lower,
            json.dumps(certificate, sort_keys=True) if certificate is not None else None,
            json.dumps(metadata or {}, sort_keys=True),
            now(),
        ),
    )
    db.commit()


def search_external_curve(
    db,
    *,
    source,
    source_id,
    stages,
    timeout=20,
    certificate_timeout=120,
    certificate_limit=8,
    ratpoints=None,
    force=False,
):
    row = get_external_curve(db, source, source_id)
    if row is None:
        raise ValueError(f"external curve {source}:{source_id} not found; sync the catalog first")
    stages = parse_stages(stages) if isinstance(stages, str) else sorted(set(map(int, stages)))
    certificate_limit = int(certificate_limit)
    if certificate_limit < 0:
        raise ValueError("certificate_limit must be nonnegative")
    info = probe_version(ratpoints)
    curve_fingerprint = hashlib.sha256(
        (
            str(row["curve_key"] or "") + "\0" + row["a_invariants_json"] + "\0"
            + row["points_json"] + "\0" + str(row["rank_lower_bound"])
        ).encode()
    ).hexdigest()
    search, created = _create_search(
        db,
        source=source,
        source_id=source_id,
        stages=stages,
        timeout=timeout,
        certificate_timeout=certificate_timeout,
        certificate_limit=certificate_limit,
        curve_fingerprint=curve_fingerprint,
        ratpoints_executable=info["executable"],
        force=force,
    )
    if not created and search["status"] in {"done", "certified_extension", "timeout"}:
        return {"search_id": int(search["id"]), "status": search["status"], "reused": True}

    ainvs = json.loads(row["a_invariants_json"])
    witness_xy = json.loads(row["points_json"])
    E = EllipticCurve(QQ, [QQ(str(x)) for x in ainvs])
    if E.discriminant() == 0:
        raise ValueError("external curve is singular")
    witness = [E(QQ(str(x)), QQ(str(y))) for x, y in witness_xy]
    witness_keys = {_point_key(P) for P in witness}
    witness_keys.update(_point_key(-P) for P in witness)

    model = short_model_from_ainvs(ainvs)
    known_short_x = {str(model.forward(P[0], P[1])[0]) for P in witness}

    db.execute(
        """
        UPDATE external_curve_searches
        SET status='running', ratpoints_executable=?, started_at=?, updated_at=?
        WHERE id=?
        """,
        (info["executable"], now(), now(), search["id"]),
    )
    db.commit()

    seen_short = set()
    candidate_count = 0
    certificate_attempts = 0
    certified_rank = None
    stage_log = []
    try:
        for H in stages:
            started = time.monotonic()
            try:
                result = run_ratpoints(
                    model.polynomial_coefficients,
                    H,
                    executable=info["executable"],
                    timeout=timeout,
                )
            except RatpointsTimeout as exc:
                stage_log.append({"height": H, "status": "timeout", "runtime": time.monotonic() - started})
                _finish(
                    db,
                    search["id"],
                    status="timeout",
                    points_found=candidate_count,
                    certified_rank_lower=certified_rank,
                    error=str(exc),
                    metadata=_search_metadata(
                        stage_log=stage_log, model=model, curve_fingerprint=curve_fingerprint,
                        certificate_attempts=certificate_attempts, certificate_limit=certificate_limit, info=info,
                    ),
                )
                return {"search_id": int(search["id"]), "status": "timeout", "candidates": candidate_count}
            stage_new = 0
            for RP in result["points"]:
                skey = (str(RP.x), str(RP.y))
                if skey in seen_short:
                    continue
                seen_short.add(skey)
                if str(RP.x) in known_short_x:
                    continue
                if not model.verify_short(RP.x, RP.y):
                    raise RuntimeError("ratpoints hit failed short-model exact verification")
                x, y = model.inverse(RP.x, RP.y)
                try:
                    P = E(QQ(str(x)), QQ(str(y)))
                except Exception as exc:
                    raise RuntimeError(f"inverse-mapped point is not on external curve: {exc}") from exc
                if _point_key(P) in witness_keys:
                    continue
                stage_new += 1
                candidate_count += 1

                augmented = witness_xy + [[str(P[0]), str(P[1])]]
                cert = None
                relation_status = "uncertified_candidate"
                rank_lb = None
                if certificate_attempts >= certificate_limit:
                    cert = {
                        "status": "not_attempted_budget",
                        "certificate_limit": certificate_limit,
                    }
                else:
                    certificate_attempts += 1
                    try:
                        cert = run_exact_certificate(
                            ainvs,
                            augmented,
                            timeout=certificate_timeout,
                        )
                        if cert.get("independent"):
                            rank_lb = int(cert["rank_lower_bound"])
                            certified_rank = max(certified_rank or 0, rank_lb)
                            if rank_lb > int(row["rank_lower_bound"]):
                                relation_status = "certified_extension"
                            else:
                                relation_status = "certified_augmented_set"
                        elif cert.get("status") == "dependent":
                            # This proves the augmented working set is dependent.
                            # We deliberately do not claim a specific subgroup
                            # relation for P here: halving replacements inside the
                            # certificate can change the meaning of returned row
                            # indices even though the final dependence verdict is exact.
                            relation_status = "augmented_set_dependent"
                    except ExactCertificateTimeout as exc:
                        cert = {"status": "timeout", "error": str(exc)}
                    except ExactCertificateFailure as exc:
                        cert = {"status": "error", "error": str(exc)}

                _store_extra(
                    db,
                    search_id=search["id"],
                    P=P,
                    short_xy=(RP.x, RP.y),
                    relation_status=relation_status,
                    certified_rank_lower=rank_lb,
                    certificate=cert,
                    metadata={
                        "height_stage": H,
                        "source_rank_lower": int(row["rank_lower_bound"]),
                        "certificate_attempt": certificate_attempts if cert and cert.get("status") != "not_attempted_budget" else None,
                    },
                )
                if relation_status == "certified_extension":
                    stage_log.append(
                        {"height": H, "status": "certified_extension", "runtime": result["runtime"], "new_candidates": stage_new}
                    )
                    _finish(
                        db,
                        search["id"],
                        status="certified_extension",
                        points_found=candidate_count,
                        certified_rank_lower=certified_rank,
                        metadata=_search_metadata(
                            stage_log=stage_log, model=model, curve_fingerprint=curve_fingerprint,
                            certificate_attempts=certificate_attempts, certificate_limit=certificate_limit, info=info,
                        ),
                    )
                    return {
                        "search_id": int(search["id"]),
                        "status": "certified_extension",
                        "certified_rank_lower": certified_rank,
                        "point": [str(P[0]), str(P[1])],
                    }
            stage_log.append(
                {"height": H, "status": "done", "runtime": result["runtime"], "new_candidates": stage_new}
            )

        _finish(
            db,
            search["id"],
            status="done",
            points_found=candidate_count,
            certified_rank_lower=certified_rank,
            metadata=_search_metadata(
                stage_log=stage_log, model=model, curve_fingerprint=curve_fingerprint,
                certificate_attempts=certificate_attempts, certificate_limit=certificate_limit, info=info,
            ),
        )
        return {
            "search_id": int(search["id"]),
            "status": "done",
            "candidates": candidate_count,
            "certified_rank_lower": certified_rank,
        }
    except (RatpointsFailure, RatpointsNotFound) as exc:
        _finish(
            db,
            search["id"],
            status="error",
            points_found=candidate_count,
            certified_rank_lower=certified_rank,
            error=str(exc),
            metadata=_search_metadata(
                stage_log=stage_log, model=model, curve_fingerprint=curve_fingerprint,
                certificate_attempts=certificate_attempts, certificate_limit=certificate_limit, info=info,
            ),
        )
        raise
    except BaseException as exc:
        _finish(
            db,
            search["id"],
            status="error",
            points_found=candidate_count,
            certified_rank_lower=certified_rank,
            error=repr(exc),
            metadata=_search_metadata(
                stage_log=stage_log, model=model, curve_fingerprint=curve_fingerprint,
                certificate_attempts=certificate_attempts, certificate_limit=certificate_limit, info=info,
            ),
        )
        raise


def parse_args():
    ap = argparse.ArgumentParser(description="Extend an external high-rank curve with ratpoints")
    ap.add_argument("--db", default="rank42.db")
    ap.add_argument("--source", default="icarm")
    ap.add_argument("--id", required=True, dest="source_id")
    ap.add_argument("--stages", default="1000,10000,100000")
    ap.add_argument("--timeout", type=int, default=20, help="hard timeout per ratpoints height stage")
    ap.add_argument("--certificate-timeout", type=int, default=120)
    ap.add_argument(
        "--certificate-limit", type=int, default=8,
        help="maximum augmented witness sets to send to the exact certifier (0 = store candidates only)",
    )
    ap.add_argument("--ratpoints")
    ap.add_argument("--force", action="store_true")
    return ap.parse_args()


def main():
    args = parse_args()
    db = connect(args.db)
    try:
        result = search_external_curve(
            db,
            source=args.source,
            source_id=args.source_id,
            stages=args.stages,
            timeout=args.timeout,
            certificate_timeout=args.certificate_timeout,
            certificate_limit=args.certificate_limit,
            ratpoints=args.ratpoints,
            force=args.force,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        if result.get("status") == "certified_extension":
            print(
                f"\n*** RIGOROUS EXTERNAL-CURVE EXTENSION: rank >= {result['certified_rank_lower']} ***"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
