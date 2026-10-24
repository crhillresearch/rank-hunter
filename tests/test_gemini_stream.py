from __future__ import annotations

import io

from rank42 import gemini_api


def test_stream_text_reads_sse_chunks_without_returning_credentials(monkeypatch):
    monkeypatch.setattr(gemini_api, "read_key", lambda _root: "private-test-key")

    def fake_open(request, *, timeout):
        assert request.get_header("X-goog-api-key") == "private-test-key"
        assert "alt=sse" in request.full_url
        assert timeout == 10
        return io.BytesIO(
            b'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}\n\n'
            b'data: {"candidates":[{"content":{"parts":[{"text":" world"}]}}]}\n\n'
        )

    monkeypatch.setattr(gemini_api.urllib.request, "urlopen", fake_open)
    chunks = list(gemini_api.stream_text(".", "Hi", system_instruction="Be brief", timeout=10))
    assert chunks == ["Hello", " world"]
