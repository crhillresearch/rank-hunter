"""Exact Q-isomorphism identity helpers for stored elliptic curves.

A canonical scientific curve row is distinct from any search/derivation path.
The invariant signature is only a candidate lookup/prefilter; reuse always
requires Sage's exact QQ-isomorphism test.
"""
from __future__ import annotations

import hashlib
import json

from sage.all import EllipticCurve, QQ

from rank42.db import get_curve_by_key, upsert_curve


CANONICAL_FAMILY = "canonical:q-isomorphism"
IDENTITY_METHOD = "global-minimal-invariants+sage-is_isomorphic-v1"


def _minimal_curve(E):
    try:
        return E.global_minimal_model()
    except Exception:
        try:
            return E.minimal_model()
        except Exception:
            return E


def _model(E):
    return [str(QQ(x)) for x in E.a_invariants()]


def q_isomorphism_signature(E):
    """Return an exact prefilter signature for a Q-isomorphism class.

    For global minimal models over Q, c4, c6 and discriminant are invariant
    under integral minimal-model coordinate changes. This signature narrows
    candidate rows but is never used by itself to prove identity.
    """
    Em = _minimal_curve(E)
    payload = {
        "c4": str(QQ(Em.c4())),
        "c6": str(QQ(Em.c6())),
        "discriminant": str(QQ(Em.discriminant())),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "method": IDENTITY_METHOD,
        "minimal_a_invariants": _model(Em),
        "invariants": payload,
        "signature_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _stored_curve_from_row(row):
    try:
        raw = json.loads(row["a_invariants_json"] or "[]")
        if not isinstance(raw, list) or len(raw) != 5:
            return None
        return EllipticCurve(QQ, [QQ(str(x)) for x in raw])
    except Exception:
        return None


def _exact_q_isomorphic(E, other):
    try:
        return bool(E.is_isomorphic(other))
    except Exception:
        return False


def find_q_isomorphic_curve(db, E):
    """Find an existing stored curve exactly Q-isomorphic to E.

    Canonical rows are checked first by deterministic signature key. Legacy
    family/general rows are then scanned with the exact signature as prefilter.
    Every reuse is confirmed by Sage's exact is_isomorphic() over QQ.
    """
    Em = _minimal_curve(E)
    identity = q_isomorphism_signature(Em)
    signature = identity["signature_sha256"]
    canonical = get_curve_by_key(db, CANONICAL_FAMILY, f"qiso:{signature}")
    if canonical is not None:
        stored = _stored_curve_from_row(canonical)
        if stored is not None and _exact_q_isomorphic(Em, stored):
            return canonical, {
                **identity,
                "matched_by": "canonical_signature_key",
                "exact_q_isomorphism_verified": True,
            }

    rows = db.execute(
        """SELECT id,family,parameter,a_invariants_json
           FROM curves
           WHERE a_invariants_json IS NOT NULL
             AND a_invariants_json!=''
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        stored = _stored_curve_from_row(row)
        if stored is None:
            continue
        try:
            stored_identity = q_isomorphism_signature(stored)
        except Exception:
            continue
        if stored_identity["invariants"] != identity["invariants"]:
            continue
        if not _exact_q_isomorphic(Em, stored):
            continue
        return row, {
            **identity,
            "matched_by": "existing_curve_exact_q_isomorphism",
            "exact_q_isomorphism_verified": True,
            "matched_family": str(row["family"]),
            "matched_parameter": str(row["parameter"]),
        }
    return None, {
        **identity,
        "matched_by": None,
        "exact_q_isomorphism_verified": False,
    }


def get_or_create_canonical_curve(db, E, *, status="pipeline_derived"):
    """Return one scientific curve row for the exact Q-isomorphism class."""
    Em = _minimal_curve(E)
    existing, identity = find_q_isomorphic_curve(db, Em)
    if existing is not None:
        return {
            "curve_id": int(existing["id"]),
            "created": False,
            "family": str(existing["family"]),
            "parameter": str(existing["parameter"]),
            "identity": identity,
            "minimal_curve": Em,
        }

    signature = identity["signature_sha256"]
    label = f"qiso:{signature}"
    curve_id = upsert_curve(
        db,
        family=CANONICAL_FAMILY,
        parameter=label,
        score=None,
        a_invariants_json=json.dumps(_model(Em)),
        status=status,
    )
    row = get_curve_by_key(db, CANONICAL_FAMILY, label)
    stored = None if row is None else _stored_curve_from_row(row)
    if (
        row is None
        or stored is None
        or not _exact_q_isomorphic(Em, stored)
    ):
        raise RuntimeError(
            "canonical curve row failed exact Q-isomorphism verification"
        )
    return {
        "curve_id": int(curve_id),
        "created": True,
        "family": CANONICAL_FAMILY,
        "parameter": label,
        "identity": {
            **identity,
            "matched_by": "created_canonical_curve",
            "exact_q_isomorphism_verified": True,
        },
        "minimal_curve": Em,
    }
