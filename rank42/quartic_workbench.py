"""Read-only Quartics workbench projections and launch-plan helpers."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math

from rank42.curve_research_state import curve_research_state_map
from rank42.ratpoints import normalize_polynomial


def _loads(value, default):
    try:
        result = json.loads(value or "")
    except Exception:
        return default
    return result if isinstance(result, type(default)) else default


def quartic_kind(row):
    metadata = _loads(row["metadata_json"], {})
    schema = str(metadata.get("schema") or "")
    hole = str(row["hole_label"] or "")
    if schema.startswith("rank42.pointed_quartic") or hole.startswith("pointed:"):
        return "point-centered"
    if schema.startswith("rank42.covering"):
        return "covering"
    if metadata.get("plugin_id") or metadata.get("family_spec"):
        return "plugin/family"
    return "quartic"


def quartic_history(db, curve_id, *, limit=200):
    """Return persisted quartic searches for one curve without mutation."""
    rows = db.execute(
        """SELECT * FROM quartic_searches
           WHERE curve_id=?
           ORDER BY id DESC LIMIT ?""",
        (int(curve_id), max(1, int(limit))),
    ).fetchall()
    mapped_counts = {
        int(str(row["search_ref"]).split(":", 1)[1]): int(row["n"])
        for row in db.execute(
            """SELECT search_ref,COUNT(*) AS n
               FROM points
               WHERE curve_id=? AND search_ref LIKE 'quartic:%'
               GROUP BY search_ref""",
            (int(curve_id),),
        ).fetchall()
        if str(row["search_ref"] or "").startswith("quartic:")
        and str(row["search_ref"]).split(":", 1)[1].isdigit()
    }
    result = []
    for row in rows:
        metadata = _loads(row["metadata_json"], {})
        quartic_points = db.execute(
            "SELECT COUNT(*) AS n FROM quartic_points WHERE search_id=?",
            (int(row["id"]),),
        ).fetchone()
        result.append({
            "row": row,
            "id": int(row["id"]),
            "kind": quartic_kind(row),
            "status": str(row["status"] or "new"),
            "height": int(row["height_bound"] or 0),
            "denominator_low": row["denominator_low"],
            "denominator_high": row["denominator_high"],
            "quartic_points": int(quartic_points["n"] or 0),
            "mapped_points": int(mapped_counts.get(int(row["id"]), 0)),
            "runtime": row["runtime"],
            "hole_label": row["hole_label"],
            "anchor_source": metadata.get("anchor_source"),
            "anchor_vector": metadata.get("anchor_vector"),
            "reduced": bool(metadata.get("reduction")),
            "map_back_failures": int(metadata.get("map_back_failure_count") or 0),
            "metadata": metadata,
        })
    return result


def covering_history(db, curve_id, *, limit=100):
    """Return stored exact coverings with their latest append-only search attempt."""
    rows = db.execute(
        """SELECT * FROM coverings
           WHERE curve_id=?
           ORDER BY id DESC LIMIT ?""",
        (int(curve_id), max(1, int(limit))),
    ).fetchall()
    result = []
    for row in rows:
        attempt = db.execute(
            """SELECT * FROM covering_search_attempts
               WHERE covering_id=?
               ORDER BY id DESC LIMIT 1""",
            (int(row["id"]),),
        ).fetchone()
        quartic = _loads(row["quartic_json"], {})
        metadata = _loads(row["metadata_json"], {})
        result.append({
            "row": row,
            "id": int(row["id"]),
            "status": str(row["status"] or "ready"),
            "height": (
                int(attempt["height"])
                if attempt is not None and attempt["height"] is not None
                else int(quartic.get("height") or 0)
            ),
            "timeout": (
                int(attempt["timeout_seconds"])
                if attempt is not None and attempt["timeout_seconds"] is not None
                else None
            ),
            "outcome": None if attempt is None else str(attempt["outcome"]),
            "ratpoints_hits": 0 if attempt is None else int(attempt["ratpoints_hits"] or 0),
            "mapped_points": 0 if attempt is None else int(attempt["mapped_points"] or 0),
            "runtime": None if attempt is None else attempt["runtime_seconds"],
            "lattice_id": row["lattice_id"],
            "hole_id": row["hole_id"],
            "quartic_search_id": row["quartic_search_id"],
            "metadata": metadata,
        })
    return result


def mapped_points_for_quartic(db, curve_id, search_id):
    return db.execute(
        """SELECT * FROM points
           WHERE curve_id=? AND search_ref=?
           ORDER BY id DESC""",
        (int(curve_id), f"quartic:{int(search_id)}"),
    ).fetchall()


def mapped_points_for_covering(db, curve_id, covering_id):
    """Return points associated by current attribution or immutable discovery evidence."""
    covering_id = int(covering_id)
    direct_ref = f"covering:{covering_id}"
    metadata_pattern = f'%"covering_id": {covering_id}%'
    rows = db.execute(
        """SELECT DISTINCT p.*
           FROM points p
           LEFT JOIN point_discoveries d ON d.point_id=p.id
           WHERE p.curve_id=?
             AND (
                 p.search_ref=?
                 OR p.metadata_json LIKE ?
                 OR d.search_ref=?
                 OR d.search_ref LIKE ?
                 OR d.search_ref LIKE ?
                 OR d.metadata_json LIKE ?
             )
           ORDER BY p.id DESC""",
        (
            int(curve_id),
            direct_ref,
            metadata_pattern,
            direct_ref,
            direct_ref + ":%",
            f"%:exact-covering:%:{covering_id}",
            metadata_pattern,
        ),
    ).fetchall()
    return rows


def _fraction(value):
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _fraction_text(value):
    value = _fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _explicit_canonical_height(metadata):
    metadata = dict(metadata or {})
    containers = [metadata]
    heights = metadata.get("heights")
    if isinstance(heights, dict):
        containers.append(heights)
    for container in containers:
        for key in (
            "canonical_height",
            "neron_tate_height",
            "neron-tate_height",
            "néron_tate_height",
        ):
            value = container.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value), key
            except (TypeError, ValueError):
                continue
    return None, None


def point_height_profile(point):
    """Return exact-coordinate point-height data without claiming canonical height.

    For x=a/b in lowest terms, Hx=max(|a|,b) is the multiplicative projective
    height on P^1. The UI normally displays log10(Hx) because Hx can be huge.
    Explicit persisted canonical/Néron–Tate height, if present under an
    unambiguous metadata key, is reported separately.
    """
    try:
        x = _fraction(point["x"])
        x_height = max(abs(int(x.numerator)), int(x.denominator))
        x_height = max(1, int(x_height))
        x_height_log10 = math.log10(x_height)
    except Exception:
        x_height = None
        x_height_log10 = None

    try:
        metadata = _loads(point["metadata_json"], {})
    except Exception:
        metadata = {}
    canonical_height, canonical_source = _explicit_canonical_height(metadata)

    try:
        point_id = int(point["id"])
    except Exception:
        point_id = None

    return {
        "point": point,
        "point_id": point_id,
        "x_height": x_height,
        "x_height_text": None if x_height is None else str(x_height),
        "x_height_log10": x_height_log10,
        "canonical_height": canonical_height,
        "canonical_height_source": canonical_source,
        "quality_metric": (
            "canonical height"
            if canonical_height is not None
            else ("log10 Hx" if x_height_log10 is not None else "unavailable")
        ),
        "quality_value": (
            canonical_height
            if canonical_height is not None
            else x_height_log10
        ),
    }


def _quantile(values, q):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = max(0.0, min(1.0, float(q))) * (len(values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return values[low]
    weight = position - low
    return values[low] * (1.0 - weight) + values[high] * weight


def point_height_summary(points, *, top_n=8):
    profiles = [
        point_height_profile(point)
        for point in (points or ())
    ]
    x_profiles = [
        profile
        for profile in profiles
        if profile["x_height_log10"] is not None
    ]
    x_values = [profile["x_height_log10"] for profile in x_profiles]

    best = sorted(
        x_profiles,
        key=lambda profile: (
            0 if profile["canonical_height"] is not None else 1,
            float(
                profile["canonical_height"]
                if profile["canonical_height"] is not None
                else profile["x_height_log10"]
            ),
            int(profile["point_id"] or 0),
        ),
    )[: max(1, int(top_n))]

    unresolved = []
    for profile in x_profiles:
        point = profile["point"]
        try:
            exact = bool(point["exact_verified"])
            rigorous = bool(point["rigorous_independent"])
            status = str(point["independence_status"] or "unknown")
        except Exception:
            continue
        if exact and not rigorous and status in {"unknown", "numerical_novel", "inconclusive"}:
            unresolved.append(profile)
    unresolved.sort(
        key=lambda profile: (
            0 if profile["canonical_height"] is not None else 1,
            float(
                profile["canonical_height"]
                if profile["canonical_height"] is not None
                else profile["x_height_log10"]
            ),
            int(profile["point_id"] or 0),
        )
    )

    return {
        "count": len(profiles),
        "x_height_count": len(x_profiles),
        "canonical_height_count": sum(
            profile["canonical_height"] is not None
            for profile in profiles
        ),
        "minimum_log10_x_height": _quantile(x_values, 0.0),
        "p25_log10_x_height": _quantile(x_values, 0.25),
        "median_log10_x_height": _quantile(x_values, 0.5),
        "p75_log10_x_height": _quantile(x_values, 0.75),
        "maximum_log10_x_height": _quantile(x_values, 1.0),
        "best_points": tuple(best),
        "best_unresolved_points": tuple(unresolved[: max(1, int(top_n))]),
    }


def quartic_arithmetic(coefficients, *, reduced=False):
    """Return deterministic exact arithmetic for one stored quartic model.

    Stored Rank Hunter / ratpoints coefficients are a_0,...,a_n.  For degree
    four we homogenize as

        g(X,Z)=a X^4+b X^3 Z+c X^2 Z^2+d X Z^3+e Z^4

    with (a,b,c,d,e)=(a_4,a_3,a_2,a_1,a_0), and use the classical
    Cremona-Fisher binary-quartic invariants.
    """
    values = [_fraction(value) for value in coefficients]
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    if len(values) < 2:
        raise ValueError("quartic arithmetic requires a positive-degree polynomial")

    exact_coefficients = tuple(_fraction_text(value) for value in values)
    payload = json.dumps(
        {
            "schema": "rank42.quartic_fingerprint.v1",
            "coefficients": exact_coefficients,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    short = "-".join(
        digest[index:index + 4].upper()
        for index in range(0, 12, 4)
    )

    normalized = normalize_polynomial(values)
    integer_coefficients = tuple(int(value) for value in normalized.integer_coefficients)
    content = 0
    for value in integer_coefficients:
        content = math.gcd(content, abs(int(value)))
    coefficient_height = max(abs(int(value)) for value in integer_coefficients)

    result = {
        "schema": "rank42.quartic_fingerprint.v1",
        "fingerprint": f"QF:{short}",
        "fingerprint_sha256": digest,
        "degree": len(values) - 1,
        "coefficients": exact_coefficients,
        "integer_coefficients": integer_coefficients,
        "y_scale": int(normalized.y_scale),
        "coefficient_height": coefficient_height,
        "content": content,
        "primitive": content == 1,
        "reduced": bool(reduced),
        "I": None,
        "J": None,
        "invariant_delta": None,
        "polynomial_discriminant": None,
        "nonsingular_binary_quartic": None,
    }

    if len(values) == 5:
        a, b, c, d, e = values[4], values[3], values[2], values[1], values[0]
        I = 12 * a * e - 3 * b * d + c * c
        J = (
            72 * a * c * e
            + 9 * b * c * d
            - 27 * a * d * d
            - 27 * e * b * b
            - 2 * c * c * c
        )
        invariant_delta = 4 * I * I * I - J * J
        polynomial_discriminant = invariant_delta / 27
        result.update(
            {
                "I": _fraction_text(I),
                "J": _fraction_text(J),
                "invariant_delta": _fraction_text(invariant_delta),
                "polynomial_discriminant": _fraction_text(polynomial_discriminant),
                "nonsingular_binary_quartic": invariant_delta != 0,
            }
        )
    return result


def _normalize_local_state(value):
    if isinstance(value, bool):
        return "soluble" if value else "obstructed"
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"soluble", "locally_soluble", "pass", "passed", "ok", "yes"}:
        return "soluble"
    if text in {
        "obstructed",
        "locally_obstructed",
        "insoluble",
        "locally_insoluble",
        "fail",
        "failed",
        "no",
    }:
        return "obstructed"
    if text in {"not_checked", "unchecked", "unknown", "pending", "not_run"}:
        return "not_checked"
    return "recorded"


def quartic_local_evidence(metadata=None, *, status=None):
    """Project only explicitly persisted local-solubility information.

    Zero search yield is deliberately ignored. The preferred covering-local
    source is the rigorous covering_local_height_plan.local_analysis record
    produced by Rank Hunter's full local solver. Compatibility metadata keys
    are still recognized for older/plugin records.
    """
    metadata = dict(metadata or {})
    sources = []
    places = {}
    attempted = False
    proof_scope = None
    solver_status = None
    all_places_checked = False
    unsupported_reason = None
    first_obstructing_place = None

    covering_plan = metadata.get("covering_local_height_plan")
    covering_local = {}
    if isinstance(covering_plan, dict):
        candidate = covering_plan.get("local_analysis")
        if isinstance(candidate, dict):
            covering_local = candidate
            attempted = True
            sources.append("covering_local_height_plan.local_analysis")
            proof_scope = covering_plan.get("proof_scope")
            solver_status = str(covering_local.get("status") or "")
            all_places_checked = bool(covering_local.get("all_places_checked"))
            unsupported_reason = covering_local.get("reason")

            for check in covering_local.get("checks") or ():
                if not isinstance(check, dict):
                    continue
                place = check.get("place")
                if place is None:
                    continue
                places[str(place)] = (
                    "soluble"
                    if bool(check.get("locally_soluble"))
                    else "obstructed"
                )

            obstruction = covering_local.get("obstruction")
            if obstruction is not None:
                first_obstructing_place = str(obstruction)

    local = metadata.get("local")
    if isinstance(local, dict):
        for key in ("places", "prime_status", "statuses"):
            candidate = local.get(key)
            if isinstance(candidate, dict):
                places.update(candidate)
                sources.append(f"local.{key}")

    for key in (
        "local_prime_status",
        "local_prime_statuses",
        "local_solubility",
        "local_status",
    ):
        candidate = metadata.get(key)
        if isinstance(candidate, dict):
            places.update(candidate)
            sources.append(key)

    rows = []
    for place, value in places.items():
        label = str(place)
        if label.lower() in {"r", "real", "infinity", "inf", "∞"}:
            label = "∞"
        elif label.lstrip("+-").isdigit():
            label = f"p={int(label)}"
        rows.append(
            {
                "place": label,
                "state": _normalize_local_state(value),
                "recorded": value,
            }
        )
    rows.sort(key=lambda rec: (rec["place"] != "∞", rec["place"]))

    first_obstructing_prime = None
    if first_obstructing_place is not None:
        text = str(first_obstructing_place)
        if text.lstrip("+-").isdigit():
            first_obstructing_prime = str(int(text))

    for container in (metadata, local if isinstance(local, dict) else {}):
        for key in ("first_obstructing_prime", "obstructing_prime"):
            value = container.get(key)
            if value is not None:
                first_obstructing_prime = str(value)
                if first_obstructing_place is None:
                    first_obstructing_place = str(value)
                sources.append(key if container is metadata else f"local.{key}")
                break
        if first_obstructing_prime is not None:
            break

    explicit_obstruction = any(rec["state"] == "obstructed" for rec in rows)
    status_obstruction = str(status or "") == "locally_obstructed"
    rigorous_obstruction = solver_status == "locally_insoluble"
    rigorous_soluble = (
        solver_status == "everywhere_locally_soluble"
        and all_places_checked
    )

    if rigorous_obstruction or explicit_obstruction or status_obstruction:
        place_label = None
        if first_obstructing_place is not None:
            raw = str(first_obstructing_place)
            if raw.lower() in {"infinity", "inf", "∞", "real", "r"}:
                place_label = "∞"
            elif raw.lstrip("+-").isdigit():
                place_label = f"p={int(raw)}"
            else:
                place_label = raw
        overall = (
            f"obstructed at {place_label}"
            if place_label
            else "obstruction recorded"
        )
    elif rigorous_soluble:
        overall = "everywhere locally soluble"
    elif solver_status == "unsupported":
        overall = "local solver unsupported"
    elif rows and all(rec["state"] == "soluble" for rec in rows):
        overall = "soluble at all recorded places"
    elif rows:
        overall = "partial local record"
    else:
        overall = "not checked / no local record"

    recorded = bool(
        rows
        or status_obstruction
        or rigorous_obstruction
        or rigorous_soluble
        or first_obstructing_prime is not None
        or first_obstructing_place is not None
    )
    return {
        "overall": overall,
        "places": tuple(rows),
        "first_obstructing_prime": first_obstructing_prime,
        "first_obstructing_place": first_obstructing_place,
        "recorded": recorded,
        "attempted": attempted,
        "solver_status": solver_status,
        "all_places_checked": all_places_checked,
        "proof_scope": proof_scope,
        "unsupported_reason": unsupported_reason,
        "sources": tuple(dict.fromkeys(sources)),
    }

def _effective_search_region(height, denominator_low=None, denominator_high=None):
    """Return the persisted ratpoints rectangle used for coverage proxies.

    The proxy is H times denominator-band width. It is bookkeeping for repeated
    bounded searches of the same exact model, not a count of rational points.
    """
    height = max(0, int(height or 0))
    if height <= 0:
        return None
    low = max(1, int(denominator_low or 1))
    requested_high = height if denominator_high is None else int(denominator_high)
    high = min(height, requested_high)
    if high < low:
        return None
    width = high - low + 1
    return {
        "height": height,
        "denominator_low": low,
        "denominator_high": high,
        "denominator_width": width,
        "proxy_area": height * width,
    }


def _coverage_union_proxy(regions):
    regions = [dict(rec) for rec in regions if rec]
    if not regions:
        return 0
    boundaries = sorted(
        {
            boundary
            for rec in regions
            for boundary in (
                int(rec["denominator_low"]),
                int(rec["denominator_high"]) + 1,
            )
        }
    )
    total = 0
    for start, end in zip(boundaries, boundaries[1:]):
        heights = [
            int(rec["height"])
            for rec in regions
            if int(rec["denominator_low"]) <= start <= int(rec["denominator_high"])
        ]
        if heights:
            total += max(heights) * (end - start)
    return int(total)


def _merge_denominator_intervals(regions):
    intervals = sorted(
        (
            int(rec["denominator_low"]),
            int(rec["denominator_high"]),
        )
        for rec in regions
        if rec
    )
    merged = []
    for low, high in intervals:
        if not merged or low > merged[-1][1] + 1:
            merged.append([low, high])
        else:
            merged[-1][1] = max(merged[-1][1], high)
    return tuple((low, high) for low, high in merged)


def _coverage_overlap_summary(regions):
    regions = [dict(rec) for rec in regions if rec]
    attempted = sum(int(rec["proxy_area"]) for rec in regions)
    union = _coverage_union_proxy(regions)
    repeated = max(0, attempted - union)
    return {
        "attempted_proxy": attempted,
        "union_proxy": union,
        "repeated_proxy": repeated,
        "overlap_fraction": (float(repeated) / float(attempted)) if attempted else 0.0,
        "denominator_intervals": _merge_denominator_intervals(regions),
    }


def _coverage_depth_label(completed_regions):
    completed_regions = int(completed_regions or 0)
    if completed_regions <= 0:
        return "no completed bounded coverage"
    if completed_regions == 1:
        return "barely searched"
    if completed_regions <= 3:
        return "lightly searched"
    if completed_regions <= 7:
        return "moderately searched"
    return "heavily searched"


def _empirical_search_state(
    *,
    completed_regions,
    incomplete_attempts,
    total_hits,
    recent_hits,
    mapped_points,
    rigorous_points,
    local_obstruction=False,
):
    if local_obstruction:
        return "local obstruction recorded"
    if rigorous_points:
        return "productive · rigorous witness"
    if mapped_points and recent_hits:
        return "productive"
    if total_hits and not recent_hits:
        return "historically productive"
    if completed_regions == 0 and incomplete_attempts:
        return "coverage incomplete"
    if total_hits:
        return "quartic hits recorded"
    if completed_regions >= 3:
        return "no recent yield"
    return f"{_coverage_depth_label(completed_regions)} · no hits yet"


def _search_fingerprint(rec):
    coefficients = _loads(rec["row"]["polynomial_json"], [])
    return quartic_arithmetic(
        coefficients,
        reduced=bool(rec.get("reduced")),
    )["fingerprint"]


def quartic_region_overlap(
    searches,
    rec,
    *,
    height,
    denominator_low=None,
    denominator_high=None,
):
    """Estimate prior coverage of a proposed same-model search rectangle.

    Only searches whose persisted status is done count as completed coverage.
    Timeouts and errors contribute no completed area because partial ratpoints
    traversal state is not persisted.
    """
    target = _effective_search_region(height, denominator_low, denominator_high)
    if target is None:
        return {
            "valid": False,
            "overlap_fraction": None,
            "covered_proxy": 0,
            "target_proxy": 0,
        }

    fingerprint = _search_fingerprint(rec)
    clipped = []
    for prior in searches or ():
        if prior["status"] != "done" or _search_fingerprint(prior) != fingerprint:
            continue
        region = _effective_search_region(
            prior["height"],
            prior["denominator_low"],
            prior["denominator_high"],
        )
        if region is None:
            continue
        low = max(target["denominator_low"], region["denominator_low"])
        high = min(target["denominator_high"], region["denominator_high"])
        if high < low:
            continue
        clipped_height = min(target["height"], region["height"])
        clipped.append(
            {
                "height": clipped_height,
                "denominator_low": low,
                "denominator_high": high,
                "denominator_width": high - low + 1,
                "proxy_area": clipped_height * (high - low + 1),
            }
        )
    covered = min(target["proxy_area"], _coverage_union_proxy(clipped))
    return {
        "valid": True,
        "fingerprint": fingerprint,
        "overlap_fraction": float(covered) / float(target["proxy_area"]),
        "covered_proxy": int(covered),
        "target_proxy": int(target["proxy_area"]),
        "effective_region": target,
    }


def quartic_search_coverage(db, curve_id, *, searches=None, coverings=None):
    """Aggregate completed search coverage and empirical yield conservatively."""
    searches = (
        list(searches)
        if searches is not None
        else quartic_history(db, curve_id, limit=100000)
    )
    coverings = (
        list(coverings)
        if coverings is not None
        else covering_history(db, curve_id, limit=100000)
    )

    grouped = {}
    for rec in searches:
        fingerprint = _search_fingerprint(rec)
        grouped.setdefault(fingerprint, []).append(rec)

    model_rows = []
    for fingerprint, records in grouped.items():
        records = sorted(records, key=lambda rec: int(rec["id"]), reverse=True)
        completed = [rec for rec in records if rec["status"] == "done"]
        incomplete = [
            rec
            for rec in records
            if rec["status"] in {"timeout", "error", "running", "new"}
        ]
        regions = [
            _effective_search_region(
                rec["height"],
                rec["denominator_low"],
                rec["denominator_high"],
            )
            for rec in completed
        ]
        regions = [region for region in regions if region]
        overlap = _coverage_overlap_summary(regions)
        hits = sum(int(rec["quartic_points"]) for rec in records)
        mapped = {}
        for rec in records:
            for point in mapped_points_for_quartic(db, curve_id, rec["id"]):
                mapped[int(point["id"])] = point
        rigorous = sum(bool(point["rigorous_independent"]) for point in mapped.values())
        recent = records[: min(3, len(records))]
        recent_hits = sum(int(rec["quartic_points"]) for rec in recent)
        runtime = sum(float(rec["runtime"] or 0.0) for rec in records)
        max_height = max((int(rec["height"]) for rec in completed), default=0)
        local_obstruction = any(rec["status"] == "locally_obstructed" for rec in records)
        state = _empirical_search_state(
            completed_regions=len(regions),
            incomplete_attempts=len(incomplete),
            total_hits=hits,
            recent_hits=recent_hits,
            mapped_points=len(mapped),
            rigorous_points=rigorous,
            local_obstruction=local_obstruction,
        )
        model_rows.append(
            {
                "fingerprint": fingerprint,
                "kinds": tuple(sorted({str(rec["kind"]) for rec in records})),
                "jobs": len(records),
                "completed_regions": len(regions),
                "incomplete_attempts": len(incomplete),
                "max_height": max_height,
                "denominator_intervals": overlap["denominator_intervals"],
                "overlap_fraction": overlap["overlap_fraction"],
                "quartic_hits": hits,
                "mapped_points": len(mapped),
                "rigorous_points": rigorous,
                "runtime_seconds": runtime,
                "seconds_per_hit": (runtime / hits) if hits else None,
                "seconds_per_mapped": (runtime / len(mapped)) if mapped else None,
                "recent_jobs": len(recent),
                "recent_hits": recent_hits,
                "depth_label": _coverage_depth_label(len(regions)),
                "state": state,
            }
        )
    model_rows.sort(
        key=lambda rec: (
            -int(rec["rigorous_points"]),
            -int(rec["mapped_points"]),
            -int(rec["quartic_hits"]),
            -int(rec["max_height"]),
            rec["fingerprint"],
        )
    )

    covering_rows = []
    for rec in coverings:
        attempts = db.execute(
            """SELECT * FROM covering_search_attempts
               WHERE covering_id=?
               ORDER BY id DESC""",
            (int(rec["id"]),),
        ).fetchall()
        completed_attempts = [
            attempt
            for attempt in attempts
            if str(attempt["outcome"] or "") in {"completed", "partial"}
            and not bool(attempt["one_point"])
        ]
        incomplete_attempts = [
            attempt
            for attempt in attempts
            if str(attempt["outcome"] or "") in {"timeout", "error"}
        ]
        regions = [
            _effective_search_region(attempt["height"], None, None)
            for attempt in completed_attempts
        ]
        regions = [region for region in regions if region]
        overlap = _coverage_overlap_summary(regions)
        hits = sum(int(attempt["ratpoints_hits"] or 0) for attempt in attempts)
        recent = attempts[: min(3, len(attempts))]
        recent_hits = sum(int(attempt["ratpoints_hits"] or 0) for attempt in recent)
        runtime = sum(float(attempt["runtime_seconds"] or 0.0) for attempt in attempts)
        mapped = {
            int(point["id"]): point
            for point in mapped_points_for_covering(db, curve_id, rec["id"])
        }
        rigorous = sum(bool(point["rigorous_independent"]) for point in mapped.values())
        state = _empirical_search_state(
            completed_regions=len(regions),
            incomplete_attempts=len(incomplete_attempts),
            total_hits=hits,
            recent_hits=recent_hits,
            mapped_points=len(mapped),
            rigorous_points=rigorous,
            local_obstruction=str(rec["status"] or "") == "locally_obstructed",
        )
        covering_rows.append(
            {
                "id": int(rec["id"]),
                "attempts": len(attempts),
                "completed_regions": len(regions),
                "incomplete_attempts": len(incomplete_attempts),
                "early_stop_attempts": sum(bool(attempt["one_point"]) for attempt in attempts),
                "max_height": max(
                    (int(attempt["height"] or 0) for attempt in completed_attempts),
                    default=0,
                ),
                "overlap_fraction": overlap["overlap_fraction"],
                "quartic_hits": hits,
                "mapped_points": len(mapped),
                "rigorous_points": rigorous,
                "runtime_seconds": runtime,
                "seconds_per_hit": (runtime / hits) if hits else None,
                "seconds_per_mapped": (runtime / len(mapped)) if mapped else None,
                "recent_jobs": len(recent),
                "recent_hits": recent_hits,
                "depth_label": _coverage_depth_label(len(regions)),
                "state": state,
            }
        )
    covering_rows.sort(
        key=lambda rec: (
            -int(rec["rigorous_points"]),
            -int(rec["mapped_points"]),
            -int(rec["quartic_hits"]),
            -int(rec["max_height"]),
            int(rec["id"]),
        )
    )

    all_overlap = [
        float(rec["overlap_fraction"])
        for rec in (*model_rows, *covering_rows)
        if int(rec["completed_regions"]) >= 2
    ]
    all_runtime = sum(float(rec["runtime_seconds"]) for rec in model_rows) + sum(
        float(rec["runtime_seconds"]) for rec in covering_rows
    )
    return {
        "models": tuple(model_rows),
        "coverings": tuple(covering_rows),
        "summary": {
            "models": len(model_rows),
            "coverings": len(covering_rows),
            "attempts": sum(int(rec["jobs"]) for rec in model_rows)
            + sum(int(rec["attempts"]) for rec in covering_rows),
            "completed_regions": sum(int(rec["completed_regions"]) for rec in model_rows)
            + sum(int(rec["completed_regions"]) for rec in covering_rows),
            "incomplete_attempts": sum(int(rec["incomplete_attempts"]) for rec in model_rows)
            + sum(int(rec["incomplete_attempts"]) for rec in covering_rows),
            "mean_repeat_overlap": (
                sum(all_overlap) / len(all_overlap) if all_overlap else 0.0
            ),
            "runtime_seconds": all_runtime,
        },
    }


def _attack_evidence(model=None, *, local=None):
    model = dict(model or {})
    local = dict(local or {})
    return {
        "state": str(model.get("state") or "no aggregate history"),
        "completed_regions": int(model.get("completed_regions") or 0),
        "incomplete_attempts": int(model.get("incomplete_attempts") or 0),
        "recent_hits": int(model.get("recent_hits") or 0),
        "quartic_hits": int(model.get("quartic_hits") or 0),
        "mapped_points": int(model.get("mapped_points") or 0),
        "rigorous_points": int(model.get("rigorous_points") or 0),
        "max_height": int(model.get("max_height") or 0),
        "repeat_overlap": float(model.get("overlap_fraction") or 0.0),
        "runtime_seconds": float(model.get("runtime_seconds") or 0.0),
        "local": str(local.get("overall") or "not recorded"),
    }


def quartic_next_attack_plan(searches, rec, *, model_coverage=None):
    """Return an inspectable compute-allocation plan for one quartic search.

    The plan ranks existing workbench actions from persisted evidence.  It does
    not estimate a probability of finding a point.
    """
    model = dict(model_coverage or {})
    local = quartic_local_evidence(rec.get("metadata"), status=rec.get("status"))
    evidence = _attack_evidence(model, local=local)
    current_height = max(1, int(rec.get("height") or 1))
    current_low = max(1, int(rec.get("denominator_low") or 1))
    current_high = int(rec.get("denominator_high") or min(current_height, max(10, current_low)))
    current_high = max(current_low, min(current_height, current_high))
    width = max(10, current_high - current_low + 1)
    next_low = current_high + 1
    next_high = next_low + width - 1

    if local["overall"] == "obstruction recorded":
        return {
            "action": "hold_local_obstruction",
            "headline": "Verify the recorded local obstruction before spending more ratpoints time",
            "launch_recommended": False,
            "why": (
                "This exact model carries explicit local-obstruction evidence.",
                "A local obstruction is stronger evidence than bounded zero-yield search history.",
            ),
            "suggested": {},
            "evidence": evidence,
        }

    if evidence["mapped_points"] > evidence["rigorous_points"]:
        return {
            "action": "resolve_independence",
            "headline": "Resolve the mapped elliptic point(s) before buying more search",
            "launch_recommended": False,
            "why": (
                f"{evidence['mapped_points']} mapped point(s) are associated with this exact model.",
                f"Only {evidence['rigorous_points']} currently carry the rigorous independent flag.",
            ),
            "suggested": {},
            "evidence": evidence,
        }

    if rec.get("status") in {"timeout", "error"} or (
        evidence["completed_regions"] == 0 and evidence["incomplete_attempts"] > 0
    ):
        return {
            "action": "retry_incomplete",
            "headline": "Finish the incomplete geometry before interpreting its yield",
            "launch_recommended": True,
            "why": (
                "The latest/same-model history contains incomplete work.",
                "Timeouts and errors do not count as completed coverage in the workbench.",
            ),
            "suggested": {
                "height": current_height,
                "height_factor": 1,
                "timeout_multiplier": 2,
                "anchors": 8,
            },
            "evidence": evidence,
        }

    if rec.get("kind") == "point-centered":
        if evidence["recent_hits"] > 0 or evidence["rigorous_points"] > 0:
            return {
                "action": "widen_height",
                "headline": "Widen this productive point-centered model",
                "launch_recommended": True,
                "why": (
                    f"The newest persisted attempts record {evidence['recent_hits']} quartic hit(s).",
                    "Widening keeps the same exact geometry while adding height territory.",
                ),
                "suggested": {
                    "height": current_height * 5,
                    "height_factor": 5,
                    "timeout_multiplier": 2,
                    "anchors": 8,
                },
                "evidence": evidence,
            }
        if evidence["completed_regions"] >= 3 or evidence["repeat_overlap"] >= 0.35:
            return {
                "action": "alternate_anchors",
                "headline": "Diversify the anchor portfolio instead of repeating the same geometry",
                "launch_recommended": True,
                "why": (
                    f"This model has {evidence['completed_regions']} completed bounded region(s).",
                    f"Recent persisted yield is {evidence['recent_hits']} hit(s); changing anchors explores different pointed quartics.",
                ),
                "suggested": {
                    "height": current_height,
                    "height_factor": 1,
                    "timeout_multiplier": 1,
                    "anchors": 12,
                },
                "evidence": evidence,
            }
        return {
            "action": "widen_height",
            "headline": "Add height before abandoning this lightly searched point-centered model",
            "launch_recommended": True,
            "why": (
                f"Only {evidence['completed_regions']} completed bounded region(s) are recorded.",
                "There is not yet enough same-model completed coverage to call the geometry heavily searched.",
            ),
            "suggested": {
                "height": current_height * 5,
                "height_factor": 5,
                "timeout_multiplier": 1,
                "anchors": 8,
            },
            "evidence": evidence,
        }

    proposed_height = current_height
    if next_low > proposed_height:
        proposed_height = max(current_height * 2, next_high)
    neighbor = quartic_region_overlap(
        searches or (),
        rec,
        height=proposed_height,
        denominator_low=next_low,
        denominator_high=next_high,
    )
    neighbor_overlap = (
        float(neighbor["overlap_fraction"])
        if neighbor.get("valid") and neighbor.get("overlap_fraction") is not None
        else 1.0
    )

    if evidence["recent_hits"] > 0 or evidence["rigorous_points"] > 0:
        return {
            "action": "widen_height",
            "headline": "Widen the productive exact model",
            "launch_recommended": True,
            "why": (
                f"The newest persisted attempts record {evidence['recent_hits']} quartic hit(s).",
                "Widening preserves the productive exact model while adding search height.",
            ),
            "suggested": {
                "height": current_height * 5,
                "height_factor": 5,
                "timeout_multiplier": 2,
                "neighbor_low": next_low,
                "neighbor_high": next_high,
                "neighbor_overlap": neighbor_overlap,
            },
            "evidence": evidence,
        }

    if neighbor.get("valid") and neighbor_overlap <= 0.20:
        return {
            "action": "neighbor_band",
            "headline": "Move into a fresh denominator band",
            "launch_recommended": True,
            "why": (
                f"The proposed neighboring band has {100.0 * neighbor_overlap:.1f}% prior same-model overlap.",
                f"Recent persisted yield is {evidence['recent_hits']} hit(s), so fresh coordinates are preferable to repeating covered territory.",
            ),
            "suggested": {
                "height": proposed_height,
                "height_factor": max(1, int(math.ceil(proposed_height / current_height))),
                "timeout_multiplier": 1,
                "neighbor_low": next_low,
                "neighbor_high": next_high,
                "neighbor_overlap": neighbor_overlap,
            },
            "evidence": evidence,
        }

    if evidence["completed_regions"] >= 3 or evidence["repeat_overlap"] >= 0.35:
        return {
            "action": "widen_height",
            "headline": "Push beyond the completed height ceiling",
            "launch_recommended": True,
            "why": (
                f"Same-model repeat overlap is {100.0 * evidence['repeat_overlap']:.1f}%.",
                f"The completed height ceiling is H={evidence['max_height'] or current_height}; more same-height repetition adds little new coordinate territory.",
            ),
            "suggested": {
                "height": max(current_height * 2, evidence["max_height"] * 2),
                "height_factor": 2,
                "timeout_multiplier": 2,
                "neighbor_low": next_low,
                "neighbor_high": next_high,
                "neighbor_overlap": neighbor_overlap,
            },
            "evidence": evidence,
        }

    return {
        "action": "widen_height",
        "headline": "Add height to this lightly searched exact model",
        "launch_recommended": True,
        "why": (
            f"Only {evidence['completed_regions']} completed bounded region(s) are recorded.",
            "The current history is too shallow for a diminishing-yield conclusion.",
        ),
        "suggested": {
            "height": current_height * 5,
            "height_factor": 5,
            "timeout_multiplier": 1,
            "neighbor_low": next_low,
            "neighbor_high": next_high,
            "neighbor_overlap": neighbor_overlap,
        },
        "evidence": evidence,
    }


def covering_next_attack_plan(rec, *, coverage=None):
    """Return conservative next-search guidance for one exact covering."""
    coverage = dict(coverage or {})
    local = rec.get("local_evidence") or quartic_local_evidence(
        rec.get("metadata"),
        status=rec.get("status"),
    )
    evidence = _attack_evidence(coverage, local=local)
    current_height = max(1, int(rec.get("max_height") or rec.get("height") or 100000))
    current_timeout = max(30, int(rec.get("timeout") or 60))

    if local["overall"] == "obstruction recorded":
        return {
            "action": "hold_local_obstruction",
            "headline": "Do not spend more ratpoints time until the covering obstruction is reviewed",
            "launch_recommended": False,
            "why": (
                "This covering carries explicit local-obstruction evidence.",
                "A recorded local obstruction outranks bounded search-yield heuristics.",
            ),
            "suggested": {},
            "evidence": evidence,
        }

    mapped = int(rec.get("unique_mapped_points") or coverage.get("mapped_points") or 0)
    rigorous = int(rec.get("independent_points") or coverage.get("rigorous_points") or 0)
    if mapped > rigorous:
        return {
            "action": "resolve_independence",
            "headline": "Resolve mapped covering points before widening the search",
            "launch_recommended": False,
            "why": (
                f"This covering already maps to {mapped} unique elliptic point(s).",
                f"Only {rigorous} currently carry the rigorous independent flag.",
            ),
            "suggested": {},
            "evidence": evidence,
        }

    attempts = int(coverage.get("attempts") or rec.get("search_count") or 0)
    if attempts == 0:
        return {
            "action": "initial_search",
            "headline": "Search this exact covering before deprioritizing it",
            "launch_recommended": True,
            "why": (
                "No persisted covering-search attempt exists.",
                "There is no empirical yield history yet for this exact covering.",
            ),
            "suggested": {
                "height": current_height,
                "timeout": current_timeout,
                "one_point": False,
            },
            "evidence": evidence,
        }

    if str(rec.get("outcome") or "") in {"timeout", "error"} or (
        evidence["completed_regions"] == 0 and evidence["incomplete_attempts"] > 0
    ):
        return {
            "action": "retry_incomplete",
            "headline": "Retry the incomplete covering search with more time",
            "launch_recommended": True,
            "why": (
                "The latest/aggregate covering history contains incomplete work.",
                "Incomplete attempts are not counted as completed bounded coverage.",
            ),
            "suggested": {
                "height": max(current_height, int(rec.get("height") or current_height)),
                "timeout": current_timeout * 2,
                "one_point": False,
            },
            "evidence": evidence,
        }

    if evidence["recent_hits"] > 0 or rigorous > 0:
        return {
            "action": "widen_height",
            "headline": "Widen this empirically productive covering",
            "launch_recommended": True,
            "why": (
                f"The newest persisted attempts record {evidence['recent_hits']} quartic hit(s).",
                f"The current completed height ceiling is H={evidence['max_height'] or current_height}.",
            ),
            "suggested": {
                "height": max(current_height * 2, evidence["max_height"] * 2),
                "timeout": current_timeout * 2,
                "one_point": False,
            },
            "evidence": evidence,
        }

    if evidence["completed_regions"] >= 3:
        return {
            "action": "widen_height",
            "headline": "Only widen if you want fresh height territory",
            "launch_recommended": True,
            "why": (
                f"{evidence['completed_regions']} completed bounded covering region(s) have produced no recent hits.",
                "Repeating the same height adds overlap; widening is the available fresh-coordinate move for this covering.",
            ),
            "suggested": {
                "height": max(current_height * 2, evidence["max_height"] * 2),
                "timeout": current_timeout,
                "one_point": False,
            },
            "evidence": evidence,
        }

    return {
        "action": "widen_height",
        "headline": "Add one wider bounded search before judging this covering",
        "launch_recommended": True,
        "why": (
            f"Only {evidence['completed_regions']} completed bounded region(s) are recorded.",
            "The current empirical history is still shallow.",
        ),
        "suggested": {
            "height": max(current_height * 2, evidence["max_height"] * 2),
            "timeout": current_timeout,
            "one_point": False,
        },
        "evidence": evidence,
    }


def _combined_local_state(states):
    states = {str(value) for value in states if value}
    if "obstruction recorded" in states:
        return "obstruction recorded"
    if "partial local record" in states:
        return "partial local record"
    if "soluble at all recorded places" in states:
        return "soluble at recorded places"
    return "not recorded"


def quartic_model_comparison(
    db,
    curve_id,
    *,
    searches=None,
    coverings=None,
    coverage=None,
    matrix=None,
):
    """Group only exactly identical stored coefficient models by fingerprint.

    This is not a GL2-equivalence classifier.  It is a reproducible identity
    view over the exact rational coefficient vector stored by Rank Hunter.
    """
    searches = list(searches) if searches is not None else quartic_history(db, curve_id)
    coverings = list(coverings) if coverings is not None else covering_history(db, curve_id)
    coverage = coverage or quartic_search_coverage(
        db,
        curve_id,
        searches=searches,
        coverings=coverings,
    )
    matrix = list(matrix) if matrix is not None else covering_matrix(
        db,
        curve_id,
        coverings=coverings,
    )
    model_cov = {rec["fingerprint"]: rec for rec in coverage["models"]}
    covering_cov = {int(rec["id"]): rec for rec in coverage["coverings"]}
    matrix_by_id = {int(rec["id"]): rec for rec in matrix}

    groups = {}

    def ensure(arithmetic):
        fingerprint = arithmetic["fingerprint"]
        group = groups.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "search_ids": [],
                "covering_ids": [],
                "kinds": set(),
                "statuses": set(),
                "local_states": set(),
                "I": arithmetic["I"],
                "J": arithmetic["J"],
                "polynomial_discriminant": arithmetic["polynomial_discriminant"],
                "coefficient_height": arithmetic["coefficient_height"],
                "content": arithmetic["content"],
                "primitive": arithmetic["primitive"],
                "search_jobs": 0,
                "covering_attempts": 0,
                "completed_regions": 0,
                "quartic_hits": 0,
                "mapped_point_ids": set(),
                "rigorous_point_ids": set(),
                "runtime_seconds": 0.0,
            },
        )
        return group

    seen_search_fingerprints = set()
    for rec in searches:
        arithmetic = quartic_arithmetic(
            _loads(rec["row"]["polynomial_json"], []),
            reduced=bool(rec.get("reduced")),
        )
        group = ensure(arithmetic)
        fingerprint = arithmetic["fingerprint"]
        group["search_ids"].append(int(rec["id"]))
        group["kinds"].add(str(rec["kind"]))
        group["statuses"].add(str(rec["status"]))
        group["local_states"].add(
            quartic_local_evidence(
                rec.get("metadata"),
                status=rec.get("status"),
            )["overall"]
        )
        if fingerprint not in seen_search_fingerprints:
            cov = model_cov.get(fingerprint, {})
            group["search_jobs"] += int(cov.get("jobs") or 0)
            group["completed_regions"] += int(cov.get("completed_regions") or 0)
            group["quartic_hits"] += int(cov.get("quartic_hits") or 0)
            group["runtime_seconds"] += float(cov.get("runtime_seconds") or 0.0)
            seen_search_fingerprints.add(fingerprint)
        for point in mapped_points_for_quartic(db, curve_id, rec["id"]):
            point_id = int(point["id"])
            group["mapped_point_ids"].add(point_id)
            if bool(point["rigorous_independent"]):
                group["rigorous_point_ids"].add(point_id)

    for rec in coverings:
        quartic = _loads(rec["row"]["quartic_json"], {})
        arithmetic = quartic_arithmetic(
            quartic.get("coefficients") or [],
            reduced=bool(
                rec.get("metadata", {}).get("reduced")
                or rec.get("metadata", {}).get("reduction")
            ),
        )
        group = ensure(arithmetic)
        covering_id = int(rec["id"])
        group["covering_ids"].append(covering_id)
        group["kinds"].add("exact covering")
        group["statuses"].add(str(rec["status"]))
        local = matrix_by_id.get(covering_id, {}).get("local_evidence")
        if not local:
            local = quartic_local_evidence(
                rec.get("metadata"),
                status=rec.get("status"),
            )
        group["local_states"].add(local["overall"])
        cov = covering_cov.get(covering_id, {})
        group["covering_attempts"] += int(cov.get("attempts") or 0)
        group["completed_regions"] += int(cov.get("completed_regions") or 0)
        group["quartic_hits"] += int(cov.get("quartic_hits") or 0)
        group["runtime_seconds"] += float(cov.get("runtime_seconds") or 0.0)
        for point in mapped_points_for_covering(db, curve_id, covering_id):
            point_id = int(point["id"])
            group["mapped_point_ids"].add(point_id)
            if bool(point["rigorous_independent"]):
                group["rigorous_point_ids"].add(point_id)

    result = []
    for group in groups.values():
        result.append(
            {
                "fingerprint": group["fingerprint"],
                "search_ids": tuple(sorted(group["search_ids"])),
                "covering_ids": tuple(sorted(group["covering_ids"])),
                "kinds": tuple(sorted(group["kinds"])),
                "statuses": tuple(sorted(group["statuses"])),
                "local_state": _combined_local_state(group["local_states"]),
                "I": group["I"],
                "J": group["J"],
                "polynomial_discriminant": group["polynomial_discriminant"],
                "coefficient_height": int(group["coefficient_height"]),
                "content": int(group["content"]),
                "primitive": bool(group["primitive"]),
                "search_jobs": int(group["search_jobs"]),
                "covering_attempts": int(group["covering_attempts"]),
                "completed_regions": int(group["completed_regions"]),
                "quartic_hits": int(group["quartic_hits"]),
                "mapped_points": len(group["mapped_point_ids"]),
                "rigorous_points": len(group["rigorous_point_ids"]),
                "runtime_seconds": float(group["runtime_seconds"]),
            }
        )
    result.sort(
        key=lambda rec: (
            -int(rec["rigorous_points"]),
            -int(rec["mapped_points"]),
            -int(rec["quartic_hits"]),
            rec["fingerprint"],
        )
    )
    return tuple(result)


def quartic_search_research_record(db, curve_id, rec):
    """Build provenance and a portable-data reproduction recipe for a search."""
    row = rec["row"]
    curve = db.execute("SELECT * FROM curves WHERE id=?", (int(curve_id),)).fetchone()
    coefficients = _loads(row["polynomial_json"], [])
    arithmetic = quartic_arithmetic(
        coefficients,
        reduced=bool(rec.get("reduced")),
    )
    qpoints = db.execute(
        """SELECT * FROM quartic_points
           WHERE search_id=?
           ORDER BY id""",
        (int(rec["id"]),),
    ).fetchall()
    mapped = mapped_points_for_quartic(db, curve_id, rec["id"])
    rigorous = [point for point in mapped if bool(point["rigorous_independent"])]

    provenance = (
        {
            "stage": "exact quartic model",
            "record": arithmetic["fingerprint"],
            "state": f"degree {arithmetic['degree']}",
            "count": 1,
        },
        {
            "stage": "persisted search",
            "record": f"S{int(rec['id'])}",
            "state": str(rec["status"]),
            "count": 1,
        },
        {
            "stage": "exact quartic hits",
            "record": "quartic_points",
            "state": "exact stored points",
            "count": len(qpoints),
        },
        {
            "stage": "mapped E(Q)",
            "record": "Point Ledger",
            "state": "unique search_ref points",
            "count": len(mapped),
        },
        {
            "stage": "rigorous witnesses",
            "record": "Point Ledger",
            "state": "rigorous_independent=true",
            "count": len(rigorous),
        },
    )

    recipe = {
        "schema": "rank42.quartic_reproduction.v1",
        "object": "quartic_search",
        "curve": {
            "id": int(curve_id),
            "family": None if curve is None else curve["family"],
            "parameter": None if curve is None else curve["parameter"],
            "a_invariants_json": None if curve is None else curve["a_invariants_json"],
        },
        "quartic": {
            "fingerprint": arithmetic["fingerprint"],
            "coefficients": list(arithmetic["coefficients"]),
            "integer_coefficients": list(arithmetic["integer_coefficients"]),
            "y_scale": int(arithmetic["y_scale"]),
            "degree": int(arithmetic["degree"]),
            "I": arithmetic["I"],
            "J": arithmetic["J"],
            "polynomial_discriminant": arithmetic["polynomial_discriminant"],
        },
        "search": {
            "id": int(rec["id"]),
            "kind": str(rec["kind"]),
            "status": str(rec["status"]),
            "height": int(rec["height"]),
            "denominator_low": rec["denominator_low"],
            "denominator_high": rec["denominator_high"],
            "hole_label": rec["hole_label"],
            "ratpoints_executable_recorded": row["ratpoints_executable"],
            "runtime_seconds": row["runtime"],
            "timeout_seconds": None,
            "metadata": dict(rec.get("metadata") or {}),
        },
        "results": {
            "quartic_point_ids": [int(point["id"]) for point in qpoints],
            "mapped_point_ids": [int(point["id"]) for point in mapped],
            "rigorous_point_ids": [int(point["id"]) for point in rigorous],
        },
        "replay": {
            "module": "rank42.quartic_search",
            "requires": ["science Python", "rank42.db path", "ratpoints executable", "timeout"],
            "argv_template": [
                "<science-python>",
                "-m",
                "rank42.quartic_search",
                "--db",
                "<rank42.db>",
                "--curve-id",
                str(int(curve_id)),
                "--coefficients",
                ",".join(arithmetic["coefficients"]),
                "--height",
                str(int(rec["height"])),
                "--timeout",
                "<timeout>",
                "--source-search-id",
                str(int(rec["id"])),
                *(
                    ["--denominator-low", str(int(rec["denominator_low"]))]
                    if rec["denominator_low"] is not None
                    else []
                ),
                *(
                    ["--denominator-high", str(int(rec["denominator_high"]))]
                    if rec["denominator_high"] is not None
                    else []
                ),
                *(
                    ["--family", str(row["family"])]
                    if row["family"]
                    else []
                ),
                *(
                    ["--parameter", str(row["parameter"])]
                    if row["parameter"] is not None
                    else []
                ),
                *(
                    ["--hole-label", str(row["hole_label"])]
                    if row["hole_label"]
                    else []
                ),
                "--ratpoints",
                "<ratpoints>",
            ],
            "note": "Original timeout is not persisted on quartic_searches; choose it explicitly when replaying. Machine-specific paths are placeholders.",
        },
    }
    return {"provenance": provenance, "recipe": recipe}


def covering_research_record(db, curve_id, rec):
    """Build provenance and reproduction data for one exact stored covering."""
    row = rec["row"]
    curve = db.execute("SELECT * FROM curves WHERE id=?", (int(curve_id),)).fetchone()
    quartic = _loads(row["quartic_json"], {})
    arithmetic = quartic_arithmetic(
        quartic.get("coefficients") or [],
        reduced=bool(
            rec.get("metadata", {}).get("reduced")
            or rec.get("metadata", {}).get("reduction")
        ),
    )
    attempts = db.execute(
        """SELECT * FROM covering_search_attempts
           WHERE covering_id=?
           ORDER BY id""",
        (int(rec["id"]),),
    ).fetchall()
    mapped = mapped_points_for_covering(db, curve_id, rec["id"])
    rigorous = [point for point in mapped if bool(point["rigorous_independent"])]
    total_hits = sum(int(attempt["ratpoints_hits"] or 0) for attempt in attempts)

    provenance = (
        {
            "stage": "exact covering",
            "record": f"C{int(rec['id'])}",
            "state": str(rec["status"]),
            "count": 1,
        },
        {
            "stage": "exact quartic model",
            "record": arithmetic["fingerprint"],
            "state": f"degree {arithmetic['degree']}",
            "count": 1,
        },
        {
            "stage": "search attempts",
            "record": "covering_search_attempts",
            "state": rec["outcome"] or "unsearched",
            "count": len(attempts),
        },
        {
            "stage": "quartic hits",
            "record": "ratpoints hits",
            "state": "attempt aggregate",
            "count": total_hits,
        },
        {
            "stage": "mapped E(Q)",
            "record": "Point Ledger",
            "state": "covering provenance",
            "count": len(mapped),
        },
        {
            "stage": "rigorous witnesses",
            "record": "Point Ledger",
            "state": "rigorous_independent=true",
            "count": len(rigorous),
        },
    )

    latest = attempts[-1] if attempts else None
    recipe = {
        "schema": "rank42.quartic_reproduction.v1",
        "object": "exact_covering",
        "curve": {
            "id": int(curve_id),
            "family": None if curve is None else curve["family"],
            "parameter": None if curve is None else curve["parameter"],
            "a_invariants_json": None if curve is None else curve["a_invariants_json"],
        },
        "covering": {
            "id": int(rec["id"]),
            "covering_key": row["covering_key"],
            "schema_version": row["schema_version"],
            "lattice_id": row["lattice_id"],
            "hole_id": row["hole_id"],
            "status": str(rec["status"]),
            "quartic": quartic,
            "map": {"x": row["map_x"], "y": row["map_y"]},
            "metadata": dict(rec.get("metadata") or {}),
        },
        "quartic": {
            "fingerprint": arithmetic["fingerprint"],
            "I": arithmetic["I"],
            "J": arithmetic["J"],
            "polynomial_discriminant": arithmetic["polynomial_discriminant"],
        },
        "latest_attempt": None if latest is None else {
            "id": int(latest["id"]),
            "height": int(latest["height"]),
            "timeout_seconds": int(latest["timeout_seconds"]),
            "backend": latest["backend"],
            "one_point": bool(latest["one_point"]),
            "outcome": latest["outcome"],
            "runtime_seconds": latest["runtime_seconds"],
        },
        "results": {
            "attempt_ids": [int(attempt["id"]) for attempt in attempts],
            "mapped_point_ids": [int(point["id"]) for point in mapped],
            "rigorous_point_ids": [int(point["id"]) for point in rigorous],
        },
        "replay": {
            "module": "rank42.quartic_workbench_runner",
            "mode": "covering",
            "requires": ["science Python", "rank42.db path", "ratpoints executable"],
            "argv_template": [
                "<science-python>",
                "-m",
                "rank42.quartic_workbench_runner",
                "--db",
                "<rank42.db>",
                "--mode",
                "covering",
                "--covering-id",
                str(int(rec["id"])),
                "--height",
                str(
                    int(latest["height"])
                    if latest is not None
                    else max(1, int(rec.get("height") or 100000))
                ),
                "--timeout",
                str(int(latest["timeout_seconds"])) if latest is not None else "<timeout>",
                *(
                    ["--one-point"]
                    if latest is not None and bool(latest["one_point"])
                    else []
                ),
                "--ratpoints",
                "<ratpoints>",
            ],
            "note": "Exact quartic and exact map are persisted; machine-specific paths are placeholders.",
        },
    }
    return {"provenance": provenance, "recipe": recipe}


def _quartic_points_by_search(db, curve_id):
    result = {}
    for point in db.execute(
        """SELECT * FROM points
           WHERE curve_id=? AND search_ref LIKE 'quartic:%'
           ORDER BY id""",
        (int(curve_id),),
    ).fetchall():
        search_ref = str(point["search_ref"] or "")
        suffix = search_ref.split(":", 1)[1] if ":" in search_ref else ""
        if suffix.isdigit():
            result.setdefault(int(suffix), {})[int(point["id"])] = point
    return result


def quartic_coverage_chart_data(db, curve_id, *, searches=None):
    """Return exact requested/completed rectangles for the proposal-style coverage map."""
    searches = list(searches) if searches is not None else quartic_history(db, curve_id)
    points_by_search = _quartic_points_by_search(db, curve_id)
    rows = []
    for rec in searches:
        region = _effective_search_region(
            rec["height"],
            rec["denominator_low"],
            rec["denominator_high"],
        )
        if region is None:
            continue
        mapped = points_by_search.get(int(rec["id"]), {})
        rigorous = sum(bool(point["rigorous_independent"]) for point in mapped.values())
        low = int(region["denominator_low"])
        high = int(region["denominator_high"])
        rows.append(
            {
                "search_id": int(rec["id"]),
                "fingerprint": _search_fingerprint(rec),
                "kind": str(rec["kind"]),
                "status": str(rec["status"]),
                "coverage_state": "completed" if rec["status"] == "done" else "incomplete",
                "denominator_low": low,
                "denominator_high": high,
                "denominator_mid": math.sqrt(float(low) * float(high)),
                "height_low": 1,
                "height_high": int(region["height"]),
                "quartic_hits": int(rec["quartic_points"]),
                "mapped_points": len(mapped),
                "rigorous_points": rigorous,
                "result_state": (
                    "rigorous witness"
                    if rigorous
                    else ("mapped point" if mapped else ("quartic hit" if int(rec["quartic_points"]) else "no hit"))
                ),
            }
        )
    return tuple(rows)


def _decade_bins(max_value):
    max_value = max(1, int(max_value or 1))
    result = []
    low = 1
    index = 0
    while low <= max_value:
        high = min(max_value, low * 10 - 1)
        result.append(
            {
                "index": index,
                "low": int(low),
                "high": int(high),
                "label": (
                    f"{low}"
                    if low == high
                    else f"{low:g}–{high:g}"
                ),
            }
        )
        low *= 10
        index += 1
    return tuple(result)


def _bin_index(value, bins):
    value = max(1, float(value or 1))
    for item in bins:
        if float(item["low"]) <= value <= float(item["high"]):
            return int(item["index"])
    return int(bins[-1]["index"]) if bins else 0


def quartic_coverage_density(rows, *, fingerprint=None, kind=None):
    """Bin persisted search rectangles into readable log-decade coverage cells.

    A completed search contributes to every denominator/height decade cell that
    its bounded rectangle touches. Incomplete searches are counted separately.
    Cell density is therefore descriptive search effort, never mathematical
    exhaustion or a rational-point probability.
    """
    all_rows = [dict(row) for row in (rows or ())]
    fingerprints = tuple(sorted({str(row["fingerprint"]) for row in all_rows}))
    kinds = tuple(sorted({str(row["kind"]) for row in all_rows}))
    filtered = [
        row
        for row in all_rows
        if (fingerprint in (None, "") or str(row["fingerprint"]) == str(fingerprint))
        and (kind in (None, "") or str(row["kind"]) == str(kind))
    ]
    if not filtered:
        return {
            "cells": (),
            "markers": (),
            "fingerprints": fingerprints,
            "kinds": kinds,
            "summary": {
                "completed_searches": 0,
                "incomplete_searches": 0,
                "max_height": 0,
                "max_denominator": 0,
                "blank_cells": 0,
                "total_cells": 0,
                "densest_label": "—",
                "densest_searches": 0,
            },
        }

    max_height = max(max(1, int(row["height_high"])) for row in filtered)
    max_denominator = max(max(1, int(row["denominator_high"])) for row in filtered)
    height_bins = _decade_bins(max_height)
    denominator_bins = _decade_bins(max_denominator)

    cells = []
    for hbin in height_bins:
        for dbin in denominator_bins:
            completed = []
            incomplete = []
            for row in filtered:
                denominator_touches = not (
                    int(row["denominator_high"]) < int(dbin["low"])
                    or int(row["denominator_low"]) > int(dbin["high"])
                )
                height_touches = int(row["height_high"]) >= int(hbin["low"])
                if not denominator_touches or not height_touches:
                    continue
                if row["coverage_state"] == "completed":
                    completed.append(row)
                else:
                    incomplete.append(row)

            models = {str(row["fingerprint"]) for row in completed}
            hit_searches = [row for row in completed if int(row["quartic_hits"]) > 0]
            mapped_searches = [row for row in completed if int(row["mapped_points"]) > 0]
            rigorous_searches = [row for row in completed if int(row["rigorous_points"]) > 0]
            cells.append(
                {
                    "denominator_index": int(dbin["index"]),
                    "denominator_label": dbin["label"],
                    "denominator_low": int(dbin["low"]),
                    "denominator_high": int(dbin["high"]),
                    "height_index": int(hbin["index"]),
                    "height_label": hbin["label"],
                    "height_low": int(hbin["low"]),
                    "height_high": int(hbin["high"]),
                    "completed_searches": len(completed),
                    "distinct_models": len(models),
                    "hit_bearing_searches": len(hit_searches),
                    "mapped_result_searches": len(mapped_searches),
                    "rigorous_result_searches": len(rigorous_searches),
                    "incomplete_requests": len(incomplete),
                }
            )

    marker_groups = {}
    for row in filtered:
        if (
            int(row["quartic_hits"]) <= 0
            and int(row["mapped_points"]) <= 0
            and int(row["rigorous_points"]) <= 0
        ):
            continue
        d_index = _bin_index(row["denominator_mid"], denominator_bins)
        h_index = _bin_index(row["height_high"], height_bins)
        key = (d_index, h_index)
        rec = marker_groups.setdefault(
            key,
            {
                "denominator_index": d_index,
                "denominator_label": denominator_bins[d_index]["label"],
                "height_index": h_index,
                "height_label": height_bins[h_index]["label"],
                "searches": 0,
                "quartic_hits": 0,
                "mapped_points": 0,
                "rigorous_points": 0,
            },
        )
        rec["searches"] += 1
        rec["quartic_hits"] += int(row["quartic_hits"])
        rec["mapped_points"] += int(row["mapped_points"])
        rec["rigorous_points"] += int(row["rigorous_points"])

    markers = []
    for rec in marker_groups.values():
        rec = dict(rec)
        rec["result_state"] = (
            "rigorous witness"
            if rec["rigorous_points"]
            else ("mapped point" if rec["mapped_points"] else "quartic hit")
        )
        markers.append(rec)
    markers.sort(key=lambda rec: (rec["height_index"], rec["denominator_index"]))

    densest = max(
        cells,
        key=lambda rec: (
            int(rec["completed_searches"]),
            int(rec["distinct_models"]),
            int(rec["height_index"]),
            int(rec["denominator_index"]),
        ),
    )
    blank_cells = sum(int(rec["completed_searches"]) == 0 for rec in cells)
    return {
        "cells": tuple(cells),
        "markers": tuple(markers),
        "fingerprints": fingerprints,
        "kinds": kinds,
        "summary": {
            "completed_searches": sum(row["coverage_state"] == "completed" for row in filtered),
            "incomplete_searches": sum(row["coverage_state"] != "completed" for row in filtered),
            "max_height": int(max_height),
            "max_denominator": int(max_denominator),
            "blank_cells": int(blank_cells),
            "total_cells": len(cells),
            "densest_label": (
                f"H {densest['height_label']} · D {densest['denominator_label']}"
                if int(densest["completed_searches"])
                else "—"
            ),
            "densest_searches": int(densest["completed_searches"]),
        },
    }


def quartic_model_yield_series(db, curve_id, rec, *, searches=None):
    """Cumulative exact-model yield as completed search height increases."""
    searches = list(searches) if searches is not None else quartic_history(db, curve_id)
    fingerprint = _search_fingerprint(rec)
    matching = [
        item
        for item in searches
        if item["status"] == "done" and _search_fingerprint(item) == fingerprint
    ]
    matching.sort(key=lambda item: (int(item["height"]), int(item["id"])))
    points_by_search = _quartic_points_by_search(db, curve_id)

    grouped = {}
    for item in matching:
        grouped.setdefault(int(item["height"]), []).append(item)

    cumulative_hits = 0
    cumulative_runtime = 0.0
    cumulative_attempts = 0
    mapped_ids = set()
    rigorous_ids = set()
    result = []
    for height in sorted(grouped):
        for item in grouped[height]:
            cumulative_attempts += 1
            cumulative_hits += int(item["quartic_points"])
            cumulative_runtime += float(item["runtime"] or 0.0)
            for point_id, point in points_by_search.get(int(item["id"]), {}).items():
                mapped_ids.add(int(point_id))
                if bool(point["rigorous_independent"]):
                    rigorous_ids.add(int(point_id))
        result.append(
            {
                "height": int(height),
                "cumulative_hits": int(cumulative_hits),
                "cumulative_mapped": len(mapped_ids),
                "cumulative_rigorous": len(rigorous_ids),
                "cumulative_runtime_seconds": float(cumulative_runtime),
                "attempts": int(cumulative_attempts),
                "fingerprint": fingerprint,
            }
        )
    return tuple(result)


def covering_yield_series(db, covering_id):
    """Cumulative covering attempt yield by configured search height."""
    attempts = db.execute(
        """SELECT * FROM covering_search_attempts
           WHERE covering_id=?
           ORDER BY height,id""",
        (int(covering_id),),
    ).fetchall()
    grouped = {}
    for attempt in attempts:
        grouped.setdefault(int(attempt["height"] or 0), []).append(attempt)

    cumulative_hits = 0
    cumulative_mapped_reports = 0
    cumulative_runtime = 0.0
    result = []
    for height in sorted(grouped):
        for attempt in grouped[height]:
            cumulative_hits += int(attempt["ratpoints_hits"] or 0)
            cumulative_mapped_reports += int(attempt["mapped_points"] or 0)
            cumulative_runtime += float(attempt["runtime_seconds"] or 0.0)
        result.append(
            {
                "height": int(height),
                "cumulative_hits": int(cumulative_hits),
                "cumulative_mapped_reports": int(cumulative_mapped_reports),
                "cumulative_runtime_seconds": float(cumulative_runtime),
                "attempts_at_or_below_height": sum(
                    len(grouped[value]) for value in grouped if value <= height
                ),
            }
        )
    return tuple(result)


def _yield_recap(
    rows,
    *,
    hit_key,
    mapped_key,
    rigorous_key=None,
    attempts_key=None,
):
    rows = [dict(row) for row in (rows or ())]
    if not rows:
        return {
            "history_points": 0,
            "trend_ready": False,
            "max_height": 0,
            "hits": 0,
            "mapped": 0,
            "rigorous": 0,
            "attempts": 0,
            "runtime_seconds": 0.0,
            "last_yield_height": None,
            "recent_hit_gain": 0,
            "recent_mapped_gain": 0,
            "recent_rigorous_gain": 0,
            "state": "no completed yield history",
        }

    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None
    hits = int(latest.get(hit_key) or 0)
    mapped = int(latest.get(mapped_key) or 0)
    rigorous = int(latest.get(rigorous_key) or 0) if rigorous_key else 0
    attempts = (
        int(latest.get(attempts_key) or 0)
        if attempts_key
        else len(rows)
    )
    runtime_seconds = float(latest.get("cumulative_runtime_seconds") or 0.0)

    last_yield_height = None
    prev_hits = prev_mapped = prev_rigorous = 0
    for row in rows:
        row_hits = int(row.get(hit_key) or 0)
        row_mapped = int(row.get(mapped_key) or 0)
        row_rigorous = int(row.get(rigorous_key) or 0) if rigorous_key else 0
        if (
            row_hits > prev_hits
            or row_mapped > prev_mapped
            or row_rigorous > prev_rigorous
        ):
            last_yield_height = int(row["height"])
        prev_hits = row_hits
        prev_mapped = row_mapped
        prev_rigorous = row_rigorous

    if previous is None:
        recent_hit_gain = hits
        recent_mapped_gain = mapped
        recent_rigorous_gain = rigorous
    else:
        recent_hit_gain = hits - int(previous.get(hit_key) or 0)
        recent_mapped_gain = mapped - int(previous.get(mapped_key) or 0)
        recent_rigorous_gain = (
            rigorous - int(previous.get(rigorous_key) or 0)
            if rigorous_key
            else 0
        )

    max_height = int(latest["height"])
    if len(rows) == 1:
        state = "single completed height — not enough history for a trend"
    elif hits == 0 and mapped == 0 and rigorous == 0:
        state = f"{len(rows)} completed height levels · no yield recorded"
    elif last_yield_height == max_height:
        state = f"new yield recorded at current max H={max_height}"
    elif last_yield_height is not None:
        state = f"no new yield above H={last_yield_height}"
    else:
        state = f"{len(rows)} completed height levels · no new yield recorded"

    return {
        "history_points": len(rows),
        "trend_ready": len(rows) >= 2,
        "max_height": max_height,
        "hits": hits,
        "mapped": mapped,
        "rigorous": rigorous,
        "attempts": attempts,
        "runtime_seconds": runtime_seconds,
        "last_yield_height": last_yield_height,
        "recent_hit_gain": int(recent_hit_gain),
        "recent_mapped_gain": int(recent_mapped_gain),
        "recent_rigorous_gain": int(recent_rigorous_gain),
        "state": state,
    }


def quartic_model_yield_recap(rows):
    return _yield_recap(
        rows,
        hit_key="cumulative_hits",
        mapped_key="cumulative_mapped",
        rigorous_key="cumulative_rigorous",
        attempts_key="attempts",
    )


def covering_yield_recap(rows):
    return _yield_recap(
        rows,
        hit_key="cumulative_hits",
        mapped_key="cumulative_mapped_reports",
        attempts_key="attempts_at_or_below_height",
    )


def _metadata_reduced_flag(metadata):
    metadata = dict(metadata or {})
    transform = metadata.get("transform")
    return bool(
        metadata.get("reduced")
        or metadata.get("reduction")
        or (isinstance(transform, dict) and transform.get("reduced"))
    )


def family_quartic_signature_matrix(db, family, *, max_models=5000):
    """Exploratory feature prevalence by authoritative rigorous lower bound.

    Rows are exact curve/model identities, deduplicated by (curve_id,fingerprint).
    The chart is descriptive pattern mining, not a causal or rank-prediction model.
    """
    family = str(family or "")
    max_models = max(1, int(max_models))
    raw = []

    search_rows = db.execute(
        """SELECT qs.curve_id,qs.polynomial_json,qs.metadata_json
           FROM quartic_searches qs
           JOIN curves c ON c.id=qs.curve_id
           WHERE c.family=?
           ORDER BY qs.id DESC
           LIMIT ?""",
        (family, max_models + 1),
    ).fetchall()
    for row in search_rows[:max_models]:
        metadata = _loads(row["metadata_json"], {})
        coefficients = _loads(row["polynomial_json"], [])
        if coefficients:
            raw.append(
                {
                    "curve_id": int(row["curve_id"]),
                    "coefficients": coefficients,
                    "reduced": _metadata_reduced_flag(metadata),
                }
            )

    remaining = max(0, max_models - len(raw))
    covering_rows = []
    if remaining:
        covering_rows = db.execute(
            """SELECT cv.curve_id,cv.quartic_json,cv.metadata_json
               FROM coverings cv
               JOIN curves c ON c.id=cv.curve_id
               WHERE c.family=?
               ORDER BY cv.id DESC
               LIMIT ?""",
            (family, remaining + 1),
        ).fetchall()
        for row in covering_rows[:remaining]:
            metadata = _loads(row["metadata_json"], {})
            quartic = _loads(row["quartic_json"], {})
            coefficients = quartic.get("coefficients") or []
            if coefficients:
                raw.append(
                    {
                        "curve_id": int(row["curve_id"]),
                        "coefficients": coefficients,
                        "reduced": _metadata_reduced_flag(metadata),
                    }
                )

    truncated = len(search_rows) > max_models or len(covering_rows) > remaining
    unique = {}
    for item in raw:
        arithmetic = quartic_arithmetic(
            item["coefficients"],
            reduced=bool(item["reduced"]),
        )
        unique[(int(item["curve_id"]), arithmetic["fingerprint"])] = {
            "curve_id": int(item["curve_id"]),
            "arithmetic": arithmetic,
        }

    curve_ids = sorted({curve_id for curve_id, _ in unique})
    states = curve_research_state_map(db, curve_ids=curve_ids) if curve_ids else {}

    features = (
        ("I > 0", lambda a: a["I"] is not None and _fraction(a["I"]) > 0),
        ("I < 0", lambda a: a["I"] is not None and _fraction(a["I"]) < 0),
        ("J > 0", lambda a: a["J"] is not None and _fraction(a["J"]) > 0),
        ("J < 0", lambda a: a["J"] is not None and _fraction(a["J"]) < 0),
        ("Δ > 0", lambda a: a["polynomial_discriminant"] is not None and _fraction(a["polynomial_discriminant"]) > 0),
        ("Δ < 0", lambda a: a["polynomial_discriminant"] is not None and _fraction(a["polynomial_discriminant"]) < 0),
        ("Primitive", lambda a: bool(a["primitive"])),
        ("Reduced", lambda a: bool(a["reduced"])),
    )

    models_by_rank = {}
    for item in unique.values():
        state = states.get(int(item["curve_id"]))
        if state is None:
            continue
        rank_lower = int(state["rigorous_lower"])
        models_by_rank.setdefault(rank_lower, []).append(item["arithmetic"])

    rows = []
    for rank_lower in sorted(models_by_rank):
        models = models_by_rank[rank_lower]
        total = len(models)
        for feature, predicate in features:
            count = sum(bool(predicate(arithmetic)) for arithmetic in models)
            rows.append(
                {
                    "rank_lower": int(rank_lower),
                    "rank_label": f"≥{int(rank_lower)}",
                    "feature": feature,
                    "models_with_feature": int(count),
                    "models_total": int(total),
                    "prevalence_pct": (100.0 * float(count) / float(total)) if total else 0.0,
                }
            )

    return {
        "family": family,
        "rows": tuple(rows),
        "models": len(unique),
        "curves": len(curve_ids),
        "rank_buckets": len(models_by_rank),
        "truncated": bool(truncated),
        "max_models": max_models,
    }


def quartic_curve_summary(db, curve_id, *, searches=None, coverings=None):
    """Return a conservative curve-level Quartics projection.

    This is intentionally descriptive.  A mapped point is counted once by
    Point Ledger id, and "independent" means the persisted rigorous flag is
    already present; this helper never infers rank from search provenance.
    """
    searches = list(searches) if searches is not None else quartic_history(db, curve_id)
    coverings = list(coverings) if coverings is not None else covering_history(db, curve_id)

    point_by_id = {}
    for rec in searches:
        for point in mapped_points_for_quartic(db, curve_id, rec["id"]):
            point_by_id[int(point["id"])] = point
    for rec in coverings:
        for point in mapped_points_for_covering(db, curve_id, rec["id"]):
            point_by_id[int(point["id"])] = point

    covering_attempts = db.execute(
        """SELECT COUNT(*) AS n
           FROM covering_search_attempts a
           JOIN coverings c ON c.id=a.covering_id
           WHERE c.curve_id=?""",
        (int(curve_id),),
    ).fetchone()

    search_quartic_hits = sum(int(rec["quartic_points"]) for rec in searches)
    covering_quartic_hits = sum(int(rec["ratpoints_hits"]) for rec in coverings)
    mapped_points = list(point_by_id.values())
    rigorous_independent = sum(
        bool(point["rigorous_independent"])
        for point in mapped_points
    )
    local_evidence = [
        quartic_local_evidence(
            rec.get("metadata") or {},
            status=rec.get("status"),
        )
        for rec in coverings
    ]
    locally_soluble_coverings = sum(
        rec["overall"] in {
            "everywhere locally soluble",
            "soluble at all recorded places",
        }
        for rec in local_evidence
    )
    locally_obstructed_coverings = sum(
        str(rec["overall"]).startswith("obstruct")
        or str(rec["overall"]) == "obstruction recorded"
        for rec in local_evidence
    )
    local_record_coverings = sum(
        bool(rec["recorded"])
        for rec in local_evidence
    )

    return {
        "coverings": len(coverings),
        "locally_soluble_coverings": int(locally_soluble_coverings),
        "locally_obstructed_coverings": int(locally_obstructed_coverings),
        "local_record_coverings": int(local_record_coverings),
        "persisted_searches": len(searches),
        "covering_attempts": int(covering_attempts["n"] or 0),
        "search_jobs": len(searches) + int(covering_attempts["n"] or 0),
        "timeout_or_error": sum(
            rec["status"] in {"timeout", "error"} for rec in searches
        ),
        "search_quartic_hits": search_quartic_hits,
        "covering_quartic_hits": covering_quartic_hits,
        "quartic_hits": search_quartic_hits + covering_quartic_hits,
        "mapped_points": len(mapped_points),
        "rigorous_independent_points": rigorous_independent,
        "mapped_point_ids": tuple(sorted(point_by_id)),
        "searches_with_hits": sum(
            int(rec["quartic_points"]) > 0 for rec in searches
        ),
    }


def _discovery_matches_covering(row, covering_id):
    covering_id = int(covering_id)
    metadata = _loads(row["metadata_json"], {})
    try:
        if int(metadata.get("covering_id")) == covering_id:
            return True
    except (TypeError, ValueError):
        pass

    ref = str(row["search_ref"] or "")
    direct = f"covering:{covering_id}"
    if ref == direct or ref.startswith(direct + ":"):
        return True
    if ":exact-covering:" in ref and ref.rsplit(":", 1)[-1] == str(covering_id):
        return True
    return False


def covering_point_provenance(db, curve_id, covering):
    """Classify current covering-associated points using immutable evidence when available.

    Current point search_ref / metadata establishes association only. Original
    discovery is credited to a covering only when an immutable point-discovery
    observation explicitly records that covering and says the point was not
    pre-existing. Legacy ambiguity remains explicit.
    """
    rec = dict(covering)
    row = rec.get("row")
    covering_id = int(rec["id"])
    covering_created_at = None if row is None else str(row["created_at"] or "")
    mapped = list(mapped_points_for_covering(db, curve_id, covering_id))
    if not mapped:
        return tuple()

    point_ids = [int(point["id"]) for point in mapped]
    placeholders = ",".join("?" for _ in point_ids)
    discovery_rows = db.execute(
        f"""SELECT * FROM point_discoveries
            WHERE curve_id=? AND point_id IN ({placeholders})
            ORDER BY created_at,id""",
        [int(curve_id), *point_ids],
    ).fetchall()
    discoveries_by_point = {}
    for discovery in discovery_rows:
        discoveries_by_point.setdefault(int(discovery["point_id"]), []).append(discovery)

    result = []
    for point in mapped:
        point_id = int(point["id"])
        discoveries = list(discoveries_by_point.get(point_id, ()))
        covering_events = [
            discovery
            for discovery in discoveries
            if _discovery_matches_covering(discovery, covering_id)
        ]
        earliest_covering_event = covering_events[0] if covering_events else None
        earliest_covering_time = (
            str(earliest_covering_event["created_at"] or "")
            if earliest_covering_event is not None
            else ""
        )
        earlier_other_discovery = False
        if earliest_covering_time:
            earlier_other_discovery = any(
                str(discovery["created_at"] or "")
                and str(discovery["created_at"]) < earliest_covering_time
                and not _discovery_matches_covering(discovery, covering_id)
                for discovery in discoveries
            )

        point_created_at = str(point["created_at"] or "")
        point_predates_covering = bool(
            point_created_at
            and covering_created_at
            and point_created_at < covering_created_at
        )

        explicit_preexisting = None
        for discovery in covering_events:
            metadata = _loads(discovery["metadata_json"], {})
            if "preexisting_point" in metadata:
                value = metadata["preexisting_point"]
                if isinstance(value, bool):
                    explicit_preexisting = value
                elif value in (0, 1):
                    explicit_preexisting = bool(value)
                break

        if covering_events and (
            explicit_preexisting is True
            or earlier_other_discovery
            or point_predates_covering
        ):
            origin = "rediscovered here"
            evidence = "immutable covering observation + earlier point evidence"
        elif covering_events and explicit_preexisting is False:
            origin = "discovered here"
            evidence = "immutable covering observation recorded point as new"
        elif not covering_events and point_predates_covering:
            origin = "pre-existing"
            evidence = "Point Ledger row predates covering creation"
        elif covering_events:
            origin = "unknown legacy origin"
            evidence = "covering observation exists but legacy event did not record new-vs-existing"
        elif point_created_at and covering_created_at:
            origin = "current attribution only"
            evidence = "current covering association; no immutable covering discovery event"
        else:
            origin = "unknown legacy origin"
            evidence = "insufficient historical timestamps/provenance"

        result.append(
            {
                "point": point,
                "point_id": point_id,
                "origin": origin,
                "evidence": evidence,
                "point_created_at": point_created_at or None,
                "covering_created_at": covering_created_at or None,
                "covering_discovery_events": len(covering_events),
                "discovery_events": len(discoveries),
            }
        )
    result.sort(key=lambda item: int(item["point_id"]), reverse=True)
    return tuple(result)


def covering_point_provenance_summary(records):
    records = list(records or ())
    counts = {
        "discovered_here_points": 0,
        "rediscovered_points": 0,
        "preexisting_points": 0,
        "current_attribution_only_points": 0,
        "unknown_origin_points": 0,
    }
    mapping = {
        "discovered here": "discovered_here_points",
        "rediscovered here": "rediscovered_points",
        "pre-existing": "preexisting_points",
        "current attribution only": "current_attribution_only_points",
        "unknown legacy origin": "unknown_origin_points",
    }
    for record in records:
        key = mapping[str(record["origin"])]
        counts[key] += 1
    counts["origin_resolved_points"] = (
        counts["discovered_here_points"]
        + counts["rediscovered_points"]
        + counts["preexisting_points"]
    )
    counts["origin_unclear_points"] = (
        counts["current_attribution_only_points"]
        + counts["unknown_origin_points"]
    )
    counts["provenance_total_points"] = len(records)
    return counts

def covering_matrix(db, curve_id, *, coverings=None):
    """Aggregate exact-covering search state without claiming exhaustion."""
    coverings = list(coverings) if coverings is not None else covering_history(db, curve_id)
    result = []
    for rec in coverings:
        stats = db.execute(
            """SELECT
                   COUNT(*) AS search_count,
                   MAX(height) AS max_height,
                   COALESCE(SUM(ratpoints_hits), 0) AS quartic_hits
               FROM covering_search_attempts
               WHERE covering_id=?""",
            (int(rec["id"]),),
        ).fetchone()

        point_provenance = covering_point_provenance(
            db,
            curve_id,
            rec,
        )
        mapped_by_id = {
            int(item["point_id"]): item["point"]
            for item in point_provenance
        }
        provenance_summary = covering_point_provenance_summary(point_provenance)
        height_summary = point_height_summary(mapped_by_id.values())
        independent = sum(
            bool(point["rigorous_independent"])
            for point in mapped_by_id.values()
        )
        unresolved_points = sum(
            bool(point["exact_verified"])
            and not bool(point["rigorous_independent"])
            and str(point["independence_status"] or "unknown")
                in {"unknown", "numerical_novel", "inconclusive"}
            for point in mapped_by_id.values()
        )
        search_count = int(stats["search_count"] or 0)
        quartic_hits = int(stats["quartic_hits"] or 0)
        mapped_count = len(mapped_by_id)

        local_evidence = quartic_local_evidence(
            rec.get("metadata"),
            status=rec.get("status"),
        )
        if str(rec["status"] or "") == "locally_obstructed":
            workbench_status = "local obstruction"
        elif independent:
            workbench_status = "rigorous independent point"
        elif mapped_count:
            workbench_status = "mapped point"
        elif quartic_hits:
            workbench_status = "quartic hit"
        elif search_count == 0:
            workbench_status = "unsearched"
        elif str(rec["outcome"] or "") in {"timeout", "error"}:
            workbench_status = "incomplete"
        else:
            workbench_status = "no mapped point yet"

        item = dict(rec)
        item.update(
            {
                "search_count": search_count,
                "max_height": int(stats["max_height"] or rec["height"] or 0),
                "total_quartic_hits": quartic_hits,
                "unique_mapped_points": mapped_count,
                "independent_points": independent,
                "unresolved_points": unresolved_points,
                "point_height_summary": height_summary,
                "best_point_log10_x_height": height_summary["minimum_log10_x_height"],
                "median_point_log10_x_height": height_summary["median_log10_x_height"],
                **provenance_summary,
                "point_provenance": point_provenance,
                "workbench_status": workbench_status,
                "local_evidence": local_evidence,
                "local_state": local_evidence["overall"],
            }
        )
        result.append(item)
    return result


def covering_matrix_view(rows, *, show="All", order_by="Default"):
    """Return a filtered/sorted covering matrix view without changing research state."""
    rows = list(rows or ())
    show = str(show or "All")
    order_by = str(order_by or "Default")

    def keep(rec):
        mapped = int(rec.get("unique_mapped_points") or 0)
        unclear = int(rec.get("origin_unclear_points") or 0)
        if show == "All":
            return True
        if show == "Productive":
            return bool(
                int(rec.get("total_quartic_hits") or 0)
                or mapped
                or int(rec.get("independent_points") or 0)
            )
        if show == "Has unresolved candidates":
            return int(rec.get("unresolved_points") or 0) > 0
        if show == "Local obstruction":
            return str(rec.get("workbench_status") or "") == "local obstruction"
        if show == "Local evidence recorded":
            return bool((rec.get("local_evidence") or {}).get("recorded"))
        if show == "Provenance complete":
            return mapped > 0 and unclear == 0
        if show == "Provenance incomplete":
            return unclear > 0
        if show == "Search attempted":
            return int(rec.get("search_count") or 0) > 0
        if show == "Never searched":
            return int(rec.get("search_count") or 0) == 0
        return True

    filtered = [rec for rec in rows if keep(rec)]

    def height_key(field):
        def key(rec):
            value = rec.get(field)
            return (
                value is None,
                float(value) if value is not None else float("inf"),
                -int(rec.get("unique_mapped_points") or 0),
                int(rec.get("id") or 0),
            )
        return key

    if order_by == "Most new points":
        filtered.sort(
            key=lambda rec: (
                -int(rec.get("discovered_here_points") or 0),
                -int(rec.get("unique_mapped_points") or 0),
                int(rec.get("id") or 0),
            )
        )
    elif order_by == "Most currently mapped":
        filtered.sort(
            key=lambda rec: (
                -int(rec.get("unique_mapped_points") or 0),
                -int(rec.get("discovered_here_points") or 0),
                int(rec.get("id") or 0),
            )
        )
    elif order_by == "Most rigorous witnesses":
        filtered.sort(
            key=lambda rec: (
                -int(rec.get("independent_points") or 0),
                -int(rec.get("unique_mapped_points") or 0),
                int(rec.get("id") or 0),
            )
        )
    elif order_by == "Lowest best point height":
        filtered.sort(key=height_key("best_point_log10_x_height"))
    elif order_by == "Lowest median point height":
        filtered.sort(key=height_key("median_point_log10_x_height"))

    return tuple(filtered)


def quartic_point_inventory(db, curve_id, *, searches=None, coverings=None):
    """Return unique quartic-associated Point Ledger records with proof-aware labels."""
    searches = list(searches) if searches is not None else quartic_history(db, curve_id)
    coverings = list(coverings) if coverings is not None else covering_history(db, curve_id)

    point_by_id = {}
    provenance = {}

    for rec in searches:
        for point in mapped_points_for_quartic(db, curve_id, rec["id"]):
            point_id = int(point["id"])
            point_by_id[point_id] = point
            provenance.setdefault(point_id, set()).add(f"search #{int(rec['id'])}")

    for rec in coverings:
        for point in mapped_points_for_covering(db, curve_id, rec["id"]):
            point_id = int(point["id"])
            point_by_id[point_id] = point
            provenance.setdefault(point_id, set()).add(f"covering C{int(rec['id'])}")

    result = []
    for point_id, point in point_by_id.items():
        exact = bool(point["exact_verified"])
        rigorous = bool(point["rigorous_independent"])
        independence_status = str(point["independence_status"] or "unknown")

        if rigorous:
            significance = "★ rigorous independent"
            rank_meaning = "Stored rigorous witness"
            priority = 0
        elif not exact:
            significance = "needs exact verification"
            rank_meaning = "No rank effect established"
            priority = 4
        elif independence_status == "dependent":
            significance = "dependent"
            rank_meaning = "No rank increase"
            priority = 3
        elif independence_status == "numerical_novel":
            significance = "promising — needs proof"
            rank_meaning = "No rank increase proven"
            priority = 1
        elif independence_status == "inconclusive":
            significance = "inconclusive — retry"
            rank_meaning = "No rank increase proven"
            priority = 1
        else:
            significance = "exact — independence untested"
            rank_meaning = "No rank increase proven"
            priority = 2

        height_profile = point_height_profile(point)
        result.append(
            {
                "row": point,
                "id": point_id,
                "height_profile": height_profile,
                "x_height_log10": height_profile["x_height_log10"],
                "canonical_height": height_profile["canonical_height"],
                "exact_verified": exact,
                "rigorous_independent": rigorous,
                "independence_status": independence_status,
                "significance": significance,
                "rank_meaning": rank_meaning,
                "source": str(point["source"] or "—"),
                "role": str(point["role"] or "—"),
                "search_ref": str(point["search_ref"] or "—"),
                "provenance": tuple(sorted(provenance.get(point_id, set()))),
                "_priority": priority,
            }
        )

    result.sort(key=lambda rec: (int(rec["_priority"]), -int(rec["id"])))
    for rec in result:
        rec.pop("_priority", None)
    return result



def _discovery_is_quartics_event(row, *, search_ids, covering_ids):
    metadata = _loads(row["metadata_json"], {})
    try:
        quartic_search_id = int(metadata.get("quartic_search_id"))
    except (TypeError, ValueError):
        quartic_search_id = None
    if quartic_search_id in search_ids:
        return True

    try:
        covering_id = int(metadata.get("covering_id"))
    except (TypeError, ValueError):
        covering_id = None
    if covering_id in covering_ids:
        return True

    ref = str(row["search_ref"] or "")
    if ref.startswith("quartic:"):
        token = ref.split(":", 2)[1]
        if token.isdigit() and int(token) in search_ids:
            return True
    if ref.startswith("covering:"):
        token = ref.split(":", 2)[1]
        if token.isdigit() and int(token) in covering_ids:
            return True
    if ":exact-covering:" in ref:
        token = ref.rsplit(":", 1)[-1]
        if token.isdigit() and int(token) in covering_ids:
            return True
    return False


def quartic_overview_accounting(
    db,
    curve_id,
    *,
    inventory=None,
    summary=None,
    searches=None,
    coverings=None,
    top_n=8,
):
    """Return conservative Overview accounting for Quartics research.

    Current association is not treated as historical causation. Original
    discovery is credited to Quartics only when an immutable point-discovery
    observation explicitly records the point as non-preexisting and no earlier
    non-Quartics discovery observation contradicts that attribution.
    """
    searches = list(searches) if searches is not None else quartic_history(db, curve_id)
    coverings = list(coverings) if coverings is not None else covering_history(db, curve_id)
    inventory = (
        list(inventory)
        if inventory is not None
        else quartic_point_inventory(
            db,
            curve_id,
            searches=searches,
            coverings=coverings,
        )
    )
    summary = (
        dict(summary)
        if summary is not None
        else quartic_curve_summary(
            db,
            curve_id,
            searches=searches,
            coverings=coverings,
        )
    )

    search_ids = {int(rec["id"]) for rec in searches}
    covering_ids = {int(rec["id"]) for rec in coverings}
    point_ids = [int(rec["id"]) for rec in inventory]

    discoveries_by_point = {}
    if point_ids:
        placeholders = ",".join("?" for _ in point_ids)
        rows = db.execute(
            f"""SELECT * FROM point_discoveries
                WHERE curve_id=? AND point_id IN ({placeholders})
                ORDER BY created_at,id""",
            [int(curve_id), *point_ids],
        ).fetchall()
        for row in rows:
            discoveries_by_point.setdefault(int(row["point_id"]), []).append(row)

    proven_origin_ids = set()
    provenance_unclear_ids = set()
    for rec in inventory:
        point_id = int(rec["id"])
        events = list(discoveries_by_point.get(point_id, ()))
        quartics_events = [
            row
            for row in events
            if _discovery_is_quartics_event(
                row,
                search_ids=search_ids,
                covering_ids=covering_ids,
            )
        ]

        proven_event = None
        for row in quartics_events:
            metadata = _loads(row["metadata_json"], {})
            outcome = str(row["outcome"] or "")
            if (
                metadata.get("preexisting_point") is False
                and not bool(metadata.get("cached_replay"))
                and outcome in {"new_point", "mapped"}
            ):
                proven_event = row
                break

        if proven_event is not None:
            event_time = str(proven_event["created_at"] or "")
            earlier_other = any(
                str(row["created_at"] or "")
                and event_time
                and str(row["created_at"]) < event_time
                and not _discovery_is_quartics_event(
                    row,
                    search_ids=search_ids,
                    covering_ids=covering_ids,
                )
                for row in events
            )
            if not earlier_other:
                proven_origin_ids.add(point_id)
                continue

        if quartics_events or rec.get("provenance"):
            provenance_unclear_ids.add(point_id)

    rigorous = [
        rec for rec in inventory
        if bool(rec["rigorous_independent"])
    ]
    unresolved = [
        rec for rec in inventory
        if bool(rec["exact_verified"])
        and not bool(rec["rigorous_independent"])
        and str(rec["independence_status"] or "unknown")
            in {"unknown", "numerical_novel", "inconclusive"}
    ]
    dependent = [
        rec for rec in inventory
        if bool(rec["exact_verified"])
        and str(rec["independence_status"] or "unknown") == "dependent"
    ]
    independence_tested = [
        rec for rec in inventory
        if bool(rec["exact_verified"])
        and str(rec["independence_status"] or "unknown") != "unknown"
    ]
    numerical_novel = [
        rec for rec in inventory
        if bool(rec["exact_verified"])
        and not bool(rec["rigorous_independent"])
        and str(rec["independence_status"] or "unknown") == "numerical_novel"
    ]
    inconclusive = [
        rec for rec in inventory
        if bool(rec["exact_verified"])
        and not bool(rec["rigorous_independent"])
        and str(rec["independence_status"] or "unknown") == "inconclusive"
    ]
    untested = [
        rec for rec in inventory
        if bool(rec["exact_verified"])
        and not bool(rec["rigorous_independent"])
        and str(rec["independence_status"] or "unknown") == "unknown"
    ]
    tested_update_times = []
    for rec in inventory:
        if not bool(rec["exact_verified"]):
            continue
        status = str(rec["independence_status"] or "unknown")
        if status == "unknown" and not bool(rec["rigorous_independent"]):
            continue
        row = rec.get("row")
        if row is not None and row["updated_at"]:
            tested_update_times.append(str(row["updated_at"]))
    latest_independence_updated_at = (
        max(tested_update_times) if tested_update_times else None
    )

    height_summary = point_height_summary(
        [rec["row"] for rec in inventory],
        top_n=max(1, int(top_n)),
    )
    inventory_by_id = {int(rec["id"]): rec for rec in inventory}
    top_unresolved = tuple(
        inventory_by_id[int(profile["point_id"])]
        for profile in height_summary["best_unresolved_points"]
        if profile["point_id"] is not None
        and int(profile["point_id"]) in inventory_by_id
    )
    x_height_values = [
        float(rec["x_height_log10"])
        for rec in inventory
        if rec.get("x_height_log10") is not None
    ]
    height_threshold_counts = {
        threshold: sum(value < threshold for value in x_height_values)
        for threshold in (16.0, 18.0, 20.0)
    }

    activity_times = []
    for rec in searches:
        row = rec.get("row")
        if row is not None and row["created_at"]:
            activity_times.append(str(row["created_at"]))
    for rec in coverings:
        row = rec.get("row")
        if row is not None and row["created_at"]:
            activity_times.append(str(row["created_at"]))
    first_activity_at = min(activity_times) if activity_times else None

    current_point_count = int(
        db.execute(
            "SELECT COUNT(*) AS n FROM points WHERE curve_id=?",
            (int(curve_id),),
        ).fetchone()["n"]
        or 0
    )
    point_count_before = None
    recorded_rank_lower_before = None
    if first_activity_at:
        point_count_before = int(
            db.execute(
                """SELECT COUNT(*) AS n FROM points
                   WHERE curve_id=? AND created_at < ?""",
                (int(curve_id), first_activity_at),
            ).fetchone()["n"]
            or 0
        )
        evidence_rows = db.execute(
            """SELECT rigorous_lower,exact_rank
               FROM rank_evidence
               WHERE curve_id=? AND rigorous=1 AND created_at < ?""",
            (int(curve_id), first_activity_at),
        ).fetchall()
        lower_values = []
        for row in evidence_rows:
            if row["rigorous_lower"] is not None:
                lower_values.append(int(row["rigorous_lower"]))
            if row["exact_rank"] is not None:
                lower_values.append(int(row["exact_rank"]))
        if lower_values:
            recorded_rank_lower_before = max(lower_values)

    if unresolved:
        diagnosis = (
            f"{len(unresolved)} exact unresolved quartic-associated point(s) remain; "
            f"prioritize the lowest-height {min(len(top_unresolved), max(1, int(top_n)))} "
            "in Independence before deeper search."
        )
    elif inventory and provenance_unclear_ids:
        diagnosis = (
            "No unresolved quartic-associated points remain, but historical origin is "
            f"unclear for {len(provenance_unclear_ids)} current association(s); treat "
            "productivity as current attribution until provenance is reconstructed."
        )
    elif inventory:
        diagnosis = (
            "No unresolved quartic-associated points remain; additional compute is better "
            "directed at uncovered or low-overlap search regions than rechecking these points."
        )
    else:
        diagnosis = (
            "No quartic-associated Point Ledger records are stored yet; prioritize productive "
            "coverings or uncovered search regions before Independence work."
        )

    return {
        "associated_points": len(inventory),
        "rigorous_witnesses": len(rigorous),
        "proven_quartics_origin_points": len(proven_origin_ids),
        "proven_quartics_origin_rigorous_witnesses": sum(
            int(rec["id"]) in proven_origin_ids
            for rec in rigorous
        ),
        "unresolved_points": len(unresolved),
        "dependent_points": len(dependent),
        "independence_tested_points": len(independence_tested),
        "numerical_novel_points": len(numerical_novel),
        "inconclusive_points": len(inconclusive),
        "untested_points": len(untested),
        "latest_independence_updated_at": latest_independence_updated_at,
        "proven_origin_point_ids": tuple(sorted(proven_origin_ids)),
        "provenance_unclear_points": len(provenance_unclear_ids),
        "height_summary": height_summary,
        "height_threshold_counts": height_threshold_counts,
        "top_unresolved": top_unresolved,
        "funnel": {
            "coverings": int(summary.get("coverings") or 0),
            "quartic_hits": int(summary.get("quartic_hits") or 0),
            "mapped_points": len(inventory),
            "independence_tested": len(independence_tested),
            "rigorous_witnesses": len(rigorous),
        },
        "first_quartics_activity_at": first_activity_at,
        "point_ledger_before_first_activity": point_count_before,
        "point_ledger_now": current_point_count,
        "recorded_rank_lower_before_first_activity": recorded_rank_lower_before,
        "diagnosis": diagnosis,
    }


def raw_quartic_command(
    row,
    *,
    python,
    db_path,
    ratpoints,
    timeout,
    height=None,
    denominator_low=None,
    denominator_high=None,
    force=False,
):
    coefficients = _loads(row["polynomial_json"], [])
    if not coefficients:
        raise ValueError("stored quartic search has no polynomial")
    command = [
        str(python),
        "-m",
        "rank42.quartic_search",
        "--db",
        str(db_path),
        "--curve-id",
        str(int(row["curve_id"])),
        "--coefficients",
        ",".join(str(x) for x in coefficients),
        "--height",
        str(int(height if height is not None else row["height_bound"])),
        "--timeout",
        str(max(1, int(timeout))),
        "--source-search-id",
        str(int(row["id"])),
    ]
    if row["family"]:
        command += ["--family", str(row["family"])]
    if row["parameter"] is not None:
        command += ["--parameter", str(row["parameter"])]
    if row["hole_label"]:
        command += ["--hole-label", str(row["hole_label"])]
    low = row["denominator_low"] if denominator_low is None else denominator_low
    high = row["denominator_high"] if denominator_high is None else denominator_high
    if low is not None:
        command += ["--denominator-low", str(int(low))]
    if high is not None:
        command += ["--denominator-high", str(int(high))]
    if ratpoints:
        command += ["--ratpoints", str(ratpoints)]
    if force:
        command.append("--force")
    return command


def pointed_quartic_command(
    row,
    *,
    python,
    db_path,
    ratpoints,
    timeout,
    height,
    anchors=8,
    alternate_anchors=False,
):
    return [
        str(python),
        "-m",
        "rank42.quartic_workbench_runner",
        "--db",
        str(db_path),
        "--mode",
        "pointed",
        "--search-id",
        str(int(row["id"])),
        "--height",
        str(max(1, int(height))),
        "--timeout",
        str(max(1, int(timeout))),
        "--anchors",
        str(max(1, int(anchors))),
        *(["--alternate-anchors"] if alternate_anchors else []),
        *(["--ratpoints", str(ratpoints)] if ratpoints else []),
    ]


def covering_command(
    row,
    *,
    python,
    db_path,
    ratpoints,
    timeout,
    height,
    one_point=False,
):
    return [
        str(python),
        "-m",
        "rank42.quartic_workbench_runner",
        "--db",
        str(db_path),
        "--mode",
        "covering",
        "--covering-id",
        str(int(row["id"])),
        "--height",
        str(max(1, int(height))),
        "--timeout",
        str(max(1, int(timeout))),
        *(["--one-point"] if one_point else []),
        *(["--ratpoints", str(ratpoints)] if ratpoints else []),
    ]


def covering_local_command(
    *,
    python,
    db_path,
    curve_id,
    timeout=30,
    max_coverings=100,
    base_height=10000,
):
    return [
        str(python),
        "-m",
        "rank42.quartic_workbench_runner",
        "--db",
        str(db_path),
        "--mode",
        "covering-local",
        "--curve-id",
        str(int(curve_id)),
        "--max-coverings",
        str(max(1, int(max_coverings))),
        "--height",
        str(max(1, int(base_height))),
        "--timeout",
        str(max(1, int(timeout))),
    ]
