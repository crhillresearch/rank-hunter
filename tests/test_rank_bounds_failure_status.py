from rank42.rank_bounds import _worker_failure_class, _worker_failure_status


def test_mwrank_incomplete_descent_is_inconclusive():
    stderr = "RuntimeError: A 2-descent did not complete successfully."
    assert _worker_failure_status("mwrank", stderr) == "inconclusive"


def test_other_worker_failures_remain_errors():
    assert _worker_failure_status("pari", "PARI stack overflows") == "error"
    assert _worker_failure_status("mwrank", "unexpected failure") == "error"


def test_mwrank_size_limit_has_stable_failure_class():
    stderr = """
    Attempt to convert -0.5243460415e28 to long fails!
    2-descent: lower bound -0.5243460415e28 on c too large
    RuntimeError: A 2-descent did not complete successfully.
    """
    assert _worker_failure_class("mwrank", stderr) == "MWRANK_SIZE_LIMIT"
    assert _worker_failure_status("mwrank", stderr) == "inconclusive"


def test_pari_stack_failure_is_distinct():
    assert _worker_failure_class("pari", "PARI stack overflows") == "PARI_STACK"
