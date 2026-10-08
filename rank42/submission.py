"""Submission packet helpers for rigorously proven local curves."""

from __future__ import annotations

import json


def certified_witness_lower(row) -> int:
    """Lower bound backed by the stored explicit rigorous witness basis.

    ``generic_lower`` can be a theorem/family bound and ``exact_rank`` can be
    an upper/lower rank proof without certifying that the *stored* points form
    the witness basis.  ICARM therefore receives only the explicit basis backed
    by Rank Hunter's exact independence lower-bound field ``descent_lower``.
    """
    try:
        value = row["descent_lower"]
    except (KeyError, IndexError):
        value = None
    return int(value) if value is not None else 0


def stored_bad_primes(row) -> list[int] | None:
    """Return stored bad primes, or ``None`` when metadata is genuinely absent.

    v0.7.13 treated SQL ``NULL`` as an empty list, which allowed ICARM
    submissions to silently omit the documented ``primes`` field.  Missing
    arithmetic metadata is now distinct from an explicitly stored list.
    """
    try:
        raw = row["bad_primes_json"]
    except (KeyError, IndexError):
        raw = None
    if raw is None or not str(raw).strip():
        return None
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError("curve has malformed bad-prime metadata") from exc
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("curve bad-prime metadata is not a list")
    out = []
    for p in value:
        try:
            q = int(p)
        except Exception as exc:
            raise ValueError(f"invalid bad prime {p!r}") from exc
        if q <= 1:
            raise ValueError(f"invalid bad prime {q}")
        out.append(q)
    return out


def icarm_api_body(
    row,
    *,
    commentary: str | None = None,
    require_bad_primes: bool = True,
) -> dict:
    """Return the documented exact JSON body for ICARM ``POST /api/submit``.

    By default the payload is submission-strict: bad-prime metadata must have
    been computed from the global minimal model.  Inventory/preview code may
    set ``require_bad_primes=False`` solely to decide what local action would
    otherwise be available; the actual write path never does so.
    """
    ainvs = json.loads(row["a_invariants_json"] or "[]")
    points = json.loads(row["generators_json"] or "[]")
    bad = stored_bad_primes(row)
    if len(ainvs) != 5:
        raise ValueError("curve has no five-term Weierstrass model stored")
    lower = certified_witness_lower(row)
    if lower <= 0:
        raise ValueError("curve has no stored exact witness-backed rank lower bound")
    if len(points) < lower:
        raise ValueError(
            f"curve has only {len(points)} stored witness points for rigorous lower bound {lower}"
        )
    if require_bad_primes and bad is None:
        raise ValueError(
            "curve is missing bad-prime metadata; compute arithmetic metadata before ICARM submission"
        )
    body = {
        "ainvs": [str(x) for x in ainvs],
        "points": [[str(p[0]), str(p[1])] for p in points[:lower]],
    }
    if bad is not None:
        body["primes"] = [str(p) for p in bad]
    if commentary is not None and str(commentary).strip():
        body["commentary"] = str(commentary).strip()
    return body


def icarm_submission_payload(row) -> dict:
    ainvs = json.loads(row["a_invariants_json"] or "[]")
    points = json.loads(row["generators_json"] or "[]")
    bad = stored_bad_primes(row)
    if len(ainvs) != 5:
        raise ValueError("curve has no five-term Weierstrass model stored")
    lower = certified_witness_lower(row)
    if lower <= 0 or len(points) < lower:
        raise ValueError("curve does not have enough stored rigorous witness points")
    if bad is None:
        raise ValueError("curve is missing bad-prime metadata")
    return {
        "a_invariants": [str(x) for x in ainvs],
        "points": [[str(p[0]), str(p[1])] for p in points[:lower]],
        "bad_primes": [int(p) for p in bad],
        "rank_lower_bound": lower,
        "exact_rank": int(row["exact_rank"]) if row["exact_rank"] is not None else None,
        "rank_hunter": {
            "curve_id": int(row["id"]),
            "family": row["family"],
            "parameter": row["parameter"],
            "status": row["status"],
        },
    }


def icarm_submission_text(row) -> str:
    payload = icarm_submission_payload(row)
    ainvs = ", ".join(payload["a_invariants"])
    pts = "\n".join(f"{x}, {y}" for x, y in payload["points"])
    bad = ", ".join(str(p) for p in payload["bad_primes"])
    lines = [
        "ICARM submission fields", "", f"a-invariants: {ainvs}", "", "points:", pts,
    ]
    if bad:
        lines += ["", f"primes of bad reduction: {bad}"]
    lines += ["", f"Rank Hunter proven lower bound: {payload['rank_lower_bound']}"]
    if payload["exact_rank"] is not None:
        lines.append(f"Rank Hunter exact rank: {payload['exact_rank']}")
    lines.append("Catalog novelty is a separate question from the rank certificate.")
    return "\n".join(lines) + "\n"
