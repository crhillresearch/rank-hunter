"""Persistent family-level evidence, separate from per-fiber rank evidence."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def family_evidence_key(family_spec, family_sha256=None):
    raw = json.dumps(
        {
            "family_spec": str(family_spec or ""),
            "family_sha256": str(family_sha256 or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def record_family_evidence(
    db,
    *,
    family_spec,
    family_sha256=None,
    plugin_id=None,
    plugin_version=None,
    criterion,
    specialization_parameter,
    specialized_curve_id=None,
    generic_lower=None,
    generic_upper=None,
    exact_generic_rank=None,
    certificate,
    status="completed",
):
    family_key = family_evidence_key(family_spec, family_sha256)
    payload = json.dumps(certificate or {}, sort_keys=True, default=str)
    evidence_key = hashlib.sha256(
        (
            family_key
            + "\n"
            + str(criterion)
            + "\n"
            + str(specialization_parameter)
            + "\n"
            + str(specialized_curve_id or "")
        ).encode()
    ).hexdigest()
    ts = _now()
    db.execute(
        """
        INSERT INTO family_evidence(
            evidence_key,family_key,family_spec,family_sha256,plugin_id,
            plugin_version,criterion,specialization_parameter,
            specialized_curve_id,generic_lower,generic_upper,exact_generic_rank,
            certificate_json,status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(evidence_key) DO UPDATE SET
            family_sha256=excluded.family_sha256,
            plugin_id=excluded.plugin_id,
            plugin_version=excluded.plugin_version,
            specialized_curve_id=excluded.specialized_curve_id,
            generic_lower=excluded.generic_lower,
            generic_upper=excluded.generic_upper,
            exact_generic_rank=excluded.exact_generic_rank,
            certificate_json=excluded.certificate_json,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            evidence_key,
            family_key,
            str(family_spec or ""),
            family_sha256,
            plugin_id,
            plugin_version,
            str(criterion),
            str(specialization_parameter),
            specialized_curve_id,
            generic_lower,
            generic_upper,
            exact_generic_rank,
            payload,
            str(status),
            ts,
            ts,
        ),
    )
    db.commit()
    return int(
        db.execute(
            "SELECT id FROM family_evidence WHERE evidence_key=?",
            (evidence_key,),
        ).fetchone()[0]
    )


def list_family_evidence(db, family_key):
    return list(
        db.execute(
            """
            SELECT * FROM family_evidence
            WHERE family_key=?
            ORDER BY id DESC
            """,
            (str(family_key),),
        )
    )


def reduce_family_evidence(db, family_key):
    rows = list_family_evidence(db, family_key)
    completed = [row for row in rows if str(row["status"] or "") == "completed"]

    lowers = [
        int(value)
        for row in completed
        for value in (row["generic_lower"], row["exact_generic_rank"])
        if value is not None
    ]
    uppers = [
        int(value)
        for row in completed
        for value in (row["generic_upper"], row["exact_generic_rank"])
        if value is not None
    ]
    lower = max(lowers) if lowers else None
    upper = min(uppers) if uppers else None
    inconsistent = (
        lower is not None and upper is not None and int(lower) > int(upper)
    )
    exact = (
        int(lower)
        if not inconsistent
        and lower is not None
        and upper is not None
        and int(lower) == int(upper)
        else None
    )
    exact_evidence_ids = [
        int(row["id"])
        for row in completed
        if row["exact_generic_rank"] is not None
        and exact is not None
        and int(row["exact_generic_rank"]) == int(exact)
    ]
    return {
        "family_key": str(family_key),
        "rigorous_generic_lower": lower,
        "rigorous_generic_upper": upper,
        "exact_generic_rank": exact,
        "rank_inconsistent": inconsistent,
        "evidence_count": len(rows),
        "completed_evidence_count": len(completed),
        "exact_evidence_ids": exact_evidence_ids,
    }


def family_evidence_state_for_curve(db, curve_row):
    """Return rigorous generic-rank state bound to one curve's family fingerprint."""
    family_spec = str(curve_row["family_spec"] or "").strip()
    family_sha256 = curve_row["family_sha256"]
    if not family_spec:
        return {
            "available": False,
            "reason": "family_spec_unavailable",
            "family_key": None,
            "family_spec": None,
            "family_sha256": family_sha256,
            "rigorous_generic_lower": None,
            "rigorous_generic_upper": None,
            "exact_generic_rank": None,
            "rank_inconsistent": False,
            "evidence_count": 0,
            "completed_evidence_count": 0,
            "exact_evidence_ids": [],
            "evidence": [],
        }

    family_key = family_evidence_key(family_spec, family_sha256)
    reduced = reduce_family_evidence(db, family_key)
    rows = list_family_evidence(db, family_key)
    evidence = []
    for row in rows:
        try:
            certificate = json.loads(row["certificate_json"] or "{}")
        except Exception:
            certificate = {}
        evidence.append({
            "id": int(row["id"]),
            "criterion": str(row["criterion"]),
            "status": str(row["status"]),
            "specialization_parameter": str(row["specialization_parameter"]),
            "specialized_curve_id": row["specialized_curve_id"],
            "generic_lower": row["generic_lower"],
            "generic_upper": row["generic_upper"],
            "exact_generic_rank": row["exact_generic_rank"],
            "plugin_id": row["plugin_id"],
            "plugin_version": row["plugin_version"],
            "family_spec": row["family_spec"],
            "family_sha256": row["family_sha256"],
            "certificate": certificate if isinstance(certificate, dict) else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })

    reason = (
        "family_evidence_conflict"
        if reduced["rank_inconsistent"]
        else "exact_generic_rank_known"
        if reduced["exact_generic_rank"] is not None
        else "generic_rank_bounds_only"
        if reduced["rigorous_generic_lower"] is not None
        or reduced["rigorous_generic_upper"] is not None
        else "no_completed_generic_rank_evidence"
    )
    return {
        "available": bool(rows),
        "reason": reason,
        "family_spec": family_spec,
        "family_sha256": family_sha256,
        "evidence": evidence,
        **reduced,
    }
