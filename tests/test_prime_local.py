import json
import sqlite3

import pytest
from sage.all import EllipticCurve, QQ, ZZ, prime_divisors

import rank42.prime_local as prime_local
from rank42.prime_schema import PrimeSchemaNotReady
from rank42.prime_local import (
    bad_prime_fingerprint,
    cache_curve_primes,
    curve_prime_rows,
    ensure_prime_schema,
    first_quartic_local_obstruction,
    explicit_formula_indicator_score,
    frobenius_persistence_score,
    local_root_number_profile,
    mestre_nagao_ensemble_score,
    multi_scale_frobenius_score,
    quartic_mod_p_has_projective_point,
    sieve_stored_quartics,
    torsion_mod_p_sieve,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        """CREATE TABLE curves(
               id INTEGER PRIMARY KEY,
               conductor TEXT,
               discriminant TEXT,
               bad_primes_json TEXT,
               root_number INTEGER,
               updated_at TEXT
           )"""
    )
    db.execute(
        """CREATE TABLE quartic_searches(
               id INTEGER PRIMARY KEY,
               curve_id INTEGER,
               degree INTEGER,
               polynomial_json TEXT,
               hole_label TEXT,
               FOREIGN KEY(curve_id) REFERENCES curves(id) ON DELETE CASCADE
           )"""
    )
    ensure_prime_schema(db)
    db.execute("INSERT INTO curves(id) VALUES(1)")
    return db


def _curve():
    return EllipticCurve(QQ, [0, 0, 0, -1, 0])


def test_quartic_mod_p_obstruction_is_one_way_exact():
    # Over F_3 this quartic takes the nonsquare value 2 at every affine
    # class, and its leading coefficient 2 is also nonsquare.
    obstructed = [2, 0, 1, 0, 2]
    assert quartic_mod_p_has_projective_point(obstructed, 3) is False

    # x=0 gives y^2=1.
    assert quartic_mod_p_has_projective_point([1, 0, 0, 0, 1], 3) is True


def test_prime_runtime_schema_guard_does_not_mutate_legacy_schema():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    db.execute(
        """CREATE TABLE curve_prime_data(
               curve_id INTEGER NOT NULL,
               p INTEGER NOT NULL,
               model_key TEXT NOT NULL,
               good_reduction INTEGER NOT NULL,
               ap INTEGER,
               cardinality INTEGER,
               normalized_ap REAL,
               discriminant_valuation INTEGER,
               conductor_valuation INTEGER,
               tamagawa_number INTEGER,
               kodaira_symbol TEXT,
               local_root_number INTEGER,
               computed_at TEXT NOT NULL,
               PRIMARY KEY(curve_id,p)
           )"""
    )
    db.commit()

    with pytest.raises(PrimeSchemaNotReady, match="Migration Manager"):
        prime_local.prime_cache_state(db, 1)

    p_type = next(
        str(row["type"]).upper()
        for row in db.execute("PRAGMA table_info(curve_prime_data)")
        if row["name"] == "p"
    )
    assert p_type == "INTEGER"
    tables = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "curve_prime_cache_state" not in tables
    assert "quartic_local_tests" not in tables


def test_prime_cache_reuses_exact_frobenius_rows():
    db = _db()
    E = _curve()
    first = cache_curve_primes(
        db, curve_id=1, E=E, prime_bound=7, include_all_bad=False
    )
    assert first["status"] == "completed"
    assert first["computed"] >= 3
    assert first["frobenius_failed_primes"] == []
    assert first["requested_good_primes"] == first["computed_good_primes"]
    assert first["frobenius_complete_through"] == 7
    rows = curve_prime_rows(db, 1)
    by_p = {int(row["p"]): row for row in rows}
    assert 2 in by_p and 3 in by_p and 5 in by_p
    assert int(by_p[3]["good_reduction"]) == 1
    assert int(by_p[3]["cardinality"]) == 4

    second = cache_curve_primes(
        db, curve_id=1, E=E, prime_bound=7, include_all_bad=False
    )
    assert second["computed"] == 0
    assert second["reused"] >= first["primes_requested"]


def test_prime_cache_marks_failed_good_prime_frobenius_as_partial():
    db = _db()
    base = _curve()

    class FailingApCurve:
        def __init__(self, curve):
            self._curve = curve

        def __getattr__(self, name):
            return getattr(self._curve, name)

        def ap(self, p):
            if int(p) == 5:
                raise RuntimeError("injected Frobenius failure")
            return self._curve.ap(p)

    failed = cache_curve_primes(
        db,
        curve_id=1,
        E=FailingApCurve(base),
        prime_bound=7,
        include_all_bad=False,
    )
    assert failed["status"] == "partial"
    assert failed["frobenius_failed_primes"] == [5]
    assert failed["requested_good_primes"] == [3, 5]
    assert failed["computed_good_primes"] == [3]
    assert failed["requested_good_prime_count"] == 2
    assert failed["computed_good_prime_count"] == 1
    assert failed["frobenius_complete_through"] == 5

    state = prime_local.prime_cache_state(db, 1)
    assert int(state["good_prime_bound"]) == 5

    repaired = cache_curve_primes(
        db,
        curve_id=1,
        E=base,
        prime_bound=7,
        include_all_bad=False,
    )
    assert repaired["status"] == "completed"
    assert repaired["frobenius_failed_primes"] == []
    assert repaired["requested_good_primes"] == [3, 5]
    assert repaired["computed_good_primes"] == [3, 5]
    assert repaired["frobenius_complete_through"] == 7
    state = prime_local.prime_cache_state(db, 1)
    assert int(state["good_prime_bound"]) == 7


def test_prime_cache_repairs_legacy_overstated_good_prime_bound():
    db = _db()
    E = _curve()
    cache_curve_primes(
        db, curve_id=1, E=E, prime_bound=7, include_all_bad=False
    )
    db.execute(
        "UPDATE curve_prime_data SET ap=NULL, cardinality=NULL WHERE curve_id=1 AND p='5'"
    )
    db.execute(
        "UPDATE curve_prime_cache_state SET good_prime_bound=29 WHERE curve_id=1"
    )
    db.commit()

    result = cache_curve_primes(
        db, curve_id=1, E=E, prime_bound=3, include_all_bad=False
    )
    # The old 29 marker is not trusted: actual durable coverage stops at the
    # first missing/incomplete prime row.
    assert result["frobenius_complete_through"] == 5
    state = prime_local.prime_cache_state(db, 1)
    assert int(state["good_prime_bound"]) == 5


def test_multi_scale_frobenius_returns_cached_pipeline_score():
    db = _db()
    result = multi_scale_frobenius_score(
        db,
        curve_id=1,
        E=_curve(),
        bounds=[7, 13],
    )
    assert result["status"] == "completed"
    assert result["bounds"] == [7, 13]
    assert len(result["cumulative"]) == 2
    assert result["pipeline_score"] == result["cumulative"][-1]["score"]
    assert result["primes_used"] >= result["cumulative"][0]["primes_used"]
    assert result["algorithm"] == "cached-nagao-log-cardinality-v1"
    assert result["heuristic_kind"] == "rank_hunter_historical_nagao_style"
    assert result["published_mestre_nagao_formula"] is False


def test_prime_heuristics_propagate_partial_cache_domain():
    db = _db()
    base = _curve()

    class FailingApCurve:
        def __init__(self, curve):
            self._curve = curve

        def __getattr__(self, name):
            return getattr(self._curve, name)

        def ap(self, p):
            if int(p) == 5:
                raise RuntimeError("injected Frobenius failure")
            return self._curve.ap(p)

    E = FailingApCurve(base)
    results = [
        multi_scale_frobenius_score(
            db, curve_id=1, E=E, bounds=[7, 13, 29]
        ),
        mestre_nagao_ensemble_score(
            db, curve_id=1, E=E, prime_bound=29
        ),
        frobenius_persistence_score(
            db, curve_id=1, E=E, bounds=[7, 13, 29]
        ),
        explicit_formula_indicator_score(
            db, curve_id=1, E=E, prime_bound=29, max_prime_power=3
        ),
    ]

    for result in results:
        assert result["status"] == "partial"
        assert result["pipeline_score"] is not None
        domain = result["score_domain"]
        assert domain["algorithm"] == result["algorithm"]
        assert domain["cache_status"] == "partial"
        assert domain["complete"] is False
        assert domain["frobenius_complete_through"] == 5
        assert 5 in domain["good_primes_requested"]
        assert 5 in domain["failed_primes"]
        assert 5 not in domain["good_primes_used"]
        assert domain["good_primes_used_count"] < domain["good_primes_requested_count"]
        assert domain["unknown_reduction_primes"] == []


def test_prime_heuristics_clean_domain_is_complete():
    cases = [
        lambda db, E: multi_scale_frobenius_score(
            db, curve_id=1, E=E, bounds=[7, 13, 29]
        ),
        lambda db, E: mestre_nagao_ensemble_score(
            db, curve_id=1, E=E, prime_bound=29
        ),
        lambda db, E: frobenius_persistence_score(
            db, curve_id=1, E=E, bounds=[7, 13, 29]
        ),
        lambda db, E: explicit_formula_indicator_score(
            db, curve_id=1, E=E, prime_bound=29, max_prime_power=3
        ),
    ]
    for call in cases:
        db = _db()
        result = call(db, _curve())
        assert result["status"] == "completed"
        domain = result["score_domain"]
        assert domain["cache_status"] == "completed"
        assert domain["complete"] is True
        assert domain["failed_primes"] == []
        assert domain["unknown_reduction_primes"] == []
        assert domain["good_primes_used"] == domain["good_primes_requested"]
        assert domain["good_primes_used_count"] == domain["good_primes_requested_count"]


def test_torsion_mod_p_sieve_rigorously_rejects_impossible_target():
    db = _db()
    result = torsion_mod_p_sieve(
        db,
        curve_id=1,
        E=_curve(),
        torsion_group="C5",
        prime_bound=20,
        max_primes=8,
    )
    assert result["rigorous_rejection"] is True
    assert result["obstruction"] is not None
    assert result["passing_is_proof"] is False




def test_torsion_mod_p_sieve_rejects_group_structure_not_just_order():
    db = _db()
    result = torsion_mod_p_sieve(
        db,
        curve_id=1,
        E=_curve(),
        torsion_group="C4",
        prime_bound=5,
        max_primes=1,
    )
    assert result["rigorous_rejection"] is True
    assert result["obstruction_kind"] == "group_structure"
    assert result["obstruction"]["p"] == 3
    assert result["obstruction"]["cardinality"] == 4
    assert result["obstruction"]["order_divisible"] is True
    assert result["obstruction"]["finite_group_invariants"] == [2, 2]
    assert result["obstruction"]["structure_checked"] is True
    assert result["obstruction"]["structure_compatible"] is False
    assert result["passing_is_proof"] is False


def test_torsion_mod_p_sieve_accepts_compatible_finite_group_structure():
    db = _db()
    result = torsion_mod_p_sieve(
        db,
        curve_id=1,
        E=_curve(),
        torsion_group="C2 × C2",
        prime_bound=5,
        max_primes=1,
    )
    assert result["rigorous_rejection"] is False
    assert result["sieve_status"] == "passed"
    assert result["structure_complete"] is True
    assert result["checks"][0]["p"] == 3
    assert result["checks"][0]["finite_group_invariants"] == [2, 2]
    assert result["checks"][0]["structure_compatible"] is True
    assert result["passing_is_proof"] is False


def test_local_and_bad_prime_profiles_include_bad_prime_two(monkeypatch):
    db = _db()
    E = _curve()
    monkeypatch.setattr(
        prime_local,
        "_factor_all_bad_primes",
        lambda Em, timeout: ((2,), True, None),
    )
    roots = local_root_number_profile(db, curve_id=1, E=E, prime_bound=11)
    assert roots["global_root_number"] in (-1, 1)
    assert 2 in [rec["p"] for rec in roots["local_factors"]]

    fingerprint = bad_prime_fingerprint(db, curve_id=1, E=E, prime_bound=11)
    assert 2 in fingerprint["bad_primes"]
    assert fingerprint["bad_prime_count"] >= 1


def test_bad_prime_fingerprint_cross_checks_curve_arithmetic_authority():
    db = _db()
    E = _curve()
    Em = E.global_minimal_model()
    expected_conductor = str(Em.conductor())
    expected_discriminant = str(Em.discriminant())
    expected_bad = [int(p) for p in prime_divisors(abs(ZZ(Em.discriminant())))]

    db.execute(
        """UPDATE curves
           SET conductor=?,discriminant=?,bad_primes_json=?
           WHERE id=1""",
        (
            expected_conductor,
            expected_discriminant,
            json.dumps(expected_bad),
        ),
    )
    db.commit()

    matched = bad_prime_fingerprint(db, curve_id=1, E=E, prime_bound=11)
    assert matched["status"] == "completed"
    assert matched["conductor"] == expected_conductor
    assert matched["minimal_discriminant"] == expected_discriminant
    assert matched["bad_primes"] == expected_bad
    assert matched["arithmetic_conflicts"] == []
    assert set(matched["arithmetic_authority_check"]["matches"]) >= {
        "bad_primes", "conductor", "discriminant"
    }



def test_bad_prime_fingerprint_and_root_controls_match_sage(monkeypatch):
    fixtures = (
        [0, 0, 0, -1, 0],          # bad 2; additive reduction
        [0, 0, 0, 0, -1],          # bad 2 and 3; additive reduction
        [0, -1, 1, -10, -20],       # 11a1; multiplicative reduction at 11
    )
    seen_bad = set()
    seen_multiplicative = False
    seen_additive = False

    def exact_bad_primes(Em, timeout):
        bad = tuple(
            int(p)
            for p in prime_divisors(abs(ZZ(Em.discriminant())))
        )
        return bad, True, None

    monkeypatch.setattr(
        prime_local,
        "_factor_all_bad_primes",
        exact_bad_primes,
    )

    for ainvs in fixtures:
        db = _db()
        E = EllipticCurve(QQ, ainvs)
        Em = E.global_minimal_model()
        expected_bad = [
            int(p)
            for p in prime_divisors(abs(ZZ(Em.discriminant())))
        ]
        expected_conductor = str(Em.conductor())
        expected_discriminant = str(Em.discriminant())
        expected_root = int(Em.root_number())

        db.execute(
            """UPDATE curves
               SET conductor=?,discriminant=?,bad_primes_json=?,root_number=?
               WHERE id=1""",
            (
                expected_conductor,
                expected_discriminant,
                json.dumps(expected_bad),
                expected_root,
            ),
        )
        db.commit()

        fingerprint = bad_prime_fingerprint(
            db,
            curve_id=1,
            E=E,
            prime_bound=13,
            bad_prime_timeout=5,
        )
        roots = local_root_number_profile(
            db,
            curve_id=1,
            E=E,
            prime_bound=13,
            bad_prime_timeout=5,
        )

        assert fingerprint["status"] == "completed"
        assert roots["status"] == "completed"
        assert fingerprint["bad_primes"] == expected_bad
        assert fingerprint["conductor"] == expected_conductor
        assert fingerprint["minimal_discriminant"] == expected_discriminant
        assert roots["global_root_number"] == expected_root
        assert fingerprint["arithmetic_conflicts"] == []
        assert roots["arithmetic_conflicts"] == []

        fp_by_p = {
            rec["p"]: rec for rec in fingerprint["local_data"]
        }
        root_by_p = {
            rec["p"]: rec for rec in roots["local_factors"]
        }
        assert set(fp_by_p) == set(expected_bad)
        assert set(root_by_p) == set(expected_bad)

        for p in expected_bad:
            seen_bad.add(p)
            ld = Em.local_data(p)
            kodaira = str(ld.kodaira_symbol())
            expected_local_root = int(Em.root_number(p))

            assert fp_by_p[p]["v_delta"] == int(
                ld.discriminant_valuation()
            )
            assert fp_by_p[p]["conductor_exponent"] == int(
                ld.conductor_valuation()
            )
            assert fp_by_p[p]["kodaira_symbol"] == kodaira
            assert fp_by_p[p]["tamagawa_number"] == int(
                ld.tamagawa_number()
            )
            assert fp_by_p[p]["local_root_number"] == expected_local_root

            assert root_by_p[p]["local_root_number"] == expected_local_root
            assert root_by_p[p]["kodaira_symbol"] == kodaira
            assert root_by_p[p]["conductor_valuation"] == int(
                ld.conductor_valuation()
            )

            if kodaira.startswith("I") and kodaira[1:].isdigit():
                seen_multiplicative = True
            else:
                seen_additive = True

    assert {2, 3}.issubset(seen_bad)
    assert seen_multiplicative is True
    assert seen_additive is True


def test_prime_local_global_conflict_is_visible_and_inconclusive():
    db = _db()
    E = _curve()
    wrong_root = -int(E.root_number())
    db.execute(
        """UPDATE curves
           SET conductor='999999',discriminant='-999999',
               bad_primes_json='[999983]',root_number=?
           WHERE id=1""",
        (wrong_root,),
    )
    db.commit()

    fingerprint = bad_prime_fingerprint(db, curve_id=1, E=E, prime_bound=11)
    assert fingerprint["status"] == "inconclusive"
    assert set(fingerprint["arithmetic_conflicts"]) >= {
        "bad_primes", "conductor", "discriminant"
    }

    roots = local_root_number_profile(db, curve_id=1, E=E, prime_bound=11)
    assert roots["global_root_number"] == int(E.root_number())
    assert roots["status"] == "inconclusive"
    assert roots["arithmetic_conflicts"] == ["root_number"]


def test_stored_quartic_local_sieve_persists_obstruction():
    db = _db()
    db.execute(
        """INSERT INTO quartic_searches(id,curve_id,degree,polynomial_json,hole_label)
           VALUES(11,1,4,?,?)""",
        (json.dumps(["2", "0", "1", "0", "2"]), "test"),
    )
    result = sieve_stored_quartics(db, curve_id=1, primes=[3])
    assert result["quartics_tested"] == 1
    assert result["obstructed_count"] == 1
    assert result["obstructed"][0]["p"] == 3

    cached = first_quartic_local_obstruction(
        db,
        search_id=11,
        coefficients=[2, 0, 1, 0, 2],
        primes=[3],
    )
    assert cached["p"] == 3
    assert cached["tested"][0]["cached"] is True



def test_incomplete_bad_prime_factorization_stays_inconclusive(monkeypatch):
    db = _db()
    E = _curve()
    monkeypatch.setattr(
        prime_local,
        "_factor_all_bad_primes",
        lambda Em, timeout: ((), False, "timeout"),
    )
    cached = cache_curve_primes(
        db,
        curve_id=1,
        E=E,
        prime_bound=11,
        include_all_bad=True,
        bad_prime_timeout=1,
    )
    assert cached["status"] == "inconclusive"
    assert cached["bad_primes_complete"] is False
    assert 2 in cached["bad_primes"]

    roots = local_root_number_profile(
        db,
        curve_id=1,
        E=E,
        prime_bound=11,
        bad_prime_timeout=1,
    )
    assert roots["status"] == "inconclusive"
    assert roots["global_root_number"] is None



def test_mestre_nagao_ensemble_returns_consensus_pipeline_score():
    db = _db()
    result = mestre_nagao_ensemble_score(
        db, curve_id=1, E=_curve(), prime_bound=29,
    )
    assert result["status"] == "completed"
    assert result["primes_used"] > 0
    assert result["pipeline_score"] is not None
    assert set(result["components"]) >= {
        "log_cardinality_signal",
        "nagao_trace_signal",
        "normalized_trace_signal",
    }
    assert result["heuristic_only"] is True
    assert result["algorithm"] == "mestre-nagao-ensemble-v1"
    assert result["heuristic_kind"] == "rank_hunter_experimental_prime_ensemble"
    assert result["published_multi_value_mestre_nagao"] is False
    assert result["trace_sign_convention"] == "negative_ap_log_p_over_p"


def test_frobenius_persistence_uses_weakest_disjoint_band():
    db = _db()
    result = frobenius_persistence_score(
        db, curve_id=1, E=_curve(), bounds=[7, 13, 29],
    )
    assert result["status"] == "completed"
    signals = [rec["normalized_signal"] for rec in result["bands"]]
    assert all(value is not None for value in signals)
    assert result["pipeline_score"] == min(signals)
    assert result["heuristic_only"] is True
    assert result["algorithm"] == "frobenius-persistence-worst-band-v1"
    assert result["heuristic_kind"] == "rank_hunter_experimental_scheduling_heuristic"
    assert result["published_standard_statistic"] is False
    assert result["rank_evidence"] is False


def test_explicit_formula_indicator_is_explicitly_not_a_rank_bound():
    db = _db()
    result = explicit_formula_indicator_score(
        db, curve_id=1, E=_curve(), prime_bound=29, max_prime_power=3,
    )
    assert result["status"] == "completed"
    assert result["prime_power_terms"] > 0
    assert result["pipeline_score"] is not None
    assert result["analytic_rank_bound"] is False
    assert result["heuristic_only"] is True



def test_prime_schema_migrates_legacy_integer_prime_column():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE curves(id INTEGER PRIMARY KEY)")
    db.execute(
        """CREATE TABLE quartic_searches(
               id INTEGER PRIMARY KEY,
               curve_id INTEGER,
               degree INTEGER,
               polynomial_json TEXT,
               hole_label TEXT
           )"""
    )
    db.execute(
        """CREATE TABLE curve_prime_data(
               curve_id INTEGER NOT NULL,
               p INTEGER NOT NULL,
               model_key TEXT NOT NULL,
               good_reduction INTEGER NOT NULL,
               ap INTEGER,
               cardinality INTEGER,
               normalized_ap REAL,
               discriminant_valuation INTEGER,
               conductor_valuation INTEGER,
               tamagawa_number INTEGER,
               kodaira_symbol TEXT,
               local_root_number INTEGER,
               computed_at TEXT NOT NULL,
               PRIMARY KEY(curve_id,p)
           )"""
    )
    db.execute("INSERT INTO curves(id) VALUES(1)")
    db.execute(
        """INSERT INTO curve_prime_data(
               curve_id,p,model_key,good_reduction,ap,cardinality,
               normalized_ap,computed_at
           ) VALUES(1,?,?,1,?,?,?,?)""",
        (5, "legacy", -2, 8, -2 / (5 ** 0.5), "now"),
    )
    ensure_prime_schema(db)
    p_type = next(
        str(row["type"]).upper()
        for row in db.execute("PRAGMA table_info(curve_prime_data)")
        if row["name"] == "p"
    )
    assert p_type == "TEXT"
    row = db.execute(
        "SELECT p FROM curve_prime_data WHERE curve_id=1"
    ).fetchone()
    assert row["p"] == "5"


def test_prime_cache_preserves_bad_prime_above_sqlite_int64(monkeypatch):
    db = _db()
    E = _curve()
    huge = int(ZZ(2**63).next_prime())
    assert huge > 2**63 - 1

    monkeypatch.setattr(
        prime_local,
        "_factor_all_bad_primes",
        lambda Em, timeout: ((huge,), True, None),
    )
    original_local = prime_local._local_data_record

    def fake_local(Em, p, *, good):
        if int(p) == huge:
            return {
                "discriminant_valuation": 1,
                "conductor_valuation": 1,
                "tamagawa_number": 1,
                "kodaira_symbol": "I1",
                "local_root_number": -1,
            }
        return original_local(Em, p, good=good)

    monkeypatch.setattr(prime_local, "_local_data_record", fake_local)
    result = cache_curve_primes(
        db,
        curve_id=1,
        E=E,
        prime_bound=11,
        include_all_bad=True,
    )
    assert result["bad_primes_complete"] is True
    assert huge in result["bad_primes"]

    rows = curve_prime_rows(db, 1)
    by_p = {int(row["p"]): row for row in rows}
    assert huge in by_p
    assert by_p[huge]["p"] == str(huge)

    bounded = curve_prime_rows(db, 1, max_p=11)
    assert all(int(row["p"]) < 11 for row in bounded)
    assert huge not in {int(row["p"]) for row in bounded}
