"""Versioned, read-only research dimensions for Analysis -> Landscape.

The registry owns labels, provenance/rigor semantics, and single-object value
projection. Cohort persistence/querying and visualization belong to later
Research Environment slices.
"""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from rank42.candidate_ranking_state import (
    pipeline_candidate_ranking_state,
    pool_candidate_ranking_state,
)
from rank42.curve_arithmetic_state import get_curve_arithmetic_state
from rank42.curve_research_state import get_curve_research_state
from rank42.family_evidence import family_evidence_state_for_curve
from rank42.lattice_analysis import analyze_lattice


LANDSCAPE_DIMENSION_REGISTRY_VERSION = 1
LANDSCAPE_FEATURE_DEFINITION_VERSION = 1

_DIMENSIONS = (
    # Rank / proof.
    {
        "id": "rank.rigorous_lower",
        "label": "Rigorous lower",
        "category": "rank_proof",
        "value_type": "integer",
        "rigor_class": "rigorous",
        "source_authority": "Curve Research State",
        "provenance_policy": "reduced rank-evidence authority",
        "version": 1,
    },
    {
        "id": "rank.rigorous_upper",
        "label": "Rigorous upper",
        "category": "rank_proof",
        "value_type": "integer_optional",
        "rigor_class": "rigorous",
        "source_authority": "Curve Research State",
        "provenance_policy": "reduced rank-evidence authority",
        "version": 1,
    },
    {
        "id": "rank.exact_rank",
        "label": "Exact rank",
        "category": "rank_proof",
        "value_type": "integer_optional",
        "rigor_class": "rigorous",
        "source_authority": "Curve Research State",
        "provenance_policy": "present only when reduced rigorous bounds agree",
        "version": 1,
    },
    {
        "id": "rank.generic_exact",
        "label": "Exact generic rank",
        "category": "rank_proof",
        "value_type": "integer_optional",
        "rigor_class": "rigorous",
        "source_authority": "Family Evidence",
        "provenance_policy": "completed exact family evidence bound to family fingerprint",
        "version": 1,
    },
    {
        "id": "rank.specialization_surplus",
        "label": "Specialization surplus",
        "category": "rank_proof",
        "value_type": "integer_optional",
        "rigor_class": "rigorous",
        "source_authority": "Curve Research State + Family Evidence",
        "provenance_policy": "rigorous lower minus exact generic rank; unavailable without exact family evidence",
        "version": 1,
    },
    {
        "id": "points.rigorous_witness_count",
        "label": "Rigorous witness count",
        "category": "rank_proof",
        "value_type": "integer",
        "rigor_class": "rigorous_record",
        "source_authority": "Point Ledger",
        "provenance_policy": "count persisted exact rigorous-independent point rows",
        "version": 1,
    },
    {
        "id": "points.unresolved_exact_count",
        "label": "Unresolved exact-point count",
        "category": "rank_proof",
        "value_type": "integer",
        "rigor_class": "descriptive_exact",
        "source_authority": "Point Ledger",
        "provenance_policy": "count exact point rows with unknown/inconclusive independence state",
        "version": 1,
    },
    # Arithmetic.
    {
        "id": "arithmetic.conductor",
        "label": "Conductor",
        "category": "arithmetic",
        "value_type": "integer_optional",
        "rigor_class": "reference_aware",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local authority first; labeled unambiguous catalog fallback allowed",
        "version": 1,
    },
    {
        "id": "arithmetic.log_conductor",
        "label": "log conductor",
        "category": "arithmetic",
        "value_type": "real_optional",
        "rigor_class": "reference_aware_derived",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "Curve Arithmetic derived display projection",
        "version": 1,
    },
    {
        "id": "arithmetic.discriminant",
        "label": "Discriminant",
        "category": "arithmetic",
        "value_type": "integer_optional",
        "rigor_class": "reference_aware",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local authority first; labeled unambiguous catalog fallback allowed",
        "version": 1,
    },
    {
        "id": "arithmetic.root_number",
        "label": "Root number",
        "category": "arithmetic",
        "value_type": "integer_optional",
        "rigor_class": "local_exact",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local persisted/computed authority only",
        "version": 1,
    },
    {
        "id": "arithmetic.torsion",
        "label": "Torsion",
        "category": "arithmetic",
        "value_type": "object_optional",
        "rigor_class": "local_exact",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local persisted/computed torsion projection",
        "version": 1,
    },
    {
        "id": "arithmetic.bad_primes",
        "label": "Bad primes",
        "category": "arithmetic",
        "value_type": "integer_list_optional",
        "rigor_class": "reference_aware",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local authority first; labeled unambiguous catalog fallback allowed",
        "version": 1,
    },
    {
        "id": "arithmetic.bad_prime_count",
        "label": "Bad-prime count",
        "category": "arithmetic",
        "value_type": "integer_optional",
        "rigor_class": "reference_aware_derived",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "count of Curve Arithmetic bad-prime display set",
        "version": 1,
    },
    {
        "id": "arithmetic.naive_height",
        "label": "Naive height",
        "category": "arithmetic",
        "value_type": "real_text_optional",
        "rigor_class": "reference_aware",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local computed authority or labeled unambiguous catalog fallback",
        "version": 1,
    },
    {
        "id": "arithmetic.faltings_height",
        "label": "Faltings height",
        "category": "arithmetic",
        "value_type": "real_text_optional",
        "rigor_class": "reference_aware",
        "source_authority": "Curve Arithmetic",
        "provenance_policy": "local computed authority or labeled unambiguous catalog fallback",
        "version": 1,
    },
    # Parameter / chart identity.
    {
        "id": "parameter.native",
        "label": "Native parameter",
        "category": "parameter_geometry",
        "value_type": "rational_text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "stored native-family coordinate",
        "version": 1,
    },
    {
        "id": "parameter.chart",
        "label": "Chart parameter",
        "category": "parameter_geometry",
        "value_type": "rational_text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "stored chart coordinate",
        "version": 1,
    },
    {
        "id": "parameter.numerator_abs",
        "label": "|native numerator|",
        "category": "parameter_geometry",
        "value_type": "integer_optional",
        "rigor_class": "descriptive_derived",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "exact rational parse of stored native parameter",
        "version": 1,
    },
    {
        "id": "parameter.denominator",
        "label": "Native denominator",
        "category": "parameter_geometry",
        "value_type": "integer_optional",
        "rigor_class": "descriptive_derived",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "exact rational parse of stored native parameter",
        "version": 1,
    },
    {
        "id": "parameter.denominator_band",
        "label": "Denominator decade",
        "category": "parameter_geometry",
        "value_type": "integer_optional",
        "rigor_class": "descriptive_derived",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "base-10 order of stored native denominator",
        "version": 1,
    },
    {
        "id": "parameter.family_key",
        "label": "Native family key",
        "category": "parameter_geometry",
        "value_type": "text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "stored native family key",
        "version": 1,
    },
    {
        "id": "parameter.chart_id",
        "label": "Chart id",
        "category": "parameter_geometry",
        "value_type": "text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Retained curve provenance",
        "provenance_policy": "stored chart id and map fingerprint",
        "version": 1,
    },
    # Mordell-Weil numerical geometry.
    {
        "id": "mw.basis_count",
        "label": "MW basis count",
        "category": "mw_geometry",
        "value_type": "integer_optional",
        "rigor_class": "numerical",
        "source_authority": "MW Geometry summary",
        "provenance_policy": "latest persisted lattice analyzed numerically",
        "version": 1,
    },
    {
        "id": "mw.lattice_determinant",
        "label": "Lattice determinant",
        "category": "mw_geometry",
        "value_type": "real_optional",
        "rigor_class": "numerical",
        "source_authority": "MW Geometry summary",
        "provenance_policy": "latest persisted lattice numerical summary",
        "version": 1,
    },
    {
        "id": "mw.condition_number",
        "label": "Lattice condition number",
        "category": "mw_geometry",
        "value_type": "real_optional",
        "rigor_class": "numerical",
        "source_authority": "MW Geometry summary",
        "provenance_policy": "reconstructed from latest persisted Gram matrix",
        "version": 1,
    },
    {
        "id": "mw.weakest_ratio",
        "label": "Weakest eigenvalue ratio",
        "category": "mw_geometry",
        "value_type": "real_optional",
        "rigor_class": "numerical",
        "source_authority": "MW Geometry summary",
        "provenance_policy": "reconstructed from latest persisted Gram matrix",
        "version": 1,
    },
    {
        "id": "mw.strongest_correlation",
        "label": "Strongest generator correlation",
        "category": "mw_geometry",
        "value_type": "real_optional",
        "rigor_class": "numerical",
        "source_authority": "MW Geometry summary",
        "provenance_policy": "largest absolute pair correlation in latest persisted Gram matrix",
        "version": 1,
    },
    # Candidate context.
    {
        "id": "candidate.ranking_value",
        "label": "Candidate ranking value",
        "category": "search_heuristic",
        "value_type": "real_optional",
        "rigor_class": "heuristic",
        "source_authority": "Candidate ranking/provenance",
        "provenance_policy": "stored score/ranking value; never recomputed",
        "version": 1,
    },
    {
        "id": "candidate.ranking_kind",
        "label": "Candidate ranking kind",
        "category": "search_heuristic",
        "value_type": "text",
        "rigor_class": "heuristic_metadata",
        "source_authority": "Candidate ranking/provenance",
        "provenance_policy": "typed persisted ranking kind",
        "version": 1,
    },
    {
        "id": "candidate.native_parameter",
        "label": "Candidate native parameter",
        "category": "parameter_geometry",
        "value_type": "rational_text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Candidate ranking/provenance",
        "provenance_policy": "stored native candidate identity",
        "version": 1,
    },
    {
        "id": "candidate.chart_parameter",
        "label": "Candidate chart parameter",
        "category": "parameter_geometry",
        "value_type": "rational_text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Candidate ranking/provenance",
        "provenance_policy": "stored chart candidate identity",
        "version": 1,
    },
    {
        "id": "candidate.family_key",
        "label": "Candidate native family key",
        "category": "parameter_geometry",
        "value_type": "text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Candidate ranking/provenance",
        "provenance_policy": "stored native family key",
        "version": 1,
    },
    {
        "id": "candidate.chart_id",
        "label": "Candidate chart id",
        "category": "parameter_geometry",
        "value_type": "text_optional",
        "rigor_class": "descriptive_identity",
        "source_authority": "Candidate ranking/provenance",
        "provenance_policy": "stored chart id and map fingerprint",
        "version": 1,
    },
)

_DIMENSION_MAP = {rec["id"]: dict(rec) for rec in _DIMENSIONS}
LANDSCAPE_DIMENSION_REGISTRY_HASH = hashlib.sha256(
    json.dumps(
        {
            "registry_version": LANDSCAPE_DIMENSION_REGISTRY_VERSION,
            "dimensions": _DIMENSIONS,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

_FEATURE_KINDS = {
    "bad_prime_contains",
    "bad_prime_superset",
    "root_number_equals",
    "denominator_divisible_by",
}


def landscape_dimensions():
    """Return the immutable public registry as copied dictionaries."""
    return [dict(rec) for rec in _DIMENSIONS]


def get_landscape_dimension(dimension_id):
    try:
        return dict(_DIMENSION_MAP[str(dimension_id)])
    except KeyError:
        raise ValueError(f"unknown Landscape dimension {dimension_id!r}") from None


def _record(dimension_id, value, provenance):
    spec = get_landscape_dimension(dimension_id)
    return {
        **spec,
        "value": value,
        "provenance": dict(provenance or {}),
    }


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _native_fraction(curve):
    value = curve.get("native_parameter")
    if value in (None, ""):
        value = curve.get("parameter")
    try:
        return Fraction(str(value))
    except Exception:
        return None


def _denominator_band(denominator):
    if denominator is None or denominator <= 0:
        return None
    band = 0
    value = int(denominator)
    while value >= 10:
        value //= 10
        band += 1
    return band


def _point_counts(db, curve_id):
    if not _table_exists(db, "points"):
        return 0, 0
    row = db.execute(
        """
        SELECT
          SUM(exact_verified=1 AND rigorous_independent=1) AS witnesses,
          SUM(
            exact_verified=1 AND rigorous_independent=0
            AND independence_status IN ('unknown','inconclusive')
          ) AS unresolved
        FROM points
        WHERE curve_id=?
        """,
        (int(curve_id),),
    ).fetchone()
    return int(row["witnesses"] or 0), int(row["unresolved"] or 0)


def _latest_lattice_summary(db, curve_id):
    if not _table_exists(db, "mw_lattices"):
        return None, None
    row = db.execute(
        "SELECT * FROM mw_lattices WHERE curve_id=? ORDER BY id DESC LIMIT 1",
        (int(curve_id),),
    ).fetchone()
    if row is None:
        return None, None
    return analyze_lattice(row), int(row["id"])


def resolve_curve_landscape_dimensions(db, curve_id, *, dimension_ids=None):
    """Resolve registered curve dimensions without changing scientific state."""
    state = get_curve_research_state(db, int(curve_id))
    curve = dict(state["curve"])
    arithmetic = get_curve_arithmetic_state(db, int(curve_id))
    family = family_evidence_state_for_curve(db, curve)
    witnesses, unresolved = _point_counts(db, int(curve_id))
    lattice, lattice_id = _latest_lattice_summary(db, int(curve_id))
    native = _native_fraction(curve)

    generic_exact = family.get("exact_generic_rank")
    surplus = (
        None
        if generic_exact is None or family.get("rank_inconsistent")
        else max(0, int(state["rigorous_lower"]) - int(generic_exact))
    )

    arithmetic_provenance = dict(arithmetic.get("provenance") or {})
    bad_primes = arithmetic.get("bad_primes")
    if bad_primes is not None:
        bad_primes = list(bad_primes)

    strongest = None if lattice is None else lattice.get("strongest_pair")
    strongest_corr = (
        None
        if strongest is None
        else strongest.get("absolute_correlation")
    )

    values = {
        "rank.rigorous_lower": _record(
            "rank.rigorous_lower",
            int(state["rigorous_lower"]),
            {
                "authority": "Curve Research State",
                "latest_rank_evidence_id": state.get("latest_rank_evidence_id"),
                "rank_inconsistent": bool(state.get("rank_inconsistent")),
            },
        ),
        "rank.rigorous_upper": _record(
            "rank.rigorous_upper",
            state.get("rigorous_upper"),
            {
                "authority": "Curve Research State",
                "latest_rank_evidence_id": state.get("latest_rank_evidence_id"),
                "rank_inconsistent": bool(state.get("rank_inconsistent")),
            },
        ),
        "rank.exact_rank": _record(
            "rank.exact_rank",
            state.get("exact_rank"),
            {
                "authority": "Curve Research State",
                "latest_rank_evidence_id": state.get("latest_rank_evidence_id"),
                "rank_inconsistent": bool(state.get("rank_inconsistent")),
            },
        ),
        "rank.generic_exact": _record(
            "rank.generic_exact",
            generic_exact,
            {
                "authority": "Family Evidence",
                "family_key": family.get("family_key"),
                "family_sha256": family.get("family_sha256"),
                "rank_inconsistent": bool(family.get("rank_inconsistent")),
                "exact_evidence_ids": list(family.get("exact_evidence_ids") or []),
                "reason": family.get("reason"),
            },
        ),
        "rank.specialization_surplus": _record(
            "rank.specialization_surplus",
            surplus,
            {
                "authority": "Curve Research State + Family Evidence",
                "curve_rigorous_lower": int(state["rigorous_lower"]),
                "family_evidence_exact": generic_exact,
                "family_rank_inconsistent": bool(family.get("rank_inconsistent")),
            },
        ),
        "points.rigorous_witness_count": _record(
            "points.rigorous_witness_count",
            witnesses,
            {"authority": "Point Ledger", "curve_id": int(curve_id)},
        ),
        "points.unresolved_exact_count": _record(
            "points.unresolved_exact_count",
            unresolved,
            {"authority": "Point Ledger", "curve_id": int(curve_id)},
        ),
    }

    for dimension_id, field in (
        ("arithmetic.conductor", "conductor"),
        ("arithmetic.log_conductor", "log_conductor"),
        ("arithmetic.discriminant", "discriminant"),
        ("arithmetic.root_number", "root_number"),
        ("arithmetic.torsion", "torsion"),
        ("arithmetic.bad_primes", "bad_primes"),
        ("arithmetic.naive_height", "naive_height"),
        ("arithmetic.faltings_height", "faltings_height"),
    ):
        values[dimension_id] = _record(
            dimension_id,
            bad_primes if field == "bad_primes" else arithmetic.get(field),
            {
                "authority": "Curve Arithmetic",
                "field": field,
                "field_provenance": dict(arithmetic_provenance.get(field) or {}),
                "conflict": field in set(arithmetic.get("conflicts") or []),
            },
        )

    values["arithmetic.bad_prime_count"] = _record(
        "arithmetic.bad_prime_count",
        None if bad_primes is None else len(bad_primes),
        {
            "authority": "Curve Arithmetic",
            "derived_from": "arithmetic.bad_primes",
            "field_provenance": dict(arithmetic_provenance.get("bad_primes") or {}),
        },
    )

    values.update(
        {
            "parameter.native": _record(
                "parameter.native",
                curve.get("native_parameter") or curve.get("parameter"),
                {
                    "authority": "Retained curve provenance",
                    "native_family_key": curve.get("native_family_key"),
                    "native_family_spec": curve.get("native_family_spec"),
                },
            ),
            "parameter.chart": _record(
                "parameter.chart",
                curve.get("chart_parameter") or curve.get("parameter"),
                {
                    "authority": "Retained curve provenance",
                    "chart_id": curve.get("chart_id"),
                    "chart_map_fingerprint": curve.get("chart_map_fingerprint"),
                },
            ),
            "parameter.numerator_abs": _record(
                "parameter.numerator_abs",
                None if native is None else abs(int(native.numerator)),
                {"authority": "Retained curve provenance", "parsed_exactly": native is not None},
            ),
            "parameter.denominator": _record(
                "parameter.denominator",
                None if native is None else int(native.denominator),
                {"authority": "Retained curve provenance", "parsed_exactly": native is not None},
            ),
            "parameter.denominator_band": _record(
                "parameter.denominator_band",
                None if native is None else _denominator_band(int(native.denominator)),
                {
                    "authority": "Retained curve provenance",
                    "derived_from": "parameter.denominator",
                    "band_definition": "floor(log10(denominator))",
                },
            ),
            "parameter.family_key": _record(
                "parameter.family_key",
                curve.get("native_family_key"),
                {
                    "authority": "Retained curve provenance",
                    "family_spec": curve.get("native_family_spec"),
                    "family_sha256": curve.get("family_sha256"),
                },
            ),
            "parameter.chart_id": _record(
                "parameter.chart_id",
                curve.get("chart_id"),
                {
                    "authority": "Retained curve provenance",
                    "chart_map_fingerprint": curve.get("chart_map_fingerprint"),
                },
            ),
        }
    )

    lattice_provenance = {
        "authority": "MW Geometry summary",
        "lattice_id": lattice_id,
        "numerical_only": True,
    }
    values.update(
        {
            "mw.basis_count": _record(
                "mw.basis_count",
                None if lattice is None else lattice.get("basis_count"),
                lattice_provenance,
            ),
            "mw.lattice_determinant": _record(
                "mw.lattice_determinant",
                None if lattice is None else lattice.get("determinant"),
                lattice_provenance,
            ),
            "mw.condition_number": _record(
                "mw.condition_number",
                None if lattice is None else lattice.get("condition"),
                lattice_provenance,
            ),
            "mw.weakest_ratio": _record(
                "mw.weakest_ratio",
                None if lattice is None else lattice.get("weakest_ratio"),
                lattice_provenance,
            ),
            "mw.strongest_correlation": _record(
                "mw.strongest_correlation",
                strongest_corr,
                lattice_provenance,
            ),
        }
    )

    if dimension_ids is None:
        wanted = [
            rec["id"] for rec in _DIMENSIONS
            if not rec["id"].startswith("candidate.")
        ]
    else:
        wanted = [str(value) for value in dimension_ids]
        for dimension_id in wanted:
            if dimension_id.startswith("candidate."):
                raise ValueError(
                    f"candidate dimension {dimension_id!r} requires candidate context"
                )
            get_landscape_dimension(dimension_id)
    return {dimension_id: values[dimension_id] for dimension_id in wanted}


def resolve_candidate_landscape_dimensions(row, *, source_kind):
    """Resolve typed candidate dimensions from the shared ranking authority."""
    source_kind = str(source_kind)
    if source_kind == "candidate_pool":
        state = pool_candidate_ranking_state(row)
    elif source_kind == "pipeline_candidate":
        state = pipeline_candidate_ranking_state(row)
    else:
        raise ValueError(f"unsupported Landscape candidate source {source_kind!r}")

    provenance = {
        "authority": "Candidate ranking/provenance",
        "source_kind": state.get("source_kind"),
        "candidate_id": state.get("candidate_id"),
        "score_provenance": dict(state.get("score_provenance") or {}),
    }
    return {
        "candidate.ranking_value": _record(
            "candidate.ranking_value",
            state.get("ranking_value"),
            provenance,
        ),
        "candidate.ranking_kind": _record(
            "candidate.ranking_kind",
            state.get("ranking_kind"),
            provenance,
        ),
        "candidate.native_parameter": _record(
            "candidate.native_parameter",
            state.get("native_parameter"),
            {
                **provenance,
                "native_family_spec": state.get("native_family_spec"),
            },
        ),
        "candidate.chart_parameter": _record(
            "candidate.chart_parameter",
            state.get("chart_parameter"),
            {
                **provenance,
                "chart_map_fingerprint": state.get("chart_map_fingerprint"),
            },
        ),
        "candidate.family_key": _record(
            "candidate.family_key",
            state.get("native_family_key"),
            {
                **provenance,
                "native_family_spec": state.get("native_family_spec"),
            },
        ),
        "candidate.chart_id": _record(
            "candidate.chart_id",
            state.get("chart_id"),
            {
                **provenance,
                "chart_map_fingerprint": state.get("chart_map_fingerprint"),
            },
        ),
    }


def resolve_frozen_candidate_landscape_dimensions(member):
    """Project Candidate dimensions from one frozen R5 cohort member.

    R5 freezes ranking/provenance at cohort creation. Landscape UI must use that
    frozen snapshot rather than re-reading a mutable Candidate row.
    """
    member = dict(member or {})
    ranking = dict(member.get("ranking") or {})
    identity = (
        dict(member.get("curve_identity") or {})
        if str(member.get("kind") or "") == "pipeline_candidate"
        else member
    )
    provenance = {
        "authority": "Frozen Landscape cohort Candidate provenance",
        "source_kind": member.get("kind"),
        "candidate_id": member.get("candidate_id"),
        "score_provenance": dict(ranking.get("score_provenance") or {}),
        "frozen": True,
    }
    return {
        "candidate.ranking_value": _record(
            "candidate.ranking_value",
            ranking.get("ranking_value"),
            provenance,
        ),
        "candidate.ranking_kind": _record(
            "candidate.ranking_kind",
            ranking.get("ranking_kind"),
            provenance,
        ),
        "candidate.native_parameter": _record(
            "candidate.native_parameter",
            identity.get("native_parameter"),
            {
                **provenance,
                "native_family_spec": identity.get("native_family_spec"),
            },
        ),
        "candidate.chart_parameter": _record(
            "candidate.chart_parameter",
            identity.get("chart_parameter"),
            {
                **provenance,
                "chart_map_fingerprint": identity.get("chart_map_fingerprint"),
            },
        ),
        "candidate.family_key": _record(
            "candidate.family_key",
            identity.get("native_family_key"),
            {
                **provenance,
                "native_family_spec": identity.get("native_family_spec"),
            },
        ),
        "candidate.chart_id": _record(
            "candidate.chart_id",
            identity.get("chart_id"),
            {
                **provenance,
                "chart_map_fingerprint": identity.get("chart_map_fingerprint"),
            },
        ),
    }


def normalize_landscape_feature(spec):
    """Validate one user/config-defined descriptive feature.

    Feature definitions are research-analysis configuration. They never become
    rank evidence or automatic Pipeline rules.
    """
    if not isinstance(spec, dict):
        raise ValueError("Landscape feature must be an object")
    kind = str(spec.get("kind") or "").strip()
    if kind not in _FEATURE_KINDS:
        raise ValueError(f"unsupported Landscape feature kind {kind!r}")

    if kind in {"bad_prime_contains", "denominator_divisible_by"}:
        try:
            prime = int(spec.get("prime"))
        except (TypeError, ValueError):
            raise ValueError("Landscape feature prime must be an integer > 1") from None
        if prime <= 1:
            raise ValueError("Landscape feature prime must be an integer > 1")
        return {
            "feature_version": LANDSCAPE_FEATURE_DEFINITION_VERSION,
            "kind": kind,
            "prime": prime,
        }

    if kind == "bad_prime_superset":
        raw = spec.get("primes")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError("bad_prime_superset requires a non-empty primes list")
        try:
            primes = sorted({int(value) for value in raw})
        except (TypeError, ValueError):
            raise ValueError("Landscape feature primes must be integers > 1") from None
        if any(value <= 1 for value in primes):
            raise ValueError("Landscape feature primes must be integers > 1")
        return {
            "feature_version": LANDSCAPE_FEATURE_DEFINITION_VERSION,
            "kind": kind,
            "primes": primes,
        }

    try:
        value = int(spec.get("value"))
    except (TypeError, ValueError):
        raise ValueError("root_number_equals value must be -1 or 1") from None
    if value not in (-1, 1):
        raise ValueError("root_number_equals value must be -1 or 1")
    return {
        "feature_version": LANDSCAPE_FEATURE_DEFINITION_VERSION,
        "kind": kind,
        "value": value,
    }


def evaluate_landscape_feature(feature, dimensions):
    """Evaluate a normalized descriptive feature over resolved dimensions."""
    feature = normalize_landscape_feature(feature)
    kind = feature["kind"]

    if kind == "bad_prime_contains":
        values = dimensions["arithmetic.bad_primes"]["value"] or []
        return int(feature["prime"]) in {int(value) for value in values}

    if kind == "bad_prime_superset":
        values = {int(value) for value in (dimensions["arithmetic.bad_primes"]["value"] or [])}
        return set(feature["primes"]).issubset(values)

    if kind == "root_number_equals":
        return dimensions["arithmetic.root_number"]["value"] == int(feature["value"])

    denominator = dimensions["parameter.denominator"]["value"]
    return denominator is not None and int(denominator) % int(feature["prime"]) == 0
