"""Subprocess worker for isolated family research hooks."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

MARKER = "RANK42_PLUGIN_HOOK_RESULT="


def _load_module(path: str, plugin_id: str):
    resolved = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(
        f"rank42_isolated_plugin_{plugin_id.replace('-', '_')}",
        resolved,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin adapter {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    started = time.monotonic()
    try:
        request = json.load(sys.stdin)
        adapter_path = str(request["adapter_path"])
        plugin_id = str(request.get("plugin_id") or "unknown")
        hook_name = str(request["hook_name"])
        payload = dict(request.get("payload") or {})
        module = _load_module(adapter_path, plugin_id)
        hook = getattr(module, hook_name, None)
        if not callable(hook):
            result = {
                "status": "unsupported",
                "reason": f"plugin adapter does not define {hook_name}",
            }
        else:
            raw = hook(payload)
            if hook_name == "derive_pipeline_coverings":
                if raw is None:
                    result = {"status": "completed", "coverings": []}
                elif isinstance(raw, dict):
                    result = dict(raw)
                    result.setdefault("status", "completed")
                elif isinstance(raw, (list, tuple)):
                    result = {
                        "status": "completed",
                        "coverings": list(raw),
                        "legacy_list_result": True,
                    }
                else:
                    raise TypeError(
                        "derive_pipeline_coverings must return an object, "
                        "a list of coverings, or None"
                    )
            elif hook_name == "derive_pipeline_transform":
                if raw is None:
                    result = {
                        "status": "inconclusive",
                        "reason": "plugin transform returned no result",
                        "children": [],
                    }
                elif isinstance(raw, dict):
                    result = dict(raw)
                    result.setdefault("status", "completed")
                elif isinstance(raw, (list, tuple)):
                    result = {
                        "status": "completed",
                        "children": list(raw),
                        "legacy_list_result": True,
                    }
                else:
                    raise TypeError(
                        "derive_pipeline_transform must return an object, "
                        "a list of children, or None"
                    )
            elif hook_name in {
                "derive_pipeline_section_shell",
                "derive_pipeline_trace_sections",
                "derive_pipeline_bisection_conditions",
                "derive_pipeline_square_specializations",
            }:
                key = {
                    "derive_pipeline_section_shell": "sections",
                    "derive_pipeline_trace_sections": "trace_sections",
                    "derive_pipeline_bisection_conditions": "conditions",
                    "derive_pipeline_square_specializations": "children",
                }[hook_name]
                if raw is None:
                    result = {
                        "status": "inconclusive",
                        "reason": f"{hook_name} returned no result",
                        key: [],
                    }
                elif isinstance(raw, dict):
                    result = dict(raw)
                    result.setdefault("status", "completed")
                elif isinstance(raw, (list, tuple)):
                    result = {
                        "status": "completed",
                        key: list(raw),
                        "legacy_list_result": True,
                    }
                else:
                    raise TypeError(
                        f"{hook_name} must return an object, "
                        f"a list of {key}, or None"
                    )
            elif raw is None:
                result = {
                    "status": "unsupported",
                    "reason": f"{hook_name} returned no result",
                }
            elif isinstance(raw, dict):
                result = dict(raw)
                result.setdefault("status", "completed")
            else:
                raise TypeError(f"{hook_name} must return an object or None")
        result["_isolated_hook"] = {
            "plugin_id": plugin_id,
            "hook_name": hook_name,
            "adapter_path": adapter_path,
            "runtime_seconds": time.monotonic() - started,
        }
        print(MARKER + json.dumps(result, sort_keys=True, default=str), flush=True)
        return 0
    except Exception as exc:
        result = {
            "status": "error",
            "reason": "isolated plugin hook failed",
            "error": repr(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "_isolated_hook": {
                "runtime_seconds": time.monotonic() - started,
            },
        }
        print(MARKER + json.dumps(result, sort_keys=True, default=str), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
