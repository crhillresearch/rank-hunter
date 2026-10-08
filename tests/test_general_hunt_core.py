from fractions import Fraction

from rank42.general_hunt_core import (
    canonical_affine_key,
    numerical_independent_indices,
    parse_height_stages,
    sample_unique_pairs,
    score_cache_key,
    short_curve_nagao_score,
    short_curve_nagao_score_details,
    short_curve_point_count_mod_p,
)


def test_short_curve_point_count_is_exact_on_tiny_prime():
    # y^2=x^3+14x+1 over F_5 has good reduction; compare with direct brute force.
    A, B, p = 14, 1, 5
    brute = 1
    for x in range(p):
        rhs = (x**3 + A*x + B) % p
        brute += sum(1 for y in range(p) if (y*y - rhs) % p == 0)
    assert short_curve_point_count_mod_p(A, B, p) == brute


def test_nagao_score_is_deterministic_and_heuristic_tuple():
    a = short_curve_nagao_score(14, 1, 50)
    b = short_curve_nagao_score(14, 1, 50)
    assert a == b
    assert isinstance(a[0], float)
    assert a[1] > 0


def test_general_nagao_provenance_preserves_score_and_prime_convention():
    score, used = short_curve_nagao_score(14, 1, 50)
    detailed_score, detailed_used, provenance = short_curve_nagao_score_details(
        14, 1, 50
    )
    assert detailed_score == score
    assert detailed_used == used
    assert provenance["algorithm"] == "short-nagao-v1"
    assert provenance["scorer_version"] == 1
    assert provenance["source_mode"] == "general_short_weierstrass"
    assert provenance["formula"] == "sum_log_cardinality_over_p"
    assert provenance["prime_bound"] == 50
    assert provenance["terms_used"] == used
    assert 2 not in provenance["primes_used"]
    assert 3 not in provenance["primes_used"]
    assert provenance["primes_used"]
    assert "skip 2 and 3" in provenance["prime_convention"]
    assert provenance["published_mestre_nagao_formula"] is False


def test_pair_sampling_reproducible_and_unique():
    a = sample_unique_pairs(1, 100, 1, 100, 200, seed=17)
    b = sample_unique_pairs(1, 100, 1, 100, 200, seed=17)
    assert a == b
    assert len(a) == len(set(a)) == 200


def test_parse_height_stages_normalizes():
    assert parse_height_stages("1000,100,1000,10000") == [100, 1000, 10000]


def test_canonical_affine_key_identifies_opposite_signs():
    assert canonical_affine_key(Fraction(2, 3), Fraction(5, 7)) == canonical_affine_key(Fraction(2, 3), Fraction(-5, 7))


def test_numerical_basis_selector_skips_dependent_vector():
    # Third row/column represents v1+v2 and is dependent; fourth adds a new direction.
    gram = [
        [1, 0, 1, 0],
        [0, 1, 1, 0],
        [1, 1, 2, 0],
        [0, 0, 0, 1],
    ]
    assert numerical_independent_indices(gram) == [0, 1, 3]


def test_score_cache_key_changes_with_scientific_config():
    base = {"pool_mode": "seeded", "pool_size": 1000, "nagao_bound": 100, "seed": 42}
    assert score_cache_key(base) == score_cache_key(dict(base))
    changed = dict(base, nagao_bound=200)
    assert score_cache_key(base) != score_cache_key(changed)
