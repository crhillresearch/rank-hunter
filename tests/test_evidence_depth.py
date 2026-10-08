from rank42.evidence_depth import (
    EVIDENCE_DEPTH_STAGES,
    deepest_completed_stage,
    reached_stage_counts,
)


def _depth(**overrides):
    flags = {
        "independent_points": False,
        "mw_lattice": False,
        "descent": False,
        "saturation": False,
        "exact_rank": None,
    }
    flags.update(overrides)
    return deepest_completed_stage(**flags)


def test_evidence_depth_requires_continuous_prerequisites():
    assert _depth(exact_rank=6) == 0
    assert _depth(independent_points=True, exact_rank=6) == 1
    assert _depth(independent_points=True, mw_lattice=True, saturation=True, exact_rank=6) == 2
    assert _depth(independent_points=True, mw_lattice=True, descent=True, exact_rank=6) == 3


def test_evidence_depth_progresses_through_all_stages():
    assert _depth() == 0
    assert _depth(independent_points=True) == 1
    assert _depth(independent_points=True, mw_lattice=True) == 2
    assert _depth(independent_points=True, mw_lattice=True, descent=True) == 3
    assert _depth(independent_points=True, mw_lattice=True, descent=True, saturation=True) == 4
    assert _depth(independent_points=True, mw_lattice=True, descent=True, saturation=True, exact_rank=6) == 5


def test_evidence_depth_labels_bounded_saturation_and_exact_rank():
    labels = {depth: label for depth, _key, label in EVIDENCE_DEPTH_STAGES}
    assert labels[4] == "Bounded Saturation"
    assert labels[5] == "Exact Rank"
    assert "Global" not in labels[4]


def test_exact_rank_does_not_skip_missing_evidence_stages():
    assert _depth(exact_rank=0) == 0
    assert _depth(independent_points=True, exact_rank=4) == 1
    assert _depth(independent_points=True, mw_lattice=True, exact_rank=4) == 2


def test_reached_stage_counts_are_cumulative_without_inflating_none():
    deepest = {
        0: 12816,
        1: 3279,
        2: 16,
        3: 0,
        4: 5,
        5: 0,
    }
    assert reached_stage_counts(deepest) == {
        0: 12816,
        1: 3300,
        2: 21,
        3: 5,
        4: 5,
        5: 0,
    }


def test_reached_stage_counts_preserve_monotone_positive_depths():
    reached = reached_stage_counts({0: 8, 1: 4, 2: 3, 3: 2, 4: 1, 5: 1})
    assert reached[1] >= reached[2] >= reached[3] >= reached[4] >= reached[5]
    assert reached[0] == 8
