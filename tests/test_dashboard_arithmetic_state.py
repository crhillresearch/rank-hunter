import json
import math

from rank42.catalog import import_icarm_payload
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.dashboard_charts import _exceptional_curve_rows
from rank42.ui_pages.dashboard_complexity import _record_complexity_rows


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter, **fields):
    curve_id = upsert_curve(
        db,
        family="dashboard-arithmetic",
        parameter=str(parameter),
    )
    update_curve(
        db,
        curve_id,
        a_invariants_json=json.dumps(MODEL),
        **fields,
    )
    return curve_id


def _evidence(db, curve_id, *, lower=None, upper=None, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "dashboard-arithmetic-test",
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
    conductor=None,
    discriminant=None,
):
    row = {
        "id": int(source_id),
        "ainvs": MODEL,
        "rank_lower_bound": 1,
        "points": [],
    }
    if conductor is not None:
        row["conductor"] = str(conductor)
    if discriminant is not None:
        row["discriminant"] = str(discriminant)
    import_icarm_payload(db, {"count": 1, "curves": [row]})
    checked = now()
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
            checked,
        ),
    )
    db.commit()


def test_dashboard_exceptional_radar_uses_labeled_external_discriminant_fallback(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "radar")
        _evidence(db, curve_id, lower=5, key="dashboard-radar-lower")
        _icarm_match(
            db,
            curve_id,
            source_id="970",
            discriminant=-1000000,
        )

        rows = _exceptional_curve_rows(db)
        row = next(item for item in rows if item["Curve"] == f"#{curve_id}")

        assert math.isclose(row["log10|Δ|"], 6.0)
        assert row["Δ source"] == "ICARM reference"
        assert row["Rank certificate"] == "≥ 5"
    finally:
        db.close()


def test_dashboard_record_complexity_uses_labeled_external_arithmetic_fallback(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "records")
        _evidence(db, curve_id, lower=7, upper=7, key="dashboard-record-exact")
        _icarm_match(
            db,
            curve_id,
            source_id="971",
            conductor=97,
            discriminant=-100000000,
        )

        records = [
            row for row in _record_complexity_rows(db)
            if int(row["rank"]) == 7
        ]
        by_record = {row["record"]: row for row in records}

        assert int(by_record["Smallest conductor"]["value"]) == 97
        assert by_record["Smallest conductor"]["source"] == "ICARM reference"
        assert int(by_record["Smallest |Δ|"]["value"]) == 100000000
        assert by_record["Smallest |Δ|"]["source"] == "ICARM reference"
        assert by_record["Smallest coefficient height"]["source"] == "local model"
        assert int(by_record["Smallest conductor"]["row"]["id"]) == curve_id
    finally:
        db.close()


def test_dashboard_record_complexity_keeps_local_value_and_marks_reference_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(
            db,
            "conflict",
            conductor="101",
            discriminant="-1000",
        )
        _evidence(db, curve_id, lower=8, upper=8, key="dashboard-record-conflict-exact")
        _icarm_match(
            db,
            curve_id,
            source_id="972",
            conductor=89,
            discriminant=-900,
        )

        records = [
            row for row in _record_complexity_rows(db)
            if int(row["rank"]) == 8
        ]
        by_record = {row["record"]: row for row in records}

        assert int(by_record["Smallest conductor"]["value"]) == 101
        assert by_record["Smallest conductor"]["source"] == "local · conflict"
        assert int(by_record["Smallest |Δ|"]["value"]) == 1000
        assert by_record["Smallest |Δ|"]["source"] == "local · conflict"

        radar = next(
            row for row in _exceptional_curve_rows(db)
            if row["Curve"] == f"#{curve_id}"
        )
        assert math.isclose(radar["log10|Δ|"], 3.0)
        assert radar["Δ source"] == "local · conflict"
    finally:
        db.close()
