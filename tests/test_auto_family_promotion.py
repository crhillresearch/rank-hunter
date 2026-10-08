import json

from sage.all import EllipticCurve, QQ

import rank42.auto_search as auto_search
import rank42.torsion as torsion
from rank42.db import connect, get_curve, update_curve, upsert_curve
from rank42.rank_evidence import list_rank_evidence, record_rank_evidence


MODEL = ["0", "0", "0", "-1", "1"]


class _Family:
    def certified_specialization_metadata(self, _t):
        return {"archive": "preserved-seed"}

    def generic_section_metadata(self, _t):
        return [{"section": "A"}, {"section": "B"}]


def _curve(db, parameter="1"):
    curve_id = upsert_curve(
        db,
        family="auto-family-promotion",
        parameter=str(parameter),
        a_invariants_json=json.dumps(MODEL),
    )
    return curve_id


def _E():
    return EllipticCurve(QQ, [QQ(v) for v in MODEL])


def _certificate(tag):
    return {
        "independent": True,
        "status": "certified_independent",
        "certificate": {"method": tag, "primes": [5, 7]},
    }


def test_auto_specialization_seed_promotes_through_central_service(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        points = [E(0, 1), E(1, 1)]

        monkeypatch.setattr(
            auto_search,
            "_family_named_points_on_stored_model",
            lambda *_args, **_kwargs: points,
        )
        monkeypatch.setattr(
            auto_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: _certificate("specialization-test"),
        )

        result = auto_search._attach_family_specialization_seed(
            db,
            curve_id,
            _Family(),
            "1",
            E,
            E,
            5,
        )

        assert result["specialization_seed_points"] == 2
        assert result["specialization_seed_verified"] is True
        assert result["specialization_seed_certificate"] == {
            "method": "specialization-test",
            "primes": [5, 7],
        }
        assert result["growth"] == 2
        assert result["rigorous_lower"] == 2

        curve = get_curve(db, curve_id)
        assert int(curve["descent_lower"]) == 2
        assert curve["status"] == "proven_lower"

        evidence = list_rank_evidence(db, curve_id)
        subgroup = next(row for row in evidence if row["evidence_type"] == "certified_subgroup")
        assert subgroup["engine"] == "auto_family_specialization_certificate"
        assert int(subgroup["rigorous_lower"]) == 2

        rows = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert len(rows) == 2
        for index, row in enumerate(rows, 1):
            assert row["role"] == "rigorous_witness"
            assert int(row["rigorous_independent"]) == 1
            assert row["search_ref"] == "auto:family-specialization-certificate:exact"
            metadata = json.loads(row["metadata_json"])
            assert metadata["archive"] == "preserved-seed"
            assert metadata["certificate_point_index"] == index
            assert metadata["rank_hunter_recertified"] is True
            assert metadata["certificate"]["method"] == "specialization-test"
    finally:
        db.close()


def test_family_seed_does_not_treat_generic_lower_as_specialization_proof(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "generic-only")
        update_curve(db, curve_id, generic_lower=10)
        E = _E()
        points = [E(0, 1), E(1, 1)]

        monkeypatch.setattr(
            auto_search,
            "_family_named_points_on_stored_model",
            lambda *_args, **_kwargs: points,
        )
        monkeypatch.setattr(
            auto_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: _certificate(
                "generic-is-not-specialization-proof"
            ),
        )

        result = auto_search._attach_family_specialization_seed(
            db,
            curve_id,
            _Family(),
            "1",
            E,
            E,
            5,
        )

        assert result["certificate_status"] == "certified"
        assert result["specialization_seed_verified"] is True
        assert result["growth"] == 2
        assert result["specialization_rigorous_lower"] == 2
        assert result["rigorous_lower"] == 2
        assert result["research_rigorous_lower"] == 10
    finally:
        db.close()


def test_family_seed_uses_structured_specialization_lower_when_columns_lag(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "structured-seed")
        E = _E()
        points = [E(0, 1), E(1, 1)]
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "structured-lower",
                "evidence_type": "certified_subgroup",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 3,
                "rigorous_upper": None,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )
        assert get_curve(db, curve_id)["descent_lower"] is None

        monkeypatch.setattr(
            auto_search,
            "_family_named_points_on_stored_model",
            lambda *_args, **_kwargs: points,
        )
        monkeypatch.setattr(
            auto_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError(
                    "structured specialization lower should make seed recertification unnecessary"
                )
            ),
        )

        result = auto_search._attach_family_specialization_seed(
            db,
            curve_id,
            _Family(),
            "1",
            E,
            E,
            5,
        )

        assert result["certificate_status"] == "not_needed"
        assert result["attempts"] == 0
        assert result["growth"] == 0
        assert result["rigorous_lower"] == 3
        assert result["specialization_rigorous_lower"] == 3
        assert result["research_rigorous_lower"] == 3
    finally:
        db.close()


def test_family_baseline_uses_structured_specialization_lower_for_bundle_decision(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db, "structured-baseline")
        E = _E()
        points = [E(0, 1), E(1, 1)]
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "structured-lower",
                "evidence_type": "certified_subgroup",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 3,
                "rigorous_upper": None,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )
        assert get_curve(db, curve_id)["descent_lower"] is None

        monkeypatch.setattr(
            auto_search,
            "_family_points_on_stored_model",
            lambda *_args, **_kwargs: points,
        )
        monkeypatch.setattr(
            auto_search,
            "_family_named_points_on_stored_model",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            auto_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError(
                    "structured specialization lower should make bundle recertification unnecessary"
                )
            ),
        )
        monkeypatch.setattr(
            auto_search,
            "certify_ledger_growth",
            lambda *_args, **_kwargs: {
                "attempts": 0,
                "growth": 0,
                "rigorous_lower": 0,
                "basis_complete": True,
            },
        )

        result = auto_search._attach_family_baseline(
            db,
            curve_id,
            _Family(),
            "1",
            E,
            E,
            5,
            8,
        )

        assert result["bundle_certificate_status"] == "not_needed"
        assert result["rigorous_lower"] == 3
        assert result["specialization_rigorous_lower"] == 3
        assert result["research_rigorous_lower"] == 3
    finally:
        db.close()


def test_auto_generic_family_bundle_promotes_with_per_section_provenance(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        monkeypatch.setattr(torsion, "enrich_retained_curve_torsion", lambda *_args, **_kwargs: None)
        curve_id = _curve(db)
        E = _E()
        points = [E(0, 1), E(1, 1)]

        monkeypatch.setattr(
            auto_search,
            "_family_points_on_stored_model",
            lambda *_args, **_kwargs: points,
        )
        monkeypatch.setattr(
            auto_search,
            "_family_named_points_on_stored_model",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            auto_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: _certificate("family-bundle-test"),
        )

        def _unexpected_fallback(*_args, **_kwargs):
            raise AssertionError("certify_ledger_growth fallback should not run after bundle certification")

        monkeypatch.setattr(auto_search, "certify_ledger_growth", _unexpected_fallback)

        result = auto_search._attach_family_baseline(
            db,
            curve_id,
            _Family(),
            "1",
            E,
            E,
            5,
            8,
        )

        assert result["section_points"] == 2
        assert result["specialization_seed_points"] == 0
        assert result["specialization_seed_verified"] is False
        assert result["bundle_independent"] is True
        assert result["attempts"] == 1
        assert result["growth"] == 2
        assert result["rigorous_lower"] == 2

        evidence = list_rank_evidence(db, curve_id)
        subgroup = next(row for row in evidence if row["evidence_type"] == "certified_subgroup")
        assert subgroup["engine"] == "auto_family_section_bundle"
        assert int(subgroup["rigorous_lower"]) == 2

        rows = db.execute(
            "SELECT * FROM points WHERE curve_id=? ORDER BY id",
            (curve_id,),
        ).fetchall()
        assert [json.loads(row["metadata_json"])["section"] for row in rows] == ["A", "B"]
        assert [json.loads(row["metadata_json"])["section_index"] for row in rows] == [0, 1]
        assert all(row["search_ref"] == "auto:family-baseline:bundle" for row in rows)
        assert all(row["role"] == "rigorous_witness" for row in rows)
        assert all(int(row["rigorous_independent"]) == 1 for row in rows)
    finally:
        db.close()


def test_auto_family_bundle_inconclusive_keeps_ledger_fallback(tmp_path, monkeypatch):
    db = connect(tmp_path / "rank42.db")
    try:
        curve_id = _curve(db)
        E = _E()
        P = E(0, 1)

        monkeypatch.setattr(
            auto_search,
            "_family_points_on_stored_model",
            lambda *_args, **_kwargs: [P],
        )
        monkeypatch.setattr(
            auto_search,
            "_family_named_points_on_stored_model",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            auto_search,
            "run_exact_certificate",
            lambda *_args, **_kwargs: {
                "independent": False,
                "status": "inconclusive",
                "certificate": {"method": "family-bundle-test"},
            },
        )

        calls = []
        def _fallback(*_args, **kwargs):
            calls.append(kwargs)
            return {
                "attempts": 3,
                "growth": 1,
                "rigorous_lower": 1,
                "basis_complete": True,
            }

        monkeypatch.setattr(auto_search, "certify_ledger_growth", _fallback)

        result = auto_search._attach_family_baseline(
            db,
            curve_id,
            _Family(),
            "1",
            E,
            E,
            7,
            4,
        )

        assert len(calls) == 1
        assert calls[0]["source"] == "auto_family_section"
        assert calls[0]["search_ref"] == "auto:family-baseline:exact"
        assert calls[0]["certificate_timeout"] == 7
        assert calls[0]["max_candidates"] == 4
        assert result["bundle_independent"] is False
        assert result["attempts"] == 3
        assert result["growth"] == 1
        assert result["rigorous_lower"] == 1
    finally:
        db.close()
