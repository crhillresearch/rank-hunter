import json

from rank42.candidates import create_pool, candidate_rows, replace_pool_rows
from rank42.db import connect, upsert_curve
from rank42.family_evidence import record_family_evidence
from rank42.rank_evidence import record_rank_evidence
from rank42.landscape_dimensions import (
    LANDSCAPE_DIMENSION_REGISTRY_HASH,
    LANDSCAPE_DIMENSION_REGISTRY_VERSION,
    LANDSCAPE_FEATURE_DEFINITION_VERSION,
    evaluate_landscape_feature,
    get_landscape_dimension,
    landscape_dimensions,
    normalize_landscape_feature,
    resolve_candidate_landscape_dimensions,
    resolve_curve_landscape_dimensions,
)


MODEL = ["0", "0", "0", "-1", "1"]


def _curve(db, *, parameter="5/7", native_parameter="5/7"):
    return upsert_curve(
        db,
        family="landscape-family",
        parameter=parameter,
        a_invariants_json=json.dumps(MODEL),
        descent_lower=1,
        conductor="37",
        discriminant="-37",
        bad_primes_json="[3, 37, 41]",
        root_number=-1,
        family_spec="json:landscape-family",
        family_sha256="family-sha",
        native_family_key="landscape-family:default",
        native_family_spec="json:landscape-family",
        native_parameter=native_parameter,
        chart_id="mobius-a",
        chart_parameter=parameter,
        chart_map_fingerprint="chart-sha",
        plugin_id="landscape-fixture",
        plugin_version="1.0",
    )


def test_landscape_registry_declares_provenance_and_rigor_metadata():
    dims = landscape_dimensions()
    ids = {rec["id"] for rec in dims}

    assert LANDSCAPE_DIMENSION_REGISTRY_VERSION == 1
    assert len(LANDSCAPE_DIMENSION_REGISTRY_HASH) == 64
    assert LANDSCAPE_FEATURE_DEFINITION_VERSION == 1
    assert len(ids) == len(dims)
    for rec in dims:
        assert rec["version"] >= 1
        assert rec["category"]
        assert rec["value_type"]
        assert rec["rigor_class"]
        assert rec["source_authority"]
        assert rec["provenance_policy"]

    for dimension_id in (
        "rank.rigorous_lower",
        "rank.rigorous_upper",
        "rank.exact_rank",
        "rank.generic_exact",
        "rank.specialization_surplus",
        "points.rigorous_witness_count",
        "points.unresolved_exact_count",
        "arithmetic.log_conductor",
        "arithmetic.root_number",
        "arithmetic.bad_prime_count",
        "parameter.native",
        "parameter.denominator",
        "parameter.chart_id",
        "mw.condition_number",
        "candidate.ranking_value",
        "candidate.ranking_kind",
    ):
        assert dimension_id in ids

    assert get_landscape_dimension("rank.rigorous_lower")["source_authority"] == "Curve Research State"
    assert get_landscape_dimension("arithmetic.log_conductor")["source_authority"] == "Curve Arithmetic"
    assert get_landscape_dimension("candidate.ranking_value")["source_authority"] == "Candidate ranking/provenance"


def test_curve_rank_dimensions_use_authoritative_reducer_and_exact_family_evidence(tmp_path):
    db = connect(tmp_path / "landscape-rank.db")
    try:
        curve_id = _curve(db)
        record_rank_evidence(
            db,
            curve_id=curve_id,
            model=MODEL,
            data={
                "engine": "landscape-test",
                "evidence_type": "rank_bounds",
                "status": "completed",
                "rigorous": True,
                "rigorous_lower": 6,
                "rigorous_upper": 8,
                "exact_rank": None,
                "conditional_analytic_upper": None,
                "conditional_mw_upper": None,
                "numerical_rank_signal": None,
                "assumptions": [],
                "points_found": [],
                "options": {},
            },
        )
        record_family_evidence(
            db,
            family_spec="json:landscape-family",
            family_sha256="family-sha",
            plugin_id="landscape-fixture",
            plugin_version="1.0",
            criterion="fixture_exact_generic_rank",
            specialization_parameter="0",
            generic_lower=3,
            generic_upper=3,
            exact_generic_rank=3,
            certificate={"kind": "fixture"},
            status="completed",
        )

        values = resolve_curve_landscape_dimensions(
            db,
            curve_id,
            dimension_ids=[
                "rank.rigorous_lower",
                "rank.rigorous_upper",
                "rank.exact_rank",
                "rank.generic_exact",
                "rank.specialization_surplus",
            ],
        )

        assert values["rank.rigorous_lower"]["value"] == 6
        assert values["rank.rigorous_upper"]["value"] == 8
        assert values["rank.exact_rank"]["value"] is None
        assert values["rank.generic_exact"]["value"] == 3
        assert values["rank.specialization_surplus"]["value"] == 3
        assert values["rank.specialization_surplus"]["provenance"]["family_evidence_exact"] == 3
        assert values["rank.rigorous_lower"]["rigor_class"] == "rigorous"
    finally:
        db.close()


def test_curve_arithmetic_parameter_and_point_dimensions_use_shared_read_authorities(tmp_path):
    db = connect(tmp_path / "landscape-arithmetic.db")
    try:
        curve_id = _curve(db, parameter="9/14", native_parameter="5/7")
        db.execute(
            """INSERT INTO points(
                   curve_id,x,y,source,role,exact_verified,independence_status,
                   rigorous_independent,metadata_json,created_at,updated_at
               ) VALUES
               (?, '0','1','fixture','rigorous_witness',1,'rigorous_independent',1,'{}','x','x'),
               (?, '1','1','fixture','candidate',1,'unknown',0,'{}','x','x')""",
            (curve_id, curve_id),
        )
        db.commit()

        values = resolve_curve_landscape_dimensions(db, curve_id)

        assert values["arithmetic.conductor"]["value"] == 37
        assert values["arithmetic.log_conductor"]["value"] is not None
        assert values["arithmetic.root_number"]["value"] == -1
        assert values["arithmetic.bad_prime_count"]["value"] == 3
        assert values["arithmetic.bad_primes"]["value"] == [3, 37, 41]
        assert values["points.rigorous_witness_count"]["value"] == 1
        assert values["points.unresolved_exact_count"]["value"] == 1
        assert values["parameter.native"]["value"] == "5/7"
        assert values["parameter.chart"]["value"] == "9/14"
        assert values["parameter.numerator_abs"]["value"] == 5
        assert values["parameter.denominator"]["value"] == 7
        assert values["parameter.chart_id"]["value"] == "mobius-a"
        assert values["parameter.family_key"]["value"] == "landscape-family:default"
        assert values["arithmetic.conductor"]["provenance"]["authority"] == "Curve Arithmetic"
    finally:
        db.close()


def test_candidate_dimensions_preserve_ranking_kind_provenance_and_native_chart_identity(tmp_path):
    db = connect(tmp_path / "landscape-candidate.db")
    try:
        pool = create_pool(
            db,
            name="landscape-pool",
            plugin_id="landscape-fixture",
            plugin_version="1.0",
            family_spec="json:landscape-family",
            family_sha256="family-sha",
            adapter_sha256="adapter-sha",
            plugin_manifest_sha256="manifest-sha",
            generation={"engine": "fixture"},
        )
        replace_pool_rows(
            db,
            int(pool["id"]),
            [{
                "parameter": "9/14",
                "score": 12.5,
                "ranking_kind": "nagao",
                "ranking_value": 12.5,
                "score_provenance": {
                    "ranking_kind": "nagao",
                    "algorithm": "fixture-nagao",
                },
                "native_family_key": "landscape-family:default",
                "native_family_spec": "json:landscape-family",
                "native_parameter": "5/7",
                "chart_id": "mobius-a",
                "chart_parameter": "9/14",
                "chart_map_fingerprint": "chart-sha",
            }],
        )
        row = candidate_rows(db, int(pool["id"]), limit=1)[0]
        values = resolve_candidate_landscape_dimensions(row, source_kind="candidate_pool")

        assert values["candidate.ranking_kind"]["value"] == "nagao"
        assert values["candidate.ranking_value"]["value"] == 12.5
        assert values["candidate.ranking_value"]["provenance"]["score_provenance"]["algorithm"] == "fixture-nagao"
        assert values["candidate.native_parameter"]["value"] == "5/7"
        assert values["candidate.chart_parameter"]["value"] == "9/14"
        assert values["candidate.family_key"]["value"] == "landscape-family:default"
        assert values["candidate.chart_id"]["value"] == "mobius-a"
    finally:
        db.close()


def test_prime_cluster_features_are_generic_user_configuration(tmp_path):
    db = connect(tmp_path / "landscape-features.db")
    try:
        curve_id = _curve(db)
        values = resolve_curve_landscape_dimensions(db, curve_id)

        contains = normalize_landscape_feature(
            {"kind": "bad_prime_contains", "prime": 41}
        )
        cluster = normalize_landscape_feature(
            {"kind": "bad_prime_superset", "primes": [3, 41]}
        )
        denominator = normalize_landscape_feature(
            {"kind": "denominator_divisible_by", "prime": 7}
        )
        sign = normalize_landscape_feature(
            {"kind": "root_number_equals", "value": -1}
        )

        assert contains["feature_version"] == LANDSCAPE_FEATURE_DEFINITION_VERSION
        assert cluster["feature_version"] == LANDSCAPE_FEATURE_DEFINITION_VERSION
        assert evaluate_landscape_feature(contains, values) is True
        assert evaluate_landscape_feature(cluster, values) is True
        assert evaluate_landscape_feature(denominator, values) is True
        assert evaluate_landscape_feature(sign, values) is True

        source = __import__("rank42.landscape_dimensions", fromlist=["x"])
        text = open(source.__file__, "r", encoding="utf-8").read()
        assert "{29,37,41,73}" not in text.replace(" ", "")
        assert "{3,5,7,13}" not in text.replace(" ", "")
    finally:
        db.close()


def test_landscape_resolution_is_read_only_and_never_promotes_evidence(tmp_path):
    db = connect(tmp_path / "landscape-readonly.db")
    try:
        curve_id = _curve(db)
        before = db.execute(
            "SELECT COUNT(*) FROM rank_evidence WHERE curve_id=?",
            (curve_id,),
        ).fetchone()[0]
        traced = []
        db.set_trace_callback(traced.append)

        resolve_curve_landscape_dimensions(db, curve_id)

        after = db.execute(
            "SELECT COUNT(*) FROM rank_evidence WHERE curve_id=?",
            (curve_id,),
        ).fetchone()[0]
        writes = [
            stmt for stmt in traced
            if stmt.lstrip().upper().startswith(
                ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
            )
        ]
        assert before == after
        assert writes == []
    finally:
        db.close()
