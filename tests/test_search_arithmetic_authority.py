from pathlib import Path

import rank42.auto_analyze as auto_analyze


def test_auto_analyze_delegates_global_arithmetic_to_shared_authority(monkeypatch):
    captured = {}

    def fake_compute(db, curve_id, E, *, source, method):
        captured.update(
            {
                "db": db,
                "curve_id": curve_id,
                "E": E,
                "source": source,
                "method": method,
            }
        )
        return {
            "values": {},
            "publication": {
                "conflicts": [],
                "published_fields": [],
            },
        }

    monkeypatch.setattr(
        auto_analyze,
        "compute_and_publish_curve_arithmetic",
        fake_compute,
    )

    db = object()
    Em = object()
    result = auto_analyze.enrich_candidate(
        db,
        17,
        object(),
        1,
        object(),
        Em,
        use_generic=False,
        certificate_timeout=30,
    )

    assert result is None
    assert captured == {
        "db": db,
        "curve_id": 17,
        "E": Em,
        "source": "auto_analyze",
        "method": "sage_global_minimal_model_invariants",
    }


def test_native_search_arithmetic_writers_use_shared_authority():
    root = Path(__file__).resolve().parents[1] / "rank42"
    auto_source = (root / "auto_analyze.py").read_text(encoding="utf-8")
    hunt_source = (root / "hunt_store.py").read_text(encoding="utf-8")

    assert "compute_and_publish_curve_arithmetic" in auto_source
    assert "compute_and_publish_curve_arithmetic" in hunt_source

    for source in (auto_source, hunt_source):
        assert ".conductor()" not in source
        assert ".root_number()" not in source
        assert "prime_divisors(" not in source

    assert "global_minimal_model()" not in hunt_source
