from types import SimpleNamespace

import rank42.search as search


class DummyFamily:
    parameter_symmetry = None

    @staticmethod
    def name():
        return "dummy"


def test_denominator_shells_cover_large_range_logarithmically():
    assert search._denominator_shells(1, 1_000_000) == [
        (1, 9),
        (10, 99),
        (100, 999),
        (1000, 9999),
        (10000, 99999),
        (100000, 999999),
        (1000000, 1000000),
    ]


def test_sampled_stage_is_bounded_deterministic_and_reduced(monkeypatch):
    monkeypatch.setattr(
        search,
        "score_rational",
        lambda a, b, tables: (float((abs(a) + b) % 101), 7),
    )
    args = SimpleNamespace(
        a_min=-100,
        a_max=100,
        b_min=1,
        b_max=1_000_000,
        sample_count=200,
        sample_seed=724,
    )
    one, tested_one = search.stage0_sampled(args, {}, 25, DummyFamily())
    two, tested_two = search.stage0_sampled(args, {}, 25, DummyFamily())

    assert tested_one == 200
    assert tested_two == 200
    assert one == two
    assert len(one) == 25
    assert all(1 <= int(rec["b"]) <= 1_000_000 for rec in one)
    assert all(rec["sampled"] is True for rec in one)
