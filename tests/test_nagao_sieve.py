import math
from types import SimpleNamespace

import pytest

import rank42.nagao as nagao
import rank42.search as search
from rank42.family_loader import family_cache_key, family_cache_keys
from rank42.nagao import family_score_provenance, iter_fixed_denominator_sieve, score_rational


def test_fixed_denominator_sieve_matches_scalar():
    tables = {
        2: [0.1, None],
        3: [0.2, -0.1, 0.05],
        5: [None, 0.3, -0.2, 0.4, 0.0],
        7: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
    }
    for b in range(1, 8):
        got = {}
        for aa, scores, used in iter_fixed_denominator_sieve(
            -20, 20, b, tables, block_size=7
        ):
            for a, score, terms in zip(aa.tolist(), scores.tolist(), used.tolist()):
                got[int(a)] = (float(score), int(terms))

        for a in range(-20, 21):
            if math.gcd(a, b) != 1:
                continue
            expected = score_rational(a, b, tables)
            actual = got[a]
            assert abs(expected[0] - actual[0]) < 1e-12
            assert expected[1] == actual[1]


def test_family_score_provenance_names_historical_formula_and_exact_primes():
    tables = {
        2: [0.1, 0.2],
        3: [0.1, 0.3, None],
        5: [0.1, 0.2, 0.3, 0.4, 0.5],
    }
    score, used = score_rational(1, 1, tables)
    provenance = family_score_provenance(1, 1, tables, 7)
    assert score == pytest.approx(0.2 + 0.3 + 0.2)
    assert used == 3
    assert provenance["algorithm"] == "rh-family-table-nagao-v1"
    assert provenance["scorer_version"] == 1
    assert provenance["source_mode"] == "family_parameter_table"
    assert provenance["formula"] == "sum_log_cardinality_over_p"
    assert provenance["prime_bound"] == 7
    assert provenance["primes_used"] == [2, 3, 5]
    assert provenance["terms_used"] == used
    assert "no global 2/3 exclusion" in provenance["prime_convention"]
    assert provenance["published_mestre_nagao_formula"] is False


def test_block_top_k_preserves_score_numerator_ties():
    import numpy as np
    from rank42.search import top_block_indices

    aa = np.array([-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5], dtype=np.int64)
    scores = np.array([0.0, 1.0, 1.0, 0.5, 1.0, -1.0, 1.0, 0.5, 1.0, 1.0, 0.0])
    idx = top_block_indices(aa, scores, 4)
    got = sorted((float(scores[i]), int(aa[i])) for i in idx)[-4:]
    expected = sorted((float(s), int(a)) for a, s in zip(aa, scores))[-4:]
    assert got == expected


class _FakeFamily:
    @staticmethod
    def name():
        return "Fake Family"

    @staticmethod
    def nagao_score_table(p):
        return [float(p)] * p


def _fake_prime_range(start, stop):
    return [p for p in (2, 3, 5, 7, 11, 13, 17, 19) if start <= p < stop]


def test_precompute_checkpoint_is_explicitly_incomplete_and_resumable(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(nagao, "prime_range", _fake_prime_range)

    class InterruptingFamily(_FakeFamily):
        @staticmethod
        def nagao_score_table(p):
            if p == 5:
                raise KeyboardInterrupt
            return [float(p)] * p

    checkpoint = tmp_path / "nagao-demo-p20.checkpoint.pickle"
    with pytest.raises(KeyboardInterrupt):
        nagao.extend_score_tables(
            InterruptingFamily,
            20,
            progress=True,
            checkpoint_path=checkpoint,
            checkpoint_interval=1,
        )

    partial, metadata = nagao.load_score_tables(checkpoint)
    assert set(partial) == {2, 3}
    assert metadata["complete"] is False
    assert metadata["checkpoint"] is True
    assert nagao.score_cache_is_complete(metadata) is False
    progress = capsys.readouterr().out
    assert "0/8" in progress
    assert "2/8" in progress

    resumed = nagao.extend_score_tables(
        _FakeFamily,
        20,
        partial,
        checkpoint_path=checkpoint,
        checkpoint_interval=1,
    )
    assert set(resumed) == {2, 3, 5, 7, 11, 13, 17, 19}


def test_get_tables_reuses_largest_compatible_lower_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nagao, "prime_range", _fake_prime_range)
    monkeypatch.setattr(search, "family_cache_key", lambda spec, family: "demo")

    family = _FakeFamily
    search.save_score_tables(
        search.cache_path(tmp_path, "demo", 12),
        {p: [float(p)] * p for p in (2, 3, 5, 7, 11)},
        metadata={"family": family.name(), "family_spec": "demo", "prime_bound": 12},
    )
    lower = {p: [float(p)] * p for p in (2, 3, 5, 7, 11, 13)}
    search.save_score_tables(
        search.cache_path(tmp_path, "demo", 16),
        lower,
        metadata={"family": family.name(), "family_spec": "demo", "prime_bound": 16},
    )
    # This partial target-bound file must not win over the complete lower cache.
    search.save_score_tables(
        search.checkpoint_cache_path(tmp_path, "demo", 20),
        {2: [2.0]},
        metadata={
            "family": family.name(),
            "family_spec": "demo",
            "prime_bound": 20,
            "complete": False,
            "checkpoint": True,
            "table_count": 1,
            "target_table_count": 8,
        },
    )

    args = SimpleNamespace(cache_dir=tmp_path, no_cache=False)
    tables = search.get_tables(family, "demo", 20, {}, args)
    assert "p16.pickle" in capsys.readouterr().out
    assert set(tables) == {2, 3, 5, 7, 11, 13, 17, 19}
    assert search.cache_path(tmp_path, "demo", 20).exists()
    assert not search.checkpoint_cache_path(tmp_path, "demo", 20).exists()


def test_legacy_complete_cache_metadata_remains_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(nagao, "prime_range", _fake_prime_range)
    monkeypatch.setattr(search, "family_cache_key", lambda spec, family: "legacy")

    family = _FakeFamily
    tables = {p: [float(p)] * p for p in (2, 3, 5, 7)}
    path = search.cache_path(tmp_path, "legacy", 11)
    search.save_score_tables(path, tables)  # pre-completion-marker payload

    args = SimpleNamespace(cache_dir=tmp_path, no_cache=False)
    got = search.get_tables(family, "legacy", 11, {}, args)
    assert got == tables


def test_declared_nagao_identity_survives_unrelated_source_edits(tmp_path):
    source = tmp_path / "family.py"
    source.write_text("VERSION = 1\n")

    class Family:
        source_path = source
        nagao_cache_identity = "same-finite-field-family-v1"
        nagao_cache_legacy_keys = (
            "plugin-family-old-source-hash",
            "plugin-family-old-source-hash",
        )

        @staticmethod
        def name():
            return "Stable Family"

    before = family_cache_key("stable_family", Family)
    source.write_text("VERSION = 2\nUNRELATED_CERTIFICATE = True\n")
    after = family_cache_key("stable_family", Family)

    assert before == after
    assert family_cache_keys("stable_family", Family) == (
        before,
        "plugin-family-old-source-hash",
    )


def test_get_tables_accepts_only_explicit_legacy_cache_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "family_cache_key", lambda spec, family: "primary")
    monkeypatch.setattr(
        search,
        "family_cache_keys",
        lambda spec, family: ("primary", "verified-legacy"),
    )

    family = _FakeFamily
    tables = {p: [float(p)] * p for p in (2, 3, 5, 7)}
    search.save_score_tables(
        search.cache_path(tmp_path, "verified-legacy", 11),
        tables,
        metadata={
            "family": family.name(),
            "family_spec": "stable",
            "prime_bound": 11,
        },
    )

    args = SimpleNamespace(cache_dir=tmp_path, no_cache=False)
    assert search.get_tables(family, "stable", 11, {}, args) == tables
