from pathlib import Path
from types import SimpleNamespace

import pytest

from rank42 import pipeline_runner as runner
from rank42.candidate_generate import CANDIDATE_ENGINES
from rank42.candidates import create_pool, replace_pool_rows
from rank42.db import connect
from rank42.pipeline_catalog import validate_pipeline
from rank42.ui_pages.candidate_generate_page import _candidate_pipeline_choice


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_PAGE = ROOT / "rank42" / "ui_pages" / "candidate_generate_page.py"
PIPELINE_RUNNER = ROOT / "rank42" / "pipeline_runner.py"
CANDIDATE_GENERATE = ROOT / "rank42" / "candidate_generate.py"


def _arg_value(command, name):
    index = command.index(name)
    return command[index + 1]


def test_candidate_engine_contract_includes_sampled():
    assert CANDIDATE_ENGINES == ("sieve", "scalar", "sampled")


def test_generated_family_candidates_type_nagao_ranking_and_native_variant():
    source = CANDIDATE_GENERATE.read_text(encoding="utf-8")

    assert "rec['ranking_kind']='nagao'" in source
    assert "rec['ranking_value']=float(rec['score'])" in source
    assert "rec['score_provenance']['ranking_kind']='nagao'" in source
    assert "rec['native_variant_id']=chart.native_variant_id" in source
    assert "rec['native_variant_id']=variant.id" in source


def test_candidate_controls_serialize_to_candidate_only_pipeline():
    choice = _candidate_pipeline_choice(
        "demo-pool",
        "127,523",
        "10000,2000",
        1000,
        "annotate",
    )
    stages = choice["pipeline"]["stages"]

    assert choice["pipeline"]["target_mode"] == "family"
    assert choice["target_rank"] == 1
    assert [stage["id"] for stage in stages] == [
        "nagao_screen",
        "nagao_rescore",
        "corpus_filter",
    ]
    assert stages[0]["config"] == {"prime_bound": 127, "keep": 10000}
    assert stages[1]["config"] == {"prime_bound": 523, "keep": 2000}
    assert stages[2]["config"]["policy"] == "annotate"
    assert validate_pipeline("family", stages) == []


def test_candidate_controls_allow_progressive_nagao_rescore_rounds():
    choice = _candidate_pipeline_choice(
        "elkies-rank18",
        "200,500,1000,5000",
        "12000,2500,1000,500",
        500,
        "off",
    )
    stages = choice["pipeline"]["stages"]

    assert [stage["id"] for stage in stages] == [
        "nagao_screen",
        "nagao_rescore",
        "nagao_rescore",
        "nagao_rescore",
    ]
    assert [stage["config"]["prime_bound"] for stage in stages] == [
        200,
        500,
        1000,
        5000,
    ]
    assert [stage["config"]["keep"] for stage in stages] == [
        12000,
        2500,
        1000,
        500,
    ]
    assert validate_pipeline("family", stages) == []


@pytest.mark.parametrize(
    "bounds,keeps,top,message",
    [
        ("523,127", "10000,1000", 1000, "strictly increasing"),
        ("127,523", "1000,2000", 1000, "nonincreasing"),
        ("127,523", "10000", 1000, "must match"),
        ("127,523", "10000,500", 1000, "cannot exceed"),
    ],
)
def test_candidate_pipeline_rejects_invalid_screening_contract(
    bounds, keeps, top, message
):
    with pytest.raises(ValueError, match=message):
        _candidate_pipeline_choice("bad", bounds, keeps, top, "off")


def test_candidates_generate_surface_is_pipeline_owned():
    source = CANDIDATE_PAGE.read_text(encoding="utf-8")

    assert "launch_with_pipeline(" in source
    assert "run_config=run_config" in source
    assert "run_config['search_preset']=preset_provenance" in source
    assert "launch_metadata['search_preset']=preset_provenance" in source
    assert "search_preset_provenance(" in source
    assert "require_pipeline=True" in source
    assert "native_command=[]" in source
    assert "CANDIDATE_ENGINES" in source
    assert "engine=='sampled'" in source
    assert "'sample_count'" in source
    assert "'sample_seed'" in source
    assert "'output_pool_name':str(name)" in source
    assert ",'-m','rank42.candidate_generate'" not in source


def test_pipeline_candidate_generation_uses_frozen_target_controls(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    captured = {}

    monkeypatch.setattr(
        runner,
        "_family_candidate_defaults",
        lambda plugin, variant: {
            "a_min": -5,
            "a_max": 5,
            "b_min": 1,
            "b_max": 10,
            "engine": "sieve",
            "top": 50,
        },
    )
    monkeypatch.setattr(
        runner,
        "apply_search_command_features",
        lambda project_root, db, command, **kwargs: (list(command), []),
    )

    class Result:
        returncode = 0

    def fake_run(command, check=False):
        command = list(command)
        captured["command"] = command
        pool = create_pool(
            db,
            name=_arg_value(command, "--pool-name"),
            plugin_id="demo",
            plugin_version="1",
            family_spec="demo:family",
            generation={
                "pipeline_run_id": int(_arg_value(command, "--pipeline-run-id")),
            },
            status="generating",
        )
        replace_pool_rows(
            db,
            pool["id"],
            [
                {
                    "t": "1/2",
                    "a": 1,
                    "b": 2,
                    "score": 9.5,
                    "prime_bound": 523,
                    "score_provenance": {"algorithm": "fixture"},
                },
                {
                    "t": "2/3",
                    "a": 2,
                    "b": 3,
                    "score": 8.5,
                    "prime_bound": 523,
                    "score_provenance": {"algorithm": "fixture"},
                },
            ],
        )
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 127, "keep": 100}},
        {"id": "nagao_rescore", "config": {"prime_bound": 523, "keep": 20}},
    ]
    target = {
        "output_pool_name": "research-pool",
        "candidate_generation": {
            "a_min": -25,
            "a_max": 25,
            "b_min": 2,
            "b_max": 200,
            "engine": "sampled",
            "sample_count": 25000,
            "sample_seed": 1811,
            "top": 10,
            "chart_id": "chart-a",
        },
    }
    rows = runner._family_candidates(
        db,
        tmp_path,
        77,
        SimpleNamespace(id="demo"),
        SimpleNamespace(id="default"),
        stages,
        {},
        target=target,
    )

    command = captured["command"]
    assert _arg_value(command, "--pool-name") == "research-pool"
    assert _arg_value(command, "--pipeline-run-id") == "77"
    assert _arg_value(command, "--a-min") == "-25"
    assert _arg_value(command, "--a-max") == "25"
    assert _arg_value(command, "--b-min") == "2"
    assert _arg_value(command, "--b-max") == "200"
    assert _arg_value(command, "--stage-bounds") == "127,523"
    assert _arg_value(command, "--stage-keeps") == "100,20"
    assert _arg_value(command, "--engine") == "sampled"
    assert _arg_value(command, "--sample-count") == "25000"
    assert _arg_value(command, "--sample-seed") == "1811"
    assert _arg_value(command, "--chart") == "chart-a"
    assert _arg_value(command, "--top") == "10"
    assert [row["parameter"] for row in rows] == ["1/2", "2/3"]

    pool = db.execute(
        "SELECT generation_json FROM candidate_pools WHERE name='research-pool'"
    ).fetchone()
    assert '"pipeline_run_id": 77' in pool["generation_json"]


def test_pipeline_family_default_top_is_clipped_to_final_survivor_keep(
    tmp_path, monkeypatch
):
    db = connect(tmp_path / "rank42.db")
    captured = {}

    monkeypatch.setattr(
        runner,
        "_family_candidate_defaults",
        lambda plugin, variant: {
            "a_min": -3000,
            "a_max": 3000,
            "b_min": 1,
            "b_max": 300,
            "engine": "sieve",
            "top": 500,
        },
    )
    monkeypatch.setattr(
        runner,
        "apply_search_command_features",
        lambda project_root, db, command, **kwargs: (list(command), []),
    )

    class Result:
        returncode = 0

    def fake_run(command, check=False):
        command = list(command)
        captured["command"] = command
        pool = create_pool(
            db,
            name=_arg_value(command, "--pool-name"),
            plugin_id="c2xc4_diophantine_rank4",
            plugin_version="0.1.0",
            family_spec="c2xc4_diophantine_rank4",
            generation={
                "pipeline_run_id": int(_arg_value(command, "--pipeline-run-id")),
            },
            status="generating",
        )
        replace_pool_rows(
            db,
            pool["id"],
            [{
                "t": "15",
                "a": 15,
                "b": 1,
                "score": 4.0,
                "prime_bound": 1979,
                "score_provenance": {"algorithm": "fixture"},
            }],
        )
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    stages = [
        {"id": "nagao_screen", "config": {"prime_bound": 523, "keep": 8000}},
        {"id": "nagao_rescore", "config": {"prime_bound": 1979, "keep": 400}},
    ]
    rows = runner._family_candidates(
        db,
        tmp_path,
        3,
        SimpleNamespace(id="c2xc4_diophantine_rank4"),
        SimpleNamespace(id="default"),
        stages,
        {},
        target={},
    )

    command = captured["command"]
    assert _arg_value(command, "--stage-keeps") == "8000,400"
    assert _arg_value(command, "--top") == "400"
    assert [row["parameter"] for row in rows] == ["15"]


def test_candidate_only_lane_and_family_search_share_source_candidate_identity():
    source = PIPELINE_RUNNER.read_text(encoding="utf-8")

    assert 'if bool(run_config.get("candidate_only")):' in source
    assert "_run_candidate_generation_lane(" in source
    assert '"source_candidate_id": source_candidate_id' in source
    assert 'source_candidate_id=context.get("source_candidate_id")' in source
