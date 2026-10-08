from rank42.descent import classify_failure


def test_mwrank_large_coefficient_failure_is_size_limit():
    tail = """
    Attempt to convert -0.5337007924e32 to long fails!
    2-descent: bounds -0.5337007924e32 and/or 0.1812346635e34 on a too large
    RuntimeError: A 2-descent did not complete successfully.
    """
    assert classify_failure(1, tail) == "MWRANK_SIZE_LIMIT"


def test_generic_two_descent_failure_stays_mwrank_failure():
    assert classify_failure(1, "two_descent failed unexpectedly") == "MWRANK_FAILURE"



def test_simon_worker_only_exposes_rigorous_upper_in_deterministic_mode(monkeypatch):
    import rank42.classical_descent_worker as worker

    captured = []

    class FakeCurve:
        def simon_two_descent(self, **kwargs):
            captured.append(dict(kwargs))
            return 1, 3, []

    monkeypatch.setattr(worker, "_curve", lambda payload: (FakeCurve(), []))

    deterministic = worker.run_simon({"limbigprime": 0})
    assert deterministic["reported_upper"] == 3
    assert deterministic["rigorous_upper"] == 3
    assert deterministic["upper_bound_rigorous"] is True
    assert deterministic["upper_bound_scope"] == "deterministic_simon_local_tests"
    assert deterministic["deprecated_engine"] is True
    assert captured[-1]["limbigprime"] == 0

    probabilistic = worker.run_simon({"limbigprime": 30})
    assert probabilistic["reported_upper"] == 3
    assert probabilistic["rigorous_upper"] is None
    assert probabilistic["upper_bound_rigorous"] is False
    assert probabilistic["upper_bound_scope"] == "probabilistic_simon_large_prime_local_tests"
    assert captured[-1]["limbigprime"] == 30
