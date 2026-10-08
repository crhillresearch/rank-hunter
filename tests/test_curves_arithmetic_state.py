import json
import math

from rank42.catalog import import_icarm_payload
from rank42.curve_size_metrics import ensure_curve_size_metrics_schema, store_curve_size_metrics
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.torsion import ensure_torsion_schema
from rank42.ui_pages.curves import (
    _arithmetic_source_label,
    _curve_inventory_rows,
    _inventory_table,
)


def _db(path):
    db = connect(path)
    ensure_curve_size_metrics_schema(db)
    ensure_torsion_schema(db)
    return db


def _curve(db, parameter, **fields):
    curve_id = upsert_curve(
        db,
        family="curves-arithmetic",
        parameter=str(parameter),
        score=1.0,
    )
    update_curve(
        db,
        curve_id,
        descent_lower=1,
        a_invariants_json=json.dumps(["0", "0", "0", "-1", "0"]),
        **fields,
    )
    return curve_id


def _icarm_match(
    db,
    curve_id,
    *,
    source_id,
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
            now(),
        ),
    )
    db.commit()


def test_curves_arithmetic_inventory_prefers_local_and_surfaces_conflict(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(
            db,
            "local",
            conductor="37",
            discriminant="-11",
            bad_primes_json=json.dumps([11]),
            root_number=-1,
        )
        _icarm_match(
            db,
            curve_id,
            source_id="901",
            conductor=41,
            discriminant=-13,
            bad_primes=[13],
        )

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )
        table_row = _inventory_table([row], "Arithmetic")[0]

        assert row["arithmetic_conductor"] == 37
        assert row["arithmetic_discriminant"] == -11
        assert row["arithmetic_bad_primes"] == [11]
        assert row["arithmetic_root_number"] == -1
        assert set(row["arithmetic_conflicts"]) == {"conductor", "discriminant", "bad_primes"}
        assert math.isclose(table_row["log N"], math.log(37))
        assert math.isclose(table_row["log |Δ|"], math.log(11))
        assert table_row["N source"] == "Local · conflict"
        assert table_row["Δ source"] == "Local · conflict"
    finally:
        db.close()


def test_curves_arithmetic_inventory_labels_icarm_reference_fallback(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "reference")
        _icarm_match(
            db,
            curve_id,
            source_id="902",
            conductor=43,
            discriminant=-17,
            bad_primes=[17],
        )

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )
        table_row = _inventory_table([row], "Arithmetic")[0]

        assert row["conductor"] is None
        assert row["discriminant"] is None
        assert row["arithmetic_conductor"] == 43
        assert row["arithmetic_discriminant"] == -17
        assert row["arithmetic_bad_primes"] == [17]
        assert _arithmetic_source_label(row, "conductor") == "ICARM reference · local invariant not computed"
        assert _arithmetic_source_label(row, "discriminant") == "ICARM reference · local invariant not computed"
        assert _arithmetic_source_label(row, "bad_primes") == "ICARM reference · local invariant not computed"
        assert table_row["N source"] == "ICARM ref"
        assert table_row["Δ source"] == "ICARM ref"
        assert math.isclose(table_row["log N"], math.log(43))
    finally:
        db.close()


def test_curves_arithmetic_inventory_keeps_local_authority_separate_from_display_fallback(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "reference-separation")
        _icarm_match(
            db,
            curve_id,
            source_id="906",
            conductor=59,
            discriminant=-23,
            bad_primes=[23],
        )

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )

        assert row["arithmetic_local"]["conductor"] is None
        assert row["arithmetic_local"]["discriminant"] is None
        assert row["arithmetic_local"]["bad_primes"] is None
        assert row["arithmetic_display"]["conductor"] == 59
        assert row["arithmetic_display"]["discriminant"] == -23
        assert row["arithmetic_display"]["bad_primes"] == [23]
        assert row["arithmetic_conductor"] == 59
        assert row["arithmetic_discriminant"] == -23
        assert row["arithmetic_bad_primes"] == [23]
    finally:
        db.close()


def test_curves_arithmetic_inventory_keeps_missing_reference_bad_primes_unknown(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "missing-primes")
        _icarm_match(
            db,
            curve_id,
            source_id="903",
            conductor=47,
            discriminant=-19,
            bad_primes=None,
        )

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )

        assert row["arithmetic_bad_primes"] is None
        assert _arithmetic_source_label(row, "bad_primes") == "—"
    finally:
        db.close()


def test_curves_arithmetic_inventory_uses_authoritative_torsion_projection(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "torsion")
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

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )
        table_row = _inventory_table([row], "Arithmetic")[0]

        assert row["arithmetic_torsion"] == {
            "label": "C2 × C2",
            "order": 4,
            "invariants": [2, 2],
        }
        assert table_row["torsion"] == "C2 × C2"
    finally:
        db.close()

def test_curves_arithmetic_inventory_uses_authoritative_local_size_metrics(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "sizes-local")
        store_curve_size_metrics(
            db,
            curve_id,
            {
                "naive_height": "12.5",
                "faltings_height": "-1.25",
                "precision_bits": 256,
                "method": "sage_global_minimal_model",
            },
        )
        _icarm_match(
            db,
            curve_id,
            source_id="904",
            naive_height="13.5",
            faltings_height="-1.5",
        )

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )
        table_row = _inventory_table([row], "Arithmetic")[0]

        assert row["arithmetic_naive_height"] == "12.5"
        assert row["arithmetic_faltings_height"] == "-1.25"
        assert table_row["naive height"] == 12.5
        assert table_row["Faltings height"] == -1.25
        assert table_row["naive source"] == "Sage · conflict"
        assert table_row["Faltings source"] == "Sage · conflict"
    finally:
        db.close()


def test_curves_arithmetic_inventory_labels_reference_size_metric_fallback(tmp_path):
    db = _db(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "sizes-reference")
        _icarm_match(
            db,
            curve_id,
            source_id="905",
            naive_height="8.75",
            faltings_height="-0.75",
        )

        row = next(
            item for item in _curve_inventory_rows(db)
            if int(item["id"]) == curve_id
        )
        table_row = _inventory_table([row], "Arithmetic")[0]

        assert row["arithmetic_naive_height"] == "8.75"
        assert row["arithmetic_faltings_height"] == "-0.75"
        assert table_row["naive source"] == "ICARM ref"
        assert table_row["Faltings source"] == "ICARM ref"
    finally:
        db.close()

