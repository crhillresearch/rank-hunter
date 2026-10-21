from rank42.exact_lb import run_exact_certificate


def test_exact_certificate_rank_one_37a1():
    # 37a1: y^2 + y = x^3 - x, with non-torsion generator (0,0).
    result = run_exact_certificate(
        ["0", "0", "1", "-1", "0"],
        [["0", "0"]],
        timeout=30,
    )
    assert result["status"] == "certified_independent"
    assert result["independent"] is True
    assert result["rank_lower_bound"] == 1
    assert result["certificate"]["matrix_rank"] - result["certificate"]["torsion_rank"] == 1
