import inspect
import json

import rank42.pipeline_runner as runner
from rank42.ui_pages import family_search, jobs_page


def test_torsion_pipeline_choice_is_valid_and_exact_gated():
    options = family_search._torsion_pipeline_options()
    assert options
    auto = next(rec for rec in options if rec["id"] == "auto")
    ids = [stage["id"] for stage in auto["stages"]]

    assert "exact_torsion" in ids
    assert ids.index("exact_torsion") < ids.index("integral_seed")

    choice = family_search._torsion_pipeline_choice("auto", 4)
    assert choice["pipeline"]["target_mode"] == "torsion"
    assert choice["target_rank"] == 4
    assert choice["pipeline"]["stages"] == auto["stages"]


def test_family_search_exposes_torsion_backend_without_family_adapter():
    source = inspect.getsource(family_search.render)

    assert 'if torsion_groups:' in source
    assert 'modes.append("Torsion")' in source
    assert 'if "family_search" in plugin.capabilities:' in source
    assert 'elif search_mode == "Torsion":' in source
    assert '"Exact torsion target:' in source
    assert '"Torsion pipeline"' in source
    assert 'launch_target_mode = "torsion" if launch_mode == "Torsion" else "family"' in source
    assert 'launch_target["torsion_group"] = str(torsion_group)' in source
    assert 'launch_target["include_secondary_providers"] = True' in source


def test_family_search_uses_target_style_two_column_setup():
    source = inspect.getsource(family_search.render)

    assert 'launch_col, guide_col = st.columns([1.12, 0.88])' in source
    assert 'section_title("Search setup"' in source
    assert 'section_title(' in source
    assert '"Search style"' in source
    assert 'with guide_col:' in source


def test_torsion_pool_provider_is_restricted_to_source_variant():
    providers = [
        {"plugin_id": "p", "variant_id": "v1", "provider_key": "p:v1"},
        {"plugin_id": "p", "variant_id": "v2", "provider_key": "p:v2"},
        {"plugin_id": "q", "variant_id": "v1", "provider_key": "q:v1"},
    ]

    got = runner._restrict_torsion_providers(
        providers,
        {"plugin_id": "p", "variant_id": "v2"},
    )
    assert got == [providers[1]]
    assert runner._restrict_torsion_providers(providers, {}) == providers


def test_jobs_paused_state_is_active_ui_work():
    paused = {
        "id": 291,
        "status": "stopped",
        "metadata_json": json.dumps({
            "pause_requested": True,
            "paused_at": "2026-09-24T15:00:00+00:00",
        }),
    }
    resumed = {
        "id": 291,
        "status": "stopped",
        "metadata_json": json.dumps({
            "pause_requested": True,
            "resumed_as_job_id": 292,
        }),
    }

    assert jobs_page._job_is_paused(paused) is True
    assert jobs_page._job_display_status(paused) == "paused"
    assert jobs_page._job_is_paused(resumed) is False


def test_jobs_active_page_counts_and_lists_paused_jobs():
    active = inspect.getsource(jobs_page._active)
    history = inspect.getsource(jobs_page._history)

    assert "paused_rows" in active
    assert 'c1, c2, c3, c4 = st.columns(4)' in active
    assert 'c2.metric("Paused", len(paused_rows))' in active
    assert 'ui_rows = [*running_rows, *paused_rows]' in active
    assert '"No UI-launched jobs are currently running or paused."' in active
    assert "and not job_is_paused(row)" in history
