from types import SimpleNamespace

from rank42 import constructive_family as constructive


def _context():
    return {
        "plugin": SimpleNamespace(id="demo", adapter_path="adapter.py"),
        "variant": SimpleNamespace(id="v1", family_spec="json:demo"),
        "parameter": "5/7",
        "curve_id": 11,
        "E": SimpleNamespace(a_invariants=lambda: [0, 0, 0, 1, 1]),
    }


def _patch_adapter(monkeypatch, adapter):
    def isolated(plugin, hook_name, payload, timeout):
        hook = getattr(adapter, hook_name, None)
        if not callable(hook):
            return {
                "status": "unsupported",
                "reason": f"plugin adapter does not define {hook_name}",
                "_isolated_hook": {
                    "runtime_seconds": 0.001,
                    "worker_exit_code": 0,
                },
            }
        raw = hook(payload)
        if isinstance(raw, dict):
            result = dict(raw)
            result.setdefault("status", "completed")
        elif isinstance(raw, (list, tuple)):
            key = {
                "derive_pipeline_section_shell": "sections",
                "derive_pipeline_trace_sections": "trace_sections",
                "derive_pipeline_bisection_conditions": "conditions",
            }[hook_name]
            result = {
                "status": "completed",
                key: list(raw),
                "legacy_list_result": True,
            }
        elif raw is None:
            result = {
                "status": "inconclusive",
                "reason": f"{hook_name} returned no result",
            }
        else:
            return {
                "status": "error",
                "reason": "fixture hook returned malformed result",
                "error": repr(raw),
            }
        result["_isolated_hook"] = {
            "runtime_seconds": 0.001,
            "worker_exit_code": 0,
        }
        return result

    monkeypatch.setattr(
        constructive, "run_isolated_plugin_hook", isolated
    )


def test_constructive_family_hook_chain(monkeypatch):
    class Adapter:
        @staticmethod
        def derive_pipeline_section_shell(request):
            assert request["height"] == 10
            assert request["pipeline_run_id"] is None
            assert request["pipeline_candidate_id"] is None
            return {"sections": [{"id": "s1", "height": 10}]}

        @staticmethod
        def derive_pipeline_trace_sections(request):
            assert request["sections"][0]["id"] == "s1"
            return {"trace_sections": [{"id": "tr1"}]}

        @staticmethod
        def derive_pipeline_bisection_conditions(request):
            assert request["trace_sections"][0]["id"] == "tr1"
            return {"conditions": [{"id": "b1", "equation": "z^2=F(t)"}]}

    _patch_adapter(monkeypatch, Adapter)
    shell = constructive.section_height_shell(
        context=_context(), config={"height": 10, "height_mode": "exact"}
    )
    trace = constructive.trace_section_constructor(
        context=_context(), config={}, section_shell=shell
    )
    bisect = constructive.forced_bisection_constructor(
        context=_context(), config={}, trace_state=trace
    )
    assert shell["section_count"] == 1
    assert shell["height_kind"] == "family_enumeration_shell"
    assert shell["height_normalization"] == "family_defined"
    assert shell["sections"][0]["exact_construction"] is False
    assert shell["sections"][0]["verification_status"] == "unverified"
    assert trace["trace_count"] == 1
    assert bisect["condition_count"] == 1
    assert bisect["conditions"][0]["rank_claim"] is False


def test_constructive_family_missing_hook_is_explicit(monkeypatch):
    monkeypatch.setattr(
        constructive,
        "run_isolated_plugin_hook",
        lambda plugin, hook_name, payload, timeout: {
            "status": "unsupported",
            "reason": f"plugin adapter does not define {hook_name}",
            "_isolated_hook": {
                "runtime_seconds": 0.001,
                "worker_exit_code": 0,
            },
        },
    )
    result = constructive.section_height_shell(context=_context(), config={})
    assert result["status"] == "unsupported"
    assert result["sections"] == []


def test_constructive_hook_request_carries_pipeline_path_identity(monkeypatch):
    captured = {}

    class Adapter:
        @staticmethod
        def derive_pipeline_section_shell(request):
            captured.update(request)
            return {"status": "completed", "sections": []}

    _patch_adapter(monkeypatch, Adapter)
    context = {
        **_context(),
        "pipeline_run_id": 77,
        "pipeline_candidate_id": 123,
    }
    result = constructive.section_height_shell(
        context=context,
        config={
            "height": 10,
            "height_kind": "family_enumeration_shell",
            "height_normalization": "family_defined",
        },
    )
    assert result["status"] == "completed"
    assert captured["pipeline_run_id"] == 77
    assert captured["pipeline_candidate_id"] == 123
