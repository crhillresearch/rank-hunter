import json

from rank42.analyze_workspace import curve_analysis_snapshot
from rank42.catalog import import_icarm_payload
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.ui_pages.analyze_page import _arithmetic_source_label


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter, *, conductor=None, discriminant=None, bad_primes=None, root_number=None):
    curve_id = upsert_curve(
        db,
        family="analyze-arithmetic",
        parameter=str(parameter),
        score=1.0,
    )
    fields = {
        "a_invariants_json": json.dumps(MODEL),
        "descent_lower": 1,
    }
    if conductor is not None:
        fields["conductor"] = str(conductor)
    if discriminant is not None:
        fields["discriminant"] = str(discriminant)
    if bad_primes is not None:
        fields["bad_primes_json"] = json.dumps(list(bad_primes))
    if root_number is not None:
        fields["root_number"] = int(root_number)
    update_curve(db, curve_id, **fields)
    return curve_id


def _icarm_match(
    db,
    curve_id,
    *,
    source_id,
    conductor=None,
    discriminant=None,
    bad_primes=None,
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
    if bad_primes is not None:
        row["bad_primes"] = list(bad_primes)
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


def test_analyze_snapshot_uses_labeled_external_arithmetic_fallback(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "reference")
        _icarm_match(
            db,
            curve_id,
            source_id="990",
            conductor=109,
            discriminant=-113,
            bad_primes=[113],
        )

        snapshot = curve_analysis_snapshot(db, curve_id)
        arithmetic = snapshot["arithmetic"]

        assert arithmetic["conductor"] == 109
        assert arithmetic["discriminant"] == -113
        assert arithmetic["bad_primes"] == [113]
        assert _arithmetic_source_label(arithmetic, "conductor") == "ICARM reference"
        assert _arithmetic_source_label(arithmetic, "discriminant") == "ICARM reference"
        assert _arithmetic_source_label(arithmetic, "bad_primes") == "ICARM reference"
    finally:
        db.close()


def test_analyze_snapshot_keeps_local_arithmetic_and_marks_reference_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(
            db,
            "local",
            conductor=127,
            discriminant=-131,
            bad_primes=[131],
            root_number=-1,
        )
        _icarm_match(
            db,
            curve_id,
            source_id="991",
            conductor=137,
            discriminant=-139,
            bad_primes=[139],
        )

        snapshot = curve_analysis_snapshot(db, curve_id)
        arithmetic = snapshot["arithmetic"]

        assert arithmetic["conductor"] == 127
        assert arithmetic["discriminant"] == -131
        assert arithmetic["bad_primes"] == [131]
        assert arithmetic["root_number"] == -1
        assert set(arithmetic["conflicts"]) == {"conductor", "discriminant", "bad_primes"}
        assert _arithmetic_source_label(arithmetic, "conductor") == "local · conflict"
        assert _arithmetic_source_label(arithmetic, "root_number") == "local"
    finally:
        db.close()


def test_analyze_family_jumps_use_arithmetic_authority_for_root_number(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        selected = _curve(db, "1", root_number=1)
        peer = _curve(db, "2", root_number=-1)

        snapshot = curve_analysis_snapshot(db, selected)
        by_id = {int(row["curve_id"]): row for row in snapshot["family_jumps"]}

        assert by_id[selected]["root_number"] == 1
        assert by_id[peer]["root_number"] == -1
        assert snapshot["arithmetic"]["root_number"] == 1
    finally:
        db.close()
