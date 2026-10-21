from pathlib import Path

from rank42.pipeline_catalog import general_search_stages, validate_pipeline


ROOT = Path(__file__).resolve().parents[1]
GENERAL_PAGE = ROOT / "rank42" / "ui_pages" / "general_hunt.py"


def _source() -> str:
    return GENERAL_PAGE.read_text(encoding="utf-8")


def test_general_search_surface_requires_pipeline_execution():
    source = _source()

    assert 'target_mode="general"' in source
    assert "require_pipeline=True" in source
    assert 'builtin_label="Built-in Pipeline · Current General controls"' in source
    assert '"pipeline": {' in source
    assert '"target_mode": "general"' in source

    # SEARCH-R5 guard: the General page must not reconstruct or launch the
    # standalone legacy engine.  The legacy module remains temporarily for
    # parity/reference until the later removal chunk.
    assert "rank42.general_hunt" not in source
    assert "python -m rank42.general_hunt" not in source


def test_general_visible_controls_serialize_into_pipeline_target_and_stages():
    source = _source()

    for target_field in (
        '"pool_mode": mode',
        '"pool_size": pool',
        '"random_seed": 42',
        "u_min=umin",
        "u_max=umax",
        "v_min=vmin",
        "v_max=vmax",
        "a_min=amin",
        "a_max=amax",
        "b_min=bmin",
        "b_max=bmax",
    ):
        assert target_field in source

    stages = general_search_stages(
        nagao_bound=100,
        shortlist=100,
        ratpoints_stages="100,1000,10000",
        ratpoints_timeout=5,
        certificate_timeout=120,
        exact_candidates=64,
    )

    assert [stage["id"] for stage in stages] == [
        "nagao_screen",
        "integral_seed",
        "independence",
        "stop_goal",
    ]
    assert stages[0]["config"] == {"prime_bound": 100, "keep": 100}
    assert stages[1]["config"]["heights"] == [100, 1000, 10000]
    assert stages[1]["config"]["timeout"] == 5
    assert stages[2]["config"]["certificate_timeout"] == 120
    assert stages[2]["config"]["max_candidates"] == 64
    assert validate_pipeline("general", stages) == []


def test_general_surface_preserves_pipeline_launch_contract():
    source = _source()

    assert "launch_with_pipeline(" in source
    assert 'native_kind="general_hunt"' in source
    assert "native_command=[]" in source

    # native_kind is retained only as launch-surface/provenance metadata.
    # require_pipeline=True prevents this empty native command from executing.
    require_pos = source.index("require_pipeline=True")
    launch_pos = source.index("launch_with_pipeline(")
    assert require_pos > launch_pos


def test_general_embedded_surface_matches_auto_focus_width():
    source = _source()

    assert 'surface = st.container()' in source
    assert 'if embedded:' in source
    assert '_, surface, _ = st.columns([0.6, 2.8, 0.6])' in source
    assert 'with surface:' in source
    assert source.index('_, surface, _ = st.columns([0.6, 2.8, 0.6])') < source.index('with st.form("general-hunt"):')
