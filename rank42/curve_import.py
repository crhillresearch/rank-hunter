"""Manual import of isolated elliptic curves into Rank Hunter.

Imported curves are durable research objects, but external rank claims remain
provenance only. Rigorous Rank Hunter fields are populated only by Rank
Hunter's normal evidence/certification pipeline.
"""
from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction

from rank42.db import get_curve_by_key, log_event, upsert_curve
from rank42.points import upsert_point

DEFAULT_FAMILY = "External / unclassified"


def _qstr(value):
    q = Fraction(str(value).strip())
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def parse_a_invariants(value):
    """Return canonical rational strings [a1,a2,a3,a4,a6]."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("a-invariants are required")
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("a-invariants must be valid JSON or five comma-separated rationals") from exc
        else:
            raw = [part.strip() for part in text.split(",")]
    else:
        raw = list(value)

    if len(raw) != 5:
        raise ValueError("expected exactly five a-invariants: [a1,a2,a3,a4,a6]")
    try:
        return [_qstr(item) for item in raw]
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("every a-invariant must be a rational number") from exc


def model_discriminant(a_invariants):
    """Compute the discriminant of the supplied long Weierstrass model over Q."""
    a1, a2, a3, a4, a6 = (Fraction(x) for x in parse_a_invariants(a_invariants))
    b2 = a1 * a1 + 4 * a2
    b4 = 2 * a4 + a1 * a3
    b6 = a3 * a3 + 4 * a6
    b8 = a1 * a1 * a6 + 4 * a2 * a6 - a1 * a3 * a4 + a2 * a3 * a3 - a4 * a4
    return -(b2 * b2 * b8) - 8 * b4 * b4 * b4 - 27 * b6 * b6 + 9 * b2 * b4 * b6


def model_fingerprint(a_invariants):
    payload = json.dumps(parse_a_invariants(a_invariants), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_points(value):
    """Return canonical affine rational points [(x,y), ...]."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = None
            if isinstance(raw, list) and (not raw or isinstance(raw[0], (list, tuple))):
                items = raw
            elif isinstance(raw, list) and len(raw) == 2:
                items = [raw]
            else:
                items = []
        else:
            items = []
        if not items:
            items = []
            fallback = text
            if fallback.startswith("[[") and fallback.endswith("]]"):
                fallback = re.sub(r"\]\s*,\s*\[", "\n", fallback[2:-2])
            for line in fallback.replace(";", "\n").splitlines():
                line = line.strip()
                if not line:
                    continue
                line = line.strip("()[] ")
                parts = [part.strip() for part in line.split(",")]
                if len(parts) != 2:
                    raise ValueError(
                        "points must be JSON [[x,y],...] or one x,y pair per line"
                    )
                items.append(parts)
    else:
        items = list(value)

    out = []
    for rec in items:
        if not isinstance(rec, (list, tuple)) or len(rec) != 2:
            raise ValueError("each point must contain exactly two coordinates [x,y]")
        try:
            out.append((_qstr(rec[0]), _qstr(rec[1])))
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("every point coordinate must be a rational number") from exc
    return out


def point_on_curve(a_invariants, point):
    """Exact affine membership test on the supplied long Weierstrass model."""
    a1, a2, a3, a4, a6 = (Fraction(x) for x in parse_a_invariants(a_invariants))
    x, y = (Fraction(z) for z in point)
    lhs = y * y + a1 * x * y + a3 * y
    rhs = x * x * x + a2 * x * x + a4 * x + a6
    return lhs == rhs



def _first_present(mapping, *keys):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def parse_curve_import_json(value):
    """Parse one or more external curves from JSON into import_curve kwargs.

    Accepted top-level shapes:
      * {"a_invariants": [...], ...}
      * {"curves": [{...}, ...]}
      * [{...}, {...}]
      * [a1,a2,a3,a4,a6]
      * [[a1,a2,a3,a4,a6], [...]]
    """
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("curve JSON file must be UTF-8 text") from exc
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("curve JSON file is empty")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid curve JSON: {exc.msg} at line {exc.lineno} column {exc.colno}") from exc
    else:
        payload = value

    if isinstance(payload, dict) and "curves" in payload:
        records = payload["curves"]
        if not isinstance(records, list):
            raise ValueError('top-level "curves" must be a JSON array')
    elif isinstance(payload, dict):
        records = [payload]
    elif isinstance(payload, list):
        # A bare five-term list is the simplest single-curve file.
        if len(payload) == 5 and not any(isinstance(x, (dict, list, tuple)) for x in payload):
            records = [{"a_invariants": payload}]
        # Also allow a batch containing bare five-term models.
        elif payload and all(isinstance(x, (list, tuple)) and len(x) == 5 for x in payload):
            records = [{"a_invariants": x} for x in payload]
        else:
            records = payload
    else:
        raise ValueError("curve JSON must contain a curve object, curve array, or five a-invariants")

    if not records:
        raise ValueError("curve JSON contains no curves")

    normalized = []
    for index, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            raise ValueError(f"curve record {index} must be a JSON object")

        raw_ainvs = _first_present(rec, "a_invariants", "ainvs", "a-invariants", "model")
        if raw_ainvs is None:
            raise ValueError(f"curve record {index} is missing a_invariants")
        try:
            ainvs = parse_a_invariants(raw_ainvs)
            if model_discriminant(ainvs) == 0:
                raise ValueError("the supplied Weierstrass model is singular (discriminant = 0)")
            points = parse_points(_first_present(rec, "points", "known_points", "rational_points"))
            for point in points:
                if not point_on_curve(ainvs, point):
                    raise ValueError(f"point ({point[0]}, {point[1]}) is not on the supplied curve")
        except ValueError as exc:
            raise ValueError(f"curve record {index}: {exc}") from exc

        normalized.append({
            "a_invariants": ainvs,
            "family": _first_present(rec, "family", "family_label", "label"),
            "parameter": _first_present(rec, "parameter", "identifier"),
            "source": rec.get("source"),
            "claimed_rank": _first_present(rec, "claimed_rank", "rank_claim", "rank"),
            "notes": rec.get("notes"),
            "points": points,
        })
    return normalized


def import_curve_records(db, records, *, default_source=None):
    """Preflight and import normalized JSON records through import_curve()."""
    records = list(records)
    if not records:
        return []

    # Validate every record and every key collision before the first durable write.
    prepared = []
    batch_keys = {}
    for index, rec in enumerate(records, 1):
        try:
            ainvs = parse_a_invariants(rec["a_invariants"])
            if model_discriminant(ainvs) == 0:
                raise ValueError("the supplied Weierstrass model is singular (discriminant = 0)")
            points = parse_points(rec.get("points"))
            for point in points:
                if not point_on_curve(ainvs, point):
                    raise ValueError(f"point ({point[0]}, {point[1]}) is not on the supplied curve")
        except (KeyError, ValueError) as exc:
            raise ValueError(f"curve record {index}: {exc}") from exc

        family = str(rec.get("family") or "").strip() or DEFAULT_FAMILY
        parameter = str(rec.get("parameter") or "").strip() or f"model:{model_fingerprint(ainvs)[:12]}"
        key = (family, parameter)
        previous = batch_keys.get(key)
        if previous is not None and previous != ainvs:
            raise ValueError(
                f"curve record {index}: {family} / {parameter} is duplicated in the JSON with a different model"
            )
        batch_keys[key] = ainvs

        existing = get_curve_by_key(db, family, parameter)
        if existing is not None and existing["a_invariants_json"]:
            try:
                same_model = parse_a_invariants(existing["a_invariants_json"]) == ainvs
            except ValueError:
                same_model = False
            if not same_model:
                raise ValueError(
                    f"curve record {index}: {family} / {parameter} already exists with a different stored model"
                )

        prepared.append({
            **rec,
            "a_invariants": ainvs,
            "family": family,
            "parameter": parameter,
            "points": points,
            "source": rec.get("source") or default_source,
        })

    results = []
    for rec in prepared:
        curve_id, created = import_curve(db, **rec)
        results.append({"curve_id": int(curve_id), "created": bool(created)})
    return results

def import_curve(
    db,
    *,
    a_invariants,
    family=None,
    parameter=None,
    source=None,
    claimed_rank=None,
    notes=None,
    points=None,
):
    """Insert one durable external curve and return (curve_id, created).

    claimed_rank is deliberately recorded only in the event/provenance log.
    """
    ainvs = parse_a_invariants(a_invariants)
    if model_discriminant(ainvs) == 0:
        raise ValueError("the supplied Weierstrass model is singular (discriminant = 0)")
    imported_points = parse_points(points)
    for point in imported_points:
        if not point_on_curve(ainvs, point):
            raise ValueError(f"point ({point[0]}, {point[1]}) is not on the supplied curve")

    family = str(family or "").strip() or DEFAULT_FAMILY
    parameter = str(parameter or "").strip()
    if not parameter:
        parameter = f"model:{model_fingerprint(ainvs)[:12]}"

    encoded = json.dumps(ainvs, separators=(",", ":"))
    existing = get_curve_by_key(db, family, parameter)
    created = existing is None
    if existing is not None:
        existing_model = existing["a_invariants_json"]
        if existing_model:
            try:
                same_model = parse_a_invariants(existing_model) == ainvs
            except ValueError:
                same_model = False
            if not same_model:
                raise ValueError(
                    f"{family} / {parameter} already exists with a different stored model; "
                    "use a different family or parameter label"
                )
        curve_id = int(existing["id"])
    else:
        curve_id = upsert_curve(
            db,
            family=family,
            parameter=parameter,
            a_invariants_json=encoded,
            status="imported",
        )

    point_source = str(source or "").strip() or "manual_external_import"
    for index, (x, y) in enumerate(imported_points, 1):
        upsert_point(
            db,
            curve_id=curve_id,
            x=x,
            y=y,
            source=point_source,
            role="external_point",
            exact_verified=True,
            independence_status="unknown",
            rigorous_independent=False,
            search_ref="manual_import",
            metadata={"imported": True, "import_index": index},
        )

    details = ["Manual external curve import", f"model={encoded}"]
    if imported_points:
        details.append(f"exact_points_imported={len(imported_points)}")
    if source and str(source).strip():
        details.append(f"source={str(source).strip()}")
    if claimed_rank and str(claimed_rank).strip():
        details.append(f"external_rank_claim={str(claimed_rank).strip()}")
    if notes and str(notes).strip():
        details.append(f"notes={str(notes).strip()}")
    if not created:
        details.append("matched_existing_curve_key=true")
    log_event(db, curve_id, "info", " · ".join(details))
    return curve_id, created
