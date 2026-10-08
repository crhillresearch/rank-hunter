from rank42.curve_hall_of_fame import hall_of_fame_groups, hall_of_fame_summary


def test_hall_of_fame_groups_by_stored_torsion_and_rigorous_rank():
    groups = hall_of_fame_groups(
        [
            {
                "id": 1,
                "torsion_label": "C2",
                "rigorous_lower": 9,
                "exact_rank": None,
                "score": 4.0,
            },
            {
                "id": 2,
                "torsion_label": "C2",
                "rigorous_lower": 11,
                "exact_rank": None,
                "score": 1.0,
            },
            {
                "id": 3,
                "torsion_label": "C3",
                "rigorous_lower": 8,
                "exact_rank": 8,
                "score": 9.0,
            },
            {
                "id": 4,
                "torsion_label": None,
                "rigorous_lower": 20,
                "exact_rank": None,
                "score": 20.0,
            },
        ],
        top_per_group=2,
    )

    assert [group["torsion"] for group in groups] == ["C2", "C3", "Unknown"]
    assert groups[0]["best_rigorous_lower"] == 11
    assert [row["id"] for row in groups[0]["rows"]] == [2, 1]
    assert groups[-1]["rows"][0]["id"] == 4


def test_hall_of_fame_uses_score_only_as_secondary_tie_breaker():
    groups = hall_of_fame_groups(
        [
            {
                "id": 10,
                "torsion_label": "Trivial",
                "rigorous_lower": 7,
                "score": 2.0,
            },
            {
                "id": 11,
                "torsion_label": "Trivial",
                "rigorous_lower": 7,
                "score": 5.0,
            },
        ]
    )

    assert [row["id"] for row in groups[0]["rows"]] == [11, 10]



def test_hall_of_fame_summary_uses_authoritative_rank_and_stored_torsion():
    summary = hall_of_fame_summary(
        [
            {
                "id": 1,
                "torsion_label": "C2",
                "rigorous_lower": 11,
                "exact_rank": None,
            },
            {
                "id": 2,
                "torsion_label": "C2",
                "rigorous_lower": 8,
                "exact_rank": 8,
            },
            {
                "id": 3,
                "torsion_label": "C3",
                "rigorous_lower": 9,
                "exact_rank": 9,
            },
            {
                "id": 4,
                "torsion_label": None,
                "rigorous_lower": 20,
                "exact_rank": None,
            },
            {
                "id": 5,
                "torsion_label": "C5",
                "rigorous_lower": 0,
                "exact_rank": 0,
            },
        ]
    )

    assert summary["best_rigorous_lower"] == 20
    assert summary["best_exact_rank"] == 9
    assert summary["rigorous_curve_count"] == 4
    assert summary["exact_curve_count"] == 3

    # Torsion hero statistics are for retained ranked curves with locally
    # stored torsion. Unknown torsion and rank-0-only rows do not count.
    assert summary["torsion_group_count"] == 2
    assert summary["most_common_torsion"] == {"torsion": "C2", "count": 2}


def test_hall_of_fame_summary_handles_no_ranked_or_stored_torsion():
    summary = hall_of_fame_summary(
        [
            {
                "id": 1,
                "torsion_label": None,
                "rigorous_lower": 0,
                "exact_rank": None,
            }
        ]
    )

    assert summary["best_rigorous_lower"] is None
    assert summary["best_exact_rank"] is None
    assert summary["rigorous_curve_count"] == 0
    assert summary["exact_curve_count"] == 0
    assert summary["torsion_group_count"] == 0
    assert summary["most_common_torsion"] is None
