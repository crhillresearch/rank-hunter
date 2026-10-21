from rank42.plugin_padic_result import (
    SCHEMA,
    validate_padic_covering_result,
)


def _valid(**overrides):
    result = {
        "schema": SCHEMA,
        "status": "completed",
        "engine": "demo-padic",
        "engine_version": "1.2.3",
        "algorithm": "hensel-covering-lift",
        "prime": 3,
        "precision": 60,
        "precision_semantics": "3-adic digits retained after each lift",
        "covering_ids": [7, 9],
        "local_lifting_bounds": {
            "max_lift_depth": 12,
            "residue_classes_checked": 81,
        },
        "completeness": "bounded",
        "points": [["0", "1"]],
        "metadata": {"fixture": True},
    }
    result.update(overrides)
    return result


def test_validate_padic_result_normalizes_explicit_search_scope():
    result = validate_padic_covering_result(
        _valid(),
        requested_prime=3,
        requested_precision=60,
        allowed_covering_ids=[7, 9, 11],
    )
    assert result["schema"] == SCHEMA
    assert result["engine"] == "demo-padic"
    assert result["covering_ids"] == [7, 9]
    assert result["completeness"] == "bounded"
    assert result["search_complete"] is False


def test_exhaustive_completed_padic_result_marks_search_complete():
    result = validate_padic_covering_result(
        _valid(completeness="exhaustive"),
        requested_prime=3,
        requested_precision=60,
        allowed_covering_ids=[7, 9],
    )
    assert result["search_complete"] is True


def test_padic_result_rejects_untyped_or_mismatched_claims():
    import pytest

    untyped = _valid()
    untyped.pop("schema")
    with pytest.raises(ValueError, match="unsupported p-adic result schema"):
        validate_padic_covering_result(
            untyped,
            requested_prime=3,
            requested_precision=60,
            allowed_covering_ids=[7, 9],
        )

    with pytest.raises(ValueError, match="does not match requested"):
        validate_padic_covering_result(
            _valid(prime=5),
            requested_prime=3,
            requested_precision=60,
            allowed_covering_ids=[7, 9],
        )

    with pytest.raises(ValueError, match="unrequested covering ids"):
        validate_padic_covering_result(
            _valid(covering_ids=[7, 99]),
            requested_prime=3,
            requested_precision=60,
            allowed_covering_ids=[7, 9],
        )


def test_padic_result_requires_engine_precision_and_completeness_semantics():
    import pytest

    missing_engine = _valid()
    missing_engine["engine"] = ""
    with pytest.raises(ValueError, match="non-empty engine"):
        validate_padic_covering_result(
            missing_engine,
            requested_prime=3,
            requested_precision=60,
            allowed_covering_ids=[7, 9],
        )

    missing_bounds = _valid()
    missing_bounds["local_lifting_bounds"] = []
    with pytest.raises(ValueError, match="local_lifting_bounds must be an object"):
        validate_padic_covering_result(
            missing_bounds,
            requested_prime=3,
            requested_precision=60,
            allowed_covering_ids=[7, 9],
        )

    bad_completeness = _valid(completeness="probably")
    with pytest.raises(ValueError, match="heuristic, bounded, or exhaustive"):
        validate_padic_covering_result(
            bad_completeness,
            requested_prime=3,
            requested_precision=60,
            allowed_covering_ids=[7, 9],
        )
