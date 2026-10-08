import inspect
import json

from rank42.catalog import import_icarm_payload
from rank42.db import connect, now, upsert_curve, update_curve
from rank42.lattice_store import store_lattice
from rank42.rank_evidence import record_rank_evidence
from rank42.torsion import ensure_torsion_schema
from rank42.ui_pages.dashboard_sections import (
    _lattice_data,
    _torsion_data,
    _torsion_research_data,
    _torsion_sort_key,
    _unresolved_data,
)


MODEL = ["0", "0", "0", "-1", "0"]


def _curve(db, parameter, **fields):
    curve_id = upsert_curve(
        db,
        family="dashboard-sections-authority",
        parameter=str(parameter),
    )
    update_curve(db, curve_id, a_invariants_json=json.dumps(MODEL), **fields)
    return curve_id


def _evidence(db, curve_id, *, lower=None, upper=None, key):
    return record_rank_evidence(
        db,
        curve_id=curve_id,
        model=MODEL,
        data={
            "engine": "dashboard-sections-authority-test",
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


def _set_torsion(db, curve_id, *, label, order, invariants):
    ensure_torsion_schema(db)
    db.execute(
        """UPDATE curves
           SET torsion_label=?, torsion_order=?, torsion_invariants_json=?,
               torsion_computed_at=?, torsion_algorithm=?, torsion_error=NULL
           WHERE id=?""",
        (
            str(label),
            int(order),
            json.dumps(list(invariants)),
            "2026-09-25T00:00:00+00:00",
            "sage.torsion_subgroup",
            int(curve_id),
        ),
    )
    db.commit()


def _icarm_match(db, curve_id, *, source_id, conductor):
    import_icarm_payload(
        db,
        {
            "count": 1,
            "curves": [
                {
                    "id": int(source_id),
                    "ainvs": MODEL,
                    "rank_lower_bound": 1,
                    "points": [],
                    "conductor": str(conductor),
                }
            ],
        },
    )
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


def test_dashboard_torsion_data_uses_authoritative_exact_lower_and_conflict_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        exact = _curve(db, "exact")
        lower = _curve(db, "lower")
        conflict = _curve(db, "conflict")
        _set_torsion(db, exact, label="C2", order=2, invariants=[2])
        _set_torsion(db, lower, label="C3", order=3, invariants=[3])
        _set_torsion(db, conflict, label="C3", order=3, invariants=[3])

        _evidence(db, exact, lower=6, upper=6, key="torsion-exact")
        _evidence(db, lower, lower=5, key="torsion-lower")
        _evidence(db, conflict, lower=7, key="torsion-conflict-lower")
        _evidence(db, conflict, upper=6, key="torsion-conflict-upper")

        exact_rows, record_rows = _torsion_data(db)
        exact_by_key = {
            (row["torsion_label"], int(row["exact_rank"])): int(row["curve_count"])
            for row in exact_rows
        }
        records = {row["torsion_label"]: row for row in record_rows}

        assert exact_by_key == {("C2", 6): 1}
        assert int(records["C2"]["best_exact"]) == 6
        assert int(records["C2"]["best_lower"]) == 6
        assert int(records["C3"]["best_lower"]) == 5
        assert int(records["C3"]["retained_curves"]) == 2
        assert int(records["C3"]["rank_conflicts"]) == 1
    finally:
        db.close()


def test_dashboard_torsion_research_data_adds_coverage_distribution_and_followup(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        exact = _curve(db, "torsion-overview-exact")
        lower = _curve(db, "torsion-overview-lower")
        conflict = _curve(db, "torsion-overview-conflict")
        timeout = _curve(db, "torsion-overview-timeout")

        _set_torsion(db, exact, label="C2", order=2, invariants=[2])
        _set_torsion(db, lower, label="C3", order=3, invariants=[3])
        _set_torsion(db, conflict, label="C3", order=3, invariants=[3])
        ensure_torsion_schema(db)
        db.execute(
            "UPDATE curves SET torsion_error=? WHERE id=?",
            ("timeout after 20 seconds", int(timeout)),
        )
        db.commit()

        _evidence(db, exact, lower=6, upper=6, key="torsion-overview-exact-rank")
        _evidence(db, lower, lower=5, key="torsion-overview-lower-rank")
        _evidence(db, conflict, lower=7, key="torsion-overview-conflict-lower")
        _evidence(db, conflict, upper=6, key="torsion-overview-conflict-upper")

        data = _torsion_research_data(db)
        overview = data["overview"]
        distribution = {
            row["torsion_label"]: row
            for row in data["distribution_rows"]
        }
        records = {
            row["torsion_label"]: row
            for row in data["record_rows"]
        }

        assert int(overview["curve_count"]) == 4
        assert int(overview["known_torsion"]) == 3
        assert float(overview["coverage_pct"]) == 75.0
        assert int(overview["distinct_groups"]) == 2
        assert int(overview["missing_torsion"]) == 1
        assert int(overview["torsion_errors"]) == 1
        assert int(overview["torsion_timeouts"]) == 1
        assert int(overview["rank_conflicts"]) == 1

        assert int(distribution["C2"]["curve_count"]) == 1
        assert int(distribution["C2"]["exact_count"]) == 1
        assert int(distribution["C2"]["lower_only_count"]) == 0
        assert int(distribution["C3"]["curve_count"]) == 2
        assert int(distribution["C3"]["exact_count"]) == 0
        assert int(distribution["C3"]["lower_only_count"]) == 1
        assert int(distribution["C3"]["rank_conflicts"]) == 1

        assert int(records["C3"]["best_lower"]) == 5
        assert int(records["C3"]["best_lower_only"]) == 5
        assert int(records["C3"]["best_lower_only_curve_id"]) == lower

        signals = data["signals"]
        assert [int(row["curve_id"]) for row in signals["failure_rows"]] == [timeout]
        assert [row["torsion_label"] for row in signals["conflict_groups"]] == ["C3"]
        assert records["C3"] in signals["lower_only_leaders"]
    finally:
        db.close()


def test_dashboard_torsion_uses_canonical_mazur_group_order():
    assert _torsion_sort_key("Trivial", 1) < _torsion_sort_key("C2", 2)
    assert _torsion_sort_key("C3", 3) < _torsion_sort_key("C2 × C2", 4)
    assert _torsion_sort_key("C12", 12) < _torsion_sort_key("C2 × C8", 16)


def test_dashboard_torsion_distribution_is_chart_not_legacy_table():
    source = inspect.getsource(
        __import__(
            "rank42.ui_pages.dashboard_sections",
            fromlist=["_render_torsion_distribution"],
        )._render_torsion_distribution
    )

    assert "rh-torsion-distribution-chart" in source
    assert "rh-torsion-dist-track" in source
    assert "rh-torsion-dist-exact" in source
    assert "rh-torsion-dist-lower" in source
    assert "rh-torsion-distribution-table" not in source
    assert "rank_conflicts" in source


def test_dashboard_torsion_research_never_computes_torsion_on_render():
    source = inspect.getsource(_torsion_research_data)

    assert "compute_torsion_data" not in source
    assert "compute_and_store_torsion" not in source
    assert "torsion_subgroup" not in source
    assert "curve_arithmetic_state_map" in source
    assert "list_curve_research_states" in source


def test_dashboard_torsion_data_is_read_only_and_does_not_create_schema(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        traced = []
        db.set_trace_callback(traced.append)

        exact_rows, record_rows = _torsion_data(db)

        assert exact_rows == []
        assert record_rows == []
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


def test_dashboard_unresolved_uses_authoritative_interval_and_conductor_reference(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "interval")
        _evidence(db, curve_id, lower=4, key="unresolved-lower")
        _evidence(db, curve_id, upper=7, key="unresolved-upper")
        _icarm_match(db, curve_id, source_id="995", conductor=109)

        row = next(item for item in _unresolved_data(db) if int(item["curve_id"]) == curve_id)

        assert int(row["lower"]) == 4
        assert int(row["upper"]) == 7
        assert int(row["gap"]) == 3
        assert int(row["conductor"]) == 109
        assert row["conductor_source"] == "ICARM reference"
    finally:
        db.close()


def test_dashboard_unresolved_keeps_local_conductor_and_marks_reference_conflict(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "local", conductor="127")
        _evidence(db, curve_id, lower=5, key="unresolved-local-lower")
        _icarm_match(db, curve_id, source_id="996", conductor=131)

        row = next(item for item in _unresolved_data(db) if int(item["curve_id"]) == curve_id)

        assert int(row["conductor"]) == 127
        assert row["conductor_source"] == "local · conflict"
        assert row["upper"] is None
        assert row["gap"] is None
    finally:
        db.close()


def test_dashboard_lattice_rows_use_authoritative_evidence_only_rank_state(tmp_path):
    db = connect(tmp_path / "rank42.db")
    try:
        exact = _curve(db, "lattice-exact")
        conflict = _curve(db, "lattice-conflict")
        _evidence(db, exact, lower=5, upper=5, key="lattice-exact-rank")
        _evidence(db, conflict, lower=7, key="lattice-conflict-lower")
        _evidence(db, conflict, upper=6, key="lattice-conflict-upper")

        store_lattice(
            db,
            curve_id=exact,
            source="test",
            basis=[["0", "1"]],
            gram=[["1.0"]],
            precision_bits=128,
            determinant="1.0",
            min_eigenvalue="1.0",
            positive_definite_screen=True,
            status="screened",
        )
        store_lattice(
            db,
            curve_id=conflict,
            source="test",
            basis=[["1", "1"], ["2", "3"]],
            gram=[["1.0", "0.0"], ["0.0", "2.0"]],
            precision_bits=128,
            determinant="2.0",
            min_eigenvalue="1.0",
            positive_definite_screen=True,
            status="screened",
        )

        rows = {int(row["curve_id"]): row for row in _lattice_data(db)}

        assert rows[exact]["evidence"] == "= 5"
        assert rows[conflict]["evidence"] == "conflict ≥ 7 / ≤ 6"
        assert int(rows[conflict]["basis_count"]) == 2
    finally:
        db.close()
