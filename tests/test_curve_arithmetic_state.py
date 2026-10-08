import json
import math
from pathlib import Path
import subprocess
import sys

from rank42.catalog import import_icarm_payload
from rank42.conductor_backfill import ensure_schema as ensure_conductor_attempt_schema
from rank42.curve_arithmetic_state import (
    compare_curve_arithmetic_values,
    compute_and_publish_curve_arithmetic,
    compute_curve_arithmetic_values,
    curve_arithmetic_state_map,
    get_curve_arithmetic_state,
    publish_curve_arithmetic_values,
    publish_curve_root_number,
)
from rank42.curve_size_metrics import ensure_curve_size_metrics_schema, store_curve_size_metrics
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.torsion import ensure_torsion_schema


def test_curve_arithmetic_state_import_does_not_require_sage():
    code = r'''\
import builtins

real_import = builtins.__import__

def reject_sage(name, *args, **kwargs):
    if name == "sage" or name.startswith("sage."):
        raise ModuleNotFoundError("sage intentionally unavailable")
    return real_import(name, *args, **kwargs)

builtins.__import__ = reject_sage
import rank42.curve_arithmetic_state
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _curve(db, parameter="1"):
    return upsert_curve(db, family="arithmetic-test", parameter=str(parameter))


def _icarm_match(
    db,
    curve_id,
    *,
    source_id="901",
    conductor=None,
    discriminant=None,
    bad_primes=None,
    naive_height=None,
    faltings_height=None,
):
    row = {
        "id": int(source_id),
        "ainvs": ["0", "0", "0", "-1", "0"],
        "rank_lower_bound": 1,
        "points": [],
    }
    if conductor is not None:
        row["conductor"] = str(conductor)
    if discriminant is not None:
        row["discriminant"] = str(discriminant)
    if bad_primes is not None:
        row["bad_primes"] = list(bad_primes)
    if naive_height is not None:
        row["naive_height"] = str(naive_height)
    if faltings_height is not None:
        row["faltings_height"] = str(faltings_height)
    import_icarm_payload(db, {"count": 1, "curves": [row]})
    ts = now()
    db.execute(
        """INSERT INTO curve_catalog_checks(
               curve_id,source,status,source_label,source_url,source_rank,metadata_json,checked_at
           ) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(curve_id,source) DO UPDATE SET
               status=excluded.status,
               source_label=excluded.source_label,
               source_url=excluded.source_url,
               source_rank=excluded.source_rank,
               metadata_json=excluded.metadata_json,
               checked_at=excluded.checked_at""",
        (
            int(curve_id),
            "icarm",
            "known",
            str(source_id),
            f"https://elliptic-rank.icarm.cloud/curve/{source_id}",
            1,
            "{}",
            ts,
        ),
    )
    db.commit()


def test_curve_arithmetic_prefers_local_and_reports_reference_conflicts(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        update_curve(
            db,
            curve_id,
            conductor="37",
            discriminant="-11",
            bad_primes_json=json.dumps([11]),
            root_number=-1,
        )
        _icarm_match(
            db,
            curve_id,
            conductor=41,
            discriminant=-13,
            bad_primes=[13],
        )

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["conductor"] == 37
        assert math.isclose(state["log_conductor"], math.log(37))
        assert state["discriminant"] == -11
        assert state["bad_primes"] == [11]
        assert state["root_number"] == -1
        assert state["provenance"]["conductor"]["kind"] == "local_persisted"
        assert state["references"]["conductor"][0]["value"] == 41
        assert set(state["conflicts"]) == {"conductor", "discriminant", "bad_primes"}
    finally:
        db.close()


def test_curve_arithmetic_root_publisher_fills_missing_and_preserves_conflicts(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "root-publisher")

        promoted = publish_curve_root_number(
            db,
            curve_id,
            -1,
            source="test",
            method="finite_local_product",
        )
        assert promoted["status"] == "promoted"
        assert promoted["published"] is True
        assert promoted["conflict"] is False
        assert promoted["authority"] == -1
        assert promoted["provenance"]["source"] == "test"
        assert get_curve_arithmetic_state(db, curve_id)["root_number"] == -1

        matched = publish_curve_root_number(
            db,
            curve_id,
            -1,
            source="test-repeat",
            method="finite_local_product",
        )
        assert matched["status"] == "match"
        assert matched["published"] is True
        assert matched["authority"] == -1

        conflict = publish_curve_root_number(
            db,
            curve_id,
            1,
            source="test-conflict",
            method="finite_local_product",
        )
        assert conflict["status"] == "conflict"
        assert conflict["published"] is False
        assert conflict["conflict"] is True
        assert conflict["authority"] == -1
        assert conflict["authority_check"]["conflicts"] == ["root_number"]
        assert get_curve_arithmetic_state(db, curve_id)["root_number"] == -1
    finally:
        db.close()


def test_curve_arithmetic_compare_service_surfaces_matches_missing_and_conflicts(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "compare")
        update_curve(
            db,
            curve_id,
            conductor="37",
            discriminant="-11",
            bad_primes_json=json.dumps([11]),
            root_number=-1,
        )

        result = compare_curve_arithmetic_values(
            db,
            curve_id,
            {
                "conductor": "37",
                "discriminant": "-13",
                "bad_primes": [11],
                "root_number": 1,
            },
        )

        assert result["matches"] == ["bad_primes", "conductor"]
        assert result["missing_authority"] == []
        assert result["conflicts"] == ["discriminant", "root_number"]
        assert result["consistent"] is False
        assert result["fields"]["conductor"]["status"] == "match"
        assert result["fields"]["discriminant"]["status"] == "conflict"
        assert result["fields"]["root_number"]["authority"] == -1
    finally:
        db.close()


def test_curve_arithmetic_compare_service_marks_missing_authority(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "compare-missing")
        result = compare_curve_arithmetic_values(
            db,
            curve_id,
            {"conductor": 37, "root_number": -1},
        )
        assert result["conflicts"] == []
        assert result["missing_authority"] == ["conductor", "root_number"]
        assert result["consistent"] is True
    finally:
        db.close()


def test_curve_arithmetic_uses_labeled_external_fallback_when_local_missing(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        _icarm_match(
            db,
            curve_id,
            conductor=37,
            discriminant=-11,
            bad_primes=[11],
        )

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["conductor"] == 37
        assert state["discriminant"] == -11
        assert state["bad_primes"] == [11]
        assert state["provenance"]["conductor"] == {
            "kind": "external_reference",
            "source": "icarm",
            "method": "exact_catalog_match",
            "detail": "local invariant not computed",
        }
        assert state["provenance"]["discriminant"]["kind"] == "external_reference"
        assert state["provenance"]["bad_primes"]["kind"] == "external_reference"
        assert state["provenance"]["log_conductor"]["kind"] == "derived"
        assert math.isclose(state["log_conductor"], math.log(37))
        assert state["conflicts"] == []
    finally:
        db.close()


def test_curve_arithmetic_fallback_stays_separate_from_local_authority(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "reference-not-authority")
        _icarm_match(
            db,
            curve_id,
            source_id="920",
            conductor=41,
            discriminant=-17,
            bad_primes=[17],
        )

        state = get_curve_arithmetic_state(db, curve_id)
        comparison = compare_curve_arithmetic_values(
            db,
            curve_id,
            {"conductor": 41, "discriminant": -17, "bad_primes": [17]},
        )

        assert state["conductor"] == 41
        assert state["display"]["conductor"] == 41
        assert state["local"]["conductor"] is None
        assert state["local"]["discriminant"] is None
        assert state["local"]["bad_primes"] is None
        assert state["local_complete"]["conductor"] is False
        assert state["complete"]["conductor"] is True
        assert comparison["missing_authority"] == [
            "bad_primes",
            "conductor",
            "discriminant",
        ]
        assert comparison["conflicts"] == []
        assert comparison["reference_conflicts"] == []
        assert comparison["fields"]["conductor"]["reference_status"] == "match"
        assert comparison["fields"]["conductor"]["authority"] is None
        assert comparison["fields"]["conductor"]["display_value"] == 41
    finally:
        db.close()


def test_local_arithmetic_publication_wins_authority_and_preserves_reference_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "publish-over-reference")
        _icarm_match(
            db,
            curve_id,
            source_id="921",
            conductor=41,
            discriminant=-17,
            bad_primes=[17],
        )

        result = publish_curve_arithmetic_values(
            db,
            curve_id,
            {
                "conductor": 37,
                "discriminant": -11,
                "bad_primes": [11],
            },
            source="test-local-exact",
            method="fixture",
        )
        state = get_curve_arithmetic_state(db, curve_id)

        assert set(result["published_fields"]) == {
            "bad_primes_json",
            "conductor",
            "discriminant",
        }
        assert result["authority_conflicts"] == []
        assert set(result["reference_conflicts"]) == {
            "bad_primes",
            "conductor",
            "discriminant",
        }
        assert set(result["conflicts"]) == {
            "bad_primes",
            "conductor",
            "discriminant",
        }
        assert result["authority"] == {
            "conductor": 37,
            "discriminant": -11,
            "bad_primes": [11],
        }
        assert result["display"] == {
            "conductor": 37,
            "discriminant": -11,
            "bad_primes": [11],
        }
        assert state["local"]["conductor"] == 37
        assert state["local"]["discriminant"] == -11
        assert state["local"]["bad_primes"] == [11]
        assert state["conductor"] == 37
        assert state["discriminant"] == -11
        assert state["bad_primes"] == [11]
        assert set(state["conflicts"]) == {
            "bad_primes",
            "conductor",
            "discriminant",
        }
        assert state["provenance"]["conductor"]["kind"] == "local_persisted"
        comparison = result["authority_check"]
        assert comparison["conflicts"] == []
        assert set(comparison["reference_conflicts"]) == {
            "bad_primes",
            "conductor",
            "discriminant",
        }
    finally:
        db.close()


def test_curve_arithmetic_does_not_invent_external_bad_primes_when_source_omits_them(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        _icarm_match(
            db,
            curve_id,
            conductor=37,
            discriminant=-11,
            bad_primes=None,
        )

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["bad_primes"] is None
        assert state["references"]["bad_primes"] == []
        assert state["complete"]["bad_primes"] is False
    finally:
        db.close()


def test_curve_arithmetic_exposes_torsion_algorithm_provenance(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        ensure_torsion_schema(db)
        curve_id = _curve(db)
        db.execute(
            """UPDATE curves
               SET torsion_order=?, torsion_invariants_json=?, torsion_label=?,
                   torsion_computed_at=?, torsion_algorithm=?, torsion_error=NULL
               WHERE id=?""",
            (
                4,
                json.dumps([2, 2]),
                "C2 × C2",
                "2026-09-25T00:00:00+00:00",
                "sage.torsion_subgroup",
                int(curve_id),
            ),
        )
        db.commit()

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["torsion"] == {
            "label": "C2 × C2",
            "order": 4,
            "invariants": [2, 2],
        }
        assert state["provenance"]["torsion"]["kind"] == "local_computed"
        assert state["provenance"]["torsion"]["method"] == "sage.torsion_subgroup"
        assert state["issues"] == []
    finally:
        db.close()


def test_curve_arithmetic_recovers_cached_external_conductor_provenance(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        ensure_conductor_attempt_schema(db)
        curve_id = _curve(db)
        update_curve(db, curve_id, conductor="37")
        db.execute(
            """INSERT INTO conductor_backfill_attempts(
                   curve_id,status,method,detail,attempted_at
               ) VALUES(?,?,?,?,?)""",
            (
                int(curve_id),
                "done",
                "icarm_exact_match",
                "ICARM curve 901",
                "2026-09-25T00:00:00+00:00",
            ),
        )
        db.commit()

        state = get_curve_arithmetic_state(db, curve_id)

        provenance = state["provenance"]["conductor"]
        assert state["conductor"] == 37
        assert provenance["kind"] == "external_reference_cached_local"
        assert provenance["source"] == "icarm"
        assert provenance["method"] == "icarm_exact_match"
        assert provenance["detail"] == "ICARM curve 901"
    finally:
        db.close()


def test_curve_arithmetic_missing_state_is_explicit(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["conductor"] is None
        assert state["log_conductor"] is None
        assert state["discriminant"] is None
        assert state["bad_primes"] is None
        assert state["root_number"] is None
        assert state["torsion"] is None
        assert state["conflicts"] == []
        assert state["issues"] == []
        assert not any(state["complete"].values())
    finally:
        db.close()

def test_curve_arithmetic_read_service_performs_no_writes(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        update_curve(
            db,
            curve_id,
            conductor="37",
            discriminant="-11",
            bad_primes_json=json.dumps([11]),
            root_number=1,
        )
        traced = []
        db.set_trace_callback(traced.append)

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["conductor"] == 37
        writes = [
            statement
            for statement in traced
            if statement.lstrip().upper().startswith(
                ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "DROP ", "REPLACE ")
            )
        ]
        assert writes == []
    finally:
        db.close()

def test_curve_arithmetic_bulk_map_matches_single_curve_projection(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        first = _curve(db, "bulk-1")
        second = _curve(db, "bulk-2")
        update_curve(
            db,
            first,
            conductor="37",
            discriminant="-11",
            bad_primes_json=json.dumps([11]),
        )
        ensure_curve_size_metrics_schema(db)
        store_curve_size_metrics(
            db,
            first,
            {
                "naive_height": "5.5",
                "faltings_height": "-0.5",
                "precision_bits": 128,
                "method": "sage_global_minimal_model",
            },
        )
        _icarm_match(
            db,
            second,
            source_id="904",
            conductor=43,
            discriminant=-17,
            bad_primes=[17],
            naive_height="6.5",
            faltings_height="-0.75",
        )

        states = curve_arithmetic_state_map(db, [first, second])

        assert set(states) == {first, second}
        assert states[first]["conductor"] == get_curve_arithmetic_state(db, first)["conductor"]
        assert states[first]["discriminant"] == get_curve_arithmetic_state(db, first)["discriminant"]
        assert states[second]["conductor"] == get_curve_arithmetic_state(db, second)["conductor"]
        assert states[second]["provenance"]["conductor"] == get_curve_arithmetic_state(db, second)["provenance"]["conductor"]
        assert states[first]["naive_height"] == get_curve_arithmetic_state(db, first)["naive_height"]
        assert states[first]["provenance"]["naive_height"] == get_curve_arithmetic_state(db, first)["provenance"]["naive_height"]
        assert states[second]["faltings_height"] == get_curve_arithmetic_state(db, second)["faltings_height"]
        assert states[second]["provenance"]["faltings_height"] == get_curve_arithmetic_state(db, second)["provenance"]["faltings_height"]
    finally:
        db.close()

def test_curve_arithmetic_size_metrics_prefer_local_with_method_provenance(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        ensure_curve_size_metrics_schema(db)
        curve_id = _curve(db, "sizes-local")
        store_curve_size_metrics(
            db,
            curve_id,
            {
                "naive_height": "12.5000",
                "faltings_height": "-1.2500",
                "precision_bits": 256,
                "method": "sage_global_minimal_model",
            },
        )
        _icarm_match(
            db,
            curve_id,
            source_id="905",
            naive_height="13.0",
            faltings_height="-1.5",
        )

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["naive_height"] == "12.5"
        assert state["faltings_height"] == "-1.25"
        assert state["provenance"]["naive_height"]["kind"] == "local_computed"
        assert state["provenance"]["naive_height"]["method"] == "sage_global_minimal_model"
        assert state["provenance"]["naive_height"]["precision_bits"] == 256
        assert "naive_height" in state["conflicts"]
        assert "faltings_height" in state["conflicts"]
    finally:
        db.close()


def test_curve_arithmetic_size_metrics_use_labeled_external_fallback(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "sizes-reference")
        _icarm_match(
            db,
            curve_id,
            source_id="906",
            naive_height="9.75",
            faltings_height="-0.625",
        )

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["naive_height"] == "9.75"
        assert state["faltings_height"] == "-0.625"
        assert state["provenance"]["naive_height"]["kind"] == "external_reference"
        assert state["provenance"]["naive_height"]["source"] == "icarm"
        assert state["provenance"]["faltings_height"]["kind"] == "external_reference"
        assert state["conflicts"] == []
    finally:
        db.close()


def test_curve_arithmetic_size_metric_formatting_equivalence_is_not_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        ensure_curve_size_metrics_schema(db)
        curve_id = _curve(db, "sizes-equivalent")
        store_curve_size_metrics(
            db,
            curve_id,
            {
                "naive_height": "1.0000",
                "faltings_height": "-2.5000",
                "precision_bits": 128,
                "method": "sage_global_minimal_model",
            },
        )
        _icarm_match(
            db,
            curve_id,
            source_id="907",
            naive_height="1.0",
            faltings_height="-2.5",
        )

        state = get_curve_arithmetic_state(db, curve_id)

        assert state["naive_height"] == "1"
        assert state["faltings_height"] == "-2.5"
        assert "naive_height" not in state["conflicts"]
        assert "faltings_height" not in state["conflicts"]
    finally:
        db.close()



def test_curve_arithmetic_bundle_matches_historical_general_hunt_semantics(tmp_path):
    from sage.all import EllipticCurve, QQ, prime_divisors

    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "general-hunt-bundle")
        E = EllipticCurve(QQ, [0, 0, 0, -1, 1])

        values = compute_curve_arithmetic_values(E)
        result = compute_and_publish_curve_arithmetic(
            db,
            curve_id,
            E,
            source="general_hunt",
            method="sage_exact_global_invariants",
        )

        expected_disc = int(E.discriminant())
        expected_min_disc = int(E.global_minimal_model().discriminant())
        assert values["discriminant"] == expected_disc
        assert values["bad_primes"] == [
            int(p) for p in prime_divisors(abs(expected_min_disc))
        ]
        assert values["conductor"] == int(E.conductor())
        assert values["root_number"] == int(E.root_number())

        state = get_curve_arithmetic_state(db, curve_id)
        assert state["discriminant"] == expected_disc
        assert state["bad_primes"] == values["bad_primes"]
        assert state["conductor"] == values["conductor"]
        assert state["root_number"] == values["root_number"]
        assert result["publication"]["conflicts"] == []
        assert set(result["publication"]["published_fields"]) == {
            "bad_primes_json",
            "conductor",
            "discriminant",
            "root_number",
        }
    finally:
        db.close()


def test_curve_arithmetic_bundle_publisher_preserves_existing_conflicts(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "bundle-conflict")
        update_curve(
            db,
            curve_id,
            conductor="37",
            discriminant="-11",
            bad_primes_json=json.dumps([11]),
            root_number=-1,
        )

        result = publish_curve_arithmetic_values(
            db,
            curve_id,
            {
                "conductor": 41,
                "discriminant": -13,
                "bad_primes": [13],
                "root_number": 1,
            },
            source="test",
            method="fixture",
        )

        state = get_curve_arithmetic_state(db, curve_id)
        assert result["published_fields"] == []
        assert set(result["conflicts"]) == {
            "bad_primes",
            "conductor",
            "discriminant",
            "root_number",
        }
        assert state["conductor"] == 37
        assert state["discriminant"] == -11
        assert state["bad_primes"] == [11]
        assert state["root_number"] == -1
    finally:
        db.close()
