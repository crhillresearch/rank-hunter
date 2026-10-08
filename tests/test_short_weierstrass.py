from rank42.short_weierstrass import (
    GENERAL_FAMILY,
    candidate_state,
    integral_point_candidates,
    nonsingular_short,
    ordered_integer_pairs,
    seeded_short_curve,
    trial_key,
)


def test_seeded_curve_forces_two_exact_points():
    A, B, points = seeded_short_curve(3, 5)
    assert nonsingular_short(A, B)
    for x, y in points:
        assert y * y == x**3 + A * x + B


def test_integral_point_screen_is_exact_and_omits_y_zero():
    pts = integral_point_candidates(-1, 1, 10)
    assert all(y > 0 for _, y in pts)
    assert all(y * y == x**3 - x + 1 for x, y in pts)


def test_ordered_integer_pairs_is_small_first_and_deterministic():
    pairs = ordered_integer_pairs(-2, 2, -2, 2)
    assert pairs[0] == (0, 0)
    assert pairs == ordered_integer_pairs(-2, 2, -2, 2)
    radii = [abs(a) + abs(b) for a, b in pairs]
    assert radii == sorted(radii)


def test_trial_key_changes_with_proof_target():
    a = trial_key(mode="seeded", source_a=1, source_b=2, A=2, B=1, x_bound=100, target=2)
    b = trial_key(mode="seeded", source_a=1, source_b=2, A=2, B=1, x_bound=100, target=3)
    assert a != b


def test_candidate_state_four_way_contract():
    assert candidate_state(None) == "Unsearched"
    assert candidate_state({"status": "extra_done", "generic_lower": None, "descent_lower": None, "exact_rank": None}) == "Searched"
    assert candidate_state({"status": "pruned", "generic_lower": None, "descent_lower": None, "exact_rank": None}) == "Pruned"
    assert candidate_state({"status": "proven_lower", "generic_lower": None, "descent_lower": 2, "exact_rank": None}) == "Interesting"
    assert "playground" in GENERAL_FAMILY.lower()
