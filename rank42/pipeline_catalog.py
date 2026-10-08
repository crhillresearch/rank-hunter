"""Declarative search-pipeline stage catalog.

The catalog describes *contracts*, not mathematical claims.  Pipeline ordering
is validated by capabilities that a stage may make available.  A runtime stage
can still find zero points or time out; the contract only says what kind of
evidence it is capable of producing.

Family-specific mathematics remains in family plugins.  Core stages operate on
an exact stored curve model and may invoke an optional family target adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from math import isqrt
from typing import Any

from rank42.constructive_artifact import (
    HEIGHT_KINDS,
    SUPPORTED_DIVISIONS,
    SUPPORTED_SLOPE_MODES,
)
from rank42.torsion import canonical_torsion_label


CATALOG_VERSION = 11
TARGET_MODES = ("family", "torsion", "general", "curve")
EVIDENCE_LEVELS = ("heuristic", "exact", "conditional", "rigorous", "control")
COST_LEVELS = ("cheap", "medium", "expensive")


@dataclass(frozen=True)
class PipelineStageSpec:
    id: str
    label: str
    category: str
    description: str
    targets: frozenset[str]
    requires: frozenset[str]
    provides: frozenset[str]
    evidence: str
    cost: str
    defaults: dict[str, Any]
    repeatable: bool = False
    conditional: str | None = None


_STAGE_LIST = (
    PipelineStageSpec(
        "nagao_screen",
        "Nagao Screen",
        "Candidates",
        "Generate a broad candidate set and rank it with Rank Hunter's historical Nagao-style finite-field score at a modest prime bound. This project-specific statistic is heuristic scheduling data and is not presented as the published Mestre–Nagao S(N,E) formula.",
        frozenset(TARGET_MODES),
        frozenset({"source"}),
        frozenset({"candidates", "exact_curve"}),
        "heuristic",
        "cheap",
        {"prime_bound": 523, "keep": 5000},
    ),
    PipelineStageSpec(
        "nagao_rescore",
        "Nagao Rescore",
        "Candidates",
        "Rescore only the survivors at a larger prime bound with the same Rank Hunter Nagao-style scorer, preserving scorer/prime provenance, then keep a smaller shortlist.",
        frozenset(TARGET_MODES),
        frozenset({"candidates"}),
        frozenset({"candidates", "exact_curve"}),
        "heuristic",
        "medium",
        {"prime_bound": 1979, "keep": 250},
        repeatable=True,
    ),
    PipelineStageSpec(
        "corpus_filter",
        "Library Filter",
        "Candidates",
        "Annotate/filter the final family shortlist against plugin-declared Research Libraries without importing historical library curves.",
        frozenset({"family", "torsion"}),
        frozenset({"candidates"}),
        frozenset({"candidates"}),
        "exact",
        "cheap",
        {"policy": "annotate"},
        conditional="Requires a built/ready Research Library for exclude-known or known-only policies. This stage must be the final candidate stage.",
    ),
    PipelineStageSpec(
        "quadratic_twist_sweep",
        "Quadratic Twist Sweep",
        "Transforms",
        "Fan one exact curve out into squarefree quadratic twists E^(d). The transform is exact; no rank is inherited from the parent.",
        frozenset({"family", "general", "curve"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population"}),
        "exact",
        "medium",
        {"d_values": [], "d_min": -50, "d_max": 50, "max_children": 24, "include_parent": False},
        repeatable=True,
    ),
    PipelineStageSpec(
        "targeted_twist_search",
        "Targeted Twist Search",
        "Transforms",
        "Generate exact quadratic twists, optionally filter by exact root-number sign, rank survivors with Rank Hunter's audited Nagao-style log-cardinality heuristic, and keep only the strongest children under explicit stage/per-twist hard budgets.",
        frozenset({"family", "general", "curve"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population", "prime_score"}),
        "heuristic",
        "expensive",
        {
            "d_values": [],
            "d_min": -200,
            "d_max": 200,
            "scan_limit": 200,
            "max_children": 12,
            "prime_bound": 199,
            "root_sign": "any",
            "timeout": 60,
            "per_twist_timeout": 10,
            "allow_partial_scores": True,
            "include_parent": False,
        },
        repeatable=True,
        conditional="Root number is exact; using its sign to prioritize rank is scheduling only, not a rank-parity proof. Score algorithm is cached-nagao-log-cardinality-v1 with explicit direct-source/cache provenance; partially covered scores are usable only when allow_partial_scores is enabled.",
    ),
    PipelineStageSpec(
        "rank_jump_base_change",
        "Rank-Jump / Base-Change",
        "Transforms",
        "Invoke an exact family-plugin transform hook that constructs published or researcher-supplied rank-jump/base-change children. Core records lineage but never invents the family map.",
        frozenset({"family"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population"}),
        "exact",
        "expensive",
        {"max_children": 32, "timeout": 30, "include_parent": False},
        repeatable=True,
        conditional="Requires the active family adapter to define derive_pipeline_transform(context) for transform_kind=rank_jump_base_change. The hook runs in a core-owned subprocess with the configured hard timeout.",
    ),
    PipelineStageSpec(
        "parameter_pullback",
        "Möbius Parameter Transform",
        "Transforms",
        "Apply exact nondegenerate Möbius maps to the current family parameter and continue on the resulting rational specializations. This is a specialization move, not a symbolic family pullback.",
        frozenset({"family"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population"}),
        "exact",
        "cheap",
        {
            "maps": [[1, 1, 0, 1], [1, -1, 0, 1], [1, 0, 1, 1]],
            "include_parent": False,
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "isogeny_walk",
        "Isogeny Walk",
        "Transforms",
        "Enumerate rational prime-degree isogeny neighbors and continue the search on alternate models. Q-rank is invariant, but Rank Hunter does not auto-copy the parent's rank evidence.",
        frozenset({"family", "general", "curve"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population", "point_source", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "degrees": [2, 3, 5, 7, 11, 13],
            "max_children": 12,
            "include_parent": False,
            "transfer_basis": True,
            "timeout": 60,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "covering_selmer_branch",
        "Covering / Selmer Branch",
        "Arithmetic",
        "Run several independent classical 2-descent/Selmer/covering branches on the same curve, persist rigorous uppers, collect exact points, then exact-certify any rank growth.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "upper_bound_possible", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "engines": ["simon_known", "mwrank_selmer", "mwrank_coverings"],
            "timeout": 60,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        repeatable=True,
        conditional="Each branch may time out independently. Timeouts and failures are inconclusive.",
    ),
    PipelineStageSpec(
        "section_height_shell",
        "Section Search Shell",
        "Constructive",
        "Ask the active family plugin for generic-section construction artifacts in an explicitly named search-height notion. Artifacts are unverified by default and become exact only through the typed section verifier; they are not specialization-rank evidence.",
        frozenset({"family"}),
        frozenset({"exact_curve"}),
        frozenset({"constructive_sections"}),
        "exact",
        "medium",
        {
            "height": 10,
            "height_mode": "exact",
            "height_kind": "family_enumeration_shell",
            "height_normalization": "family_defined",
            "coefficient_bound": 8,
            "max_sections": 128,
            "timeout": 30,
        },
        repeatable=True,
        conditional="Requires derive_pipeline_section_shell(). Height kind/normalization are explicit. Core never invents a family section basis or height pairing, and producer-declared exactness is ignored unless a typed section artifact passes a registered verifier.",
    ),
    PipelineStageSpec(
        "trace_section_constructor",
        "Trace Section Constructor",
        "Constructive",
        "Ask the family plugin for trace-section artifacts bound to an exact source section and explicit extension/Galois provenance. Artifacts become exact only after a registered trace verifier succeeds.",
        frozenset({"family"}),
        frozenset({"exact_curve", "constructive_sections"}),
        frozenset({"trace_sections"}),
        "exact",
        "medium",
        {"extension_degree": 2, "max_sections": 128, "timeout": 30},
        repeatable=True,
        conditional="Requires derive_pipeline_trace_sections(). Producer-declared exactness is ignored; exact_construction requires an exact verified source section plus a typed trace artifact accepted by a registered verifier.",
    ),
    PipelineStageSpec(
        "forced_bisection_constructor",
        "Forced Bisection / Division Constructor",
        "Constructive",
        "Ask the family plugin for typed divisibility conditions such as nP=Q, bound to an exact source trace and an explicit supported division/slope mode. Exactness requires verifier success.",
        frozenset({"family"}),
        frozenset({"exact_curve", "trace_sections"}),
        frozenset({"bisection_conditions"}),
        "exact",
        "medium",
        {
            "division": 2,
            "slope_mode": "forced_tangent",
            "max_conditions": 128,
            "timeout": 30,
        },
        repeatable=True,
        conditional="Requires derive_pipeline_bisection_conditions(). Supported divisions are 2,3,4 and slope modes are forced_tangent, division_polynomial, or family_exact. Producer-declared exactness is ignored unless a typed parent-bound condition passes a registered verifier.",
    ),
    PipelineStageSpec(
        "square_condition_specializer",
        "Square-Condition Specializer",
        "Constructive",
        "Search plugin-supplied exact division conditions for rational specializations behind a core-owned hard timeout. The result records requested/completed test and domain coverage; missing coverage cannot be reported as completed. A child is emitted only when a typed square-specialization artifact binds the exact parent-condition hash and a registered verifier independently confirms condition(child_parameter)=square_value while core checks square_root^2=square_value. New specializations never inherit the parent heuristic score.",
        frozenset({"family"}),
        frozenset({"exact_curve", "bisection_conditions"}),
        frozenset({"exact_curve", "derived_population", "point_source"}),
        "exact",
        "expensive",
        {
            "numerator_abs": 500,
            "denominator_max": 500,
            "max_tests": 200000,
            "max_children": 64,
            "timeout": 30,
            "include_parent": False,
        },
        repeatable=True,
        conditional="Requires derive_pipeline_square_specializations(). The hook is process-isolated under the configured hard timeout; Plugin status/reason/error and explicit tests/domain coverage are preserved. Exact fan-out requires a verified parent condition plus typed rank42.constructive_square_specialization.v1 artifact accepted by a registered verifier; a bare rational square is insufficient. Child heuristic scores are cleared unless a later child-scoring stage recomputes them.",
    ),
    PipelineStageSpec(
        "constructive_rank_jump_loop",
        "Constructive Rank-Jump Loop",
        "Strategies",
        "Run successive section-height shells through trace construction, forced division, and exact square-condition specialization, durably checkpointing each completed shell so resume starts at the first unfinished height without repeating verified symbolic work.",
        frozenset({"family"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population", "point_source"}),
        "exact",
        "expensive",
        {
            "heights": [6, 8, 10, 12],
            "height_mode": "exact",
            "coefficient_bound": 10,
            "extension_degree": 2,
            "division": 2,
            "slope_mode": "forced",
            "numerator_abs": 1000,
            "denominator_max": 1000,
            "max_tests": 500000,
            "max_children": 96,
            "section_timeout": 30,
            "trace_timeout": 30,
            "division_timeout": 30,
            "square_timeout": 30,
            "retry_policy": "automatic",
            "stop_on_first_success": False,
            "include_parent": True,
        },
        repeatable=True,
        conditional="Requires the four constructive-family adapter hooks. Core exact-checks every verified symbolic artifact, square witness, and constructed child point; no rank is inherited. Each height shell is a durable candidate-local strategy checkpoint. Completed shells are reused on resume; the first incomplete shell remains the resume token and carries the declared retry-policy metadata.",
    ),
    PipelineStageSpec(
        "upper_bound_rescue_ladder",
        "Upper-Bound Rescue Ladder",
        "Strategies",
        "Try several bounded rigorous rank-upper routes in sequence: quick PARI, exact Q-isogenous PARI retries, optional short 2-Selmer, known-basis covering descents, and true higher-descent plugin hooks. Any timeout or unsupported branch is inconclusive and the candidate continues.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"upper_bound_possible", "point_source", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "pari_enabled": True,
            "pari_timeout": 3,
            "isogeny_pari_enabled": True,
            "isogeny_degrees": [2, 3, 5, 7, 11, 13],
            "isogeny_max_models": 6,
            "isogeny_discovery_timeout": 15,
            "isogeny_pari_timeout": 3,
            "selmer_enabled": True,
            "selmer_timeout": 10,
            "selmer_first_limit": 12,
            "selmer_second_limit": 6,
            "known_basis_coverings": True,
            "simon_timeout": 12,
            "simon_lim1": 5,
            "simon_lim3": 40,
            "simon_limbigprime": 0,
            "covering_timeout": 12,
            "covering_first_limit": 12,
            "covering_second_limit": 6,
            "higher_descent_hooks": True,
            "higher_levels": [4, 8, 12],
            "higher_timeout": 20,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        repeatable=True,
        conditional="Every accepted upper must come from rigorous engine output. Prime-degree isogeny discovery is process-isolated under a per-degree hard timeout, and accepted isogenous PARI uppers use the shared rigorous interval promotion path. The Simon rescue route is restricted to deterministic LIMBIGPRIME=0. Higher-descent plugin hooks are hard-isolated, and plugin upper claims require the typed core-verifiable rigorous-upper certificate path before authoritative rank state can change. Q-isogenous retries use exact rational isogenies and rank invariance; heuristic signals never become upper bounds.",
    ),
    PipelineStageSpec(
        "large_height_generator_hunt",
        "Large-Height Generator Hunt",
        "Strategies",
        "Adaptive generator-recovery strategy: try structured descent/covering hooks, then search MW feedback geometry and higher denominator bands before spending time on late saturation/LLL rescue. Nested completed/partial/timeout/error/inconclusive/unsupported coverage is aggregated honestly; budget exhaustion is reported only when every configured step actually completed.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible", "upper_bound_possible"}),
        "rigorous",
        "expensive",
        {
            "descent_levels": [2, 4, 8, 12],
            "descent_timeout": 120,
            "max_coverings": 24,
            "covering_height": 1000000,
            "covering_timeout": 60,
            "padic_enabled": True,
            "padic_prime": 2,
            "padic_precision": 60,
            "padic_timeout": 180,
            "late_saturation_enabled": True,
            "saturation_bounds": [7, 31],
            "saturation_timeout": 20,
            "stop_on_unit_index": True,
            "late_lattice_enabled": True,
            "lattice_precision_bits": 256,
            "mw_growth_rounds": 6,
            "mw_growth_anchors": 32,
            "mw_growth_pool": 320,
            "denominator_bands": [[2, 100], [101, 1000], [1001, 10000], [10001, 100000]],
            "denominator_charts": 8,
            "denominator_heights": [100000, 1000000],
            "denominator_timeout": 12,
            "certificate_timeout": 180,
            "exact_candidates": 128,
            "stop_on_growth": True,
            "wall_timeout": 1800,
            "retry_policy": "automatic",
            "retry_timeout": 3600,
        },
        repeatable=True,
        conditional="True higher descent and p-adic branches run only when real engine hooks exist. Unsupported or incomplete branches remain explicit in strategy coverage; only exact independence certification raises rank. The strategy never calls an incomplete plan exhausted. A durable strategy occurrence records each substep/returned round/denominator band and automatic retry resumes from the first unfinished checkpoint under one total wall-clock budget. Stop metadata comes from one shared state: stop_reason, goal_reached, growth_stop_reached, and witness_requirement_met. Strategy lower/upper/exact-rank progress comes from reducer-backed Curve Research State rather than compatibility curve columns.",
    ),
    PipelineStageSpec(
        "record_breaker_lane",
        "Record Breaker Lane",
        "Strategies",
        "Escalate only curves whose rigorous lower bound has reached a configured record-hunt threshold. Curves below the threshold are skipped, not pruned, so the rest of the campaign continues.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible", "upper_bound_possible"}),
        "rigorous",
        "expensive",
        {
            "minimum_rank_mode": "fixed",
            "minimum_rank": 25,
            "descent_levels": [4, 8, 12],
            "descent_timeout": 120,
            "max_coverings": 32,
            "covering_height": 10000000,
            "covering_timeout": 120,
            "padic_enabled": True,
            "padic_prime": 2,
            "padic_precision": 80,
            "padic_timeout": 300,
            "late_saturation_enabled": True,
            "saturation_bounds": [31, 127],
            "saturation_timeout": 60,
            "stop_on_unit_index": False,
            "late_lattice_enabled": True,
            "lattice_precision_bits": 384,
            "mw_growth_rounds": 12,
            "mw_growth_anchors": 128,
            "mw_growth_pool": 768,
            "mw_growth_heights": [1000000, 10000000, 100000000],
            "mw_growth_deep_keep": 32,
            "mw_growth_timeout": 20,
            "mw_growth_reduce_timeout": 60,
            "denominator_bands": [[1000001, 10000000], [10000001, 100000000]],
            "denominator_charts": 128,
            "denominator_heights": [1000000, 10000000, 100000000],
            "denominator_timeout": 30,
            "certificate_timeout": 600,
            "exact_candidates": 256,
            "stop_on_growth": False,
            "wall_timeout": 7200,
            "retry_policy": "automatic",
            "retry_timeout": 14400,
        },
        repeatable=True,
        conditional="The threshold is checked only against the persisted rigorous lower bound. Entering this lane never follows from Nagao/root-number/point-count heuristics alone. Once entered, the lane preserves the nested Large-Height Generator Hunt scientific status instead of rewriting partial/timeout/error/inconclusive work as completed, and inherits its durable checkpoint/resume plan and total wall-clock budget. Record-mode goal metadata requires both the rigorous lower bound and the rigorous witness basis. Builder/runtime budget accounting exposes both the raw configured denominator portfolio and the runnable timeout envelope; every denominator chart/height attempt uses the durable Pipeline point-attempt checkpoint. Entry threshold and strategy progress read reducer-backed Curve Research State for rigorous lower/upper/exact rank, with exact witness-basis completeness checked separately for record-mode goals.",
    ),
    PipelineStageSpec(
        "surface_fibration_switch",
        "Surface / Fibration Switch",
        "Transforms",
        "Ask the active family plugin for an exact alternative elliptic fibration/surface model and continue the pipeline on the derived family or curve with full lineage.",
        frozenset({"family"}),
        frozenset({"exact_curve"}),
        frozenset({"exact_curve", "derived_population"}),
        "exact",
        "expensive",
        {"max_children": 16, "timeout": 30, "include_parent": False},
        repeatable=True,
        conditional="Requires family-owned derive_pipeline_transform() support for transform_kind=surface_fibration_switch. The hook runs in a core-owned subprocess with the configured hard timeout.",
    ),
    PipelineStageSpec(
        "prime_table_cache",
        "Prime Table Cache",
        "Primes",
        "Cache reusable exact Frobenius data for good primes below a bound. Full bad-prime factorization is deferred unless explicitly requested.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"prime_table"}),
        "exact",
        "medium",
        {"prime_bound": 2000, "include_all_bad": False, "eager_bad_primes": False, "bad_prime_timeout": 20},
    ),
    PipelineStageSpec(
        "multi_scale_frobenius",
        "Multi-Scale Frobenius",
        "Primes",
        "Compute Rank Hunter's historical log-cardinality Nagao-style score cumulatively and by prime band from cached #E(F_p). This is an RH scheduling heuristic, not the published classical Mestre–Nagao S(N,E) statistic.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"prime_score"}),
        "heuristic",
        "medium",
        {"bounds": [523, 1979, 5000]},
    ),
    PipelineStageSpec(
        "mestre_nagao_ensemble",
        "Rank Hunter Prime-Signal Ensemble",
        "Primes",
        "Combine three RH prime signals (log-cardinality, sign-reversed a_p log(p)/p, and normalized trace) into one experimental consensus scheduling score. This is not the published multi-value Mestre–Nagao classifier.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"prime_score"}),
        "heuristic",
        "cheap",
        {"prime_bound": 5000},
    ),
    PipelineStageSpec(
        "frobenius_persistence",
        "Frobenius Persistence",
        "Primes",
        "Rank Hunter experimental scheduling heuristic: score how consistently the RH Nagao-style signal survives across disjoint prime bands, using the weakest normalized band. This is not a published standard statistic or rank proof.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"prime_score"}),
        "heuristic",
        "cheap",
        {"bounds": [523, 1979, 5000, 10000]},
    ),
    PipelineStageSpec(
        "explicit_formula_indicator",
        "Explicit-Formula Indicator",
        "Primes",
        "Compute a smoothed good-prime/prime-power explicit-formula proxy for central-zero pressure. This is not an analytic-rank bound.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"prime_score"}),
        "heuristic",
        "medium",
        {"prime_bound": 5000, "max_prime_power": 4},
    ),
    PipelineStageSpec(
        "mestre_bober_analytic_upper",
        "Conditional Analytic Rank Upper",
        "Arithmetic",
        "Run Sage's Mestre-Bober zero-sum analytic-rank upper computation in an isolated worker. The returned bound is conditional on GRH, is stored only as conditional analytic evidence, and can never become a rigorous Mordell-Weil upper or exact-rank certificate.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"conditional_analytic_upper"}),
        "conditional",
        "expensive",
        {
            "max_delta": 1.5,
            "adaptive": True,
            "ncpus": 1,
            "timeout": 60,
        },
        repeatable=True,
        conditional="Sage analytic_rank_upper_bound() uses the Mestre-Bober zero-sum method and is conditional on the Generalized Riemann Hypothesis for L(E,s). Larger Delta can tighten the analytic-rank bound but has exponential runtime; timeout/error is inconclusive and never changes rigorous rank state.",
    ),
    PipelineStageSpec(
        "brumer_kramer_classgroup_bound",
        "Cubic Class-Group / 2-Selmer Bound",
        "Arithmetic",
        "For curves with E(Q)[2]=0, compute the Brumer-Kramer class-group upper bound dim Sel_2(E/Q) <= g(E)+u(E)+n(E) from the cubic 2-division field and exact bad-reduction/splitting data. Fast class-group mode is GRH-conditional and persists only a conditional Mordell-Weil upper; unconditional class-group certification may promote the bound as rigorous.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"conditional_mw_upper", "upper_bound_possible"}),
        "conditional",
        "expensive",
        {
            "proof_mode": "grh",
            "timeout": 300,
        },
        repeatable=True,
        conditional="Applies only when E(Q)[2]=0. proof_mode='grh' calls Sage class_group(proof=False) and never changes rigorous rank state. proof_mode='unconditional' calls class_group(proof=True); only a completed unconditional result may populate rigorous_upper. Timeout/error/unsupported is inconclusive.",
    ),
    PipelineStageSpec(
        "cassels_tate_refinement",
        "Cassels–Tate Selmer Refinement",
        "Arithmetic",
        "Run PARI's unconditional 2-descent and 2-part Cassels pairing in an isolated worker. Persist the raw pre-pairing Selmer ceiling, the even pairing rank, and the refined rigorous Mordell-Weil upper separately; timeout/error or malformed proof attestation is inconclusive.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"upper_bound_possible", "cassels_tate_refinement"}),
        "rigorous",
        "expensive",
        {
            "effort": 0,
            "timeout": 300,
        },
        repeatable=True,
        conditional="PARI ellrank computes the 2-Selmer rank C, rational 2-torsion rank T, and even rank s of Sha[2]/2Sha[4] unconditionally, giving the refined upper C-T-s. The stage does not promote PARI's returned points or lower diagnostic; only the proof-attested refined upper enters rank evidence.",
    ),
    PipelineStageSpec(
        "isogeny_descent",
        "Isogeny Descent",
        "Arithmetic",
        "For curves with a rational 2-isogeny, run eclib/mwrank descent through each rational 2-isogeny and its dual, record the exact phi- and dual-phi Selmer dimensions, verify every route against Sage's exact maps, and promote only the resulting rigorous Mordell-Weil upper. This is descent, not an isogeny walk or a retry on a neighboring curve.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"upper_bound_possible", "isogeny_selmer_data"}),
        "rigorous",
        "expensive",
        {
            "degree": 2,
            "first_limit": 20,
            "second_limit": 8,
            "second_descent": True,
            "timeout": 300,
        },
        repeatable=True,
        conditional="Core v1 supports rational 2-isogeny descent only. Curves without a rational 2-isogeny are unsupported. Degree-3 isogeny descent is not claimed because the installed Sage stack delegates that computation to optional Magma. Timeout/error/malformed route evidence is inconclusive; returned lower and point-search diagnostics do not raise rigorous lower state.",
    ),
    PipelineStageSpec(
        "local_root_numbers",
        "Local Root Numbers",
        "Primes",
        "Compute the exact bad-prime local root factors and global root number; optionally use an exact sign filter for scheduling. With include_all_bad enabled, this may spend the bounded bad-prime factorization timeout and is not a trivial per-curve stage.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"root_number_known"}),
        "exact",
        "medium",
        {
            "global_sign": "any",
            "prune_mismatch": False,
            "prime_bound": 200,
            "include_all_bad": True,
            "bad_prime_timeout": 20,
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "bad_prime_fingerprint",
        "Bad-Prime Fingerprint",
        "Primes",
        "Record bad primes, Kodaira symbols, discriminant/conductor valuations, Tamagawa numbers and local root factors; optional exact scheduling filters. With include_all_bad enabled, this may spend the bounded bad-prime factorization timeout and is not a trivial per-curve stage.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"bad_prime_data"}),
        "exact",
        "medium",
        {
            "required_primes": [],
            "max_bad_primes": 0,
            "prune_mismatch": False,
            "prime_bound": 200,
            "include_all_bad": True,
            "bad_prime_timeout": 20,
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "torsion_mod_p_sieve",
        "Torsion mod-p Sieve",
        "Primes",
        "Use good-reduction injections into E(F_p) to rigorously reject impossible rational torsion targets by order divisibility and exact finite-group structure when available; passing is not proof.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"torsion_presieved"}),
        "exact",
        "cheap",
        {
            "torsion_group": "target",
            "prime_bound": 100,
            "max_primes": 12,
            "prune_obstructed": True,
        },
        conditional="'target' means the selected Torsion Group source; Family/General pipelines must choose a concrete Mazur group.",
    ),
    PipelineStageSpec(
        "local_solubility_sieve",
        "Local Solubility Sieve",
        "Primes",
        "Apply rigorous mod-p obstruction tests to stored degree-4 coverings and pre-sieve downstream point-centered quartics before ratpoints. Passing is not proof of Q_p solubility.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "prime_table"}),
        frozenset({"local_sieve_config"}),
        "exact",
        "medium",
        {
            "explicit_primes": [2, 3, 5, 7, 11],
            "include_bad": True,
            "max_prime": 97,
            "bad_prime_timeout": 20,
        },
    ),
    PipelineStageSpec(
        "exact_torsion",
        "Exact Torsion",
        "Arithmetic",
        "Compute Sage E(Q)_tors exactly in an isolated bounded worker. In torsion-target mode, only a completed exact mismatch rejects a fiber; timeout/error stays inconclusive.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"torsion_known"}),
        "exact",
        "medium",
        {"timeout": 30},
    ),
    PipelineStageSpec(
        "specialization_seeds",
        "Known Specialization Seeds",
        "Arithmetic",
        "Ask the active family plugin for preserved exact specialization point bundles and independently certify them before using any historical rank metadata as rigorous evidence.",
        frozenset({"family", "torsion"}),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible"}),
        "rigorous",
        "medium",
        {"certificate_timeout": 180},
        repeatable=True,
        conditional="Requires certified_specialization_points(); absent or unmatched historical bundles are a cheap no-op.",
    ),
    PipelineStageSpec(
        "family_baseline",
        "Family Sections",
        "Arithmetic",
        "Specialize plugin-provided generic sections and exact-certify their independence on the stored curve.",
        frozenset({"family", "torsion"}),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible"}),
        "rigorous",
        "medium",
        {"certificate_timeout": 120, "exact_candidates": 64},
        repeatable=True,
        conditional="Requires a family that exposes exact specialized section points; otherwise it safely contributes zero.",
    ),
    PipelineStageSpec(
        "specialization_injectivity_certificate",
        "Specialization Injectivity Certificate",
        "Arithmetic",
        "Certify the Gusic-Tadic specialization criterion for eligible FormulaFamily models and persist generic-family rank evidence separately from fiber rank evidence.",
        frozenset({"family", "torsion"}),
        frozenset({"exact_curve"}),
        frozenset({"family_injectivity_certificate", "generic_rank_evidence"}),
        "rigorous",
        "medium",
        {
            "timeout": 60,
            "max_divisors": 4096,
            "certificate_timeout": 120,
        },
        repeatable=True,
        conditional=(
            "Gusic-Tadic v1 applies only to exact FormulaFamily models "
            "y^2=x^3+A(t)x^2+B(t)x with A,B in Z[t] and exactly one "
            "nontrivial rational 2-torsion point. Unsupported models are not "
            "transformed implicitly."
        ),
    ),
    PipelineStageSpec(
        "root_number",
        "Root Number",
        "Arithmetic",
        "Compute the exact global root number through the shared bounded prime/local arithmetic service and publish it through Curve Arithmetic authority. It does not by itself promote rank.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"root_number_known"}),
        "exact",
        "medium",
        {"prime_bound": 200, "bad_prime_timeout": 20},
    ),
    PipelineStageSpec(
        "pari_upper_gate",
        "PARI Upper Gate",
        "Arithmetic",
        "Try a short rigorous PARI rank upper bound with a screening-budget timeout. Timeout/inconclusive attempts stay explicit; a fiber is eliminated only when an actual rigorous upper bound is below the goal.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"upper_bound_possible"}),
        "rigorous",
        "cheap",
        {"timeout": 2, "eliminate_below_goal": True},
    ),
    PipelineStageSpec(
        "selmer_bound",
        "mwrank 2-Descent Rank Upper",
        "Arithmetic",
        "Run bounded eclib/mwrank 2-descent and use mwrank rank_bound() as a rigorous Mordell-Weil rank upper bound. This is not the raw 2-Selmer rank. Timeout/inconclusive attempts are operationally complete; resume retry policy is explicit and defaults to manual.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"upper_bound_possible", "mwrank_rank_upper_possible"}),
        "rigorous",
        "expensive",
        {
            "timeout": 60,
            "first_limit": 20,
            "second_limit": 10,
            "retry_policy": "manual",
            "retry_timeout": 300,
        },
    ),
    PipelineStageSpec(
        "selmer_headroom",
        "mwrank Rank Headroom",
        "Arithmetic",
        "Rank unresolved fibers by the gap between the rigorous mwrank rank_bound() upper and current rigorous lower bound. This is not a raw Selmer-rank gap; unresolved descent/Sha effects may contribute, so it is only a scheduling heuristic.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "mwrank_rank_upper_possible"}),
        frozenset({"prime_score"}),
        "heuristic",
        "cheap",
        {},
    ),
    PipelineStageSpec(
        "small_point_density",
        "Small-Point Discovery Yield",
        "Points",
        "Rank Hunter experimental search-yield statistic: prepare one fixed exact search model for the stage, then run shallow denominator-1 searches at increasing heights and score fresh exact-point yield only from completed comparable rounds. This is not a mathematical density statistic. Global-minimal preparation is attempted once; if it fails, every round uses the same stored-model fallback. Timeout/error rounds are missing coverage, not zero yield. A completed no-hit round means only that no finite point was found in that configured model/height/denominator region; it is not evidence that no rational point or extra generator exists. Chart/height attempts are durably checkpointed; terminal timeout/error retries are controlled explicitly by manual, automatic, or escalated policy. Any rank growth still uses exact certification.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible", "heuristic_point_score"}),
        "heuristic",
        "medium",
        {"heights": [100, 1000, 10000], "timeout": 3, "retry_policy": "manual", "retry_timeout": 12, "certificate_timeout": 120, "exact_candidates": 32},
    ),
    PipelineStageSpec(
        "integral_seed",
        "Native Model Seed",
        "Points",
        "Search the exact stored/family model directly with bounded ratpoints, avoiding global-minimal-model preprocessing by default. Timeout/partial/error outcomes remain incomplete and are not reported as completed zero-yield searches. A completed no-hit search means only that no finite point was found in the configured model/height/denominator region; it does not prove nonexistence of rational points, extra generators, or rank growth. Chart/height attempts are durably checkpointed with explicit manual/automatic/escalated retry policy. Optional minimal-model mode remains available as a coordinate optimization.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source"}),
        "exact",
        "medium",
        {
            "heights": [1000, 10000],
            "timeout": 4,
            "retry_policy": "manual",
            "retry_timeout": 16,
            "model_mode": "stored",
            "model_prep_timeout": 8,
            "certify_after_search": False,
        },
    ),
    PipelineStageSpec(
        "simon_covering",
        "Simon 2-Covering (Legacy)",
        "Geometry",
        "Legacy/alternative Denis Simon two-descent search. Rank Hunter defaults to LIMBIGPRIME=0 so upper-bound evidence uses deterministic local tests only. A nonzero LIMBIGPRIME may still be used for point discovery, but its reported upper is treated as non-rigorous and is not promoted. Returned points are reconstructed exactly and rank growth still needs Rank Hunter certification.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "upper_bound_possible"}),
        "rigorous",
        "expensive",
        {"timeout": 90, "lim1": 5, "lim3": 80, "limbigprime": 0, "exact_candidates": 64},
    ),
    PipelineStageSpec(
        "mwrank_covering",
        "mwrank 2-Covering",
        "Geometry",
        "Run bounded standard eclib/mwrank full 2-descent/covering search. Exact returned points still require Rank Hunter independence certification. Timeout/error outcomes keep a durable stage attempt identity and use explicit manual, automatic same-budget, or escalated-budget retry policy. Completed rigorous uppers are persisted only through the shared rigorous-interval promotion service.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "upper_bound_possible"}),
        "rigorous",
        "expensive",
        {"timeout": 90, "retry_policy": "manual", "retry_timeout": 300, "first_limit": 20, "second_limit": 10, "exact_candidates": 64},
    ),
    PipelineStageSpec(
        "higher_descent_ladder",
        "Higher Descent Ladder",
        "Geometry",
        "Run rigorous built-in 2-descent first, then escalate through true higher-descent levels supplied by a compatible research plugin. Exact returned points are independently certified. Branch coverage is aggregated honestly, and timeout/error/partial outcomes use explicit manual, automatic same-budget, or escalated-budget retry policy.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "upper_bound_possible", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "levels": [2, 4, 8, 12],
            "timeout": 120,
            "retry_policy": "manual",
            "retry_timeout": 300,
            "first_limit": 20,
            "second_limit": 10,
            "stop_on_growth": False,
            "certificate_timeout": 120,
            "exact_candidates": 96,
        },
        repeatable=True,
        conditional="Levels above 2 require a real run_pipeline_higher_descent adapter hook; unsupported levels are reported, never simulated.",
    ),
    PipelineStageSpec(
        "covering_minimize_reduce",
        "Covering Minimization & Reduction",
        "Geometry",
        "Replace active rank42.covering.v1 square quartics by smaller exactly equivalent search models. Rational denominators are cleared, PARI computes a minimal-discriminant integral model and applies Cremona-Stoll reduction in an isolated worker, generalized output is completed back to a square quartic, and the inverse change of variables is composed into the exact map to E(Q).",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"reduced_coverings"}),
        "exact",
        "medium",
        {
            "max_coverings": 16,
            "timeout": 20,
            "require_improvement": True,
        },
        repeatable=True,
        conditional="Core v1 handles only stored affine square quartics of degree at most 4. A source is superseded only after an exactly verified child is stored; timeout/error leaves it searchable. This stage proves model equivalence only, not local solubility, Selmer membership, rational points, or rank growth.",
    ),
    PipelineStageSpec(
        "covering_local_height_planner",
        "Local Solubility & Covering Height Planner",
        "Geometry",
        "For each active square-quartic covering, test every completion of Q relevant to local solubility and attach a ranked point-search budget. Finite bad places use PARI's full Q_p binary-hyperelliptic solver; the real place uses exact sign/Sturm analysis. Search heights and timeouts are explicitly heuristic planning data.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"covering_local_profiles", "covering_search_plans"}),
        "exact",
        "medium",
        {
            "max_coverings": 16,
            "timeout": 30,
            "base_height": 10000,
            "max_height": 10000000,
            "base_timeout": 30,
            "max_timeout": 300,
            "reject_locally_insoluble": True,
        },
        repeatable=True,
        conditional="Core v1 supports nonsingular affine square quartics of degree 3 or 4. Completed local results are rigorous at all completions of Q; a local obstruction may suppress search. Difficulty tiers and recommended search budgets are heuristics, never height bounds or rank evidence.",
    ),
    PipelineStageSpec(
        "selmer_element_fanout",
        "Exact Covering Fan-Out",
        "Geometry",
        "Import exact mapped covering objects from the active plugin and/or existing Rank Hunter covering store, search each covering independently, map exact rational hits back to E(Q), and certify growth. Generic covering records are search geometry only: they do not certify a 2-Selmer class unless separate typed verified provenance is present. Every covering invocation is stored in an append-only attempt ledger, branch coverage is aggregated honestly, and incomplete outcomes use explicit manual, automatic same-budget, or escalated-budget retry policy.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "max_coverings": 16,
            "height": 100000,
            "timeout": 30,
            "derive_timeout": 30,
            "retry_policy": "manual",
            "retry_timeout": 120,
            "one_point": False,
            "use_covering_plans": True,
            "certificate_timeout": 120,
            "exact_candidates": 96,
        },
        repeatable=True,
        conditional="Legacy stage id selmer_element_fanout is retained for saved Pipeline compatibility. A family plugin may add exact coverings through isolated derive_pipeline_coverings(); existing stored coverings work without a plugin hook.",
    ),
    PipelineStageSpec(
        "padic_covering_search",
        "p-adic Covering Point Search",
        "Geometry",
        "Delegate a true p-adic covering-point search to an installed research engine/plugin through the core-owned hard-timeout worker. Successful plugin results must satisfy the versioned rank42.padic_covering_search.v1 contract, including engine/version, algorithm, searched covering ids, precision semantics, local lifting bounds, and heuristic/bounded/exhaustive completeness. Returned rational points are then validated exactly on E(Q) and any rank growth is independently certified.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "prime": 2,
            "precision": 40,
            "timeout": 120,
            "certificate_timeout": 120,
            "exact_candidates": 96,
        },
        repeatable=True,
        conditional="Requires a real run_pipeline_padic_covering_search adapter hook returning rank42.padic_covering_search.v1. Rank Hunter never substitutes ordinary ratpoints and calls it p-adic; completed does not imply exhaustive unless completeness=exhaustive.",
    ),
    PipelineStageSpec(
        "plugin_family_search",
        "Plugin Family Search",
        "Geometry",
        "Run the selected Family adapter search for one stored-pool Pipeline candidate inside a temporary database copy. Exact points are validated and certified by core before live persistence.",
        frozenset({"family"}),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "upper_bound_possible"}),
        "exact",
        "expensive",
        {
            "timeout": 3600,
            "retry_policy": "manual",
            "retry_timeout": 7200,
            "certificate_timeout": 120,
            "exact_candidates": 64,
            "options": {},
        },
        conditional="Requires the active family plugin to declare family_search. The legacy command runs once per selected stored-pool candidate in a temporary DB; live rank and curve writes are accepted only through core validation.",
    ),
    PipelineStageSpec(
        "plugin_geometry",
        "Plugin Geometry",
        "Geometry",
        "Invoke the selected family plugin's native target geometry behind a core-owned scientific-write boundary. Legacy target commands receive a temporary sandbox DB; only exact core-validated point artifacts and independently verified typed certificates may reach live scientific state. Timeout/error/partial outcomes use explicit manual, automatic same-budget, or escalated-budget retry policy.",
        frozenset({"family", "torsion", "curve"}),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "upper_bound_possible"}),
        "exact",
        "expensive",
        {
            "timeout": 180,
            "retry_policy": "manual",
            "retry_timeout": 600,
            "exact_candidates": 64,
        },
        conditional="Skipped when the active family/provider has no target_search adapter. Legacy target commands run only against a temporary sandbox DB; modern adapters may emit rank42.plugin_geometry_result.v1 through the core result-file contract.",
    ),
    PipelineStageSpec(
        "pointed_quartic",
        "Point-Centered Quartics",
        "Geometry",
        "Build exact quartic search geometry around already-known points and search mapped rational points. Each round reports planned/resolved coverage, cached completions, rigorous local obstructions, timeouts, and errors; zero new points is a dry stop only after the configured completion threshold is met. Exact map-back failures are retained as bounded diagnostics on the durable quartic search rather than silently discarded.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "point_source"}),
        frozenset({"point_source"}),
        "exact",
        "expensive",
        {
            "heights": [10000, 100000],
            "anchors": 16,
            "pool_size": 160,
            "rounds": 1,
            "deep_keep": 8,
            "timeout": 6,
            "reduce_timeout": 15,
            "dry_round_min_coverage": 1.0,
        },
    ),
    PipelineStageSpec(
        "mw_growth_loop",
        "MW Growth Loop",
        "Geometry",
        "Repeatedly rebuild point-centered quartic geometry from the enlarged exact point set; each successful round feeds its new points into the next round. The wrapper forwards the quartic engine's explicit stop reason, distinguishing dry completed coverage, timeout exhaustion, engine errors, rank goal, no-new-models, incomplete rounds, and max-round exhaustion.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "point_source"}),
        frozenset({"point_source", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "heights": [10000, 100000, 1000000],
            "anchors": 32,
            "pool_size": 320,
            "max_rounds": 8,
            "deep_keep": 12,
            "timeout": 8,
            "reduce_timeout": 20,
            "dry_round_min_coverage": 1.0,
            "certificate_timeout": 120,
            "exact_candidates": 96,
        },
        repeatable=True,
        conditional="Requires a complete rigorous witness basis. New points only raise rank after exact independence certification; the underlying quartic engine only calls a round dry after the configured resolved-coverage threshold is met.",
    ),
    PipelineStageSpec(
        "denominator_band",
        "Denominator Band Search",
        "Points",
        "Search adaptive affine charts inside the configured chart-coordinate denominator interval, then exact-certify any new independent points. These chart-denominator regions are search geometry, not invariant or disjoint native Mordell–Weil denominator shells. Each chart/height attempt is durably checkpointed, and timeout/error retry is an explicit manual/automatic/escalated policy. Timeout/partial/error coverage remains explicit rather than becoming a completed negative observation. A completed no-hit region means only that no finite point was found in that configured chart/height/denominator region; it does not prove nonexistence of rational points, extra generators, or rank growth.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source", "rigorous_lower_possible", "denominator_yield_data"}),
        "rigorous",
        "expensive",
        {
            "denominator_low": 2,
            "denominator_high": 100,
            "charts": 5,
            "heights": [10000, 100000],
            "timeout": 8,
            "retry_policy": "manual",
            "retry_timeout": 32,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "point_yield_persistence",
        "Point-Yield Persistence",
        "Points",
        "Score continued fresh exact-point discovery across completed comparable, progressively deeper chart-denominator searches. These bands describe search geometry across adaptive affine charts; they are not invariant or disjoint native Mordell–Weil denominator shells. Incomplete/timeout bands are excluded from the normal score and retained as missing coverage. Rank Hunter experimental scheduling heuristic only, not a theorem or standard Mordell–Weil statistic.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "denominator_yield_data"}),
        frozenset({"heuristic_point_score"}),
        "heuristic",
        "cheap",
        {"minimum_bands": 2},
    ),
    PipelineStageSpec(
        "affine_search",
        "Affine Rational Search",
        "Points",
        "Run exact bounded ratpoints searches over adaptive affine charts. Each chart/height attempt is durably checkpointed with explicit manual/automatic/escalated retry policy. This is a broad fallback rather than a proof stage; timeout/partial/error coverage is explicit and is never a completed zero-yield claim. A completed no-hit region means only that no finite point was found in that configured chart/height/denominator region; it does not prove nonexistence of rational points, extra generators, or rank growth.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"point_source"}),
        "exact",
        "expensive",
        {"heights": [10000, 100000, 1000000], "charts": 24, "timeout": 8, "retry_policy": "manual", "retry_timeout": 32, "model_mode": "stored", "model_prep_timeout": 8, "certify_after_search": True},
    ),
    PipelineStageSpec(
        "independence",
        "Exact Independence",
        "Evidence",
        "Certify exact ledger points against the rigorous witness basis. Only successful exact certificates can raise the rigorous lower bound.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "point_source"}),
        frozenset({"rigorous_lower_possible"}),
        "rigorous",
        "medium",
        {
            "certificate_timeout": 120,
            "max_candidates": 64,
            "max_halvings": 40,
            "max_prime": 1000000,
            "max_columns": 640,
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "saturation",
        "Bounded Saturation",
        "Evidence",
        "Saturate the complete rigorous witness subgroup through a bounded prime. Saturation improves subgroup quality; it never increases rank by itself.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "rigorous_lower_possible"}),
        frozenset({"saturation_evidence"}),
        "rigorous",
        "expensive",
        {"max_prime": 100, "timeout": 300},
    ),
    PipelineStageSpec(
        "full_saturation_index_recovery",
        "Saturation Prime Ladder / Index Recovery",
        "Evidence",
        "Run an exact bounded saturation prime ladder on the complete rigorous witness basis, ingest any recovered saturated basis points, and re-certify them. Completeness is never claimed without an independent global index bound.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "rigorous_lower_possible"}),
        frozenset({"point_source", "rigorous_lower_possible"}),
        "rigorous",
        "expensive",
        {
            "prime_bounds": [7, 31, 127, 509],
            "timeout": 180,
            "stop_on_unit_index": False,
            "retry_policy": "manual",
            "retry_timeout": 600,
            "certificate_timeout": 120,
            "exact_candidates": 96,
        },
        repeatable=True,
        conditional="Each saturation round is rigorous through its stated prime bound; the module explicitly distinguishes bounded saturation from a proof of global saturation.",
    ),
    PipelineStageSpec(
        "height_lattice_reduction",
        "Height-Lattice Reduction",
        "Evidence",
        "Compute the Néron–Tate height-pairing matrix on the rigorous witness basis, LLL-reduce it, persist the lattice, and exact-certify the reduced point basis.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve", "rigorous_lower_possible"}),
        frozenset({"point_source", "rigorous_lower_possible", "mw_lattice"}),
        "rigorous",
        "medium",
        {
            "precision_bits": 256,
            "timeout": 300,
            "retry_policy": "manual",
            "retry_timeout": 900,
            "certificate_timeout": 120,
            "exact_candidates": 96,
        },
        repeatable=True,
        conditional="The reduced points are exact; numerical height-matrix diagnostics are useful conditioning information but are not rank proofs by themselves.",
    ),
    PipelineStageSpec(
        "final_upper",
        "Final Rigorous Upper",
        "Evidence",
        "Spend a larger PARI budget after point discovery to try to close the rigorous rank interval.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"upper_bound_possible"}),
        "rigorous",
        "expensive",
        {"timeout": 30},
    ),
    PipelineStageSpec(
        "select_survivors",
        "Select Survivors",
        "Control",
        "Keep only the strongest fibers for later stages. The legacy rank_then_yield_then_score policy ranks by rigorous lower bound, then total exact-point inventory already stored for the curve, then Pipeline score; it is not run-local fresh point yield. This changes scheduling only and never mathematical evidence.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"selected_population"}),
        "control",
        "cheap",
        {
            "keep": 10,
            "ranking": "rank_then_yield_then_score",
        },
        repeatable=True,
    ),
    PipelineStageSpec(
        "adaptive_ladder",
        "Adaptive Denominator Ladder",
        "Control",
        "Progressively narrow the surviving population while moving bounded exact point-search screens through higher chart-denominator bands. These bands are search geometry, not invariant or disjoint native Mordell–Weil denominator shells. Each chart/height attempt is durably checkpointed; timeout/error retry uses explicit manual/automatic/escalated policy. The timeout is a per chart/height ratpoints-call budget, not a claim that the denominator band is exhausted. A completed no-hit region means only that no finite point was found in that configured chart/height/denominator region; it does not prove nonexistence of rational points, extra generators, or rank growth. The legacy rank_then_yield_then_score policy uses total exact-point inventory as its second ranking key, not run-local fresh yield. Each band exact-certifies discovered rank growth before the next funnel.",
        frozenset(TARGET_MODES),
        frozenset({"exact_curve"}),
        frozenset({"selected_population", "point_source", "rigorous_lower_possible", "denominator_yield_data"}),
        "rigorous",
        "expensive",
        {
            "bands": [[2, 50], [51, 500], [501, 5000]],
            "keeps": [10, 5, 2],
            "ranking": "rank_then_yield_then_score",
            "charts": 5,
            "heights": [10000, 100000],
            "timeout": 8,
            "retry_policy": "manual",
            "retry_timeout": 32,
            "certificate_timeout": 120,
            "exact_candidates": 64,
        },
        repeatable=True,
        conditional="Point search is bounded screening: timeout applies independently to each chart/height ratpoints call. Completed attempts are resumable checkpoints; terminal timeout/error attempts are retried only according to the declared retry policy. Completed work is not evidence that the full denominator band is exhausted, that no rational point exists, or that rank cannot grow.",
    ),
    PipelineStageSpec(
        "stop_goal",
        "Stop at Rank Goal",
        "Control",
        "Stop the run as soon as a candidate reaches the configured rigorous lower-bound goal.",
        frozenset(TARGET_MODES),
        frozenset({"rigorous_lower_possible"}),
        frozenset({"stop_rule"}),
        "control",
        "cheap",
        {},
    ),
)


STAGE_BY_ID = {stage.id: stage for stage in _STAGE_LIST}
POINT_SEARCH_RETRY_STAGE_IDS = frozenset({
    "small_point_density",
    "integral_seed",
    "denominator_band",
    "affine_search",
    "adaptive_ladder",
})
EXPLICIT_RETRY_STAGE_IDS = (
    POINT_SEARCH_RETRY_STAGE_IDS | frozenset({
        "mwrank_covering",
        "higher_descent_ladder",
        "selmer_element_fanout",
        "plugin_geometry",
        "plugin_family_search",
    })
)
FANOUT_RESET_STAGE_IDS = frozenset({
    "quadratic_twist_sweep",
    "targeted_twist_search",
    "rank_jump_base_change",
    "parameter_pullback",
    "isogeny_walk",
    "surface_fibration_switch",
    "square_condition_specializer",
    "constructive_rank_jump_loop",
})


def _is_prime_int(value):
    try:
        n = int(value)
    except Exception:
        return False
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    for d in range(3, isqrt(n) + 1, 2):
        if n % d == 0:
            return False
    return True


def point_search_budget(stage_id, config):
    """Estimate configured ratpoints calls and timeout budget per curve.

    This is an execution-planning estimate only. It excludes model preparation,
    certification, retries/resumes, and non-ratpoints overhead.
    """
    sid = str(stage_id)
    cfg = dict(config or {})
    if sid not in {
        "small_point_density",
        "integral_seed",
        "denominator_band",
        "affine_search",
        "adaptive_ladder",
    }:
        return None
    try:
        heights = [int(x) for x in (cfg.get("heights") or [])]
        timeout = int(cfg.get("timeout") or 0)
        charts = (
            1
            if sid in {"small_point_density", "integral_seed"}
            else int(cfg.get("charts") or 0)
        )
    except Exception:
        return {"calls": 0, "timeout_seconds": 0, "worst_case_timeout_seconds": 0}
    if not heights or timeout <= 0 or charts <= 0:
        return {"calls": 0, "timeout_seconds": max(0, timeout), "worst_case_timeout_seconds": 0}

    if sid == "denominator_band":
        try:
            low = int(cfg.get("denominator_low") or 1)
        except Exception:
            low = 1
        eligible = sum(1 for height in heights if height >= low)
        calls = charts * eligible
    elif sid == "adaptive_ladder":
        calls = 0
        for band in cfg.get("bands") or []:
            try:
                low = int(band[0])
            except Exception:
                continue
            calls += charts * sum(1 for height in heights if height >= low)
    else:
        calls = charts * len(heights)
    return {
        "calls": int(calls),
        "timeout_seconds": int(timeout),
        "worst_case_timeout_seconds": int(calls * timeout),
    }


def geometry_search_budget(stage_id, config):
    """Estimate configured Geometry branch/search timeout envelopes per curve.

    This is an execution-planning upper bound, not an ETA. It excludes exact
    certification, retry/resume attempts, cache effects, early rank-goal stops,
    and ordinary Python/process overhead.
    """
    sid = str(stage_id)
    cfg = dict(config or {})
    if sid not in {
        "pointed_quartic",
        "mw_growth_loop",
        "covering_minimize_reduce",
        "covering_local_height_planner",
        "selmer_element_fanout",
    }:
        return None

    if sid == "covering_minimize_reduce":
        try:
            branches = int(cfg.get("max_coverings") or 0)
            timeout = int(cfg.get("timeout") or 0)
        except Exception:
            branches, timeout = 0, 0
        branches = max(0, branches)
        timeout = max(0, timeout)
        return {
            "kind": "covering-reduction",
            "branches_max": int(branches),
            "searches_max": 0,
            "searches_no_growth": 0,
            "rounds_max": 1,
            "timeout_seconds": int(timeout),
            "setup_timeout_seconds": 0,
            "no_growth_timeout_seconds": int(branches * timeout),
            "worst_case_timeout_seconds": int(branches * timeout),
        }

    if sid == "covering_local_height_planner":
        try:
            branches = int(cfg.get("max_coverings") or 0)
            timeout = int(cfg.get("timeout") or 0)
        except Exception:
            branches, timeout = 0, 0
        return {
            "kind": "covering-local-height",
            "branches_max": max(0, branches),
            "searches_max": 0,
            "searches_no_growth": 0,
            "rounds_max": 1,
            "timeout_seconds": max(0, timeout),
            "setup_timeout_seconds": 0,
            "no_growth_timeout_seconds": max(0, branches) * max(0, timeout),
            "worst_case_timeout_seconds": max(0, branches) * max(0, timeout),
        }

    if sid == "selmer_element_fanout":
        try:
            branches = int(cfg.get("max_coverings") or 0)
            timeout = int(cfg.get("timeout") or 0)
            derive_timeout = int(cfg.get("derive_timeout") or 0)
        except Exception:
            branches, timeout, derive_timeout = 0, 0, 0
        branches = max(0, branches)
        timeout = max(0, timeout)
        derive_timeout = max(0, derive_timeout)
        return {
            "kind": "coverings",
            "branches_max": int(branches),
            "searches_max": int(branches),
            "searches_no_growth": int(branches),
            "rounds_max": 1,
            "timeout_seconds": int(timeout),
            "setup_timeout_seconds": int(derive_timeout),
            "no_growth_timeout_seconds": int(
                branches * timeout + derive_timeout
            ),
            "worst_case_timeout_seconds": int(
                branches * timeout + derive_timeout
            ),
        }

    try:
        heights = [int(x) for x in (cfg.get("heights") or [])]
        anchors = int(cfg.get("anchors") or 0)
        deep_keep = int(cfg.get("deep_keep") or 0)
        timeout = int(cfg.get("timeout") or 0)
        reduce_timeout = int(cfg.get("reduce_timeout") or 0)
        rounds = int(
            cfg.get("max_rounds")
            if sid == "mw_growth_loop"
            else cfg.get("rounds")
            or 0
        )
    except Exception:
        heights, anchors, deep_keep = [], 0, 0
        timeout, reduce_timeout, rounds = 0, 0, 0

    height_count = sum(1 for height in heights if height > 0)
    anchors = max(0, anchors)
    deep_keep = max(0, deep_keep)
    timeout = max(0, timeout)
    reduce_timeout = max(0, reduce_timeout)
    rounds = max(0, rounds)

    # First height searches every selected anchor. With no discovered points,
    # later heights retain only the bounded fallback set and the engine stops
    # after that completed dry round. In the configured worst case every anchor
    # is promoted at every later height and every allowed round runs.
    fallback = min(anchors, deep_keep)
    no_growth_searches = (
        anchors + fallback * max(0, height_count - 1)
        if height_count
        else 0
    )
    searches_per_round_max = anchors * height_count
    searches_max = searches_per_round_max * rounds
    no_growth_setup = reduce_timeout if rounds > 0 else 0
    worst_setup = reduce_timeout * rounds

    return {
        "kind": "quartics",
        "branches_max": int(searches_max),
        "searches_per_round_max": int(searches_per_round_max),
        "searches_max": int(searches_max),
        "searches_no_growth": int(no_growth_searches),
        "rounds_max": int(rounds),
        "timeout_seconds": int(timeout),
        "setup_timeout_seconds": int(worst_setup),
        "no_growth_timeout_seconds": int(
            no_growth_searches * timeout + no_growth_setup
        ),
        "worst_case_timeout_seconds": int(
            searches_max * timeout + worst_setup
        ),
    }


def deep_strategy_budget(config):
    """Estimate deep-strategy timeout envelopes and resumable point-search scope.

    These are configured timeout envelopes, not ETAs. The raw denominator
    portfolio counts every configured band/chart/height slot; the runnable
    portfolio removes height/band combinations that ratpoints cannot search
    because the requested denominator lower bound exceeds the height.
    """
    cfg = dict(config or {})
    try:
        anchors = max(0, int(cfg.get("mw_growth_anchors") or 0))
        deep_keep = max(0, int(cfg.get("mw_growth_deep_keep") or 0))
        mw_heights = [
            int(x) for x in (cfg.get("mw_growth_heights") or [])
            if int(x) > 0
        ]
        mw_timeout = max(0, int(cfg.get("mw_growth_timeout") or 0))
        reduce_timeout = max(
            0, int(cfg.get("mw_growth_reduce_timeout") or 0)
        )
        rounds = max(0, int(cfg.get("mw_growth_rounds") or 0))
        charts = max(0, int(cfg.get("denominator_charts") or 0))
        denom_heights = [
            int(x) for x in (cfg.get("denominator_heights") or [])
            if int(x) > 0
        ]
        denom_timeout = max(
            0, int(cfg.get("denominator_timeout") or 0)
        )
        wall_timeout = max(0, int(cfg.get("wall_timeout") or 0))
        retry_timeout = max(0, int(cfg.get("retry_timeout") or 0))
    except Exception:
        return {
            "mw_searches_no_growth": 0,
            "mw_seconds_no_growth": 0,
            "mw_searches_max": 0,
            "mw_seconds_max": 0,
            "mw_rounds_max": 0,
            "denominator_configured_slots": 0,
            "denominator_configured_seconds": 0,
            "denominator_searches": 0,
            "denominator_seconds": 0,
            "wall_timeout_seconds": 0,
            "retry_timeout_seconds": 0,
            "checkpoint_scope": "chart_height",
        }

    fallback = min(anchors, deep_keep)
    mw_searches_no_growth = (
        anchors + fallback * max(0, len(mw_heights) - 1)
        if mw_heights
        else 0
    )
    mw_searches_max = anchors * len(mw_heights) * rounds
    mw_seconds_no_growth = (
        mw_searches_no_growth * mw_timeout
        + (reduce_timeout if rounds > 0 else 0)
    )
    mw_seconds_max = (
        mw_searches_max * mw_timeout + reduce_timeout * rounds
    )

    valid_bands = []
    for band in cfg.get("denominator_bands") or []:
        try:
            valid_bands.append((int(band[0]), int(band[1])))
        except Exception:
            continue
    configured_slots = len(valid_bands) * charts * len(denom_heights)
    runnable = 0
    for low, _high in valid_bands:
        runnable += charts * sum(1 for height in denom_heights if height >= low)

    return {
        "mw_searches_no_growth": int(mw_searches_no_growth),
        "mw_seconds_no_growth": int(mw_seconds_no_growth),
        "mw_searches_max": int(mw_searches_max),
        "mw_seconds_max": int(mw_seconds_max),
        "mw_rounds_max": int(rounds),
        "denominator_configured_slots": int(configured_slots),
        "denominator_configured_seconds": int(
            configured_slots * denom_timeout
        ),
        "denominator_searches": int(runnable),
        "denominator_seconds": int(runnable * denom_timeout),
        "wall_timeout_seconds": int(wall_timeout),
        "retry_timeout_seconds": int(retry_timeout),
        "checkpoint_scope": "chart_height",
    }


def pipeline_stage_specs(target_mode=None):
    if target_mode is None:
        return _STAGE_LIST
    mode = str(target_mode)
    return tuple(stage for stage in _STAGE_LIST if mode in stage.targets)


def stage_spec(stage_id):
    try:
        return STAGE_BY_ID[str(stage_id)]
    except KeyError as exc:
        raise ValueError(f"unknown pipeline stage {stage_id!r}") from exc


def normalize_stage(raw):
    if isinstance(raw, str):
        raw = {"id": raw}
    if not isinstance(raw, dict):
        raise ValueError("pipeline stages must be strings or objects")
    spec = stage_spec(raw.get("id"))
    config = dict(spec.defaults)
    supplied = raw.get("config") or {}
    if not isinstance(supplied, dict):
        raise ValueError(f"pipeline stage {spec.id}: config must be an object")
    config.update(supplied)
    return {"id": spec.id, "config": config}


def normalize_pipeline(stages):
    return [normalize_stage(stage) for stage in (stages or [])]


def validate_pipeline(target_mode, stages):
    """Return a list of human-readable contract errors."""
    mode = str(target_mode)
    if mode not in TARGET_MODES:
        return [f"unknown target mode {mode!r}"]

    normalized = normalize_pipeline(stages)
    errors = []
    if not normalized:
        return ["add at least one pipeline stage"]

    seen = set()
    caps = {"source", "exact_curve"} if mode == "curve" else {"source"}
    candidate_stage_seen = False
    exact_torsion_index = None

    for index, rec in enumerate(normalized, 1):
        spec = stage_spec(rec["id"])
        if mode not in spec.targets:
            errors.append(f"Step {index} · {spec.label}: unavailable for {mode} targets")
        if not spec.repeatable and spec.id in seen:
            errors.append(f"Step {index} · {spec.label}: stage may only be used once")
        seen.add(spec.id)

        missing = sorted(spec.requires - caps)
        if missing:
            errors.append(
                f"Step {index} · {spec.label}: move/add a prerequisite stage providing "
                + ", ".join(missing)
            )

        if spec.id == "nagao_screen":
            candidate_stage_seen = True
        if spec.id == "exact_torsion":
            exact_torsion_index = index

        if spec.id in EXPLICIT_RETRY_STAGE_IDS:
            policy = str(rec["config"].get("retry_policy") or "manual")
            if policy not in {"manual", "automatic", "escalated"}:
                errors.append(
                    f"Step {index} · {spec.label}: retry policy must be manual, automatic, or escalated"
                )
            try:
                retry_timeout = int(rec["config"].get("retry_timeout") or 0)
            except Exception:
                retry_timeout = 0
            if retry_timeout <= 0:
                errors.append(
                    f"Step {index} · {spec.label}: retry timeout must be positive"
                )
            if policy == "escalated":
                try:
                    initial_timeout = int(rec["config"].get("timeout") or 0)
                except Exception:
                    initial_timeout = 0
                if retry_timeout <= initial_timeout:
                    errors.append(
                        f"Step {index} · {spec.label}: escalated retry timeout must exceed the initial timeout"
                    )

        if spec.id in FANOUT_RESET_STAGE_IDS:
            # Curve-local evidence/caches belong to the parent. New children
            # start with an exact curve and derivation provenance only.
            caps = {"source", "exact_curve", "derived_population"}
        caps.update(spec.provides)

    if not candidate_stage_seen and mode != "curve":
        errors.append("Nagao Screen is currently the required candidate-source stage")

    if mode == "torsion":
        if exact_torsion_index is None:
            errors.append("Torsion Group pipelines require Exact Torsion")
        else:
            for index, rec in enumerate(normalized, 1):
                if index <= exact_torsion_index:
                    continue
                # No special rule after torsion verification.
                break
            # Exact torsion should happen before any point/rank work.
            for index, rec in enumerate(normalized, 1):
                if index >= exact_torsion_index:
                    break
                if stage_spec(rec["id"]).category in {"Transforms", "Strategies", "Points", "Geometry", "Evidence", "Control"}:
                    errors.append(
                        "Exact Torsion must run before transform, strategy, point, geometry, evidence, or control stages in torsion mode"
                    )
                    break

    candidate_ids = [
        rec["id"] for rec in normalized
        if stage_spec(rec["id"]).category == "Candidates"
    ]
    if "corpus_filter" in candidate_ids and candidate_ids[-1] != "corpus_filter":
        errors.append("Library Filter must be the final candidate stage")

    screening_stages = [
        (index, rec)
        for index, rec in enumerate(normalized, 1)
        if rec["id"] in {"nagao_screen", "nagao_rescore"}
    ]
    for (previous_index, previous), (current_index, current) in zip(
        screening_stages, screening_stages[1:]
    ):
        try:
            previous_bound = int(previous["config"].get("prime_bound"))
            current_bound = int(current["config"].get("prime_bound"))
            previous_keep = int(previous["config"].get("keep"))
            current_keep = int(current["config"].get("keep"))
        except Exception:
            continue
        if current_bound <= previous_bound:
            errors.append(
                f"Step {current_index} · {stage_spec(current['id']).label}: "
                f"candidate prime bound must strictly increase from step {previous_index}"
            )
        if current_keep > previous_keep:
            errors.append(
                f"Step {current_index} · {stage_spec(current['id']).label}: "
                f"candidate survivor count must not increase from step {previous_index}"
            )

    # Candidate ranking stages operate on the pool, not per-curve state.
    exact_started = False
    for index, rec in enumerate(normalized, 1):
        spec = stage_spec(rec["id"])
        is_candidate = spec.category == "Candidates"
        if not is_candidate:
            exact_started = True
        elif exact_started:
            errors.append(
                f"Step {index} · {spec.label}: candidate screening/rescoring must precede per-curve stages"
            )

    # Rescore without screen is already caught by contracts, but make the
    # message explicit for the UI.
    ids = [rec["id"] for rec in normalized]
    if (
        "nagao_rescore" in ids
        and "nagao_screen" in ids
        and ids.index("nagao_rescore") < ids.index("nagao_screen")
    ):
        errors.append("Nagao Rescore must come after Nagao Screen")

    for index, rec in enumerate(normalized, 1):
        sid = rec["id"]
        cfg = rec["config"]
        if sid in {
            "targeted_twist_search",
            "rank_jump_base_change",
            "surface_fibration_switch",
            "isogeny_walk",
        }:
            try:
                transform_timeout = int(cfg.get("timeout") or 0)
            except Exception:
                transform_timeout = 0
            if transform_timeout <= 0:
                errors.append(
                    f"Step {index} · {stage_spec(sid).label}: "
                    "hard timeout must be positive"
                )
        if sid == "targeted_twist_search":
            try:
                per_twist_timeout = int(cfg.get("per_twist_timeout") or 0)
            except Exception:
                per_twist_timeout = 0
            if per_twist_timeout <= 0:
                errors.append(
                    f"Step {index} · Targeted Twist Search: "
                    "per-twist hard timeout must be positive"
                )
        if sid == "prime_table_cache":
            try:
                bound = int(cfg.get("prime_bound"))
            except Exception:
                bound = 0
            if bound < 3:
                errors.append(f"Step {index} · Prime Table Cache: prime bound must be at least 3")
            try:
                bad_timeout = int(cfg.get("bad_prime_timeout") or 0)
            except Exception:
                bad_timeout = 0
            if bool(cfg.get("include_all_bad", True)) and bad_timeout <= 0:
                errors.append(f"Step {index} · Prime Table Cache: bad-prime timeout must be positive")
        elif sid == "multi_scale_frobenius":
            try:
                bounds = [int(x) for x in (cfg.get("bounds") or [])]
            except Exception:
                bounds = []
            if not bounds or any(x < 3 for x in bounds):
                errors.append(f"Step {index} · Multi-Scale Frobenius: bounds must be integers at least 3")
            elif any(b <= a for a, b in zip(bounds, bounds[1:])):
                errors.append(f"Step {index} · Multi-Scale Frobenius: bounds must strictly increase")
        elif sid == "mestre_nagao_ensemble":
            try:
                bound = int(cfg.get("prime_bound"))
            except Exception:
                bound = 0
            if bound < 3:
                errors.append(f"Step {index} · Multi-Value Mestre–Nagao: prime bound must be at least 3")
        elif sid == "frobenius_persistence":
            try:
                bounds = [int(x) for x in (cfg.get("bounds") or [])]
            except Exception:
                bounds = []
            if len(bounds) < 2 or any(x < 3 for x in bounds):
                errors.append(f"Step {index} · Frobenius Persistence: use at least two integer bounds of 3 or greater")
            elif any(b <= a for a, b in zip(bounds, bounds[1:])):
                errors.append(f"Step {index} · Frobenius Persistence: bounds must strictly increase")
        elif sid == "explicit_formula_indicator":
            try:
                bound = int(cfg.get("prime_bound"))
                max_power = int(cfg.get("max_prime_power"))
            except Exception:
                bound, max_power = 0, 0
            if bound < 3:
                errors.append(f"Step {index} · Explicit-Formula Indicator: prime bound must be at least 3")
            if max_power < 1 or max_power > 12:
                errors.append(f"Step {index} · Explicit-Formula Indicator: max prime power must be between 1 and 12")
        elif sid == "mestre_bober_analytic_upper":
            try:
                max_delta = float(cfg.get("max_delta"))
                timeout = int(cfg.get("timeout"))
                ncpus = int(cfg.get("ncpus"))
            except Exception:
                max_delta, timeout, ncpus = 0.0, 0, 0
            if not (0.0 < max_delta <= 3.0):
                errors.append(
                    f"Step {index} · Conditional Analytic Rank Upper: "
                    "max Delta must be positive and at most 3.0"
                )
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Conditional Analytic Rank Upper: "
                    "hard timeout must be positive"
                )
            if ncpus <= 0:
                errors.append(
                    f"Step {index} · Conditional Analytic Rank Upper: "
                    "CPU count must be positive"
                )
            if not isinstance(cfg.get("adaptive"), bool):
                errors.append(
                    f"Step {index} · Conditional Analytic Rank Upper: "
                    "adaptive must be boolean"
                )
        elif sid == "brumer_kramer_classgroup_bound":
            proof_mode = str(cfg.get("proof_mode") or "")
            if proof_mode not in {"grh", "unconditional"}:
                errors.append(
                    f"Step {index} · Cubic Class-Group / 2-Selmer Bound: "
                    "proof mode must be grh or unconditional"
                )
            try:
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                timeout = 0
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Cubic Class-Group / 2-Selmer Bound: "
                    "hard timeout must be positive"
                )
        elif sid == "cassels_tate_refinement":
            try:
                effort = int(cfg.get("effort"))
                timeout = int(cfg.get("timeout"))
            except Exception:
                effort, timeout = -1, 0
            if effort < 0 or effort > 10:
                errors.append(
                    f"Step {index} · Cassels–Tate Selmer Refinement: "
                    "PARI point-search effort must be between 0 and 10"
                )
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Cassels–Tate Selmer Refinement: "
                    "hard timeout must be positive"
                )
        elif sid == "isogeny_descent":
            try:
                degree = int(cfg.get("degree"))
                first_limit = int(cfg.get("first_limit"))
                second_limit = int(cfg.get("second_limit"))
                timeout = int(cfg.get("timeout"))
            except Exception:
                degree, first_limit, second_limit, timeout = 0, 0, 0, 0
            if degree != 2:
                errors.append(
                    f"Step {index} · Isogeny Descent: core v1 supports degree 2 only"
                )
            if first_limit <= 0 or second_limit <= 0:
                errors.append(
                    f"Step {index} · Isogeny Descent: search limits must be positive"
                )
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Isogeny Descent: hard timeout must be positive"
                )
            if not isinstance(cfg.get("second_descent"), bool):
                errors.append(
                    f"Step {index} · Isogeny Descent: second descent must be boolean"
                )
        elif sid == "local_root_numbers":
            if str(cfg.get("global_sign") or "any") not in {"any", "+1", "-1"}:
                errors.append(f"Step {index} · Local Root Numbers: global sign must be any, +1, or -1")
            try:
                bad_timeout = int(cfg.get("bad_prime_timeout") or 0)
            except Exception:
                bad_timeout = 0
            if bad_timeout <= 0:
                errors.append(f"Step {index} · Local Root Numbers: bad-prime timeout must be positive")
        elif sid == "bad_prime_fingerprint":
            try:
                required = [int(x) for x in (cfg.get("required_primes") or [])]
                max_bad = int(cfg.get("max_bad_primes") or 0)
            except Exception:
                required, max_bad = [], -1
            if any(not _is_prime_int(x) for x in required):
                errors.append(f"Step {index} · Bad-Prime Fingerprint: required values must be prime")
            if max_bad < 0:
                errors.append(f"Step {index} · Bad-Prime Fingerprint: max bad primes cannot be negative")
            try:
                bad_timeout = int(cfg.get("bad_prime_timeout") or 0)
            except Exception:
                bad_timeout = 0
            if bad_timeout <= 0:
                errors.append(f"Step {index} · Bad-Prime Fingerprint: bad-prime timeout must be positive")
        elif sid == "torsion_mod_p_sieve":
            group = str(cfg.get("torsion_group") or "").strip()
            if group == "target":
                if mode != "torsion":
                    errors.append(
                        f"Step {index} · Torsion mod-p Sieve: choose a concrete torsion group outside Torsion Group mode"
                    )
            elif not group:
                errors.append(f"Step {index} · Torsion mod-p Sieve: torsion group is required")
            else:
                try:
                    canonical_torsion_label(group)
                except ValueError as exc:
                    errors.append(
                        f"Step {index} · Torsion mod-p Sieve: {exc}"
                    )
            try:
                prime_bound = int(cfg.get("prime_bound"))
                max_primes = int(cfg.get("max_primes"))
            except Exception:
                prime_bound, max_primes = 0, 0
            if prime_bound < 3 or max_primes <= 0:
                errors.append(
                    f"Step {index} · Torsion mod-p Sieve: prime bound must be at least 3 and max primes positive"
                )
        elif sid == "local_solubility_sieve":
            try:
                primes = [int(x) for x in (cfg.get("explicit_primes") or [])]
                max_prime = int(cfg.get("max_prime"))
            except Exception:
                primes, max_prime = [], 0
            if any(not _is_prime_int(x) for x in primes):
                errors.append(f"Step {index} · Local Solubility Sieve: explicit values must be prime")
            if max_prime < 2:
                errors.append(f"Step {index} · Local Solubility Sieve: max prime must be at least 2")
            try:
                bad_timeout = int(cfg.get("bad_prime_timeout") or 0)
            except Exception:
                bad_timeout = 0
            if bool(cfg.get("include_bad", True)) and bad_timeout <= 0:
                errors.append(f"Step {index} · Local Solubility Sieve: bad-prime timeout must be positive")
        elif sid in {"quadratic_twist_sweep", "targeted_twist_search"}:
            try:
                low = int(cfg.get("d_min"))
                high = int(cfg.get("d_max"))
                keep = int(cfg.get("max_children"))
            except Exception:
                low, high, keep = 1, 0, 0
            if not (cfg.get("d_values") or []) and high < low:
                errors.append(f"Step {index} · {spec.label}: d max must be at least d min")
            if keep <= 0:
                errors.append(f"Step {index} · {spec.label}: max children must be positive")
            if sid == "targeted_twist_search":
                try:
                    scan = int(cfg.get("scan_limit"))
                    bound = int(cfg.get("prime_bound"))
                except Exception:
                    scan, bound = 0, 0
                if scan <= 0 or bound < 3:
                    errors.append(
                        f"Step {index} · Targeted Twist Search: scan limit must be positive and prime bound at least 3"
                    )
                if str(cfg.get("root_sign") or "any") not in {"any", "+1", "-1"}:
                    errors.append(
                        f"Step {index} · Targeted Twist Search: root sign must be any, +1, or -1"
                    )
        elif sid == "rank_jump_base_change":
            try:
                max_children = int(cfg.get("max_children") or 0)
            except Exception:
                max_children = 0
            if max_children <= 0:
                errors.append(f"Step {index} · Rank-Jump / Base-Change: max children must be positive")
        elif sid == "parameter_pullback":
            maps = cfg.get("maps") or []
            label = "Möbius Parameter Transform"
            if not maps:
                errors.append(
                    f"Step {index} · {label}: add at least one Möbius map"
                )
            for map_index, values in enumerate(maps, 1):
                if not isinstance(values, (list, tuple)) or len(values) != 4:
                    errors.append(
                        f"Step {index} · {label}: map {map_index} "
                        "must be [a,b,c,d]"
                    )
                    continue
                try:
                    a, b, c, d = [Fraction(str(x)) for x in values]
                except Exception:
                    errors.append(
                        f"Step {index} · {label}: map {map_index} "
                        "coefficients must be rational"
                    )
                    continue
                if a * d - b * c == 0:
                    errors.append(
                        f"Step {index} · {label}: map {map_index} "
                        "must have nonzero determinant"
                    )
        elif sid == "isogeny_walk":
            try:
                degrees = [int(x) for x in (cfg.get("degrees") or [])]
                max_children = int(cfg.get("max_children") or 0)
            except Exception:
                degrees, max_children = [], 0
            if not degrees or any(not _is_prime_int(x) for x in degrees):
                errors.append(f"Step {index} · Isogeny Walk: degrees must be prime")
            if max_children <= 0:
                errors.append(f"Step {index} · Isogeny Walk: max children must be positive")
            try:
                cert = int(cfg.get("certificate_timeout") or 0)
                exact = int(cfg.get("exact_candidates") or 0)
            except Exception:
                cert, exact = 0, 0
            if bool(cfg.get("transfer_basis", True)) and (cert <= 0 or exact <= 0):
                errors.append(
                    f"Step {index} · Isogeny Walk: witness-transfer certificate budgets must be positive"
                )
        elif sid == "covering_selmer_branch":
            allowed = {"simon_known", "mwrank_selmer", "mwrank_coverings"}
            engines = [str(x) for x in (cfg.get("engines") or [])]
            if not engines or any(x not in allowed for x in engines):
                errors.append(
                    f"Step {index} · Covering / Selmer Branch: choose supported branch engines"
                )
            try:
                timeout = int(cfg.get("timeout") or 0)
                cert = int(cfg.get("certificate_timeout") or 0)
                exact = int(cfg.get("exact_candidates") or 0)
            except Exception:
                timeout, cert, exact = 0, 0, 0
            if timeout <= 0 or cert <= 0 or exact <= 0:
                errors.append(
                    f"Step {index} · Covering / Selmer Branch: budgets must be positive"
                )
        elif sid == "section_height_shell":
            try:
                height = int(cfg.get("height") or 0)
                coeff = int(cfg.get("coefficient_bound") or 0)
                maximum = int(cfg.get("max_sections") or 0)
            except Exception:
                height, coeff, maximum = 0, 0, 0
            if height <= 0 or coeff <= 0 or maximum <= 0:
                errors.append(f"Step {index} · Section Search Shell: height/coefficient/module limits must be positive")
            if str(cfg.get("height_mode") or "exact") not in {"exact", "up_to"}:
                errors.append(f"Step {index} · Section Search Shell: height mode must be exact or up_to")
            if str(cfg.get("height_kind") or "") not in HEIGHT_KINDS:
                errors.append(
                    f"Step {index} · Section Search Shell: choose a supported explicit height kind"
                )
            if not str(cfg.get("height_normalization") or "").strip():
                errors.append(
                    f"Step {index} · Section Search Shell: height normalization is required"
                )
            try:
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                timeout = 0
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Section Search Shell: hard timeout must be positive"
                )
        elif sid == "trace_section_constructor":
            try:
                degree = int(cfg.get("extension_degree") or 0)
                maximum = int(cfg.get("max_sections") or 0)
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                degree, maximum, timeout = 0, 0, 0
            if degree < 2 or maximum <= 0:
                errors.append(f"Step {index} · Trace Section Constructor: extension degree must be >=2 and max sections positive")
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Trace Section Constructor: hard timeout must be positive"
                )
        elif sid == "forced_bisection_constructor":
            try:
                division = int(cfg.get("division") or 0)
                maximum = int(cfg.get("max_conditions") or 0)
            except Exception:
                division, maximum = 0, 0
            slope_mode = str(cfg.get("slope_mode") or "")
            if slope_mode == "forced":
                slope_mode = "forced_tangent"
            if division not in SUPPORTED_DIVISIONS or maximum <= 0:
                errors.append(
                    f"Step {index} · Forced Bisection / Division Constructor: division must be one of 2,3,4 and condition limit positive"
                )
            if slope_mode not in SUPPORTED_SLOPE_MODES:
                errors.append(
                    f"Step {index} · Forced Bisection / Division Constructor: choose a supported slope/division mode"
                )
            try:
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                timeout = 0
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Forced Bisection / Division Constructor: hard timeout must be positive"
                )
        elif sid == "square_condition_specializer":
            try:
                numerator = int(cfg.get("numerator_abs") or 0)
                denominator = int(cfg.get("denominator_max") or 0)
                tests = int(cfg.get("max_tests") or 0)
                children = int(cfg.get("max_children") or 0)
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                numerator, denominator, tests, children, timeout = 0, 0, 0, 0, 0
            if min(numerator, denominator, tests, children) <= 0:
                errors.append(f"Step {index} · Square-Condition Specializer: search/test/child budgets must be positive")
            if timeout <= 0:
                errors.append(f"Step {index} · Square-Condition Specializer: hard timeout must be positive")
        elif sid == "constructive_rank_jump_loop":
            try:
                heights = [int(x) for x in (cfg.get("heights") or [])]
                coefficient_bound = int(cfg.get("coefficient_bound") or 0)
                extension_degree = int(cfg.get("extension_degree") or 0)
                division = int(cfg.get("division") or 0)
                numerator_abs = int(cfg.get("numerator_abs") or 0)
                denominator_max = int(cfg.get("denominator_max") or 0)
                max_tests = int(cfg.get("max_tests") or 0)
                max_children = int(cfg.get("max_children") or 0)
                hook_timeouts = [
                    int(cfg.get("section_timeout") or 0),
                    int(cfg.get("trace_timeout") or 0),
                    int(cfg.get("division_timeout") or 0),
                    int(cfg.get("square_timeout") or 0),
                ]
            except Exception:
                heights = []
                coefficient_bound = extension_degree = division = 0
                numerator_abs = denominator_max = max_tests = max_children = 0
                hook_timeouts = [0]
            if not heights or any(x <= 0 for x in heights):
                errors.append(f"Step {index} · Constructive Rank-Jump Loop: heights must be positive")
            if min(coefficient_bound, numerator_abs, denominator_max, max_tests, max_children) <= 0:
                errors.append(f"Step {index} · Constructive Rank-Jump Loop: construction/search budgets must be positive")
            if extension_degree < 2 or division < 2:
                errors.append(f"Step {index} · Constructive Rank-Jump Loop: extension degree and division must be >= 2")
            if str(cfg.get("height_mode") or "exact") not in {"exact", "up_to"}:
                errors.append(f"Step {index} · Constructive Rank-Jump Loop: height mode must be exact or up_to")
            if any(timeout <= 0 for timeout in hook_timeouts):
                errors.append(f"Step {index} · Constructive Rank-Jump Loop: all four Constructive hook timeouts must be positive")
            if str(cfg.get("retry_policy") or "automatic") not in {"manual", "automatic", "escalated"}:
                errors.append(f"Step {index} · Constructive Rank-Jump Loop: retry policy must be manual, automatic, or escalated")
        elif sid == "upper_bound_rescue_ladder":
            try:
                positive = [
                    int(cfg.get("pari_timeout") or 0),
                    int(cfg.get("isogeny_max_models") or 0),
                    int(cfg.get("isogeny_discovery_timeout") or 0),
                    int(cfg.get("isogeny_pari_timeout") or 0),
                    int(cfg.get("selmer_timeout") or 0),
                    int(cfg.get("simon_timeout") or 0),
                    int(cfg.get("covering_timeout") or 0),
                    int(cfg.get("higher_timeout") or 0),
                    int(cfg.get("certificate_timeout") or 0),
                    int(cfg.get("exact_candidates") or 0),
                ]
                degrees = [int(x) for x in (cfg.get("isogeny_degrees") or [])]
                levels = [int(x) for x in (cfg.get("higher_levels") or [])]
                simon_limbigprime = int(cfg.get("simon_limbigprime") or 0)
            except Exception:
                positive, degrees, levels, simon_limbigprime = [0], [], [], -1
            if any(x <= 0 for x in positive):
                errors.append(f"Step {index} · Upper-Bound Rescue Ladder: time/model/certificate budgets must be positive")
            if bool(cfg.get("isogeny_pari_enabled", True)) and (
                not degrees or any(not _is_prime_int(x) for x in degrees)
            ):
                errors.append(
                    f"Step {index} · Upper-Bound Rescue Ladder: isogeny degrees must be prime integers"
                )
            if bool(cfg.get("known_basis_coverings", True)) and simon_limbigprime != 0:
                errors.append(
                    f"Step {index} · Upper-Bound Rescue Ladder: rigorous Simon rescue requires LIMBIGPRIME=0"
                )
            if bool(cfg.get("higher_descent_hooks", True)) and (
                not levels or any(x <= 2 for x in levels)
            ):
                errors.append(f"Step {index} · Upper-Bound Rescue Ladder: higher descent levels must be > 2")
        elif sid == "large_height_generator_hunt":
            try:
                levels = [int(x) for x in (cfg.get("descent_levels") or [])]
                bounds = [int(x) for x in (cfg.get("saturation_bounds") or [])]
                bands = [list(map(int, pair)) for pair in (cfg.get("denominator_bands") or [])]
                cert = int(cfg.get("certificate_timeout") or 0)
                exact = int(cfg.get("exact_candidates") or 0)
                cover = int(cfg.get("max_coverings") or 0)
                growth_rounds = int(cfg.get("mw_growth_rounds") or 0)
                wall_timeout = int(cfg.get("wall_timeout") or 0)
                retry_timeout = int(cfg.get("retry_timeout") or 0)
            except Exception:
                levels, bounds, bands = [], [], []
                cert = exact = cover = growth_rounds = wall_timeout = retry_timeout = 0
            if not levels or any(x < 2 for x in levels):
                errors.append(f"Step {index} · Large-Height Generator Hunt: descent levels must be >= 2")
            if not bounds or any(x < 2 for x in bounds):
                errors.append(f"Step {index} · Large-Height Generator Hunt: saturation bounds must be >= 2")
            if not bands or any(len(pair) != 2 or pair[0] < 1 or pair[1] < pair[0] for pair in bands):
                errors.append(f"Step {index} · Large-Height Generator Hunt: denominator bands must be valid positive intervals")
            elif any(bands[j][0] <= bands[j - 1][1] for j in range(1, len(bands))):
                errors.append(f"Step {index} · Large-Height Generator Hunt: denominator bands must be strictly increasing and non-overlapping")
            if min(cert, exact, cover, growth_rounds, wall_timeout, retry_timeout) <= 0:
                errors.append(f"Step {index} · Large-Height Generator Hunt: search/certificate/wall/retry budgets must be positive")
            if str(cfg.get("retry_policy") or "automatic") not in {"manual", "escalated", "automatic"}:
                errors.append(f"Step {index} · Large-Height Generator Hunt: retry policy must be manual, escalated, or automatic")
            try:
                p = int(cfg.get("padic_prime") or 0)
            except Exception:
                p = 0
            if bool(cfg.get("padic_enabled", True)) and not _is_prime_int(p):
                errors.append(f"Step {index} · Large-Height Generator Hunt: p-adic prime must be prime")
        elif sid == "record_breaker_lane":
            mode_name = str(cfg.get("minimum_rank_mode") or "fixed")
            try:
                minimum_rank = int(cfg.get("minimum_rank") or 0)
                levels = [int(x) for x in (cfg.get("descent_levels") or [])]
                bounds = [int(x) for x in (cfg.get("saturation_bounds") or [])]
                bands = [list(map(int, pair)) for pair in (cfg.get("denominator_bands") or [])]
                cert = int(cfg.get("certificate_timeout") or 0)
                exact = int(cfg.get("exact_candidates") or 0)
                cover = int(cfg.get("max_coverings") or 0)
                growth_rounds = int(cfg.get("mw_growth_rounds") or 0)
                charts = int(cfg.get("denominator_charts") or 0)
                wall_timeout = int(cfg.get("wall_timeout") or 0)
                retry_timeout = int(cfg.get("retry_timeout") or 0)
            except Exception:
                minimum_rank = 0
                levels, bounds, bands = [], [], []
                cert = exact = cover = growth_rounds = charts = wall_timeout = retry_timeout = 0
            if mode_name not in {"fixed", "goal_minus_one"}:
                errors.append(f"Step {index} · Record Breaker Lane: threshold mode must be fixed or goal_minus_one")
            if mode_name == "fixed" and minimum_rank <= 0:
                errors.append(f"Step {index} · Record Breaker Lane: minimum rigorous rank must be positive")
            if not levels or any(x < 2 for x in levels):
                errors.append(f"Step {index} · Record Breaker Lane: descent levels must be >= 2")
            if not bounds or any(x < 2 for x in bounds):
                errors.append(f"Step {index} · Record Breaker Lane: saturation bounds must be >= 2")
            if not bands or any(len(pair) != 2 or pair[0] < 1 or pair[1] < pair[0] for pair in bands):
                errors.append(f"Step {index} · Record Breaker Lane: denominator bands must be valid positive intervals")
            elif any(bands[j][0] <= bands[j - 1][1] for j in range(1, len(bands))):
                errors.append(f"Step {index} · Record Breaker Lane: denominator bands must be strictly increasing and non-overlapping")
            if min(cert, exact, cover, growth_rounds, charts, wall_timeout, retry_timeout) <= 0:
                errors.append(f"Step {index} · Record Breaker Lane: search/certificate/wall/retry budgets must be positive")
            if str(cfg.get("retry_policy") or "automatic") not in {"manual", "escalated", "automatic"}:
                errors.append(f"Step {index} · Record Breaker Lane: retry policy must be manual, escalated, or automatic")
            try:
                p = int(cfg.get("padic_prime") or 0)
            except Exception:
                p = 0
            if bool(cfg.get("padic_enabled", True)) and not _is_prime_int(p):
                errors.append(f"Step {index} · Record Breaker Lane: p-adic prime must be prime")
        elif sid == "surface_fibration_switch":
            try:
                max_children = int(cfg.get("max_children") or 0)
            except Exception:
                max_children = 0
            if max_children <= 0:
                errors.append(f"Step {index} · Surface / Fibration Switch: max children must be positive")
        elif sid == "higher_descent_ladder":
            try:
                levels = [int(x) for x in (cfg.get("levels") or [])]
                timeout = int(cfg.get("timeout") or 0)
                cert = int(cfg.get("certificate_timeout") or 0)
                exact = int(cfg.get("exact_candidates") or 0)
            except Exception:
                levels, timeout, cert, exact = [], 0, 0, 0
            if not levels or any(x < 2 for x in levels):
                errors.append(f"Step {index} · Higher Descent Ladder: descent levels must be integers >= 2")
            if timeout <= 0 or cert <= 0 or exact <= 0:
                errors.append(f"Step {index} · Higher Descent Ladder: budgets must be positive")
        elif sid == "specialization_injectivity_certificate":
            try:
                timeout = int(cfg.get("timeout") or 0)
                maximum = int(cfg.get("max_divisors") or 0)
                cert = int(cfg.get("certificate_timeout") or 0)
            except Exception:
                timeout, maximum, cert = 0, 0, 0
            if timeout <= 0 or maximum <= 0 or cert <= 0:
                errors.append(
                    f"Step {index} · Specialization Injectivity Certificate: "
                    "timeout, divisor budget, and certificate timeout must be positive"
                )
            if maximum > 65536:
                errors.append(
                    f"Step {index} · Specialization Injectivity Certificate: "
                    "divisor budget must be at most 65536"
                )
        elif sid == "covering_minimize_reduce":
            try:
                maximum = int(cfg.get("max_coverings") or 0)
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                maximum, timeout = 0, 0
            if maximum <= 0 or timeout <= 0:
                errors.append(
                    f"Step {index} · Covering Minimization & Reduction: "
                    "covering count and timeout must be positive"
                )
            if not isinstance(cfg.get("require_improvement"), bool):
                errors.append(
                    f"Step {index} · Covering Minimization & Reduction: "
                    "require improvement must be boolean"
                )
        elif sid == "covering_local_height_planner":
            try:
                values = [
                    int(cfg.get(key) or 0)
                    for key in (
                        "max_coverings", "timeout", "base_height", "max_height",
                        "base_timeout", "max_timeout",
                    )
                ]
            except Exception:
                values = [0]
            if min(values) <= 0:
                errors.append(
                    f"Step {index} · Local Solubility & Covering Height Planner: "
                    "covering, height, and timeout budgets must be positive"
                )
            elif values[3] < values[2] or values[5] < values[4]:
                errors.append(
                    f"Step {index} · Local Solubility & Covering Height Planner: "
                    "maximum budgets must not be below base budgets"
                )
            if not isinstance(cfg.get("reject_locally_insoluble"), bool):
                errors.append(
                    f"Step {index} · Local Solubility & Covering Height Planner: "
                    "local-obstruction rejection must be boolean"
                )
        elif sid == "selmer_element_fanout":
            try:
                maximum = int(cfg.get("max_coverings") or 0)
                height = int(cfg.get("height") or 0)
                timeout = int(cfg.get("timeout") or 0)
                cert = int(cfg.get("certificate_timeout") or 0)
            except Exception:
                maximum, height, timeout, cert = 0, 0, 0, 0
            if min(maximum, height, timeout, cert) <= 0:
                errors.append(f"Step {index} · Selmer Element Fan-Out: covering/search budgets must be positive")
            if not isinstance(cfg.get("use_covering_plans"), bool):
                errors.append(
                    f"Step {index} · Selmer Element Fan-Out: "
                    "use covering plans must be boolean"
                )
        elif sid == "padic_covering_search":
            try:
                prime = int(cfg.get("prime") or 0)
                precision = int(cfg.get("precision") or 0)
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                prime, precision, timeout = 0, 0, 0
            if not _is_prime_int(prime) or precision <= 0 or timeout <= 0:
                errors.append(f"Step {index} · p-adic Covering Point Search: choose a prime and positive precision/timeout")
        elif sid == "full_saturation_index_recovery":
            try:
                bounds = [int(x) for x in (cfg.get("prime_bounds") or [])]
                timeout = int(cfg.get("timeout") or 0)
                cert = int(cfg.get("certificate_timeout") or 0)
                retry_timeout = int(cfg.get("retry_timeout") or 0)
            except Exception:
                bounds, timeout, cert, retry_timeout = [], 0, 0, 0
            if not bounds or any(x < 2 for x in bounds):
                errors.append(f"Step {index} · Saturation Prime Ladder / Index Recovery: prime bounds must be integers >= 2")
            policy = str(cfg.get("retry_policy") or "manual")
            if timeout <= 0 or cert <= 0 or retry_timeout <= 0:
                errors.append(f"Step {index} · Saturation Prime Ladder / Index Recovery: budgets must be positive")
            if policy not in {"manual", "escalated", "automatic"}:
                errors.append(
                    f"Step {index} · Saturation Prime Ladder / Index Recovery: "
                    "retry policy must be manual, escalated, or automatic"
                )
        elif sid == "height_lattice_reduction":
            try:
                precision = int(cfg.get("precision_bits") or 0)
                timeout = int(cfg.get("timeout") or 0)
                retry_timeout = int(cfg.get("retry_timeout") or 0)
                cert = int(cfg.get("certificate_timeout") or 0)
            except Exception:
                precision, timeout, retry_timeout, cert = 0, 0, 0, 0
            policy = str(cfg.get("retry_policy") or "manual")
            if precision < 64 or min(timeout, retry_timeout, cert) <= 0:
                errors.append(
                    f"Step {index} · Height-Lattice Reduction: precision must "
                    "be >=64 bits and lattice/retry/certificate budgets positive"
                )
            if policy not in {"manual", "escalated", "automatic"}:
                errors.append(
                    f"Step {index} · Height-Lattice Reduction: retry policy "
                    "must be manual, escalated, or automatic"
                )
        elif sid == "selmer_bound":
            policy = str(cfg.get("retry_policy") or "manual")
            if policy not in {"manual", "escalated", "automatic"}:
                errors.append(
                    f"Step {index} · mwrank 2-Descent Rank Upper: "
                    "retry policy must be manual, escalated, or automatic"
                )
            try:
                timeout = int(cfg.get("timeout") or 0)
                retry_timeout = int(cfg.get("retry_timeout") or 0)
            except Exception:
                timeout, retry_timeout = 0, 0
            if timeout <= 0:
                errors.append(
                    f"Step {index} · mwrank 2-Descent Rank Upper: timeout must be positive"
                )
            if policy == "escalated" and retry_timeout <= timeout:
                errors.append(
                    f"Step {index} · mwrank 2-Descent Rank Upper: "
                    "escalated retry timeout must exceed the initial timeout"
                )
            elif retry_timeout <= 0:
                errors.append(
                    f"Step {index} · mwrank 2-Descent Rank Upper: "
                    "retry timeout must be positive"
                )
        elif sid == "small_point_density":
            try:
                heights = [int(x) for x in (cfg.get("heights") or [])]
                timeout = int(cfg.get("timeout") or 0)
                certificate_timeout = int(cfg.get("certificate_timeout") or 0)
                exact_candidates = int(cfg.get("exact_candidates") or 0)
            except Exception:
                heights, timeout, certificate_timeout, exact_candidates = [], 0, 0, 0
            if not heights or any(x <= 0 for x in heights):
                errors.append(f"Step {index} · Small-Point Density: heights must be positive")
            elif any(b <= a for a, b in zip(heights, heights[1:])):
                errors.append(f"Step {index} · Small-Point Density: heights must strictly increase")
            if timeout <= 0 or certificate_timeout <= 0 or exact_candidates <= 0:
                errors.append(f"Step {index} · Small-Point Density: timeouts and exact-candidate budget must be positive")
        elif sid == "pointed_quartic":
            try:
                heights = [int(x) for x in (cfg.get("heights") or [])]
                anchors = int(cfg.get("anchors") or 0)
                pool_size = int(cfg.get("pool_size") or 0)
                rounds = int(cfg.get("rounds") or 0)
                deep_keep = int(cfg.get("deep_keep") or 0)
                timeout = int(cfg.get("timeout") or 0)
                reduce_timeout = int(cfg.get("reduce_timeout") or 0)
                coverage = float(cfg.get("dry_round_min_coverage") or 0.0)
            except Exception:
                heights, anchors, pool_size, rounds = [], 0, 0, 0
                deep_keep, timeout, reduce_timeout, coverage = 0, 0, -1, 0.0
            if not heights or any(x <= 0 for x in heights):
                errors.append(
                    f"Step {index} · Point-Centered Quartics: heights must be positive"
                )
            if anchors <= 0 or pool_size < anchors:
                errors.append(
                    f"Step {index} · Point-Centered Quartics: pool size must be at least the positive anchor count"
                )
            if rounds <= 0 or deep_keep <= 0 or timeout <= 0 or reduce_timeout < 0:
                errors.append(
                    f"Step {index} · Point-Centered Quartics: round/search budgets must be valid"
                )
            if not (0.0 < coverage <= 1.0):
                errors.append(
                    f"Step {index} · Point-Centered Quartics: dry-round coverage threshold must be in (0,1]"
                )
        elif sid == "mw_growth_loop":
            try:
                heights = [int(x) for x in (cfg.get("heights") or [])]
                anchors = int(cfg.get("anchors") or 0)
                pool_size = int(cfg.get("pool_size") or 0)
                max_rounds = int(cfg.get("max_rounds") or 0)
                deep_keep = int(cfg.get("deep_keep") or 0)
                timeout = int(cfg.get("timeout") or 0)
                reduce_timeout = int(cfg.get("reduce_timeout") or 0)
                coverage = float(cfg.get("dry_round_min_coverage") or 0.0)
                cert = int(cfg.get("certificate_timeout") or 0)
                exact = int(cfg.get("exact_candidates") or 0)
            except Exception:
                heights, anchors, pool_size, max_rounds = [], 0, 0, 0
                deep_keep, timeout, reduce_timeout, coverage = 0, 0, -1, 0.0
                cert, exact = 0, 0
            if not heights or any(x <= 0 for x in heights):
                errors.append(f"Step {index} · MW Growth Loop: heights must be positive")
            if anchors <= 0 or pool_size < anchors:
                errors.append(f"Step {index} · MW Growth Loop: pool size must be at least the positive anchor count")
            if max_rounds <= 0 or deep_keep <= 0 or timeout <= 0 or reduce_timeout < 0:
                errors.append(f"Step {index} · MW Growth Loop: round/search budgets must be valid")
            if not (0.0 < coverage <= 1.0):
                errors.append(
                    f"Step {index} · MW Growth Loop: dry-round coverage threshold must be in (0,1]"
                )
            if cert <= 0 or exact <= 0:
                errors.append(f"Step {index} · MW Growth Loop: certificate budgets must be positive")
        elif sid == "point_yield_persistence":
            try:
                minimum_bands = int(cfg.get("minimum_bands") or 0)
            except Exception:
                minimum_bands = 0
            if minimum_bands <= 0:
                errors.append(f"Step {index} · Point-Yield Persistence: minimum bands must be positive")
        elif sid == "select_survivors":
            try:
                keep = int(cfg.get("keep"))
            except Exception:
                keep = 0
            if keep <= 0:
                errors.append(f"Step {index} · Select Survivors: keep must be positive")
            if str(cfg.get("ranking") or "") not in {
                "rank_then_yield_then_score", "rank_then_score", "score"
            }:
                errors.append(f"Step {index} · Select Survivors: unknown ranking policy")
        elif sid == "simon_covering":
            try:
                timeout = int(cfg.get("timeout") or 0)
                lim1 = int(cfg.get("lim1") or 0)
                lim3 = int(cfg.get("lim3") or 0)
                limbigprime = int(
                    cfg.get("limbigprime")
                    if cfg.get("limbigprime") is not None
                    else 0
                )
                exact_candidates = int(cfg.get("exact_candidates") or 0)
            except Exception:
                timeout, lim1, lim3, limbigprime, exact_candidates = 0, 0, 0, -1, 0
            if timeout <= 0 or lim1 <= 0 or lim3 <= 0 or exact_candidates <= 0:
                errors.append(
                    f"Step {index} · Simon 2-Covering (Legacy): search/certificate budgets must be positive"
                )
            if limbigprime < 0:
                errors.append(
                    f"Step {index} · Simon 2-Covering (Legacy): LIMBIGPRIME must be zero or positive"
                )
        elif sid == "integral_seed":
            try:
                heights = [int(x) for x in (cfg.get("heights") or [])]
                timeout = int(cfg.get("timeout") or 0)
                model_prep_timeout = int(cfg.get("model_prep_timeout") or 0)
            except Exception:
                heights, timeout, model_prep_timeout = [], 0, 0
            if not heights or any(x <= 0 for x in heights):
                errors.append(f"Step {index} · Native Model Seed: heights must be positive")
            if timeout <= 0:
                errors.append(f"Step {index} · Native Model Seed: timeout must be positive")
            if str(cfg.get("model_mode") or "") not in {"stored", "minimal"}:
                errors.append(f"Step {index} · Native Model Seed: model mode must be stored or minimal")
            if model_prep_timeout <= 0:
                errors.append(f"Step {index} · Native Model Seed: model-prep timeout must be positive")
        elif sid == "affine_search":
            try:
                heights = [int(x) for x in (cfg.get("heights") or [])]
                charts = int(cfg.get("charts") or 0)
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                heights, charts, timeout = [], 0, 0
            if not heights or any(x <= 0 for x in heights):
                errors.append(f"Step {index} · Affine Rational Search: heights must be positive")
            if charts <= 0:
                errors.append(f"Step {index} · Affine Rational Search: charts must be positive")
            if timeout <= 0:
                errors.append(f"Step {index} · Affine Rational Search: timeout must be positive")
        elif sid == "denominator_band":
            try:
                low = int(cfg.get("denominator_low"))
                high = int(cfg.get("denominator_high"))
            except Exception:
                low, high = 0, -1
            if low < 1 or high < low:
                errors.append(
                    f"Step {index} · Denominator Band Search: require 1 <= denominator_low <= denominator_high"
                )
            try:
                charts = int(cfg.get("charts"))
            except Exception:
                charts = 0
            if charts <= 0:
                errors.append(f"Step {index} · Denominator Band Search: charts must be positive")
            try:
                timeout = int(cfg.get("timeout") or 0)
            except Exception:
                timeout = 0
            if timeout <= 0:
                errors.append(f"Step {index} · Denominator Band Search: timeout must be positive")
            heights = cfg.get("heights") or []
            try:
                height_values = [int(x) for x in heights]
            except Exception:
                height_values = []
            if not height_values or any(x <= 0 for x in height_values):
                errors.append(f"Step {index} · Denominator Band Search: heights must be positive")
        elif sid == "adaptive_ladder":
            bands = cfg.get("bands") or []
            keeps = cfg.get("keeps") or []
            if not isinstance(bands, list) or not bands:
                errors.append(f"Step {index} · Adaptive Denominator Ladder: add at least one band")
                continue
            parsed = []
            ok = True
            for band in bands:
                if not isinstance(band, (list, tuple)) or len(band) != 2:
                    ok = False
                    break
                try:
                    low, high = int(band[0]), int(band[1])
                except Exception:
                    ok = False
                    break
                if low < 1 or high < low:
                    ok = False
                    break
                parsed.append((low, high))
            if not ok:
                errors.append(
                    f"Step {index} · Adaptive Denominator Ladder: bands must be positive low-high pairs"
                )
            else:
                for previous, current in zip(parsed, parsed[1:]):
                    if current[0] <= previous[1]:
                        errors.append(
                            f"Step {index} · Adaptive Denominator Ladder: bands must be strictly increasing and non-overlapping"
                        )
                        break
            if not isinstance(keeps, list) or len(keeps) != len(bands):
                errors.append(
                    f"Step {index} · Adaptive Denominator Ladder: keeps must match the number of bands"
                )
            else:
                try:
                    keep_values = [int(x) for x in keeps]
                except Exception:
                    keep_values = []
                if (
                    len(keep_values) != len(keeps)
                    or any(x <= 0 for x in keep_values)
                    or any(b > a for a, b in zip(keep_values, keep_values[1:]))
                ):
                    errors.append(
                        f"Step {index} · Adaptive Denominator Ladder: keeps must be positive and nonincreasing"
                    )
            if str(cfg.get("ranking") or "") not in {
                "rank_then_yield_then_score", "rank_then_score", "score"
            }:
                errors.append(
                    f"Step {index} · Adaptive Denominator Ladder: unknown ranking policy"
                )
            try:
                charts = int(cfg.get("charts"))
                timeout = int(cfg.get("timeout"))
                height_values = [int(x) for x in (cfg.get("heights") or [])]
            except Exception:
                charts, timeout, height_values = 0, 0, []
            if charts <= 0:
                errors.append(
                    f"Step {index} · Adaptive Denominator Ladder: charts must be positive"
                )
            if timeout <= 0:
                errors.append(
                    f"Step {index} · Adaptive Denominator Ladder: timeout must be positive"
                )
            if not height_values or any(x <= 0 for x in height_values):
                errors.append(
                    f"Step {index} · Adaptive Denominator Ladder: heights must be positive"
                )

    return errors


GEOMETRY_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 5000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 250}},
    {"id": "exact_torsion"},
    {"id": "family_baseline"},
    {"id": "pari_upper_gate"},
    {"id": "integral_seed"},
    {"id": "simon_covering"},
    {"id": "plugin_geometry"},
    {"id": "pointed_quartic"},
    {"id": "affine_search"},
    {"id": "independence"},
    {"id": "final_upper"},
    {"id": "stop_goal"},
]

AUTO_HUNTER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 500, "keep": 12000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 2000, "keep": 300}},
    {"id": "exact_torsion"},
    {"id": "family_baseline"},
    {"id": "integral_seed"},
    {"id": "affine_search"},
    {"id": "independence"},
    {"id": "pari_upper_gate", "config": {"timeout": 8, "eliminate_below_goal": False}},
    {"id": "final_upper", "config": {"timeout": 30}},
    {"id": "stop_goal"},
]

AGGRESSIVE_RANK_HUNTER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 20000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 5000, "keep": 1000}},
    {
        "id": "prime_table_cache",
        "config": {
            "prime_bound": 5000,
            "include_all_bad": False,
            "eager_bad_primes": False,
        },
    },
    {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
    {"id": "frobenius_persistence", "config": {"bounds": [523, 1979, 5000]}},
    {"id": "exact_torsion"},
    {"id": "select_survivors", "config": {"keep": 64, "ranking": "score"}},
    {"id": "family_baseline"},
    {
        "id": "select_survivors",
        "config": {"keep": 16, "ranking": "rank_then_score"},
    },
    {"id": "integral_seed", "config": {"heights": [1000, 10000, 100000], "timeout": 6}},
    {"id": "independence", "config": {"certificate_timeout": 180, "max_candidates": 128}},
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [4, 8, 12],
            "descent_timeout": 15,
            "late_saturation_enabled": True,
            "saturation_bounds": [7, 31],
            "saturation_timeout": 15,
            "stop_on_unit_index": True,
            "denominator_charts": 12,
            "stop_on_growth": True,
        },
    },
    {"id": "independence", "config": {"certificate_timeout": 240, "max_candidates": 192}},
    {
        "id": "upper_bound_rescue_ladder",
        "config": {
            "pari_timeout": 3,
            "isogeny_pari_timeout": 3,
            "isogeny_max_models": 4,
            "selmer_enabled": True,
            "selmer_timeout": 8,
            "known_basis_coverings": True,
            "simon_timeout": 10,
            "covering_timeout": 10,
            "higher_descent_hooks": True,
            "higher_timeout": 15,
        },
    },
    {"id": "stop_goal"},
]

HIGH_BASELINE_RANK_HUNTER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 50000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 5000, "keep": 5000}},
    {
        "id": "prime_table_cache",
        "config": {
            "prime_bound": 10000,
            "include_all_bad": False,
            "eager_bad_primes": False,
        },
    },
    {"id": "multi_scale_frobenius", "config": {"bounds": [523, 1979, 5000, 10000]}},
    {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
    {"id": "frobenius_persistence", "config": {"bounds": [523, 1979, 5000, 10000]}},
    {"id": "explicit_formula_indicator", "config": {"prime_bound": 5000, "max_prime_power": 4}},
    {"id": "exact_torsion"},
    {"id": "select_survivors", "config": {"keep": 256, "ranking": "score"}},
    {
        "id": "local_root_numbers",
        "config": {
            "prime_bound": 200,
            "include_all_bad": False,
            "bad_prime_timeout": 1,
        },
    },
    {
        "id": "bad_prime_fingerprint",
        "config": {
            "prime_bound": 200,
            "include_all_bad": False,
            "bad_prime_timeout": 1,
        },
    },
    {"id": "family_baseline", "config": {"certificate_timeout": 240, "exact_candidates": 128}},
    {"id": "select_survivors", "config": {"keep": 256, "ranking": "rank_then_score"}},
    {
        "id": "integral_seed",
        "config": {
            "heights": [1000, 10000],
            "timeout": 4,
            "model_mode": "stored",
            "certify_after_search": False,
        },
    },
    {"id": "independence", "config": {"certificate_timeout": 240, "max_candidates": 192}},
    {"id": "select_survivors", "config": {"keep": 64, "ranking": "rank_then_score"}},
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [4, 8, 12],
            "descent_timeout": 20,
            "late_saturation_enabled": False,
            "mw_growth_rounds": 5,
            "mw_growth_anchors": 32,
            "mw_growth_pool": 320,
            "denominator_bands": [[2, 100], [101, 1000], [1001, 10000]],
            "denominator_charts": 24,
            "denominator_heights": [10000, 100000],
            "denominator_timeout": 10,
            "certificate_timeout": 240,
            "exact_candidates": 160,
            "stop_on_growth": False,
        },
    },
    {"id": "select_survivors", "config": {"keep": 24, "ranking": "rank_then_score"}},
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [4, 8, 12],
            "descent_timeout": 30,
            "late_saturation_enabled": False,
            "mw_growth_rounds": 7,
            "mw_growth_anchors": 48,
            "mw_growth_pool": 480,
            "denominator_bands": [[10001, 100000]],
            "denominator_charts": 48,
            "denominator_heights": [100000, 1000000],
            "denominator_timeout": 15,
            "certificate_timeout": 300,
            "exact_candidates": 192,
            "stop_on_growth": False,
        },
    },
    {"id": "select_survivors", "config": {"keep": 8, "ranking": "rank_then_score"}},
    {
        "id": "local_root_numbers",
        "config": {
            "prime_bound": 200,
            "include_all_bad": True,
            "bad_prime_timeout": 20,
        },
    },
    {
        "id": "bad_prime_fingerprint",
        "config": {
            "prime_bound": 200,
            "include_all_bad": True,
            "bad_prime_timeout": 20,
        },
    },
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [4, 8, 12],
            "descent_timeout": 45,
            "late_saturation_enabled": False,
            "mw_growth_rounds": 8,
            "mw_growth_anchors": 64,
            "mw_growth_pool": 640,
            "denominator_bands": [[100001, 1000000]],
            "denominator_charts": 64,
            "denominator_heights": [100000, 1000000, 10000000],
            "denominator_timeout": 20,
            "certificate_timeout": 360,
            "exact_candidates": 224,
            "stop_on_growth": False,
        },
    },
    {
        "id": "full_saturation_index_recovery",
        "config": {
            "prime_bounds": [7, 31],
            "timeout": 30,
            "stop_on_unit_index": True,
            "certificate_timeout": 360,
            "exact_candidates": 224,
        },
    },
    {
        "id": "height_lattice_reduction",
        "config": {
            "precision_bits": 384,
            "certificate_timeout": 360,
            "exact_candidates": 224,
        },
    },
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [4, 8, 12],
            "descent_timeout": 45,
            "late_saturation_enabled": True,
            "saturation_bounds": [31, 127],
            "saturation_timeout": 45,
            "stop_on_unit_index": False,
            "late_lattice_enabled": True,
            "lattice_precision_bits": 384,
            "mw_growth_rounds": 10,
            "mw_growth_anchors": 64,
            "mw_growth_pool": 640,
            "denominator_bands": [[2, 100], [101, 1000], [1001, 10000], [10001, 100000], [100001, 1000000]],
            "denominator_charts": 64,
            "denominator_heights": [100000, 1000000, 10000000],
            "denominator_timeout": 20,
            "certificate_timeout": 420,
            "exact_candidates": 256,
            "stop_on_growth": False,
        },
    },
    {"id": "record_breaker_lane"},
    {
        "id": "upper_bound_rescue_ladder",
        "config": {
            "pari_timeout": 5,
            "isogeny_pari_timeout": 5,
            "isogeny_max_models": 6,
            "selmer_enabled": True,
            "selmer_timeout": 15,
            "known_basis_coverings": True,
            "simon_timeout": 20,
            "covering_timeout": 20,
            "higher_descent_hooks": True,
            "higher_timeout": 30,
            "certificate_timeout": 300,
            "exact_candidates": 192,
        },
    },
]

HISTORICAL_SEED_FUNNEL_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 30000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 3000}},
    {
        "id": "select_survivors",
        "config": {"keep": 256, "ranking": "score"},
    },
    {
        "id": "specialization_seeds",
        "config": {"certificate_timeout": 240},
    },
    {
        "id": "select_survivors",
        "config": {"keep": 96, "ranking": "rank_then_yield_then_score"},
    },
    {
        "id": "prime_table_cache",
        "config": {
            "prime_bound": 5000,
            "include_all_bad": False,
            "eager_bad_primes": False,
        },
    },
    {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
    {"id": "frobenius_persistence", "config": {"bounds": [523, 1979, 5000]}},
    {
        "id": "select_survivors",
        "config": {"keep": 32, "ranking": "rank_then_yield_then_score"},
    },
    {
        "id": "family_baseline",
        "config": {"certificate_timeout": 180, "exact_candidates": 128},
    },
    {
        "id": "integral_seed",
        "config": {
            "heights": [1000, 10000, 100000],
            "timeout": 6,
            "model_mode": "stored",
            "certify_after_search": False,
        },
    },
    {
        "id": "independence",
        "config": {"certificate_timeout": 180, "max_candidates": 128},
    },
    {
        "id": "adaptive_ladder",
        "config": {
            "bands": [[2, 100], [101, 1000], [1001, 10000], [10001, 100000]],
            "keeps": [16, 10, 8, 6],
            "ranking": "rank_then_yield_then_score",
            "charts": 5,
            "heights": [10000, 100000],
            "timeout": 5,
            "certificate_timeout": 180,
            "exact_candidates": 128,
        },
    },
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [4, 8],
            "descent_timeout": 15,
            "max_coverings": 6,
            "covering_timeout": 15,
            "padic_enabled": False,
            "late_saturation_enabled": False,
            "late_lattice_enabled": False,
            "mw_growth_rounds": 1,
            "mw_growth_anchors": 12,
            "mw_growth_pool": 72,
            "mw_growth_deep_keep": 3,
            "mw_growth_heights": [100000, 1000000],
            "mw_growth_timeout": 4,
            "mw_growth_reduce_timeout": 8,
            "denominator_bands": [[2, 100], [101, 1000], [1001, 10000]],
            "denominator_charts": 3,
            "denominator_heights": [100000, 1000000],
            "denominator_timeout": 4,
            "certificate_timeout": 180,
            "exact_candidates": 128,
            "stop_on_growth": True,
        },
    },
    {
        "id": "record_breaker_lane",
        "config": {
            "minimum_rank_mode": "goal_minus_one",
            "descent_levels": [4],
            "descent_timeout": 15,
            "max_coverings": 8,
            "covering_timeout": 20,
            "padic_enabled": False,
            "late_saturation_enabled": True,
            "saturation_bounds": [31, 127],
            "saturation_timeout": 20,
            "late_lattice_enabled": True,
            "mw_growth_rounds": 2,
            "mw_growth_anchors": 24,
            "mw_growth_pool": 192,
            "mw_growth_deep_keep": 8,
            "mw_growth_heights": [1000000, 10000000],
            "mw_growth_timeout": 8,
            "mw_growth_reduce_timeout": 12,
            "denominator_bands": [[10001, 100000], [100001, 1000000]],
            "denominator_charts": 6,
            "denominator_heights": [1000000, 10000000],
            "denominator_timeout": 8,
            "certificate_timeout": 240,
            "exact_candidates": 192,
            "stop_on_growth": True,
        },
    },
    {"id": "stop_goal"},
]


FRONTIERMATH_RECORD_BREAKER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 30000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 5000, "keep": 2000}},
    {
        "id": "prime_table_cache",
        "config": {
            "prime_bound": 10000,
            "include_all_bad": False,
            "eager_bad_primes": False,
        },
    },
    {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
    {
        "id": "frobenius_persistence",
        "config": {"bounds": [523, 1979, 5000, 10000]},
    },
    {"id": "exact_torsion"},
    {
        "id": "select_survivors",
        "config": {"keep": 256, "ranking": "rank_then_yield_then_score"},
    },
    {"id": "family_baseline"},
    {
        "id": "integral_seed",
        "config": {
            "heights": [1000, 10000, 100000],
            "timeout": 6,
            "model_mode": "stored",
            "certify_after_search": False,
        },
    },
    {
        "id": "independence",
        "config": {"certificate_timeout": 180, "max_candidates": 192},
    },
    {
        "id": "select_survivors",
        "config": {"keep": 64, "ranking": "rank_then_yield_then_score"},
    },
    {
        "id": "adaptive_ladder",
        "config": {
            "bands": [[2, 100], [101, 1000], [1001, 10000], [10001, 100000]],
            "keeps": [32, 16, 8, 4],
            "ranking": "rank_then_yield_then_score",
            "charts": 12,
            "heights": [100000, 1000000],
            "timeout": 12,
            "certificate_timeout": 240,
            "exact_candidates": 192,
        },
    },
    {
        "id": "large_height_generator_hunt",
        "config": {
            "stop_on_growth": False,
            "certificate_timeout": 300,
            "exact_candidates": 256,
        },
    },
    {
        "id": "record_breaker_lane",
        "config": {
            "minimum_rank": 18,
            "stop_on_growth": False,
            "certificate_timeout": 600,
            "exact_candidates": 384,
        },
    },
    {"id": "stop_goal"},
]

SPECIALIZATION_TRICKSTER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 10000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 500}},
    {"id": "constructive_rank_jump_loop"},
    {"id": "family_baseline"},
    {"id": "independence", "config": {"certificate_timeout": 180, "max_candidates": 128}},
    {"id": "rank_jump_base_change", "config": {"max_children": 32, "include_parent": True}},
    {"id": "family_baseline"},
    {"id": "integral_seed"},
    {"id": "independence"},
    {"id": "stop_goal"},
]

GENERATOR_BREAKER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 5000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 250}},
    {"id": "exact_torsion"},
    {"id": "family_baseline"},
    {"id": "integral_seed"},
    {"id": "independence"},
    {"id": "large_height_generator_hunt", "config": {"stop_on_growth": False}},
    {"id": "independence", "config": {"certificate_timeout": 240, "max_candidates": 192}},
    {"id": "final_upper", "config": {"timeout": 90}},
]

SELMER_GATE_TEMPLATE = [
    {"id": "nagao_screen"},
    {"id": "exact_torsion"},
    {
        "id": "selmer_bound",
        "config": {"timeout": 180, "first_limit": 40, "second_limit": 20},
    },
    {"id": "selmer_headroom"},
]

COVERING_FOREST_TEMPLATE = [
    {"id": "nagao_screen"},
    {"id": "exact_torsion"},
    {
        "id": "selmer_bound",
        "config": {"timeout": 180, "first_limit": 40, "second_limit": 20},
    },
    {"id": "selmer_headroom"},
    {
        "id": "covering_selmer_branch",
        "config": {
            "engines": ["simon_known", "mwrank_selmer", "mwrank_coverings"],
            "timeout": 120,
            "certificate_timeout": 240,
            "exact_candidates": 192,
        },
    },
    {
        "id": "selmer_element_fanout",
        "config": {
            "max_coverings": 32,
            "height": 1000000,
            "timeout": 60,
            "one_point": False,
            "certificate_timeout": 240,
            "exact_candidates": 192,
        },
    },
    {
        "id": "independence",
        "config": {"certificate_timeout": 240, "max_candidates": 192},
    },
    {"id": "stop_goal"},
]

ISOGENY_HUNT_TEMPLATE = [
    {"id": "nagao_screen"},
    {"id": "exact_torsion"},
    {
        "id": "isogeny_walk",
        "config": {
            "degrees": [2],
            "max_children": 3,
            "include_parent": True,
            "transfer_basis": True,
            "certificate_timeout": 240,
            "exact_candidates": 192,
        },
    },
    {
        "id": "integral_seed",
        "config": {
            "heights": [1000, 10000, 100000],
            "timeout": 8,
            "model_mode": "stored",
            "certify_after_search": False,
        },
    },
    {"id": "simon_covering", "config": {"timeout": 180, "exact_candidates": 128}},
    {
        "id": "mwrank_covering",
        "config": {
            "timeout": 180,
            "first_limit": 40,
            "second_limit": 20,
            "exact_candidates": 128,
        },
    },
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [2, 4, 8, 12],
            "descent_timeout": 60,
            "max_coverings": 32,
            "covering_height": 1000000,
            "covering_timeout": 60,
            "late_saturation_enabled": True,
            "saturation_bounds": [7, 31],
            "saturation_timeout": 30,
            "mw_growth_rounds": 6,
            "mw_growth_anchors": 32,
            "mw_growth_pool": 320,
            "denominator_bands": [
                [2, 100],
                [101, 1000],
                [1001, 10000],
                [10001, 100000]
            ],
            "denominator_charts": 12,
            "denominator_heights": [100000, 1000000],
            "denominator_timeout": 15,
            "certificate_timeout": 300,
            "exact_candidates": 256,
            "stop_on_growth": False,
        },
    },
    {
        "id": "independence",
        "config": {"certificate_timeout": 300, "max_candidates": 256},
    },
    {"id": "stop_goal"},
]

ARITHMETIC_SIEGE_TEMPLATE = [
    {"id": "nagao_screen"},
    {"id": "exact_torsion"},
    {
        "id": "selmer_bound",
        "config": {"timeout": 240, "first_limit": 50, "second_limit": 25},
    },
    {"id": "selmer_headroom"},
    {
        "id": "covering_selmer_branch",
        "config": {
            "engines": ["simon_known", "mwrank_selmer", "mwrank_coverings"],
            "timeout": 180,
            "certificate_timeout": 300,
            "exact_candidates": 256,
        },
    },
    {
        "id": "selmer_element_fanout",
        "config": {
            "max_coverings": 48,
            "height": 3000000,
            "timeout": 90,
            "one_point": False,
            "certificate_timeout": 300,
            "exact_candidates": 256,
        },
    },
    {
        "id": "independence",
        "config": {"certificate_timeout": 300, "max_candidates": 256},
    },
    {
        "id": "isogeny_walk",
        "config": {
            "degrees": [2],
            "max_children": 3,
            "include_parent": True,
            "transfer_basis": True,
            "certificate_timeout": 300,
            "exact_candidates": 256,
        },
    },
    {
        "id": "large_height_generator_hunt",
        "config": {
            "descent_levels": [2, 4, 8, 12],
            "descent_timeout": 90,
            "max_coverings": 40,
            "covering_height": 3000000,
            "covering_timeout": 90,
            "late_saturation_enabled": True,
            "saturation_bounds": [7, 31, 127],
            "saturation_timeout": 45,
            "mw_growth_rounds": 8,
            "mw_growth_anchors": 48,
            "mw_growth_pool": 480,
            "denominator_bands": [
                [2, 100],
                [101, 1000],
                [1001, 10000],
                [10001, 100000],
                [100001, 1000000]
            ],
            "denominator_charts": 20,
            "denominator_heights": [100000, 1000000, 10000000],
            "denominator_timeout": 20,
            "certificate_timeout": 360,
            "exact_candidates": 320,
            "stop_on_growth": False,
        },
    },
    {
        "id": "independence",
        "config": {"certificate_timeout": 360, "max_candidates": 320},
    },
    {"id": "stop_goal"},
]

PRIME_SIEVE_FUNNEL_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 30000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 3000}},
    {"id": "prime_table_cache", "config": {"prime_bound": 10000}},
    {"id": "multi_scale_frobenius", "config": {"bounds": [523, 1979, 5000, 10000]}},
    {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
    {"id": "frobenius_persistence", "config": {"bounds": [523, 1979, 5000, 10000]}},
    {"id": "explicit_formula_indicator", "config": {"prime_bound": 5000, "max_prime_power": 4}},
    {"id": "local_root_numbers"},
    {"id": "bad_prime_fingerprint"},
    {"id": "exact_torsion"},
    {"id": "select_survivors", "config": {"keep": 100}},
    {"id": "integral_seed"},
    {"id": "independence"},
]

GEOMETRY_GRINDER_TEMPLATE = [
    {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 8000}},
    {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 400}},
    {"id": "exact_torsion"},
    {"id": "family_baseline"},
    {"id": "integral_seed"},
    {"id": "independence"},
    {"id": "simon_covering", "config": {"timeout": 120}},
    {"id": "mwrank_covering", "config": {"timeout": 120}},
    {"id": "plugin_geometry", "config": {"timeout": 240, "exact_candidates": 96}},
    {"id": "mw_growth_loop", "config": {"max_rounds": 8, "anchors": 32, "pool_size": 320}},
    {"id": "independence", "config": {"certificate_timeout": 180, "max_candidates": 128}},
    {"id": "final_upper", "config": {"timeout": 60}},
]

MINIMAL_TEMPLATE = [
    {"id": "nagao_screen"},
    {"id": "nagao_rescore"},
    {"id": "exact_torsion"},
]

_PRESET_DEFS = (
    ("historical_seed_funnel", "Historical Seed Funnel", "Family-first funnel: recertify preserved exact specialization bundles before heuristic rescoring, then spend bounded deep-search budget only near the rank goal."),
    ("frontiermath_record_breaker", "FrontierMath Record Breaker", "Target-driven record hunt: maximize exact independent points, stop immediately at the rigorous lower-bound goal, and skip exact-rank upper-bound work."),
    ("high_baseline_rank_hunter", "High-Baseline Rank Hunter", "Exploit exact family baselines, progressively concentrate search geometry, and escalate rigorous rank-25+ fibers without stopping the campaign."),
    ("aggressive_rank_hunter", "Aggressive Rank Hunter", "Wide heuristic funnel, deep generator recovery, then bounded upper-bound rescue."),
    ("specialization_trickster", "Specialization Trickster", "Family-only constructive specialization and exact rank-jump transforms."),
    ("generator_breaker", "Generator Breaker", "Focus compute on extracting hidden/high-height Mordell-Weil generators."),
    ("selmer_gate", "Selmer Gate", "Compute a rigorous 2-Selmer upper and expose rank headroom before spending more point-search time."),
    ("covering_forest", "Covering Forest", "Run independent 2-descent/covering branches, fan out stored exact coverings, and certify mapped point growth."),
    ("isogeny_hunt", "2-Isogeny Hunt", "Search the rational 2-isogeny neighborhood, transfer the known basis, and attack rank-equivalent alternate models."),
    ("arithmetic_siege", "Arithmetic Siege", "Combine Selmer headroom, covering branches, 2-isogeny neighbors, and deep generator recovery."),
    ("prime_sieve_funnel", "Prime Sieve Funnel", "Cheap-to-medium Frobenius/local filters before point-search spend."),
    ("geometry_grinder", "Geometry Grinder", "Coverings, plugin geometry, and self-feeding MW quartic attacks."),
    ("geometry", "Geometry", "Balanced geometry-first legacy recipe."),
    ("auto", "Auto", "Balanced automatic hunt."),
    ("minimal", "Minimal", "Candidate screen and rescore only."),
)

PRESET_TEMPLATE_BY_ID = {
    "historical_seed_funnel": HISTORICAL_SEED_FUNNEL_TEMPLATE,
    "frontiermath_record_breaker": FRONTIERMATH_RECORD_BREAKER_TEMPLATE,
    "high_baseline_rank_hunter": HIGH_BASELINE_RANK_HUNTER_TEMPLATE,
    "aggressive_rank_hunter": AGGRESSIVE_RANK_HUNTER_TEMPLATE,
    "specialization_trickster": SPECIALIZATION_TRICKSTER_TEMPLATE,
    "generator_breaker": GENERATOR_BREAKER_TEMPLATE,
    "selmer_gate": SELMER_GATE_TEMPLATE,
    "covering_forest": COVERING_FOREST_TEMPLATE,
    "isogeny_hunt": ISOGENY_HUNT_TEMPLATE,
    "arithmetic_siege": ARITHMETIC_SIEGE_TEMPLATE,
    "prime_sieve_funnel": PRIME_SIEVE_FUNNEL_TEMPLATE,
    "geometry_grinder": GEOMETRY_GRINDER_TEMPLATE,
    "geometry": GEOMETRY_TEMPLATE,
    "auto": AUTO_HUNTER_TEMPLATE,
    "minimal": MINIMAL_TEMPLATE,
}

def pipeline_catalog_fingerprint():
    """Return a deterministic fingerprint of executable Pipeline catalog contracts."""
    stages = []
    for spec in _STAGE_LIST:
        stages.append({
            "id": spec.id,
            "label": spec.label,
            "category": spec.category,
            "targets": sorted(spec.targets),
            "requires": sorted(spec.requires),
            "provides": sorted(spec.provides),
            "evidence": spec.evidence,
            "cost": spec.cost,
            "defaults": spec.defaults,
            "repeatable": bool(spec.repeatable),
            "conditional": spec.conditional,
        })
    presets = {
        str(key): value
        for key, value in sorted(PRESET_TEMPLATE_BY_ID.items())
    }
    payload = {
        "catalog_version": CATALOG_VERSION,
        "target_modes": list(TARGET_MODES),
        "stages": stages,
        "presets": presets,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def pipeline_preset_options(target_mode):
    mode = str(target_mode)
    out = []
    for preset_id, label, description in _PRESET_DEFS:
        if preset_id in {"specialization_trickster", "historical_seed_funnel"} and mode != "family":
            continue
        out.append({
            "id": preset_id,
            "label": label,
            "description": description,
        })
    return out



def general_search_stages(
    *,
    nagao_bound,
    shortlist,
    ratpoints_stages,
    ratpoints_timeout,
    certificate_timeout=120,
    exact_candidates=64,
):
    """Serialize the visible General Search controls into a Pipeline recipe."""
    if isinstance(ratpoints_stages, str):
        heights = [
            int(part.strip())
            for part in ratpoints_stages.split(",")
            if part.strip()
        ]
    else:
        heights = [int(value) for value in ratpoints_stages]
    heights = sorted(set(heights))
    if not heights or any(value <= 0 for value in heights):
        raise ValueError("ratpoints stages must be positive integers")

    nagao_bound = int(nagao_bound)
    shortlist = int(shortlist)
    ratpoints_timeout = int(ratpoints_timeout)
    certificate_timeout = int(certificate_timeout)
    exact_candidates = int(exact_candidates)
    if nagao_bound < 7:
        raise ValueError("Nagao bound must be at least 7")
    if shortlist <= 0:
        raise ValueError("shortlist must be positive")
    if min(ratpoints_timeout, certificate_timeout, exact_candidates) <= 0:
        raise ValueError("General Search Pipeline budgets must be positive")

    stages = normalize_pipeline([
        {
            "id": "nagao_screen",
            "config": {
                "prime_bound": nagao_bound,
                "keep": shortlist,
            },
        },
        {
            "id": "integral_seed",
            "config": {
                "heights": heights,
                "timeout": ratpoints_timeout,
                "retry_policy": "manual",
                "retry_timeout": max(ratpoints_timeout, 4 * ratpoints_timeout),
                "model_mode": "stored",
                "model_prep_timeout": max(8, ratpoints_timeout),
                "certify_after_search": False,
            },
        },
        {
            "id": "independence",
            "config": {
                "certificate_timeout": certificate_timeout,
                "max_candidates": exact_candidates,
            },
        },
        "stop_goal",
    ])
    errors = validate_pipeline("general", stages)
    if errors:
        raise ValueError("invalid built-in General Pipeline: " + "; ".join(errors))
    return stages


def template_stages(name, target_mode):
    name = str(name or "minimal").strip().lower().replace(" ", "_")
    aliases = {
        "auto_hunter": "auto",
        "hunter": "auto",
        "aggressive": "aggressive_rank_hunter",
        "high_baseline": "high_baseline_rank_hunter",
        "record_hunter": "frontiermath_record_breaker",
        "record_breaker": "frontiermath_record_breaker",
        "frontiermath": "frontiermath_record_breaker",
        "trickster": "specialization_trickster",
        "seeded": "historical_seed_funnel",
        "historical_seed": "historical_seed_funnel",
    }
    name = aliases.get(name, name)
    raw = PRESET_TEMPLATE_BY_ID.get(name, MINIMAL_TEMPLATE)

    mode = str(target_mode)
    out = []
    for rec in raw:
        spec = stage_spec(rec["id"])
        if mode not in spec.targets:
            continue
        if mode != "torsion" and rec["id"] == "exact_torsion":
            continue
        if mode == "general" and rec["id"] in {
            "family_baseline", "plugin_geometry", "corpus_filter"
        }:
            continue
        out.append(normalize_stage(rec))
    return out
