"""Heuristic attack planning for a curve with a large certified MW subgroup.

The planner only orders exact point and covering searches.  It never changes a
rank bound; the Growth Loop's existing point map and certificate do that.
"""
from __future__ import annotations

import hashlib
import json
import math


def _coordinates(P):
    return [str(P[0]), str(P[1])]


def _fingerprint(basis):
    payload = json.dumps([_coordinates(P) for P in basis], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _x_height_log10(P):
    x = P[0]
    size = max(abs(int(x.numerator())), int(x.denominator()), 1)
    return (size.bit_length() - 1) * math.log10(2)


def _matching_gram(db, curve_id, E, basis):
    """Use stored numerical heights only for ordering, with exact basis matching."""
    rows = db.execute(
        """SELECT id,basis_json,gram_json,metadata_json,precision_bits FROM mw_lattices
           WHERE curve_id=? AND basis_count=? AND positive_definite_screen=1
           ORDER BY precision_bits DESC,id DESC LIMIT 12""",
        (int(curve_id), len(basis)),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            representations = [(json.loads(row["basis_json"]), json.loads(row["gram_json"]))]
            # Height-Lattice Reduction stores an LLL basis as its primary
            # representation.  Its original certified basis and pairing are
            # retained too; the growth loop often still uses that basis.
            if metadata.get("input_basis") and metadata.get("input_gram"):
                representations.append((metadata["input_basis"], metadata["input_gram"]))
            for stored, raw in representations:
                if len(stored) != len(basis) or len(raw) != len(basis):
                    continue
                if any(len(item) != len(basis) for item in raw):
                    continue
                stored_points = [E(xy[0], xy[1]) for xy in stored]
                matched = []
                used = set()
                for P in basis:
                    match = next((
                        (index, 1 if Q == P else -1)
                        for index, Q in enumerate(stored_points)
                        if index not in used and (Q == P or Q == -P)
                    ), None)
                    if match is None:
                        break
                    used.add(match[0])
                    matched.append(match)
                if len(matched) != len(basis):
                    continue
                gram = [
                    [float(raw[matched[i][0]][matched[j][0]]) * matched[i][1] * matched[j][1]
                     for j in range(len(basis))]
                    for i in range(len(basis))
                ]
                return gram, int(row["id"]), int(row["precision_bits"])
        except (ArithmeticError, ValueError, TypeError, KeyError, IndexError):
            continue
    return None, None, None


def _coverage(db, curve_id):
    rows = db.execute(
        """SELECT metadata_json,status,height_bound,point_count
           FROM quartic_searches WHERE curve_id=?
           ORDER BY id DESC LIMIT 12000""",
        (int(curve_id),),
    ).fetchall()
    coverage = {}
    for row in rows:
        try:
            key = str(json.loads(row["metadata_json"] or "{}").get("anchor_key") or "")
        except (TypeError, ValueError):
            continue
        if not key:
            continue
        state = coverage.setdefault(key, {"completed": 0, "max_completed_height": 0,
                                           "hits": 0, "timeouts": 0})
        status = str(row["status"])
        if status == "done":
            state["completed"] += 1
            state["max_completed_height"] = max(state["max_completed_height"],
                                                   int(row["height_bound"] or 0))
            state["hits"] += int(row["point_count"] or 0)
        elif status in {"timeout", "error"}:
            state["timeouts"] += 1
    return coverage


def plan_missing_generator_attack(
    db, *, curve_id, E, basis, observations, anchor_budget, pool_size, heights
):
    """Return prioritized anchors and a reproducible, non-proof planning receipt."""
    from rank42.pointed_quartic import anchor_fingerprint

    anchor_budget = max(1, int(anchor_budget))
    highest_height = max(int(h) for h in heights)
    gram, lattice_id, precision_bits = _matching_gram(db, curve_id, E, basis)
    coverage = _coverage(db, curve_id)
    basis_hash = _fingerprint(basis)

    # Spread exact observations over the point-height distribution.  This is
    # naive x-height, used to diversify charts, not a canonical-height proof.
    observed = []
    for P in observations:
        try:
            key = anchor_fingerprint(E.a_invariants(), _coordinates(P))
            observed.append((_x_height_log10(P), key, P))
        except (ArithmeticError, ValueError, TypeError, ZeroDivisionError):
            continue
    observed.sort(key=lambda entry: (entry[0], entry[1]))
    observation_budget = min(len(observed), max(8, min(anchor_budget // 2, pool_size // 4)))
    bins = [observed[i * len(observed) // 4:(i + 1) * len(observed) // 4]
            for i in range(4)]
    for group in bins:
        group.sort(key=lambda entry: (
            coverage.get(entry[1], {}).get("max_completed_height", 0) >= highest_height,
            coverage.get(entry[1], {}).get("completed", 0),
            entry[0], entry[1],
        ))
    selected_observations = []
    while len(selected_observations) < observation_budget and any(bins):
        for group in bins:
            if group and len(selected_observations) < observation_budget:
                selected_observations.append(group.pop(0))

    # Small Gram norm makes an affordable covering candidate.  The exact
    # point still lies in the known subgroup; its only role is an anchor.
    candidates = []
    rank = len(basis)
    for i in range(rank):
        for j in range(i + 1, rank):
            for sign in (-1, 1):
                norm = (gram[i][i] + gram[j][j] + 2 * sign * gram[i][j]
                        if gram is not None else float(j - i + rank))
                if norm > 0:
                    candidates.append((norm, i, j, sign))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    vector_budget = min(max(4, anchor_budget // 2), max(0, pool_size - rank))
    ranked = []
    for norm, i, j, sign in candidates[: max(64, vector_budget * 12)]:
        try:
            P = basis[i] + sign * basis[j]
            key = anchor_fingerprint(E.a_invariants(), _coordinates(P))
        except (ArithmeticError, ValueError, TypeError, ZeroDivisionError):
            continue
        previous = coverage.get(key, {})
        exhausted = int(previous.get("max_completed_height", 0)) >= highest_height
        score = (1 if exhausted else 0, int(previous.get("completed", 0)),
                 norm, i, j, sign)
        ranked.append((score, key, i, j, sign, norm))
    ranked.sort(key=lambda item: item[0])
    preferred = []
    per_coordinate = [0] * rank
    seen = set()
    for _, key, i, j, sign, norm in ranked:
        if len(preferred) >= vector_budget:
            break
        if key in seen or per_coordinate[i] >= 3 or per_coordinate[j] >= 3:
            continue
        vector = [0] * rank
        vector[i], vector[j] = 1, sign
        preferred.append({"vector": vector, "attack_priority": len(preferred),
                          "predicted_gram_norm": round(norm, 8),
                          "prior_completed_searches": coverage.get(key, {}).get("completed", 0)})
        seen.add(key)
        per_coordinate[i] += 1
        per_coordinate[j] += 1

    heights_seen = [entry[0] for entry in observed]
    quartiles = [round(heights_seen[min(len(heights_seen) - 1,
                    (len(heights_seen) - 1) * index // 4)], 3)
                 for index in range(5)] if heights_seen else []
    receipt = {
        "schema": "rank42.missing_generator_plan.v1",
        "curve_id": int(curve_id), "basis_size": rank,
        "basis_sha256": basis_hash, "proof_state": "heuristic_search_plan",
        "stored_lattice_id": lattice_id, "lattice_precision_bits": precision_bits,
        "gram_used": gram is not None,
        "observation_count": len(observed),
        "selected_observations": len(selected_observations),
        "naive_x_height_log10_range": (
            [round(heights_seen[0], 3), round(heights_seen[-1], 3)]
            if heights_seen else None
        ),
        "naive_x_height_log10_quartiles": quartiles,
        "selected_observation_keys": [entry[1] for entry in selected_observations],
        "preferred_anchor_count": len(preferred),
        "preferred_vectors": preferred,
        "existing_anchor_coverage": len(coverage),
        "completed_searches_at_requested_top_height": sum(
            value["max_completed_height"] >= highest_height for value in coverage.values()
        ),
        "max_requested_quartic_height": highest_height,
        "note": "Gram norms and point heights route searches only; exact independence remains required",
    }
    return {
        "observations": [entry[2] for entry in selected_observations],
        "preferred_vectors": preferred,
        "coverage": coverage,
        "receipt": receipt,
    }
