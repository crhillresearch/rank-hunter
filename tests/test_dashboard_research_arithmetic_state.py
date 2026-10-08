import json

from rank42.catalog import import_icarm_payload
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.dashboard_research import (
    _arithmetic_record_rows,
    _exceptional_arithmetic_rows,
)


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter, **fields):
    curve_id = upsert_curve(
        db,
        family="dashboard-research-arithmetic",
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
            "engine": "dashboard-research-arithmetic-test",
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


def test_dashboard_research_records_use_evidence_only_exact_and_external_arithmetic(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "records")
        _evidence(db, curve_id, lower=7, upper=7, key="research-record-exact")
        _icarm_match(
            db,
            curve_id,
            source_id="980",
            conductor=97,
            discriminant=-101,
        )

        rows = _arithmetic_record_rows(db)
        row = next(item for item in rows if int(item["rank"]) == 7)

        assert int(row["min_conductor"]) == 97
        assert row["conductor_source"] == "ICARM reference"
        assert int(row["min_discriminant"]) == 101
        assert row["discriminant_source"] == "ICARM reference"
        assert int(row["representative"]["id"]) == curve_id
    finally:
        db.close()


def test_dashboard_research_records_keep_local_arithmetic_and_mark_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(
            db,
            "conflict",
            conductor="103",
            discriminant="-107",
        )
        _evidence(db, curve_id, lower=8, upper=8, key="research-record-conflict")
        _icarm_match(
            db,
            curve_id,
            source_id="981",
            conductor=89,
            discriminant=-83,
        )

        row = next(
            item for item in _arithmetic_record_rows(db)
            if int(item["rank"]) == 8
        )

        assert int(row["min_conductor"]) == 103
        assert row["conductor_source"] == "local · conflict"
        assert int(row["min_discriminant"]) == 107
        assert row["discriminant_source"] == "local · conflict"
    finally:
        db.close()


def test_dashboard_research_exceptional_uses_reduced_interval_and_root_number(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(
            db,
            "interval",
            root_number=-1,
            regulator="12.5",
        )
        _evidence(db, curve_id, lower=6, key="research-interval-lower")
        _evidence(db, curve_id, upper=8, key="research-interval-upper")
        _icarm_match(
            db,
            curve_id,
            source_id="982",
            conductor=109,
            discriminant=-113,
        )

        rows = _exceptional_arithmetic_rows(
            db,
            high_rank_floor=5,
            best_exact=None,
        )
        row = next(item for item in rows if int(item["row"]["id"]) == curve_id)

        assert row["exact"] is False
        assert int(row["lower"]) == 6
        assert int(row["upper"]) == 8
        assert int(row["gap"]) == 2
        assert int(row["root_number"]) == -1
        assert int(row["conductor"]) == 109
        assert row["conductor_source"] == "ICARM reference"
        assert int(row["discriminant"]) == 113
        assert row["discriminant_source"] == "ICARM reference"
        assert "High-rank lower" in row["flags"]
        assert "Open interval +2" in row["flags"]
    finally:
        db.close()


def test_dashboard_research_exceptional_exact_records_use_authoritative_arithmetic(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(
            db,
            "exact",
            conductor="127",
            discriminant="-131",
            root_number=1,
        )
        _evidence(db, curve_id, lower=9, upper=9, key="research-exact-record")

        rows = _exceptional_arithmetic_rows(
            db,
            high_rank_floor=8,
            best_exact=9,
        )
        row = next(item for item in rows if int(item["row"]["id"]) == curve_id)

        assert row["exact"] is True
        assert int(row["exact_rank"]) == 9
        assert int(row["upper"]) == 9
        assert int(row["root_number"]) == 1
        assert row["conductor_source"] == "local"
        assert row["discriminant_source"] == "local"
        assert "Best exact" in row["flags"]
        assert "Conductor record" in row["flags"]
        assert "Δ record" in row["flags"]
    finally:
        db.close()
