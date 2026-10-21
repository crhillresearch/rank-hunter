"""Shared Search curve materialization and transient-retention helpers.

These functions are independent of legacy Auto orchestration so Pipeline and
legacy Search paths share curve identity/provenance behavior without Pipeline
importing rank42.auto_search.
"""
from __future__ import annotations

import json

from sage.all import QQ, EllipticCurve

from rank42.db import (
    discard_unpromoted_search_curve,
    get_curve_by_key,
    update_curve,
    upsert_curve,
)


def materialize_family_curve(
    db,
    *,
    plugin,
    variant,
    family,
    parameter,
    score,
    fingerprints,
):
    """Materialize or refresh one exact family specialization in Curves."""
    existing = get_curve_by_key(db, family.name(), str(parameter))
    source = family.curve(QQ(str(parameter)))
    if source is None:
        return None, None, None, False

    if existing is not None and existing["a_invariants_json"]:
        E = EllipticCurve(
            QQ,
            [QQ(str(x)) for x in json.loads(existing["a_invariants_json"])],
        )
        update_curve(
            db,
            int(existing["id"]),
            score=float(score),
            plugin_id=plugin.id,
            plugin_version=plugin.version,
            family_spec=variant.family_spec,
            **fingerprints,
        )
        return int(existing["id"]), E, source, False

    ainvs = [str(x) for x in source.a_invariants()]
    curve_id = upsert_curve(
        db,
        family=family.name(),
        parameter=str(parameter),
        score=float(score),
        a_invariants_json=json.dumps(ainvs),
        plugin_id=plugin.id,
        plugin_version=plugin.version,
        family_spec=variant.family_spec,
        status="auto_searching",
        **fingerprints,
    )
    return int(curve_id), source, source, existing is None


__all__ = [
    "discard_unpromoted_search_curve",
    "materialize_family_curve",
]
