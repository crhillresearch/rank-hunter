from rank42.pipeline_catalog import (
    CATALOG_VERSION,
    deep_strategy_budget,
    geometry_search_budget,
    general_search_stages,
    normalize_pipeline,
    pipeline_catalog_fingerprint,
    pipeline_preset_options,
    stage_spec,
    template_stages,
    validate_pipeline,
    point_search_budget,
)


def test_pipeline_catalog_has_stable_versioned_fingerprint():
    first = pipeline_catalog_fingerprint()
    second = pipeline_catalog_fingerprint()
    assert CATALOG_VERSION >= 1
    assert first == second
    assert len(first) == 64


def test_geometry_templates_are_contract_valid_for_all_sources():
    for mode in ("family", "torsion", "general"):
        stages = template_stages("geometry", mode)
        assert validate_pipeline(mode, stages) == []


def test_nagao_catalog_qualifies_project_specific_score_identity():
    screen = stage_spec("nagao_screen")
    rescore = stage_spec("nagao_rescore")
    assert "Rank Hunter's historical Nagao-style" in screen.description
    assert "not presented as the published Mestre–Nagao" in screen.description
    assert "same Rank Hunter Nagao-style scorer" in rescore.description
    assert "provenance" in rescore.description
    assert rescore.repeatable is True


def test_rh_prime_heuristics_are_not_labeled_as_published_methods():
    multi = stage_spec("multi_scale_frobenius")
    ensemble = stage_spec("mestre_nagao_ensemble")

    assert "Rank Hunter's historical log-cardinality Nagao-style" in multi.description
    assert "not the published classical Mestre–Nagao" in multi.description

    assert ensemble.label == "Rank Hunter Prime-Signal Ensemble"
    assert "experimental consensus scheduling score" in ensemble.description
    assert "not the published multi-value Mestre–Nagao classifier" in ensemble.description


def test_frobenius_persistence_is_labeled_rh_experimental():
    persistence = stage_spec("frobenius_persistence")
    assert "Rank Hunter experimental scheduling heuristic" in persistence.description
    assert "not a published standard statistic or rank proof" in persistence.description


def test_full_bad_prime_modules_are_not_cataloged_as_cheap():
    roots = stage_spec("local_root_numbers")
    fingerprint = stage_spec("bad_prime_fingerprint")

    for spec in (roots, fingerprint):
        assert spec.cost == "medium"
        assert spec.defaults["include_all_bad"] is True
        assert spec.defaults["bad_prime_timeout"] == 20
        assert "bounded bad-prime factorization timeout" in spec.description
        assert "not a trivial per-curve stage" in spec.description


def test_torsion_mod_p_shared_validation_rejects_invalid_group_and_budgets():
    invalid_group = normalize_pipeline([
        "nagao_screen",
        {
            "id": "torsion_mod_p_sieve",
            "config": {
                "torsion_group": "C11",
                "prime_bound": 100,
                "max_primes": 12,
            },
        },
    ])
    errors = validate_pipeline("general", invalid_group)
    assert any("unsupported rational torsion group over Q" in error for error in errors)

    invalid_budget = normalize_pipeline([
        "nagao_screen",
        {
            "id": "torsion_mod_p_sieve",
            "config": {
                "torsion_group": "C5",
                "prime_bound": 2,
                "max_primes": 0,
            },
        },
    ])
    errors = validate_pipeline("general", invalid_budget)
    assert any(
        "prime bound must be at least 3 and max primes positive" in error
        for error in errors
    )


def test_torsion_mod_p_shared_validation_accepts_canonical_aliases_and_target_mode():
    for mode in ("family", "general", "curve"):
        stages = normalize_pipeline([
            *([] if mode == "curve" else ["nagao_screen"]),
            "prime_table_cache",
            {
                "id": "torsion_mod_p_sieve",
                "config": {
                    "torsion_group": "Z/5Z",
                    "prime_bound": 101,
                    "max_primes": 8,
                },
            },
        ])
        assert validate_pipeline(mode, stages) == []

    torsion_stages = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        {
            "id": "torsion_mod_p_sieve",
            "config": {
                "torsion_group": "target",
                "prime_bound": 101,
                "max_primes": 8,
            },
        },
        "exact_torsion",
    ])
    assert validate_pipeline("torsion", torsion_stages) == []

    non_torsion_target = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        {
            "id": "torsion_mod_p_sieve",
            "config": {
                "torsion_group": "target",
                "prime_bound": 101,
                "max_primes": 8,
            },
        },
    ])
    errors = validate_pipeline("general", non_torsion_target)
    assert any("choose a concrete torsion group outside Torsion Group mode" in error for error in errors)


def test_torsion_pipeline_requires_exact_torsion_before_point_work():
    stages = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        "exact_torsion",
        "independence",
    ])
    errors = validate_pipeline("torsion", stages)
    assert any("Exact Torsion must run before" in error for error in errors)


def test_torsion_pipeline_missing_exact_torsion_is_invalid():
    stages = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        "independence",
    ])
    errors = validate_pipeline("torsion", stages)
    assert any("require Exact Torsion" in error for error in errors)


def test_pointed_quartic_requires_prior_point_source():
    stages = normalize_pipeline([
        "nagao_screen",
        "pointed_quartic",
        "integral_seed",
    ])
    errors = validate_pipeline("family", stages)
    assert any("point_source" in error for error in errors)


def test_candidate_rescore_cannot_be_moved_after_curve_stages():
    stages = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        "nagao_rescore",
    ])
    errors = validate_pipeline("general", stages)
    assert any("candidate screening/rescoring must precede" in error for error in errors)


def test_general_candidate_prime_bounds_must_strictly_increase():
    stages = normalize_pipeline([
        {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 5000}},
        {"id": "nagao_rescore", "config": {"prime_bound": 523, "keep": 250}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("candidate prime bound must strictly increase" in error for error in errors)

    stages = normalize_pipeline([
        {"id": "nagao_screen", "config": {"prime_bound": 1979, "keep": 5000}},
        {"id": "nagao_rescore", "config": {"prime_bound": 523, "keep": 250}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("candidate prime bound must strictly increase" in error for error in errors)


def test_general_candidate_survivor_counts_must_not_increase():
    stages = normalize_pipeline([
        {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 250}},
        {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 500}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("candidate survivor count must not increase" in error for error in errors)


def test_candidate_screening_monotonicity_accepts_valid_general_and_family_plans():
    stages = normalize_pipeline([
        {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 5000}},
        {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 250}},
    ])
    assert validate_pipeline("general", stages) == []
    assert validate_pipeline("family", stages) == []


def test_saturation_requires_possible_rigorous_lower_bound():
    stages = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        "saturation",
        "independence",
    ])
    errors = validate_pipeline("general", stages)
    assert any("rigorous_lower_possible" in error for error in errors)



def test_corpus_filter_must_be_final_candidate_stage():
    stages = normalize_pipeline([
        "nagao_screen",
        "corpus_filter",
        "nagao_rescore",
        "integral_seed",
    ])
    errors = validate_pipeline("family", stages)
    assert any("Library Filter must be the final candidate stage" in error for error in errors)


def test_corpus_filter_is_not_available_for_general_curves():
    stages = normalize_pipeline([
        "nagao_screen",
        "corpus_filter",
        "integral_seed",
    ])
    errors = validate_pipeline("general", stages)
    assert any("unavailable for general targets" in error for error in errors)


def test_selmer_and_mwrank_covering_are_valid_after_candidate_source():
    stages = normalize_pipeline([
        "nagao_screen",
        "selmer_bound",
        "mwrank_covering",
        "independence",
    ])
    assert validate_pipeline("general", stages) == []



def test_survivor_and_denominator_modules_are_repeatable_and_valid():
    stages = normalize_pipeline([
        "nagao_screen",
        {"id": "select_survivors", "config": {"keep": 10}},
        {
            "id": "denominator_band",
            "config": {"denominator_low": 2, "denominator_high": 50},
        },
        {"id": "select_survivors", "config": {"keep": 5}},
        {
            "id": "denominator_band",
            "config": {"denominator_low": 51, "denominator_high": 500},
        },
    ])
    assert validate_pipeline("general", stages) == []


def test_adaptive_ladder_declares_bounded_per_search_timeout_semantics():
    spec = stage_spec("adaptive_ladder")
    assert spec.defaults["timeout"] == 8
    assert "per chart/height ratpoints-call budget" in spec.description
    assert "not a claim that the denominator band is exhausted" in spec.description
    assert "bounded screening" in str(spec.conditional)
    assert "not evidence that the full denominator band is exhausted" in str(spec.conditional)


def test_adaptive_denominator_ladder_validates_bands_and_keeps():
    stages = normalize_pipeline([
        "nagao_screen",
        {
            "id": "adaptive_ladder",
            "config": {
                "bands": [[2, 50], [51, 500], [501, 5000]],
                "keeps": [10, 5, 2],
            },
        },
    ])
    assert validate_pipeline("general", stages) == []

    bad = normalize_pipeline([
        "nagao_screen",
        {
            "id": "adaptive_ladder",
            "config": {
                "bands": [[2, 50], [40, 500]],
                "keeps": [5, 10],
            },
        },
    ])
    errors = validate_pipeline("general", bad)
    assert any("strictly increasing and non-overlapping" in error for error in errors)
    assert any("positive and nonincreasing" in error for error in errors)


def test_torsion_funnel_must_follow_exact_torsion():
    stages = normalize_pipeline([
        "nagao_screen",
        {"id": "select_survivors", "config": {"keep": 10}},
        "exact_torsion",
    ])
    errors = validate_pipeline("torsion", stages)
    assert any("control stages" in error for error in errors)



def test_prime_modules_form_valid_family_pipeline():
    stages = normalize_pipeline([
        "nagao_screen",
        {"id": "prime_table_cache", "config": {"prime_bound": 200}},
        {"id": "multi_scale_frobenius", "config": {"bounds": [23, 97, 199]}},
        {"id": "local_root_numbers", "config": {"global_sign": "any"}},
        {
            "id": "bad_prime_fingerprint",
            "config": {"required_primes": [2], "max_bad_primes": 0},
        },
        {
            "id": "torsion_mod_p_sieve",
            "config": {"torsion_group": "C2", "prime_bound": 50, "max_primes": 8},
        },
        {
            "id": "local_solubility_sieve",
            "config": {"explicit_primes": [2, 3, 5], "max_prime": 97},
        },
        "integral_seed",
    ])
    assert validate_pipeline("family", stages) == []


def test_prime_consumers_require_prime_table_cache():
    stages = normalize_pipeline([
        "nagao_screen",
        {"id": "multi_scale_frobenius", "config": {"bounds": [23, 97]}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("prime_table" in error for error in errors)


def test_torsion_mod_p_target_is_valid_only_for_torsion_source():
    family = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        "torsion_mod_p_sieve",
    ])
    errors = validate_pipeline("family", family)
    assert any("concrete torsion group" in error for error in errors)

    torsion = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        "torsion_mod_p_sieve",
        "exact_torsion",
    ])
    assert validate_pipeline("torsion", torsion) == []


def test_multi_scale_frobenius_bounds_must_increase():
    stages = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        {
            "id": "multi_scale_frobenius",
            "config": {"bounds": [97, 23]},
        },
    ])
    errors = validate_pipeline("general", stages)
    assert any("strictly increase" in error for error in errors)



def test_explicit_prime_lists_reject_composites():
    stages = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        {
            "id": "bad_prime_fingerprint",
            "config": {"required_primes": [29, 39]},
        },
        {
            "id": "local_solubility_sieve",
            "config": {"explicit_primes": [2, 9], "max_prime": 97},
        },
    ])
    errors = validate_pipeline("general", stages)
    assert any("required values must be prime" in error for error in errors)
    assert any("explicit values must be prime" in error for error in errors)



def test_next_six_heuristics_form_valid_pipeline():
    stages = normalize_pipeline([
        "nagao_screen",
        {"id": "prime_table_cache", "config": {"prime_bound": 10000}},
        {"id": "mestre_nagao_ensemble", "config": {"prime_bound": 5000}},
        {"id": "frobenius_persistence", "config": {"bounds": [523, 1979, 5000, 10000]}},
        {"id": "explicit_formula_indicator", "config": {"prime_bound": 5000, "max_prime_power": 4}},
        "selmer_bound",
        "selmer_headroom",
        {"id": "small_point_density", "config": {"heights": [100, 1000, 10000]}},
        {"id": "denominator_band", "config": {"denominator_low": 2, "denominator_high": 50}},
        {"id": "point_yield_persistence", "config": {"minimum_bands": 1}},
    ])
    assert validate_pipeline("general", stages) == []


def test_new_heuristics_enforce_prerequisites():
    stages = normalize_pipeline([
        "nagao_screen",
        "mestre_nagao_ensemble",
        "selmer_headroom",
        "point_yield_persistence",
    ])
    errors = validate_pipeline("general", stages)
    assert any("prime_table" in error for error in errors)
    assert any("mwrank_rank_upper_possible" in error for error in errors)
    assert any("denominator_yield_data" in error for error in errors)


def test_exact_torsion_stage_has_bounded_worker_contract():
    torsion = stage_spec("exact_torsion")
    assert torsion.defaults["timeout"] == 30
    assert "isolated bounded worker" in torsion.description
    assert "timeout/error stays inconclusive" in torsion.description


def test_point_stage_descriptions_preserve_incomplete_search_semantics():
    density = stage_spec("small_point_density")
    native = stage_spec("integral_seed")
    band = stage_spec("denominator_band")
    persistence = stage_spec("point_yield_persistence")
    affine = stage_spec("affine_search")
    adaptive = stage_spec("adaptive_ladder")

    assert density.label == "Small-Point Discovery Yield"
    assert "experimental search-yield statistic" in density.description
    assert "not a mathematical density statistic" in density.description
    assert "missing coverage, not zero yield" in density.description
    assert "one fixed exact search model" in density.description
    assert "attempted once" in density.description
    assert "same stored-model fallback" in density.description
    assert "not reported as completed zero-yield searches" in native.description
    assert "completed negative observation" in band.description
    assert "chart-denominator" in band.description
    assert "not invariant or disjoint native Mordell–Weil denominator shells" in band.description
    assert "Incomplete/timeout bands are excluded" in persistence.description
    assert "progressively deeper chart-denominator searches" in persistence.description
    assert "not a theorem or standard Mordell–Weil statistic" in persistence.description
    assert "never a completed zero-yield claim" in affine.description
    for spec in (density, native, band, affine, adaptive):
        assert "no finite point was found" in spec.description or "no rational point exists" in str(spec.conditional)
        text = spec.description + " " + str(spec.conditional or "")
        assert "does not prove nonexistence" in text or "not evidence" in text


def test_arithmetic_root_number_uses_bounded_shared_service_contract():
    root = stage_spec("root_number")
    assert root.cost == "medium"
    assert root.defaults["prime_bound"] == 200
    assert root.defaults["bad_prime_timeout"] == 20
    assert "shared bounded prime/local arithmetic service" in root.description
    assert "Curve Arithmetic authority" in root.description


def test_mwrank_retry_policy_contract_and_validation():
    stage = stage_spec("selmer_bound")
    assert stage.defaults["retry_policy"] == "manual"
    assert stage.defaults["retry_timeout"] == 300
    assert "operationally complete" in stage.description
    assert "defaults to manual" in stage.description

    invalid = normalize_pipeline([
        {
            "id": "selmer_bound",
            "config": {"retry_policy": "always", "retry_timeout": 300},
        }
    ])
    errors = validate_pipeline("curve", invalid)
    assert any("retry policy must be manual, escalated, or automatic" in e for e in errors)

    invalid_escalation = normalize_pipeline([
        {
            "id": "selmer_bound",
            "config": {
                "timeout": 60,
                "retry_policy": "escalated",
                "retry_timeout": 60,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid_escalation)
    assert any("escalated retry timeout must exceed the initial timeout" in e for e in errors)


def test_mwrank_rank_bound_stage_labels_are_precise():
    upper = stage_spec("selmer_bound")
    headroom = stage_spec("selmer_headroom")
    assert upper.label == "mwrank 2-Descent Rank Upper"
    assert "rank_bound()" in upper.description
    assert "not the raw 2-Selmer rank" in upper.description
    assert "mwrank_rank_upper_possible" in upper.provides
    assert headroom.label == "mwrank Rank Headroom"
    assert "not a raw Selmer-rank gap" in headroom.description
    assert "mwrank_rank_upper_possible" in headroom.requires


def test_frobenius_persistence_requires_multiple_increasing_bounds():
    stages = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        {"id": "frobenius_persistence", "config": {"bounds": [97]}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("at least two" in error for error in errors)

    stages = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        {"id": "frobenius_persistence", "config": {"bounds": [1979, 523]}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("strictly increase" in error for error in errors)



def test_curve_builder_pipeline_does_not_require_candidate_source():
    stages = normalize_pipeline([
        "integral_seed",
        "independence",
        "final_upper",
    ])
    assert validate_pipeline("curve", stages) == []


def test_curve_builder_pipeline_rejects_family_only_stage():
    stages = normalize_pipeline([
        "family_baseline",
    ])
    errors = validate_pipeline("curve", stages)
    assert any("unavailable for curve targets" in error for error in errors)



def test_auto_preset_alias_is_contract_valid():
    for mode in ("family", "torsion", "general"):
        stages = template_stages("auto", mode)
        assert stages
        assert validate_pipeline(mode, stages) == []



def test_transform_modules_reset_curve_local_capabilities():
    stages = normalize_pipeline([
        "nagao_screen",
        "prime_table_cache",
        "quadratic_twist_sweep",
        "multi_scale_frobenius",
    ])
    errors = validate_pipeline("general", stages)
    assert any("prime_table" in error for error in errors)

    valid = normalize_pipeline([
        "nagao_screen",
        "quadratic_twist_sweep",
        "prime_table_cache",
        "multi_scale_frobenius",
        {"id": "select_survivors", "config": {"keep": 5}},
    ])
    assert validate_pipeline("general", valid) == []


def test_rank_jump_and_pullback_are_family_only():
    general = normalize_pipeline([
        "nagao_screen",
        "rank_jump_base_change",
    ])
    errors = validate_pipeline("general", general)
    assert any("unavailable for general targets" in error for error in errors)

    family = normalize_pipeline([
        "nagao_screen",
        "rank_jump_base_change",
        "parameter_pullback",
    ])
    assert validate_pipeline("family", family) == []


def test_mobius_parameter_transform_label_and_rational_determinant_validation():
    spec = stage_spec("parameter_pullback")
    assert spec.label == "Möbius Parameter Transform"
    assert "not a symbolic family pullback" in spec.description

    valid = normalize_pipeline([
        "nagao_screen",
        {
            "id": "parameter_pullback",
            "config": {"maps": [["1/2", "1", "0", "1"]]},
        },
    ])
    assert validate_pipeline("family", valid) == []

    invalid = normalize_pipeline([
        "nagao_screen",
        {
            "id": "parameter_pullback",
            "config": {"maps": [["1/2", "1", "1", "2"]]},
        },
    ])
    errors = validate_pipeline("family", invalid)
    assert any("nonzero determinant" in error for error in errors)


def test_twist_modules_are_not_available_in_torsion_target_mode():
    stages = normalize_pipeline([
        "nagao_screen",
        "exact_torsion",
        "quadratic_twist_sweep",
    ])
    errors = validate_pipeline("torsion", stages)
    assert any("unavailable for torsion targets" in error for error in errors)


def test_covering_selmer_branch_is_valid_after_exact_torsion():
    stages = normalize_pipeline([
        "nagao_screen",
        "exact_torsion",
        "covering_selmer_branch",
        "independence",
    ])
    assert validate_pipeline("torsion", stages) == []



def test_mw_growth_loop_requires_points_and_valid_feedback_budgets():
    missing = normalize_pipeline([
        "nagao_screen",
        "mw_growth_loop",
    ])
    errors = validate_pipeline("general", missing)
    assert any("point_source" in error for error in errors)

    valid = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        {
            "id": "mw_growth_loop",
            "config": {
                "heights": [1000, 10000],
                "anchors": 8,
                "pool_size": 32,
                "max_rounds": 5,
                "deep_keep": 4,
                "timeout": 2,
                "reduce_timeout": 5,
                "certificate_timeout": 30,
                "exact_candidates": 16,
            },
        },
    ])
    assert validate_pipeline("general", valid) == []

    invalid = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        {
            "id": "mw_growth_loop",
            "config": {"anchors": 20, "pool_size": 10, "max_rounds": 0},
        },
    ])
    errors = validate_pipeline("general", invalid)
    assert any("pool size" in error for error in errors)
    assert any("round/search budgets" in error for error in errors)



def test_professor_grade_research_modules_have_valid_contracts():
    stages = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        {"id": "higher_descent_ladder", "config": {"levels": [2, 4, 8]}},
        {"id": "selmer_element_fanout", "config": {"max_coverings": 8}},
        {"id": "padic_covering_search", "config": {"prime": 3, "precision": 40}},
        {"id": "height_lattice_reduction", "config": {"precision_bits": 256}},
        {"id": "full_saturation_index_recovery", "config": {"prime_bounds": [7, 31]}},
    ])
    assert validate_pipeline("general", stages) == []

    family = normalize_pipeline([
        "nagao_screen",
        "surface_fibration_switch",
    ])
    assert validate_pipeline("family", family) == []


def test_surface_fibration_switch_is_family_only():
    stages = normalize_pipeline([
        "nagao_screen",
        "surface_fibration_switch",
    ])
    errors = validate_pipeline("general", stages)
    assert any("unavailable for general targets" in error for error in errors)


def test_research_module_budget_validation():
    stages = normalize_pipeline([
        "nagao_screen",
        "integral_seed",
        {"id": "padic_covering_search", "config": {"prime": 4, "precision": 0}},
        {"id": "height_lattice_reduction", "config": {"precision_bits": 32}},
        {"id": "full_saturation_index_recovery", "config": {"prime_bounds": []}},
    ])
    errors = validate_pipeline("general", stages)
    assert any("p-adic Covering Point Search" in error for error in errors)
    assert any("Height-Lattice Reduction" in error for error in errors)
    assert any("Saturation Prime Ladder / Index Recovery" in error for error in errors)



def test_constructive_family_chain_is_valid():
    stages = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        "trace_section_constructor",
        "forced_bisection_constructor",
        "square_condition_specializer",
        "independence",
    ])
    assert validate_pipeline("family", stages) == []


def test_constructive_family_chain_rejects_wrong_order_and_general_mode():
    wrong = normalize_pipeline([
        "nagao_screen",
        "trace_section_constructor",
    ])
    errors = validate_pipeline("family", wrong)
    assert any("constructive_sections" in error for error in errors)

    general = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
    ])
    errors = validate_pipeline("general", general)
    assert any("unavailable for general targets" in error for error in errors)



def test_all_visible_builder_presets_are_contract_valid():
    for mode in ("family", "torsion", "general"):
        for preset in pipeline_preset_options(mode):
            stages = template_stages(preset["id"], mode)
            errors = validate_pipeline(mode, stages)
            assert errors == [], (mode, preset["id"], errors)


def test_strategy_modules_are_grouped_as_strategies():
    constructive = normalize_pipeline([
        "nagao_screen",
        "constructive_rank_jump_loop",
    ])
    assert validate_pipeline("family", constructive) == []

    generator = normalize_pipeline([
        "nagao_screen",
        "large_height_generator_hunt",
    ])
    assert validate_pipeline("general", generator) == []

    rescue = normalize_pipeline([
        "nagao_screen",
        "upper_bound_rescue_ladder",
    ])
    assert validate_pipeline("general", rescue) == []


def test_specialization_trickster_is_family_only_preset():
    family_ids = {rec["id"] for rec in pipeline_preset_options("family")}
    general_ids = {rec["id"] for rec in pipeline_preset_options("general")}
    assert "specialization_trickster" in family_ids
    assert "specialization_trickster" not in general_ids



def test_aggressive_rank_hunter_funnels_before_exact_expensive_work():
    stages = template_stages("aggressive_rank_hunter", "family")
    ids = [rec["id"] for rec in stages]
    assert validate_pipeline("family", stages) == []
    first_select = ids.index("select_survivors")
    assert first_select < ids.index("family_baseline")
    assert "selmer_bound" not in ids
    assert "selmer_headroom" not in ids
    assert ids.index("large_height_generator_hunt") < ids.index("upper_bound_rescue_ladder")
    assert "local_root_numbers" not in ids
    generator = next(rec for rec in stages if rec["id"] == "large_height_generator_hunt")
    assert 2 not in generator["config"]["descent_levels"]
    assert generator["config"]["denominator_charts"] == 12
    cache = next(rec for rec in stages if rec["id"] == "prime_table_cache")
    assert cache["config"]["eager_bad_primes"] is False
    assert cache["config"]["include_all_bad"] is False



def test_upper_bound_rescue_ladder_contract_and_budget_validation():
    valid = normalize_pipeline([
        "nagao_screen",
        {
            "id": "upper_bound_rescue_ladder",
            "config": {
                "pari_timeout": 2,
                "isogeny_degrees": [2, 3, 5],
                "isogeny_max_models": 3,
                "isogeny_discovery_timeout": 4,
                "isogeny_pari_timeout": 2,
                "selmer_timeout": 5,
                "simon_timeout": 5,
                "covering_timeout": 5,
                "higher_levels": [4, 8],
                "higher_timeout": 10,
                "certificate_timeout": 30,
                "exact_candidates": 16,
            },
        },
    ])
    assert validate_pipeline("general", valid) == []
    rescue = stage_spec("upper_bound_rescue_ladder")
    assert rescue.defaults["isogeny_discovery_timeout"] == 15
    assert rescue.defaults["simon_limbigprime"] == 0
    assert "process-isolated under a per-degree hard timeout" in rescue.conditional
    assert "shared rigorous interval promotion path" in rescue.conditional
    assert "deterministic LIMBIGPRIME=0" in rescue.conditional
    assert "hard-isolated" in rescue.conditional
    assert "typed core-verifiable rigorous-upper certificate" in rescue.conditional

    bad = normalize_pipeline([
        "nagao_screen",
        {
            "id": "upper_bound_rescue_ladder",
            "config": {
                "isogeny_degrees": [4],
                "higher_levels": [2],
                "pari_timeout": 0,
            },
        },
    ])
    errors = validate_pipeline("general", bad)
    assert any("Upper-Bound Rescue Ladder" in error for error in errors)
    assert any("isogeny degrees must be prime integers" in error for error in errors)
    assert any("higher descent levels" in error for error in errors)

    bad_simon = normalize_pipeline([
        "nagao_screen",
        {
            "id": "upper_bound_rescue_ladder",
            "config": {"simon_limbigprime": 30},
        },
    ])
    assert any(
        "rigorous Simon rescue requires LIMBIGPRIME=0" in error
        for error in validate_pipeline("general", bad_simon)
    )



def test_large_height_generator_hunt_uses_bounded_late_saturation_defaults():
    stages = normalize_pipeline(["nagao_screen", "large_height_generator_hunt"])
    hunt = next(rec for rec in stages if rec["id"] == "large_height_generator_hunt")
    cfg = hunt["config"]
    assert cfg["late_saturation_enabled"] is True
    assert cfg["saturation_bounds"] == [7, 31]
    assert cfg["saturation_timeout"] == 20
    assert cfg["stop_on_unit_index"] is True



def test_aggressive_rank_hunter_is_valid_across_target_modes():
    for mode in ("family", "general", "curve", "torsion"):
        stages = template_stages("aggressive_rank_hunter", mode)
        assert stages
        assert validate_pipeline(mode, stages) == []
        ids = [rec["id"] for rec in stages]
        assert "large_height_generator_hunt" in ids
        assert "upper_bound_rescue_ladder" in ids

    assert "family_baseline" in [
        rec["id"] for rec in template_stages("aggressive_rank_hunter", "family")
    ]
    assert "family_baseline" not in [
        rec["id"] for rec in template_stages("aggressive_rank_hunter", "curve")
    ]
    assert "family_baseline" not in [
        rec["id"] for rec in template_stages("aggressive_rank_hunter", "general")
    ]


def test_high_baseline_rank_hunter_is_valid_and_contains_record_lane():
    stages = template_stages("high_baseline_rank_hunter", "family")
    ids = [rec["id"] for rec in stages]
    assert validate_pipeline("family", stages) == []
    assert ids[:2] == ["nagao_screen", "nagao_rescore"]
    assert "family_baseline" in ids
    assert ids.count("large_height_generator_hunt") >= 4
    assert "record_breaker_lane" in ids
    record = next(rec for rec in stages if rec["id"] == "record_breaker_lane")
    assert record["config"]["minimum_rank"] == 25
    assert record["config"]["denominator_charts"] == 128
    assert record["config"]["denominator_bands"][-1] == [10000001, 100000000]
    assert "stop_goal" not in ids


def test_record_breaker_lane_validates_rigorous_threshold_and_budgets():
    valid = normalize_pipeline([
        "nagao_screen",
        {
            "id": "record_breaker_lane",
            "config": {
                "minimum_rank": 25,
                "descent_levels": [4, 8],
                "saturation_bounds": [31, 127],
                "denominator_bands": [[1000001, 10000000]],
                "denominator_charts": 64,
                "certificate_timeout": 300,
                "exact_candidates": 128,
                "max_coverings": 16,
                "mw_growth_rounds": 4,
            },
        },
    ])
    assert validate_pipeline("general", valid) == []

    bad = normalize_pipeline([
        "nagao_screen",
        {
            "id": "record_breaker_lane",
            "config": {
                "minimum_rank": 0,
                "denominator_charts": 0,
                "denominator_bands": [[10, 5]],
            },
        },
    ])
    errors = validate_pipeline("general", bad)
    assert any("Record Breaker Lane" in error for error in errors)


def test_high_baseline_defers_full_bad_prime_arithmetic_until_top_eight():
    stages = template_stages("high_baseline_rank_hunter", "family")
    roots = [rec for rec in stages if rec["id"] == "local_root_numbers"]
    fingerprints = [rec for rec in stages if rec["id"] == "bad_prime_fingerprint"]

    assert len(roots) == 2
    assert len(fingerprints) == 2
    assert roots[0]["config"]["include_all_bad"] is False
    assert fingerprints[0]["config"]["include_all_bad"] is False
    assert roots[1]["config"]["include_all_bad"] is True
    assert fingerprints[1]["config"]["include_all_bad"] is True

    ids = [rec["id"] for rec in stages]
    top_eight = next(
        i for i, rec in enumerate(stages)
        if rec["id"] == "select_survivors" and rec["config"]["keep"] == 8
    )
    assert ids.index("local_root_numbers") < top_eight
    assert max(i for i, sid in enumerate(ids) if sid == "local_root_numbers") > top_eight
    assert validate_pipeline("family", stages) == []


def test_high_baseline_torsion_mode_keeps_exact_torsion_gate():
    stages = template_stages("high_baseline_rank_hunter", "torsion")
    ids = [rec["id"] for rec in stages]
    assert "exact_torsion" in ids
    assert validate_pipeline("torsion", stages) == []



def test_historical_seed_funnel_is_family_only_valid_and_seed_first():
    family_ids = {rec["id"] for rec in pipeline_preset_options("family")}
    general_ids = {rec["id"] for rec in pipeline_preset_options("general")}
    assert "historical_seed_funnel" in family_ids
    assert "historical_seed_funnel" not in general_ids

    stages = template_stages("historical_seed_funnel", "family")
    assert validate_pipeline("family", stages) == []
    ids = [rec["id"] for rec in stages]
    seed = ids.index("specialization_seeds")
    first_prime = ids.index("prime_table_cache")
    assert ids.index("select_survivors") < seed < first_prime
    record = next(rec for rec in stages if rec["id"] == "record_breaker_lane")
    assert record["config"]["minimum_rank_mode"] == "goal_minus_one"
    assert record["config"]["mw_growth_anchors"] == 24
    assert record["config"]["mw_growth_heights"] == [1000000, 10000000]


def test_record_breaker_lane_accepts_goal_minus_one_threshold_mode():
    stages = normalize_pipeline([
        "nagao_screen",
        {
            "id": "record_breaker_lane",
            "config": {
                "minimum_rank_mode": "goal_minus_one",
                "minimum_rank": 0,
            },
        },
    ])
    assert validate_pipeline("general", stages) == []


def test_point_search_retry_policy_contract_and_validation():
    for stage_id in (
        "small_point_density",
        "integral_seed",
        "denominator_band",
        "affine_search",
        "adaptive_ladder",
    ):
        spec = stage_spec(stage_id)
        assert spec.defaults["retry_policy"] == "manual"
        assert int(spec.defaults["retry_timeout"]) > int(spec.defaults["timeout"])
        assert "checkpoint" in spec.description.lower()

    invalid = normalize_pipeline([
        {
            "id": "affine_search",
            "config": {
                "retry_policy": "forever",
                "retry_timeout": 32,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid)
    assert any("retry policy must be manual, automatic, or escalated" in e for e in errors)

    invalid_escalation = normalize_pipeline([
        {
            "id": "denominator_band",
            "config": {
                "timeout": 8,
                "retry_policy": "escalated",
                "retry_timeout": 8,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid_escalation)
    assert any("escalated retry timeout must exceed the initial timeout" in e for e in errors)

    valid = normalize_pipeline([
        {
            "id": "integral_seed",
            "config": {
                "retry_policy": "escalated",
                "timeout": 4,
                "retry_timeout": 20,
            },
        }
    ])
    assert validate_pipeline("curve", valid) == []



def test_native_and_affine_point_stage_validation_and_budget_estimates():
    bad_native = normalize_pipeline([
        {
            "id": "integral_seed",
            "config": {
                "heights": [1000, 0],
                "timeout": 0,
                "model_mode": "mystery",
                "model_prep_timeout": 0,
            },
        }
    ])
    errors = validate_pipeline("curve", bad_native)
    assert any("Native Model Seed: heights must be positive" in error for error in errors)
    assert any("Native Model Seed: timeout must be positive" in error for error in errors)
    assert any("Native Model Seed: model mode must be stored or minimal" in error for error in errors)
    assert any("Native Model Seed: model-prep timeout must be positive" in error for error in errors)

    bad_affine = normalize_pipeline([
        {
            "id": "affine_search",
            "config": {
                "heights": [],
                "charts": 0,
                "timeout": 0,
            },
        }
    ])
    errors = validate_pipeline("curve", bad_affine)
    assert any("Affine Rational Search: heights must be positive" in error for error in errors)
    assert any("Affine Rational Search: charts must be positive" in error for error in errors)
    assert any("Affine Rational Search: timeout must be positive" in error for error in errors)

    affine = point_search_budget(
        "affine_search",
        {"heights": [10000, 100000, 1000000], "charts": 24, "timeout": 8},
    )
    assert affine == {
        "calls": 72,
        "timeout_seconds": 8,
        "worst_case_timeout_seconds": 576,
    }

    band = point_search_budget(
        "denominator_band",
        {
            "heights": [100, 1000, 10000],
            "charts": 5,
            "timeout": 8,
            "denominator_low": 101,
        },
    )
    assert band == {
        "calls": 10,
        "timeout_seconds": 8,
        "worst_case_timeout_seconds": 80,
    }

    native = point_search_budget(
        "integral_seed",
        {"heights": [1000, 10000], "timeout": 4},
    )
    assert native["calls"] == 2
    assert native["worst_case_timeout_seconds"] == 8



def test_geometry_search_budget_exposes_branch_and_timeout_envelopes():
    pointed = geometry_search_budget(
        "pointed_quartic",
        {
            "heights": [10000, 100000],
            "anchors": 16,
            "rounds": 1,
            "deep_keep": 8,
            "timeout": 6,
            "reduce_timeout": 15,
        },
    )
    assert pointed == {
        "kind": "quartics",
        "branches_max": 32,
        "searches_per_round_max": 32,
        "searches_max": 32,
        "searches_no_growth": 24,
        "rounds_max": 1,
        "timeout_seconds": 6,
        "setup_timeout_seconds": 15,
        "no_growth_timeout_seconds": 159,
        "worst_case_timeout_seconds": 207,
    }

    growth = geometry_search_budget(
        "mw_growth_loop",
        {
            "heights": [10000, 100000, 1000000],
            "anchors": 32,
            "max_rounds": 8,
            "deep_keep": 12,
            "timeout": 8,
            "reduce_timeout": 20,
        },
    )
    assert growth["searches_no_growth"] == 56
    assert growth["no_growth_timeout_seconds"] == 468
    assert growth["searches_max"] == 768
    assert growth["setup_timeout_seconds"] == 160
    assert growth["worst_case_timeout_seconds"] == 6304

    fanout = geometry_search_budget(
        "selmer_element_fanout",
        {
            "max_coverings": 16,
            "timeout": 30,
            "derive_timeout": 30,
        },
    )
    assert fanout == {
        "kind": "coverings",
        "branches_max": 16,
        "searches_max": 16,
        "searches_no_growth": 16,
        "rounds_max": 1,
        "timeout_seconds": 30,
        "setup_timeout_seconds": 30,
        "no_growth_timeout_seconds": 510,
        "worst_case_timeout_seconds": 510,
    }


def test_simon_covering_is_legacy_and_deterministic_by_default():
    spec = stage_spec("simon_covering")
    assert spec.label == "Simon 2-Covering (Legacy)"
    assert spec.defaults["limbigprime"] == 0
    assert "Legacy/alternative" in spec.description
    assert "LIMBIGPRIME=0" in spec.description
    assert "deterministic local tests" in spec.description
    assert "non-rigorous" in spec.description

    invalid = normalize_pipeline([
        {
            "id": "simon_covering",
            "config": {
                "timeout": 10,
                "lim1": 5,
                "lim3": 80,
                "limbigprime": -1,
                "exact_candidates": 16,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid)
    assert any("LIMBIGPRIME must be zero or positive" in error for error in errors)



def test_mwrank_covering_retry_policy_contract_and_validation():
    stage = stage_spec("mwrank_covering")
    assert stage.defaults["retry_policy"] == "manual"
    assert stage.defaults["retry_timeout"] == 300
    assert "durable stage attempt identity" in stage.description
    assert "shared rigorous-interval promotion service" in stage.description

    invalid = normalize_pipeline([
        {
            "id": "mwrank_covering",
            "config": {
                "retry_policy": "forever",
                "retry_timeout": 300,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid)
    assert any(
        "retry policy must be manual, automatic, or escalated" in error
        for error in errors
    )

    invalid_escalation = normalize_pipeline([
        {
            "id": "mwrank_covering",
            "config": {
                "timeout": 90,
                "retry_policy": "escalated",
                "retry_timeout": 90,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid_escalation)
    assert any(
        "escalated retry timeout must exceed the initial timeout" in error
        for error in errors
    )



def test_higher_descent_catalog_exposes_branch_retry_policy():
    spec = stage_spec("higher_descent_ladder")
    assert spec.defaults["retry_policy"] == "manual"
    assert spec.defaults["retry_timeout"] == 300
    assert "Branch coverage is aggregated honestly" in spec.description
    assert "explicit manual, automatic same-budget, or escalated-budget" in spec.description

    invalid = normalize_pipeline([
        {
            "id": "higher_descent_ladder",
            "config": {
                "retry_policy": "forever",
                "retry_timeout": 300,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid)
    assert any(
        "retry policy must be manual, automatic, or escalated" in error
        for error in errors
    )

    invalid_escalation = normalize_pipeline([
        {
            "id": "higher_descent_ladder",
            "config": {
                "timeout": 120,
                "retry_policy": "escalated",
                "retry_timeout": 120,
            },
        }
    ])
    errors = validate_pipeline("curve", invalid_escalation)
    assert any(
        "escalated retry timeout must exceed the initial timeout" in error
        for error in errors
    )



def test_covering_fanout_uses_exact_covering_surface_and_legacy_stage_id():
    spec = stage_spec("selmer_element_fanout")
    assert spec.label == "Exact Covering Fan-Out"
    assert spec.defaults["derive_timeout"] == 30
    assert spec.defaults["retry_policy"] == "manual"
    assert spec.defaults["retry_timeout"] == 120
    assert "Generic covering records are search geometry only" in spec.description
    assert "do not certify a 2-Selmer class" in spec.description
    assert "append-only attempt ledger" in spec.description
    assert "branch coverage is aggregated honestly" in spec.description
    assert "Legacy stage id selmer_element_fanout" in spec.conditional
    normalized = normalize_pipeline(["selmer_element_fanout"])
    assert normalized[0]["id"] == "selmer_element_fanout"
    assert normalized[0]["config"]["derive_timeout"] == 30
    assert normalized[0]["config"]["retry_policy"] == "manual"

    invalid = normalize_pipeline([{
        "id": "selmer_element_fanout",
        "config": {
            "retry_policy": "forever",
            "retry_timeout": 120,
        },
    }])
    errors = validate_pipeline("curve", invalid)
    assert any(
        "retry policy must be manual, automatic, or escalated" in error
        for error in errors
    )

    invalid_escalation = normalize_pipeline([{
        "id": "selmer_element_fanout",
        "config": {
            "timeout": 30,
            "retry_policy": "escalated",
            "retry_timeout": 30,
        },
    }])
    errors = validate_pipeline("curve", invalid_escalation)
    assert any(
        "escalated retry timeout must exceed the initial timeout" in error
        for error in errors
    )



def test_padic_covering_catalog_requires_typed_isolated_result_contract():
    spec = stage_spec("padic_covering_search")
    assert "core-owned hard-timeout worker" in spec.description
    assert "rank42.padic_covering_search.v1" in spec.description
    assert "engine/version" in spec.description
    assert "local lifting bounds" in spec.description
    assert "heuristic/bounded/exhaustive" in spec.description
    assert "rank42.padic_covering_search.v1" in spec.conditional
    assert "completed does not imply exhaustive" in spec.conditional



def test_plugin_geometry_catalog_exposes_sandbox_and_retry_contract():
    spec = stage_spec("plugin_geometry")
    assert spec.defaults["retry_policy"] == "manual"
    assert spec.defaults["retry_timeout"] == 600
    assert "temporary sandbox DB" in spec.description
    assert "core-validated point artifacts" in spec.description
    assert "independently verified typed certificates" in spec.description
    assert "rank42.plugin_geometry_result.v1" in spec.conditional

    invalid = normalize_pipeline([{
        "id": "plugin_geometry",
        "config": {
            "retry_policy": "forever",
            "retry_timeout": 600,
        },
    }])
    errors = validate_pipeline("family", invalid)
    assert any(
        "retry policy must be manual, automatic, or escalated" in error
        for error in errors
    )

    invalid_escalation = normalize_pipeline([{
        "id": "plugin_geometry",
        "config": {
            "timeout": 180,
            "retry_policy": "escalated",
            "retry_timeout": 180,
        },
    }])
    errors = validate_pipeline("family", invalid_escalation)
    assert any(
        "escalated retry timeout must exceed the initial timeout" in error
        for error in errors
    )



def test_pointed_quartic_catalog_exposes_dry_coverage_threshold():
    spec = stage_spec("pointed_quartic")
    assert spec.defaults["dry_round_min_coverage"] == 1.0
    assert "planned/resolved coverage" in spec.description
    assert "zero new points is a dry stop only" in spec.description

    valid = normalize_pipeline([
        "integral_seed",
        {
            "id": "pointed_quartic",
            "config": {
                "heights": [100, 1000],
                "anchors": 4,
                "pool_size": 8,
                "rounds": 2,
                "deep_keep": 2,
                "timeout": 3,
                "reduce_timeout": 4,
                "dry_round_min_coverage": 0.75,
            },
        },
    ])
    assert validate_pipeline("curve", valid) == []

    invalid = normalize_pipeline([{
        "id": "pointed_quartic",
        "config": {"dry_round_min_coverage": 0},
    }])
    errors = validate_pipeline("curve", invalid)
    assert any(
        "dry-round coverage threshold must be in (0,1]" in error
        for error in errors
    )


def test_mw_growth_catalog_passes_same_underlying_dry_threshold():
    spec = stage_spec("mw_growth_loop")
    assert spec.defaults["dry_round_min_coverage"] == 1.0
    assert "resolved-coverage threshold" in spec.conditional



def test_geometry_catalog_documents_map_and_feedback_stop_diagnostics():
    pointed = stage_spec("pointed_quartic")
    assert "map-back failures are retained as bounded diagnostics" in pointed.description

    growth = stage_spec("mw_growth_loop")
    assert "explicit stop reason" in growth.description
    assert "timeout exhaustion" in growth.description
    assert "engine errors" in growth.description
    assert "rank goal" in growth.description
    assert "no-new-models" in growth.description
    assert "incomplete rounds" in growth.description
    assert "max-round exhaustion" in growth.description


def test_transform_hard_timeout_budgets_are_positive_and_validated():
    for stage_id in (
        "rank_jump_base_change",
        "surface_fibration_switch",
        "isogeny_walk",
    ):
        assert int(stage_spec(stage_id).defaults["timeout"]) > 0

    rank_jump = normalize_pipeline([
        "nagao_screen",
        {"id": "rank_jump_base_change", "config": {"timeout": 0}},
    ])
    assert any(
        "hard timeout must be positive" in error
        for error in validate_pipeline("family", rank_jump)
    )

    surface = normalize_pipeline([
        "nagao_screen",
        {"id": "surface_fibration_switch", "config": {"timeout": -1}},
    ])
    assert any(
        "hard timeout must be positive" in error
        for error in validate_pipeline("family", surface)
    )

    isogeny = normalize_pipeline([
        "nagao_screen",
        {"id": "isogeny_walk", "config": {"timeout": 0}},
    ])
    assert any(
        "hard timeout must be positive" in error
        for error in validate_pipeline("general", isogeny)
    )


def test_targeted_twist_shared_score_and_hard_budget_contract():
    spec = stage_spec("targeted_twist_search")
    assert spec.defaults["timeout"] == 60
    assert spec.defaults["per_twist_timeout"] == 10
    assert spec.defaults["allow_partial_scores"] is True
    assert "cached-nagao-log-cardinality-v1" in spec.conditional
    assert "not a rank-parity proof" in spec.conditional

    bad_stage_timeout = normalize_pipeline([
        "nagao_screen",
        {
            "id": "targeted_twist_search",
            "config": {"timeout": 0, "per_twist_timeout": 10},
        },
    ])
    assert any(
        "hard timeout must be positive" in error
        for error in validate_pipeline("general", bad_stage_timeout)
    )

    bad_trial_timeout = normalize_pipeline([
        "nagao_screen",
        {
            "id": "targeted_twist_search",
            "config": {"timeout": 60, "per_twist_timeout": 0},
        },
    ])
    assert any(
        "per-twist hard timeout must be positive" in error
        for error in validate_pipeline("general", bad_trial_timeout)
    )


def test_covering_selmer_branch_is_not_cataloged_as_transform():
    spec = stage_spec("covering_selmer_branch")
    assert spec.category == "Arithmetic"
    assert "same curve" in spec.description


def test_section_search_shell_declares_height_semantics():
    spec = stage_spec("section_height_shell")
    assert spec.label == "Section Search Shell"
    assert spec.defaults["height_kind"] == "family_enumeration_shell"
    assert spec.defaults["height_normalization"] == "family_defined"
    assert "unverified by default" in spec.description
    assert "producer-declared exactness is ignored" in spec.conditional

    valid = normalize_pipeline([
        "nagao_screen",
        {
            "id": "section_height_shell",
            "config": {
                "height": 12,
                "height_mode": "up_to",
                "height_kind": "coefficient_height",
                "height_normalization": "max_abs_coefficient",
                "coefficient_bound": 16,
                "max_sections": 32,
            },
        },
    ])
    assert validate_pipeline("family", valid) == []

    bad_kind = normalize_pipeline([
        "nagao_screen",
        {
            "id": "section_height_shell",
            "config": {"height_kind": "mystery_height"},
        },
    ])
    assert any(
        "supported explicit height kind" in error
        for error in validate_pipeline("family", bad_kind)
    )

    bad_normalization = normalize_pipeline([
        "nagao_screen",
        {
            "id": "section_height_shell",
            "config": {"height_normalization": ""},
        },
    ])
    assert any(
        "height normalization is required" in error
        for error in validate_pipeline("family", bad_normalization)
    )


def test_trace_division_catalog_declares_typed_verification_boundary():
    trace = stage_spec("trace_section_constructor")
    assert "registered trace verifier" in trace.description
    assert "Producer-declared exactness is ignored" in trace.conditional

    division = stage_spec("forced_bisection_constructor")
    assert division.defaults["division"] == 2
    assert division.defaults["slope_mode"] == "forced_tangent"
    assert "Supported divisions are 2,3,4" in division.conditional
    assert "registered verifier" in division.conditional

    valid = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        "trace_section_constructor",
        {
            "id": "forced_bisection_constructor",
            "config": {
                "division": 3,
                "slope_mode": "division_polynomial",
                "max_conditions": 32,
            },
        },
    ])
    assert validate_pipeline("family", valid) == []

    bad_division = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        "trace_section_constructor",
        {
            "id": "forced_bisection_constructor",
            "config": {"division": 5},
        },
    ])
    assert any(
        "division must be one of 2,3,4" in error
        for error in validate_pipeline("family", bad_division)
    )

    bad_mode = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        "trace_section_constructor",
        {
            "id": "forced_bisection_constructor",
            "config": {"slope_mode": "opaque-plugin-mode"},
        },
    ])
    assert any(
        "supported slope/division mode" in error
        for error in validate_pipeline("family", bad_mode)
    )


def test_constructive_hook_hard_timeout_contracts():
    for stage_id in (
        "section_height_shell",
        "trace_section_constructor",
        "forced_bisection_constructor",
        "square_condition_specializer",
    ):
        assert stage_spec(stage_id).defaults["timeout"] == 30

    section = normalize_pipeline([
        "nagao_screen",
        {
            "id": "section_height_shell",
            "config": {"timeout": 0},
        },
    ])
    assert any(
        "Section Search Shell: hard timeout must be positive" in error
        for error in validate_pipeline("family", section)
    )

    trace = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        {
            "id": "trace_section_constructor",
            "config": {"timeout": 0},
        },
    ])
    assert any(
        "Trace Section Constructor: hard timeout must be positive" in error
        for error in validate_pipeline("family", trace)
    )

    division = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        "trace_section_constructor",
        {
            "id": "forced_bisection_constructor",
            "config": {"timeout": 0},
        },
    ])
    assert any(
        "Forced Bisection / Division Constructor: hard timeout must be positive"
        in error
        for error in validate_pipeline("family", division)
    )


def test_square_condition_specializer_declares_condition_binding_and_status_contract():
    spec = stage_spec("square_condition_specializer")
    assert "condition(child_parameter)=square_value" in spec.description
    assert "Plugin status/reason/error" in spec.conditional
    assert "process-isolated" in spec.conditional
    assert "tests/domain coverage" in spec.conditional
    assert "rank42.constructive_square_specialization.v1" in spec.conditional
    assert "bare rational square is insufficient" in spec.conditional
    assert "never inherit the parent heuristic score" in spec.description

    bad_timeout = normalize_pipeline([
        "nagao_screen",
        "section_height_shell",
        "trace_section_constructor",
        "forced_bisection_constructor",
        {
            "id": "square_condition_specializer",
            "config": {"timeout": 0},
        },
    ])
    assert any(
        "Square-Condition Specializer: hard timeout must be positive" in error
        for error in validate_pipeline("family", bad_timeout)
    )



def test_compound_strategy_status_contract_is_truthful():
    hunt = stage_spec("large_height_generator_hunt")
    assert "aggregated honestly" in hunt.description
    assert "only when every configured step actually completed" in hunt.description
    assert "never calls an incomplete plan exhausted" in hunt.conditional

    lane = stage_spec("record_breaker_lane")
    assert "preserves the nested Large-Height Generator Hunt scientific status" in (
        lane.conditional
    )
    assert "partial/timeout/error/inconclusive" in lane.conditional



def test_deep_strategy_wall_budget_and_checkpoint_defaults():
    hunt = stage_spec("large_height_generator_hunt")
    assert hunt.defaults["wall_timeout"] == 1800
    assert hunt.defaults["retry_policy"] == "automatic"
    assert hunt.defaults["retry_timeout"] == 3600
    assert "durable strategy occurrence" in hunt.conditional
    assert "first unfinished checkpoint" in hunt.conditional
    record = stage_spec("record_breaker_lane")
    assert record.defaults["wall_timeout"] == 7200
    assert record.defaults["retry_timeout"] == 14400
    bad = normalize_pipeline([
        "nagao_screen",
        {"id": "large_height_generator_hunt", "config": {"wall_timeout": 0}},
    ])
    assert any(
        "wall/retry budgets must be positive" in error
        for error in validate_pipeline("general", bad)
    )



def test_deep_strategy_declares_shared_stop_metadata_contract():
    hunt = stage_spec("large_height_generator_hunt")
    for field in (
        "stop_reason",
        "goal_reached",
        "growth_stop_reached",
        "witness_requirement_met",
    ):
        assert field in hunt.conditional
    record = stage_spec("record_breaker_lane")
    assert "rigorous witness basis" in record.conditional
    assert "raw configured denominator portfolio" in record.conditional
    assert "durable Pipeline point-attempt checkpoint" in record.conditional



def test_record_breaker_shared_budget_estimate_matches_default_portfolio():
    cfg = stage_spec("record_breaker_lane").defaults
    estimate = deep_strategy_budget(cfg)
    assert estimate["denominator_configured_slots"] == 768
    assert estimate["denominator_configured_seconds"] == 23040
    assert estimate["denominator_searches"] == 384
    assert estimate["denominator_seconds"] == 11520
    assert estimate["wall_timeout_seconds"] == 7200
    assert estimate["checkpoint_scope"] == "chart_height"



def test_deep_strategy_contract_uses_authoritative_research_state():
    hunt = stage_spec("large_height_generator_hunt")
    record = stage_spec("record_breaker_lane")
    assert "reducer-backed Curve Research State" in hunt.conditional
    assert "compatibility curve columns" in hunt.conditional
    assert "rigorous lower/upper/exact rank" in record.conditional
    assert "witness-basis completeness" in record.conditional


def test_specialization_injectivity_catalog_contract_and_budgets():
    assert CATALOG_VERSION == 11
    spec = stage_spec("specialization_injectivity_certificate")
    assert spec.label == "Specialization Injectivity Certificate"
    assert spec.category == "Arithmetic"
    assert spec.targets == frozenset({"family", "torsion"})
    assert spec.evidence == "rigorous"
    assert spec.defaults == {
        "timeout": 60,
        "max_divisors": 4096,
        "certificate_timeout": 120,
    }
    assert "Gusic" in spec.conditional or "Gusi" in spec.conditional
    assert "Unsupported models" in spec.conditional

    valid = normalize_pipeline([
        "nagao_screen",
        "specialization_injectivity_certificate",
    ])
    assert validate_pipeline("family", valid) == []

    bad = normalize_pipeline([
        "nagao_screen",
        {
            "id": "specialization_injectivity_certificate",
            "config": {
                "timeout": 0,
                "max_divisors": 65537,
                "certificate_timeout": 0,
            },
        },
    ])
    errors = validate_pipeline("family", bad)
    assert any("must be positive" in error for error in errors)
    assert any("at most 65536" in error for error in errors)


def test_final_post_audit_catalog_contains_63_stages():
    from rank42.pipeline_catalog import pipeline_stage_specs

    specs = pipeline_stage_specs()
    ids = {spec.id for spec in specs}
    assert len(specs) == 63
    for stage_id in (
        "mestre_bober_analytic_upper",
        "brumer_kramer_classgroup_bound",
        "cassels_tate_refinement",
        "isogeny_descent",
        "covering_minimize_reduce",
        "covering_local_height_planner",
        "specialization_injectivity_certificate",
        "plugin_family_search",
    ):
        assert stage_id in ids


def test_general_search_controls_serialize_to_valid_pipeline_recipe():
    stages = general_search_stages(
        nagao_bound=100,
        shortlist=75,
        ratpoints_stages="1000,100,1000,10000",
        ratpoints_timeout=5,
        certificate_timeout=120,
        exact_candidates=64,
    )

    assert [rec["id"] for rec in stages] == [
        "nagao_screen",
        "integral_seed",
        "independence",
        "stop_goal",
    ]
    assert stages[0]["config"]["prime_bound"] == 100
    assert stages[0]["config"]["keep"] == 75
    assert stages[1]["config"]["heights"] == [100, 1000, 10000]
    assert stages[1]["config"]["timeout"] == 5
    assert stages[1]["config"]["model_mode"] == "stored"
    assert stages[2]["config"]["certificate_timeout"] == 120
    assert stages[2]["config"]["max_candidates"] == 64
    assert validate_pipeline("general", stages) == []


def test_general_search_pipeline_recipe_rejects_invalid_visible_controls():
    import pytest

    with pytest.raises(ValueError, match="positive integers"):
        general_search_stages(
            nagao_bound=100,
            shortlist=75,
            ratpoints_stages="0,100",
            ratpoints_timeout=5,
        )
    with pytest.raises(ValueError, match="shortlist"):
        general_search_stages(
            nagao_bound=100,
            shortlist=0,
            ratpoints_stages="100,1000",
            ratpoints_timeout=5,
        )
