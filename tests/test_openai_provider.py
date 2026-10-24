from __future__ import annotations

import io
import urllib.error

import pytest

from rank42 import ai_provider, openai_api
from rank42.ai_search_runner import _active_model
from rank42.db import connect
from rank42.settings_registry import save_setting_value
from rank42.ui_store import ensure_ui_schema


def test_openai_key_is_private_and_environment_takes_precedence(tmp_path, monkeypatch):
    path = openai_api.write_key(tmp_path, "sk-local-test")
    assert path.stat().st_mode & 0o777 == 0o600
    assert openai_api.read_key(tmp_path, environ={}) == "sk-local-test"
    assert openai_api.key_source(tmp_path, environ={"OPENAI_API_KEY": "sk-env-test"}) == "environment"
    assert openai_api.read_key(tmp_path, environ={"OPENAI_API_KEY": "sk-env-test"}) == "sk-env-test"
    assert openai_api.delete_local_key(tmp_path)
    assert not path.exists()


def test_openai_json_request_uses_responses_and_does_not_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def fake_request(path, key, *, payload, timeout, attempts=2):
        seen.update(path=path, key=key, payload=payload)
        return {"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": '{"answer": 42}'}]},
        ]}

    monkeypatch.setattr(openai_api, "_request", fake_request)
    result = openai_api.generate_json(tmp_path, "Return JSON", model="gpt-6-luna")
    assert result == {"answer": 42, "_model_used": "gpt-6-luna"}
    assert seen["path"] == "/responses"
    assert seen["payload"]["text"]["format"] == {"type": "json_object"}
    assert seen["payload"]["store"] is False


def test_openai_stream_reads_text_deltas(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    lines = b"".join([
        b"event: response.output_text.delta\n",
        b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n',
        b'data: {"type":"response.output_text.delta","delta":" world"}\n\n',
        b"data: [DONE]\n\n",
    ])
    monkeypatch.setattr(openai_api.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(lines))
    assert "".join(openai_api.stream_text(tmp_path, "hi", system_instruction="help")) == "hello world"


def test_selected_service_routes_agent_and_hunt(monkeypatch, tmp_path):
    db = connect(":memory:")
    ensure_ui_schema(db)
    assert ai_provider.selected_service(db) == "Gemini"
    save_setting_value(db, "default_ai_service", "OpenAI")
    assert ai_provider.selected_service(db) == "OpenAI"
    assert _active_model(db, "gemini-3.5-flash-lite") == ("gpt-6.1-sol", False)
    save_setting_value(db, "openai_default_model", "gpt-6-luna")
    assert ai_provider.default_model("OpenAI", db=db) == "gpt-6-luna"
    assert _active_model(db, "gemini-3.5-flash-lite") == ("gpt-6-luna", False)
    assert _active_model(db, "gpt-6-luna") == ("gpt-6-luna", True)
    calls = []
    monkeypatch.setattr(openai_api, "generate_json", lambda _root, _prompt, **kwargs:
                        calls.append(kwargs) or {"ok": True})
    assert ai_provider.generate_json(tmp_path, "Return JSON", db=db) == {"ok": True}
    assert calls[0]["model"] == "gpt-6-luna"
    save_setting_value(db, "default_ai_service", "Gemini")
    save_setting_value(db, "gemini_default_model", "gemini-3.8-flash")
    assert ai_provider.default_model("Gemini", db=db) == "gemini-3.8-flash"
    save_setting_value(db, "default_ai_service", "OpenAI")
    assert ai_provider.default_model("OpenAI", db=db) == "gpt-6-luna"


@pytest.mark.parametrize("code,expected", [
    ("credit_balance_exhausted", "organization tied to this API key"),
    ("organization_usage_limit_exceeded", "usage or spend limit"),
    ("insufficient_quota", "quota or billing limit"),
    ("rate_limit_exceeded", "rate limit reached"),
])
def test_openai_429_identifies_actionable_cause(code, expected):
    body = ('{"error":{"code":"' + code + '","type":"rate_limit_error"}}').encode()
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 429,
                                 "Too Many Requests", {"Retry-After": "20"}, io.BytesIO(body))
    result = openai_api._http_error(exc)
    assert result.status_code == 429
    assert expected in str(result)
    assert "sk-" not in str(result)
    if code == "rate_limit_exceeded":
        assert "20 seconds" in str(result)
