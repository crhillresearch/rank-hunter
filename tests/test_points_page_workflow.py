from rank42.db import connect, upsert_curve, update_curve
from rank42.points import (
    list_actionable_points,
    list_hard_points,
    list_point_discoveries,
    list_points,
    record_point_discovery,
    set_point_hard_flag,
    upsert_point,
)
from rank42.rank_evidence import record_rank_evidence
from rank42.ui_pages.points_page import (
    _curve_rollup,
    _denominator_provenance,
    _point_curve_options,
    _needs_attention,
    _potential_label,
    _status_label,
    _workflow_counts,
)



def _rank_evidence(db, curve_id, *, lower=None, upper=None):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=["0", "0", "0", "-1", "0"],
        data={
            "engine": "points-page-test",
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
    )

def test_point_workflow_labels_do_not_overstate_rank_growth():
    promising = {
        "exact_verified": 1,
        "rigorous_independent": 0,
        "independence_status": "numerical_novel",
        "rigorous_lower": 17,
        "curve_exact_rank": None,
    }
    assert _status_label(promising) == "Promising — needs proof"
    assert _needs_attention(promising) is True
    assert _potential_label(promising) == "Candidate for ≥18"

    exact_closed_curve = dict(promising, curve_exact_rank=17)
    assert _potential_label(exact_closed_curve) == "Consistency review"

    dependent = dict(promising, independence_status="dependent")
    assert _needs_attention(dependent) is False
    assert _potential_label(dependent) == "Closed"

    rigorous = dict(
        promising,
        independence_status="rigorous_independent",
        rigorous_independent=1,
    )
    assert _needs_attention(rigorous) is False
    assert _potential_label(rigorous) == "Supports ≥17"


def test_point_workflow_counts_and_curve_rollup(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="demo", parameter="5")
        update_curve(db, curve_id, descent_lower=8)

        upsert_point(
            db,
            curve_id=curve_id,
            x="1",
            y="2",
            source="search",
            exact_verified=False,
        )
        upsert_point(
            db,
            curve_id=curve_id,
            x="2",
            y="3",
            source="search",
            exact_verified=True,
            independence_status="numerical_novel",
        )
        upsert_point(
            db,
            curve_id=curve_id,
            x="3",
            y="4",
            source="search",
            exact_verified=True,
            independence_status="dependent",
        )
        upsert_point(
            db,
            curve_id=curve_id,
            x="4",
            y="5",
            source="certificate",
            role="rigorous_witness",
            exact_verified=True,
            independence_status="rigorous_independent",
            rigorous_independent=True,
        )

        counts = _workflow_counts(db)
        assert counts == {
            "total": 4,
            "needs_exact": 1,
            "needs_independence": 1,
            "numerical_novel": 1,
            "rigorous_independent": 1,
            "dependent": 1,
        }

        rollup = _curve_rollup(db)
        row = next(item for item in rollup if int(item["curve_id"]) == curve_id)
        assert int(row["rigorous_lower"]) == 8
        assert int(row["needs_exact"]) == 1
        assert int(row["actionable"]) == 1
        assert int(row["promising"]) == 1
        assert int(row["rigorous"]) == 1
        assert int(row["dependent"]) == 1
        assert int(row["total"]) == 4
    finally:
        db.close()


def test_list_points_exposes_curve_exact_rank_for_consistency_review(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="closed", parameter="11")
        update_curve(db, curve_id, exact_rank=6, descent_lower=6, descent_upper=6)
        point = upsert_point(
            db,
            curve_id=curve_id,
            x="10",
            y="20",
            source="import",
            exact_verified=True,
            independence_status="unknown",
        )

        row = next(
            item
            for item in list_points(db, curve_id=curve_id)
            if int(item["id"]) == int(point["id"])
        )
        assert int(row["curve_exact_rank"]) == 6
        assert int(row["curve_descent_upper"]) == 6
        assert int(row["rigorous_lower"]) == 6
        assert _potential_label(row) == "Consistency review"
    finally:
        db.close()


def test_actionable_query_filters_before_limit(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="busy", parameter="7")
        update_curve(db, curve_id, descent_lower=5)

        for i in range(8):
            upsert_point(
                db,
                curve_id=curve_id,
                x=str(100 + i),
                y=str(200 + i),
                source="certificate",
                exact_verified=True,
                independence_status="rigorous_independent",
                rigorous_independent=True,
                role="rigorous_witness",
            )

        actionable = upsert_point(
            db,
            curve_id=curve_id,
            x="999",
            y="1000",
            source="search",
            exact_verified=True,
            independence_status="numerical_novel",
        )

        truncated_ledger = list_points(db, limit=5)
        assert all(int(row["id"]) != int(actionable["id"]) for row in truncated_ledger)

        queue = list_actionable_points(db, limit=5)
        assert [int(row["id"]) for row in queue] == [int(actionable["id"])]
        assert queue[0]["independence_status"] == "numerical_novel"
    finally:
        db.close()


def test_filtered_ledger_applies_state_before_limit(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="busy", parameter="9")
        update_curve(db, curve_id, descent_lower=4)

        for i in range(6):
            upsert_point(
                db,
                curve_id=curve_id,
                x=str(i),
                y=str(i + 10),
                source="certificate",
                exact_verified=True,
                independence_status="rigorous_independent",
                rigorous_independent=True,
                role="rigorous_witness",
            )

        pending = upsert_point(
            db,
            curve_id=curve_id,
            x="77",
            y="88",
            source="search",
            exact_verified=False,
        )

        rows = list_points(db, exact_verified=False, limit=2)
        assert [int(row["id"]) for row in rows] == [int(pending["id"])]
    finally:
        db.close()


def test_hard_bookmark_survives_point_upsert_and_can_be_cleared(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="hard", parameter="13")
        update_curve(db, curve_id, descent_lower=7)
        point = upsert_point(
            db,
            curve_id=curve_id,
            x="101",
            y="202",
            source="search",
            exact_verified=True,
            independence_status="numerical_novel",
            metadata={"first": True},
        )
        point_id = int(point["id"])

        flagged = set_point_hard_flag(
            db,
            point_id,
            True,
            reason="halving budget exhausted (151/150)",
        )
        assert int(flagged["hard_flag"]) == 1
        assert flagged["hard_reason"] == "halving budget exhausted (151/150)"

        # A later scientific upsert must not erase the researcher bookmark.
        upsert_point(
            db,
            curve_id=curve_id,
            x="101",
            y="202",
            source="search",
            exact_verified=True,
            independence_status="numerical_novel",
            metadata={"later": True},
        )
        hard = list_hard_points(db, curve_id=curve_id)
        assert [int(row["id"]) for row in hard] == [point_id]
        assert hard[0]["hard_reason"] == "halving budget exhausted (151/150)"

        cleared = set_point_hard_flag(db, point_id, False)
        assert int(cleared["hard_flag"]) == 0
        assert list_hard_points(db, curve_id=curve_id) == []
    finally:
        db.close()

def test_points_rollup_uses_authoritative_evidence_rank(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        legacy = upsert_curve(db, family="demo", parameter="legacy")
        update_curve(db, legacy, descent_lower=4)
        evidence_only = upsert_curve(db, family="demo", parameter="evidence")
        _rank_evidence(db, evidence_only, lower=6)

        for cid, x in ((legacy, "1"), (evidence_only, "2")):
            upsert_point(
                db,
                curve_id=cid,
                x=x,
                y="3",
                source="search",
                exact_verified=True,
                independence_status="dependent",
            )

        rows = _curve_rollup(db)
        by_id = {int(row["curve_id"]): row for row in rows}

        assert int(by_id[evidence_only]["rigorous_lower"]) == 6
        assert int(by_id[legacy]["rigorous_lower"]) == 4
    finally:
        db.close()


def test_points_curve_options_use_authoritative_state_and_exact_rank(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        legacy = upsert_curve(db, family="demo", parameter="legacy")
        update_curve(db, legacy, descent_lower=4)
        evidence_exact = upsert_curve(db, family="demo", parameter="evidence-exact")
        _rank_evidence(db, evidence_exact, lower=6, upper=6)
        unknown = upsert_curve(db, family="demo", parameter="unknown")

        for cid, x in ((legacy, "11"), (evidence_exact, "12"), (unknown, "13")):
            upsert_point(
                db,
                curve_id=cid,
                x=x,
                y="13",
                source="search",
                exact_verified=True,
                independence_status="unknown",
            )

        rows = _point_curve_options(db)

        assert [int(row["id"]) for row in rows[:2]] == [evidence_exact, legacy]
        assert int(rows[0]["rigorous_lower"]) == 6
        assert int(rows[0]["exact_rank"]) == 6
        unknown_row = next(row for row in rows if int(row["id"]) == unknown)
        assert unknown_row["rigorous_lower"] is None
        assert unknown_row["exact_rank"] is None
    finally:
        db.close()

def test_list_points_uses_authoritative_evidence_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="evidence-row", parameter="17")
        _rank_evidence(db, curve_id, lower=6, upper=6)
        point = upsert_point(
            db,
            curve_id=curve_id,
            x="17",
            y="18",
            source="search",
            exact_verified=True,
            independence_status="unknown",
        )

        row = next(item for item in list_points(db, curve_id=curve_id) if int(item["id"]) == int(point["id"]))

        assert int(row["rigorous_lower"]) == 6
        assert int(row["curve_exact_rank"]) == 6
        assert int(row["curve_descent_upper"]) == 6
        assert int(row["curve_rigorous_upper"]) == 6
        assert row["curve_rank_inconsistent"] is False
        assert _potential_label(row) == "Consistency review"
    finally:
        db.close()


def test_actionable_query_orders_by_authoritative_rank_before_limit(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        legacy = upsert_curve(db, family="queue", parameter="legacy")
        update_curve(db, legacy, descent_lower=4)
        evidence_high = upsert_curve(db, family="queue", parameter="evidence-high")
        _rank_evidence(db, evidence_high, lower=7)

        legacy_point = upsert_point(
            db,
            curve_id=legacy,
            x="21",
            y="22",
            source="search",
            exact_verified=True,
            independence_status="numerical_novel",
        )
        evidence_point = upsert_point(
            db,
            curve_id=evidence_high,
            x="23",
            y="24",
            source="search",
            exact_verified=True,
            independence_status="numerical_novel",
        )

        queue = list_actionable_points(db, attention="promising", limit=1)

        assert [int(row["id"]) for row in queue] == [int(evidence_point["id"])]
        assert int(queue[0]["rigorous_lower"]) == 7
        assert int(queue[0]["id"]) != int(legacy_point["id"])
    finally:
        db.close()


def test_hard_points_use_authoritative_evidence_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="hard-evidence", parameter="19")
        _rank_evidence(db, curve_id, lower=5)
        point = upsert_point(
            db,
            curve_id=curve_id,
            x="31",
            y="32",
            source="search",
            exact_verified=True,
            independence_status="inconclusive",
        )
        set_point_hard_flag(db, int(point["id"]), True, reason="test")

        rows = list_hard_points(db, curve_id=curve_id)

        assert len(rows) == 1
        assert int(rows[0]["rigorous_lower"]) == 5
        assert rows[0]["curve_exact_rank"] is None
    finally:
        db.close()




def test_denominator_provenance_labels_chart_and_stored_coordinates():
    provenance = _denominator_provenance({
        "chart_x_denominator": 7,
        "stored_x_denominator": 5,
        "requested_denominator_low": 101,
        "requested_denominator_high": 10000,
        "effective_denominator_low": 101,
        "effective_denominator_high": 1000,
    })
    assert provenance == {
        "chart_X_denominator": 7,
        "stored_x_denominator": 5,
        "requested_chart_denominator_low": 101,
        "requested_chart_denominator_high": 10000,
        "effective_chart_denominator_low": 101,
        "effective_chart_denominator_high": 1000,
    }
    assert _denominator_provenance({}) is None



def test_point_discovery_ledger_is_append_only(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = upsert_curve(db, family="discovery", parameter="1")
        point = upsert_point(
            db,
            curve_id=curve_id,
            x="0",
            y="0",
            source="search",
            exact_verified=True,
        )
        for center in ("0", "1"):
            record_point_discovery(
                db,
                curve_id=curve_id,
                point_id=int(point["id"]),
                source="auto_search_ratpoints",
                outcome="mapped",
                tier="test",
                center=center,
                scale="1",
                height=100,
                chart_x="0" if center == "0" else "-1",
                chart_w="0",
                stored_x="0",
                stored_y="0",
                exact_verified=True,
            )

        record_point_discovery(
            db,
            curve_id=curve_id,
            source="auto_search_ratpoints",
            outcome="map_back_error",
            tier="test",
            center="2",
            scale="1",
            height=100,
            chart_x="bad",
            chart_w="0",
            error_class="ValueError",
            error="injected map-back failure",
        )

        rows = list_point_discoveries(db, point_id=int(point["id"]))
        assert len(rows) == 2
        assert {row["center"] for row in rows} == {"0", "1"}
        assert all(row["outcome"] == "mapped" for row in rows)
        assert all(int(row["point_id"]) == int(point["id"]) for row in rows)

        failures = list_point_discoveries(
            db, curve_id=curve_id, outcome="map_back_error"
        )
        assert len(failures) == 1
        assert failures[0]["point_id"] is None
        assert failures[0]["chart_x"] == "bad"
        assert failures[0]["error_class"] == "ValueError"
    finally:
        db.close()
