from fractions import Fraction
from types import SimpleNamespace

from rank42 import search


class _CanonicalFamily:
    parameter_symmetry = "canonical"

    @staticmethod
    def canonical_parameter(value):
        q = Fraction(value)
        if q in {Fraction(29, 5), Fraction(5, 29), Fraction(17, 12), Fraction(12, 17)}:
            return 5, 29
        return abs(q.numerator), q.denominator

    @staticmethod
    def name():
        return "test family"


def test_family_canonicalizer_collapses_isomorphic_parameter_rows(monkeypatch):
    family = _CanonicalFamily()
    assert search.canonical_candidate_pair(family, 29, 5) == (5, 29)
    assert search.canonical_candidate_pair(family, 17, 12) == (5, 29)

    scored = []

    def fake_score(a, b, _tables):
        scored.append((a, b))
        return 0.0, 1

    monkeypatch.setattr(search, "score_rational", fake_score)
    args = SimpleNamespace(a_min=5, a_max=17, b_min=12, b_max=29)
    rows, _tested = search.stage0_scalar(args, {}, 1000, family)
    assert len([row for row in rows if (row["a"], row["b"]) == (5, 29)]) == 1
    assert sum(pair in {(29, 5), (5, 29), (17, 12), (12, 17)} for pair in scored) == 1
