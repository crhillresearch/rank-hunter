import json
import math

from rank42.catalog import import_icarm_payload
from rank42.curve_inventory import inventory_rows
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, parameter, *, lower=None, conductor=None, bad_primes=None):
    curve_id = upsert_curve(db, family="inventory-authority", parameter=str(parameter))
    fields = {
        "a_invariants_json": json.dumps(MODEL),
        "generators_json": json.dumps([["0", "1"], ["1", "1"], ["2", "3"]]),
        "status": "strong_done",
    }
    if lower is not None:
        fields["descent_lower"] = int(lower)
    if conductor is not None:
        fields["conductor"] = str(conductor)
    if bad_primes is not None:
        fields["bad_primes_json"] = json.dumps(list(bad_primes))
    update_curve(db, curve_id, **fields)
    return curve_id


def _evidence(db, curve_id, *, lower=None, upper=None, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "inventory-authority-test",
            "evidence_type": "rank_bounds",
            "status": "completed",
            "rigorous": True,
            "rigorous_lower": lower,
            "rigorous_upper": upper,
            "exact_rank": None,
            "conditional_analytic_upper": None,
            "numerical_rank_signal": None,
            "assumptions": [],
            "points_found": [],
            "options": {},
        },
        key=key,
    )


def _icarm_match(
    db,
    curve_id,
    *,
    source_id,
    rank=2,
    conductor=None,
    bad_primes=None,
):
    row = {
        "id": int(source_id),
        "ainvs": MODEL,
        "rank_lower_bound": int(rank),
        "points": [],
    }
    if conductor is not None:
        row["conductor"] = str(conductor)
    if bad_primes is not None:
        row["bad_primes"] = list(bad_primes)
    import_icarm_payload(db, {"count": 1, "curves": [row]})
    fetched = now()
    db.execute(
        """UPDATE external_catalogs
           SET fetched_at=?, updated_at=?
           WHERE source='icarm'""",
        (fetched, fetched),
    )
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
            int(rank),
            "{}",
            fetched,
        ),
    )
    db.commit()


def test_curve_inventory_uses_evidence_only_rank_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        low = _curve(db, "legacy", lower=3)
        evidence_only = _curve(db, "evidence")
        _evidence(db, evidence_only, lower=6, key="inventory-evidence-lower")

        rows = inventory_rows(db)
        by_id = {int(row["ID"]): row for row in rows}

        assert int(rows[0]["ID"]) == evidence_only
        assert by_id[evidence_only]["Rank"] == ">= 6"
        assert by_id[evidence_only]["Proven lower"] == 6
        assert by_id[evidence_only]["Exact rank"] is None
        assert by_id[low]["Rank"] == ">= 3"
        assert [int(row["ID"]) for row in inventory_rows(db, min_lower=5)] == [evidence_only]
    finally:
        db.close()


def test_curve_inventory_uses_evidence_only_exact_rank(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "exact")
        _evidence(db, curve_id, lower=5, upper=5, key="inventory-evidence-exact")

        row = next(item for item in inventory_rows(db) if int(item["ID"]) == curve_id)

        assert row["Rank"] == "= 5"
        assert row["Proven lower"] == 5
        assert row["Exact rank"] == 5
        assert row["Rank conflict"] is False
    finally:
        db.close()


def test_curve_inventory_uses_authoritative_log_n_and_labels_external_reference(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "reference", lower=3)
        _icarm_match(
            db,
            curve_id,
            source_id="950",
            conductor=43,
            bad_primes=[43],
        )

        row = next(item for item in inventory_rows(db) if int(item["ID"]) == curve_id)

        assert row["log N"] == round(math.log(43), 6)
        assert row["N source"] == "ICARM reference"
        assert row["N conflict"] is False
    finally:
        db.close()


def test_curve_inventory_local_conductor_wins_and_reference_conflict_is_visible(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "local", lower=3, conductor=37)
        _icarm_match(
            db,
            curve_id,
            source_id="951",
            conductor=41,
            bad_primes=[41],
        )

        row = next(item for item in inventory_rows(db) if int(item["ID"]) == curve_id)

        assert row["log N"] == round(math.log(37), 6)
        assert row["N source"] == "local · conflict"
        assert row["N conflict"] is True
    finally:
        db.close()


def test_curve_inventory_external_bad_primes_do_not_satisfy_submission_readiness(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "submission", lower=3, bad_primes=None)
        _icarm_match(
            db,
            curve_id,
            source_id="952",
            rank=2,
            conductor=43,
            bad_primes=[43],
        )

        row = next(item for item in inventory_rows(db) if int(item["ID"]) == curve_id)

        assert row["N source"] == "ICARM reference"
        assert row["ICARM action"] == "COMPUTE PRIMES"
    finally:
        db.close()
