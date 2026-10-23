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
    get_curve,
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

    # A seed-anchored pullback can name a curve already in the local database.
    # Reuse that exact curve and its existing rank evidence at the anchor;
    # creating a second family-key row would hide the certified seed basis.
    family_data = getattr(family, "data", None)
    seed = (family_data.get("rank_lower_certificate") or {}) if isinstance(family_data, dict) else {}
    if seed.get("method") == "three_sections_specialize_into_certified_seed_basis":
        try:
            at_seed = QQ(str(parameter)) == QQ(str(seed["derived_parameter"]))
            seed_curve_id = int(seed["curve_id"])
            evidence_id = int(seed["rank_evidence_id"])
        except (KeyError, TypeError, ValueError):
            at_seed = False
        if at_seed:
            stored_seed = get_curve(db, seed_curve_id)
            evidence = db.execute(
                "SELECT rigorous,rigorous_lower,status,model_a_invariants_json "
                "FROM rank_evidence WHERE id=? AND curve_id=?",
                (evidence_id, seed_curve_id),
            ).fetchone()
            if stored_seed is not None and evidence is not None and stored_seed["a_invariants_json"]:
                seed_model = [QQ(value) for value in json.loads(stored_seed["a_invariants_json"])]
                evidence_model = [QQ(value) for value in json.loads(evidence["model_a_invariants_json"])]
                if (evidence["rigorous"] and evidence["status"] == "completed"
                        and int(evidence["rigorous_lower"] or 0) >= int(seed.get("generic_rank_lower_bound") or 3)
                        and seed_model == evidence_model == list(source.a_invariants())):
                    return seed_curve_id, EllipticCurve(QQ, seed_model), source, False

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
