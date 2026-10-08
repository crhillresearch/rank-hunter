from pathlib import Path
from types import SimpleNamespace

from rank42 import constructive_family as constructive


def _context(adapter_path):
    return {
        "pipeline_run_id": 77,
        "pipeline_candidate_id": 123,
        "plugin": SimpleNamespace(
            id="constructive-test",
            version="1.2.3",
            adapter_path=Path(adapter_path),
        ),
        "variant": SimpleNamespace(id="v1", family_spec="json:test"),
        "parameter": "5/7",
        "curve_id": 11,
        "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
    }


def test_section_hook_hard_timeout_kills_sleeping_plugin(tmp_path):
    adapter = tmp_path / "slow_constructive.py"
    adapter.write_text(
        "import time\n"
        "def derive_pipeline_section_shell(payload):\n"
        "    time.sleep(10)\n"
        "    return {'status':'completed','sections':[]}\n",
        encoding="utf-8",
    )
    result = constructive.section_height_shell(
        context=_context(adapter),
        config={"timeout": 1},
    )
    assert result["status"] == "timeout"
    assert result["sections"] == []
    assert result["hard_isolated"] is True
    assert result["timeout_seconds"] == 1
    attempt = result["constructive_attempt"]
    assert attempt["status"] == "timeout"
    assert attempt["timeout_seconds"] == 1
    assert attempt["plugin_id"] == "constructive-test"
    assert attempt["plugin_version"] == "1.2.3"
    assert attempt["hook_name"] == "derive_pipeline_section_shell"
    assert attempt["retryable"] is True


def test_constructive_missing_hook_is_isolated_unsupported(tmp_path):
    adapter = tmp_path / "missing_hook.py"
    adapter.write_text("VALUE = 1\n", encoding="utf-8")
    result = constructive.section_height_shell(
        context=_context(adapter),
        config={"timeout": 3},
    )
    assert result["status"] == "unsupported"
    assert result["sections"] == []
    assert result["hard_isolated"] is True
    attempt = result["constructive_attempt"]
    assert attempt["status"] == "unsupported"
    assert attempt["hook_name"] == "derive_pipeline_section_shell"


def test_constructive_attempt_preserves_checkpoint_retry_and_fingerprint(
    monkeypatch,
):
    plugin = SimpleNamespace(id="demo", version="9.9", adapter_path="adapter.py")
    context = {
        **_context("adapter.py"),
        "plugin": plugin,
    }
    monkeypatch.setattr(
        constructive,
        "plugin_fingerprints",
        lambda plugin_arg, variant_arg: {
            "plugin_manifest_sha256": "manifest",
            "family_sha256": "family",
            "adapter_sha256": "adapter",
        },
    )
    monkeypatch.setattr(
        constructive,
        "run_isolated_plugin_hook",
        lambda plugin_arg, hook_name, payload, timeout: {
            "status": "partial",
            "sections": [],
            "checkpoint": {"shell_index": 4},
            "retry_token": "retry-abc",
            "retryable": True,
            "_isolated_hook": {
                "runtime_seconds": 2.5,
                "worker_exit_code": 0,
            },
        },
    )
    result = constructive.section_height_shell(
        context=context,
        config={"timeout": 7},
    )
    assert result["status"] == "partial"
    attempt = result["constructive_attempt"]
    assert attempt["runtime_seconds"] == 2.5
    assert attempt["budget"] == {"wall_clock_seconds": 7}
    assert attempt["checkpoint"] == {"shell_index": 4}
    assert attempt["retry_token"] == "retry-abc"
    assert attempt["fingerprints"]["adapter_sha256"] == "adapter"


def test_user_stopped_status_is_preserved(monkeypatch):
    monkeypatch.setattr(
        constructive,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "user_stopped",
            "sections": [],
            "reason": "fixture stop",
            "_isolated_hook": {
                "runtime_seconds": 0.2,
                "worker_exit_code": 0,
            },
        },
    )
    result = constructive.section_height_shell(
        context=_context("adapter.py"),
        config={"timeout": 4},
    )
    assert result["status"] == "user_stopped"
    assert result["reason"] == "fixture stop"
    assert result["constructive_attempt"]["status"] == "user_stopped"
    assert result["constructive_attempt"]["retryable"] is False


def test_trace_and_division_timeout_statuses_preserve_hook_identity(
    monkeypatch,
):
    calls = []

    def timed_out(plugin, hook_name, payload, timeout):
        calls.append((hook_name, timeout))
        key = (
            "trace_sections"
            if hook_name == "derive_pipeline_trace_sections"
            else "conditions"
        )
        return {
            "status": "timeout",
            "reason": "fixture timeout",
            key: [],
            "_isolated_hook": {
                "runtime_seconds": float(timeout),
                "worker_exit_code": -15,
            },
        }

    monkeypatch.setattr(
        constructive, "run_isolated_plugin_hook", timed_out
    )
    section = {
        "id": "s1",
        "artifact_hash": "section-hash",
        "exact_construction": True,
    }
    trace_result = constructive.trace_section_constructor(
        context=_context("adapter.py"),
        config={"timeout": 6, "extension_degree": 2},
        section_shell={"sections": [section]},
    )
    trace = {
        "id": "tr1",
        "artifact_hash": "trace-hash",
        "exact_construction": True,
    }
    division_result = constructive.forced_bisection_constructor(
        context=_context("adapter.py"),
        config={
            "timeout": 8,
            "division": 2,
            "slope_mode": "forced_tangent",
        },
        trace_state={"trace_sections": [trace]},
    )

    assert trace_result["status"] == "timeout"
    assert trace_result["constructive_attempt"]["hook_name"] == (
        "derive_pipeline_trace_sections"
    )
    assert division_result["status"] == "timeout"
    assert division_result["constructive_attempt"]["hook_name"] == (
        "derive_pipeline_bisection_conditions"
    )
    assert calls == [
        ("derive_pipeline_trace_sections", 6),
        ("derive_pipeline_bisection_conditions", 8),
    ]


def test_isolated_worker_preserves_legacy_constructive_list_result(tmp_path):
    adapter = tmp_path / "legacy_list.py"
    adapter.write_text(
        "def derive_pipeline_section_shell(payload):\n"
        "    return [{'id': 'legacy-section'}]\n",
        encoding="utf-8",
    )
    result = constructive.section_height_shell(
        context=_context(adapter),
        config={"timeout": 5},
    )
    assert result["status"] == "completed"
    assert result["section_count"] == 1
    assert result["sections"][0]["id"] == "legacy-section"
    assert result["sections"][0]["exact_construction"] is False
    assert result["constructive_attempt"]["status"] == "completed"
