from __future__ import annotations

import io
import urllib.error

from rank42 import ai_provider, groq_api
from rank42.ai_search_runner import _active_model
from rank42.db import connect
from rank42.settings_registry import save_setting_value
from rank42.ui_store import ensure_ui_schema


def test_groq_key_is_private_and_environment_takes_precedence(tmp_path):
    path = groq_api.write_key(tmp_path, "gsk-local-test")
    assert path.stat().st_mode & 0o777 == 0o600
    assert groq_api.read_key(tmp_path, environ={}) == "gsk-local-test"
    assert groq_api.key_source(tmp_path, environ={"GROQ_API_KEY": "gsk-env-test"}) == "environment"
    assert groq_api.read_key(tmp_path, environ={"GROQ_API_KEY": "gsk-env-test"}) == "gsk-env-test"
    assert groq_api.delete_local_key(tmp_path)
    assert not path.exists()


def test_groq_json_request_uses_chat_completions(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    seen = {}

    def fake_request(path, key, *, payload, timeout, attempts=2):
        seen.update(path=path, key=key, payload=payload)
        return {"choices": [{"message": {"content": '{"answer": 42}'}}]}

    monkeypatch.setattr(groq_api, "_request", fake_request)
    result = groq_api.generate_json(tmp_path, "Return JSON", model="openai/gpt-oss-20b")
    assert result == {"answer": 42, "_model_used": "openai/gpt-oss-20b"}
    assert seen["path"] == "/chat/completions"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert seen["payload"]["messages"][1]["content"] == "Return JSON"


def test_groq_stream_reads_chat_deltas(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    lines = b"".join([
        b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ])
    requests = []
    def fake_open(request, **_kwargs):
        requests.append(request)
        return io.BytesIO(lines)
    monkeypatch.setattr(groq_api.urllib.request, "urlopen", fake_open)
    assert "".join(groq_api.stream_text(tmp_path, "hi", system_instruction="help")) == "hello world"
    assert requests[0].get_header("User-agent").startswith("RankHunter/")


def test_groq_request_identifies_itself_to_cloudflare(monkeypatch):
    requests = []
    def fake_open(request, **_kwargs):
        requests.append(request)
        return io.BytesIO(b'{"data":[]}')
    monkeypatch.setattr(groq_api.urllib.request, "urlopen", fake_open)
    assert groq_api._request("/models", "gsk-test") == {"data": []}
    assert requests[0].get_header("User-agent").startswith("RankHunter/")


def test_groq_selection_routes_agent_and_hunt(monkeypatch, tmp_path):
    db = connect(":memory:")
    ensure_ui_schema(db)
    save_setting_value(db, "default_ai_service", "Groq")
    assert ai_provider.selected_service(db) == "Groq"
    assert _active_model(db, "gemini-3.5-flash-lite") == (groq_api.DEFAULT_MODEL, False)
    save_setting_value(db, "groq_default_model", "qwen/qwen3.8-27b")
    assert ai_provider.default_model("Groq", db=db) == "qwen/qwen3.8-27b"
    assert _active_model(db, "qwen/qwen3.8-27b") == ("qwen/qwen3.8-27b", True)
    calls = []
    monkeypatch.setattr(groq_api, "generate_json", lambda _root, _prompt, **kwargs:
                        calls.append(kwargs) or {"ok": True})
    assert ai_provider.generate_json(tmp_path, "Return JSON", db=db) == {"ok": True}
    assert calls[0]["model"] == "qwen/qwen3.8-27b"
    db.close()


def test_groq_rate_limit_error_does_not_expose_key():
    exc = urllib.error.HTTPError(
        "https://api.groq.com/openai/v1/chat/completions", 429,
        "Too Many Requests", {"Retry-After": "20"}, io.BytesIO(b'{"error":"gsk-secret"}'),
    )
    result = groq_api._http_error(exc)
    assert result.status_code == 429
    assert "20 seconds" in str(result)
    assert "gsk-secret" not in str(result)


def test_groq_cloudflare_1010_is_not_reported_as_model_access():
    exc = urllib.error.HTTPError(
        "https://api.groq.com/openai/v1/models", 403,
        "Forbidden", {"Content-Type": "text/plain"}, io.BytesIO(b"error code: 1010\n"),
    )
    result = groq_api._http_error(exc)
    assert "Cloudflare" in str(result)
    assert "HTTP client" in str(result)
