"""Local Gemini credential and small, bounded REST client for AI Hunt."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


KEY_ENV = "GEMINI_API_KEY"
KEY_RELATIVE_PATH = Path(".rank42-ui") / "gemini-api-key"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
AVAILABLE_MODELS = (DEFAULT_MODEL, "gemini-3.8-flash")
_MODEL_ID = re.compile(r"^gemini-[a-zA-Z0-9._-]+$")
_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiAPIError(RuntimeError):
    """An API failure without credential or submitted prompt content."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def key_path(project_root: str | os.PathLike) -> Path:
    return Path(project_root).resolve() / KEY_RELATIVE_PATH


def _clean_key(value) -> str:
    key = str(value or "").strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError("Gemini API key must be one non-empty line")
    return key


def key_source(project_root: str | os.PathLike, *, environ=None) -> str | None:
    env = os.environ if environ is None else environ
    if str(env.get(KEY_ENV) or "").strip():
        return "environment"
    path = key_path(project_root)
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return "local file"
    return None


def read_key(project_root: str | os.PathLike, *, environ=None) -> str:
    env = os.environ if environ is None else environ
    value = str(env.get(KEY_ENV) or "").strip()
    if value:
        return _clean_key(value)
    path = key_path(project_root)
    return _clean_key(path.read_text(encoding="utf-8")) if path.is_file() else ""


def write_key(project_root: str | os.PathLike, value: str) -> Path:
    key = _clean_key(value)
    target = key_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".gemini-key-", dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target


def delete_local_key(project_root: str | os.PathLike) -> bool:
    try:
        key_path(project_root).unlink()
        return True
    except FileNotFoundError:
        return False


def _model(value: str) -> str:
    model = str(value or DEFAULT_MODEL).strip()
    if model not in AVAILABLE_MODELS or not _MODEL_ID.fullmatch(model):
        raise ValueError("unsupported Gemini model")
    return model


def _request(url: str, key: str, *, payload=None, timeout=30, attempts=3):
    headers = {"x-goog-api-key": _clean_key(key), "Content-Type": "application/json"}
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if body is not None else "GET")
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise GeminiAPIError(
                f"Gemini API returned HTTP {exc.code}", status_code=exc.code
            ) from None
        except TimeoutError:
            raise GeminiAPIError("Gemini API timed out", status_code=408) from None
        except (urllib.error.URLError, OSError) as exc:
            raise GeminiAPIError(f"Gemini API connection failed: {type(exc).__name__}") from None
        except (ValueError, json.JSONDecodeError):
            raise GeminiAPIError("Gemini API returned an invalid response") from None


def check_connection(project_root: str | os.PathLike, *, model=DEFAULT_MODEL) -> str:
    key = read_key(project_root)
    if not key:
        raise GeminiAPIError("Add a Gemini API key in Settings → Services first")
    chosen = _model(model)
    result = _request(f"{_API_ROOT}/{chosen}", key, timeout=15)
    return str(result.get("name") or chosen)


def generate_json(project_root: str | os.PathLike, prompt: str, *, model=DEFAULT_MODEL,
                  timeout=90) -> dict:
    key = read_key(project_root)
    if not key:
        raise GeminiAPIError("Add a Gemini API key in Settings → Services first")
    chosen = _model(model)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": str(prompt)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.25,
            "maxOutputTokens": 2048,
        },
    }
    primary_timeout = min(timeout, 12) if chosen == "gemini-3.8-flash" else min(timeout, 45)
    try:
        result = _request(f"{_API_ROOT}/{chosen}:generateContent", key,
                          payload=payload, timeout=primary_timeout, attempts=2)
    except GeminiAPIError as exc:
        if chosen != "gemini-3.8-flash" or exc.status_code not in {408, 503}:
            raise
        chosen = DEFAULT_MODEL
        try:
            result = _request(f"{_API_ROOT}/{chosen}:generateContent", key,
                              payload=payload, timeout=min(timeout, 45), attempts=2)
        except GeminiAPIError as fallback_exc:
            raise GeminiAPIError(
                "Gemini 3.8 Flash is unavailable, and the Flash-Lite fallback "
                f"also failed ({fallback_exc}). Try preview again later.",
                status_code=fallback_exc.status_code,
            ) from None
    candidates = result.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    output = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    if not output:
        raise GeminiAPIError("Gemini returned no strategy; check the model and account limits")
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError:
        raise GeminiAPIError("Gemini returned invalid strategy JSON") from None
    if not isinstance(decoded, dict):
        raise GeminiAPIError("Gemini strategy must be a JSON object")
    decoded["_model_used"] = chosen
    return decoded


def stream_text(project_root: str | os.PathLike, prompt: str, *, system_instruction: str,
                model=DEFAULT_MODEL, timeout=60):
    """Yield text chunks from Gemini's SSE endpoint without exposing the key."""
    key = read_key(project_root)
    if not key:
        raise GeminiAPIError("Add a Gemini API key in Settings → Services first")
    chosen = _model(model)
    payload = {
        "systemInstruction": {"parts": [{"text": str(system_instruction)}]},
        "contents": [{"role": "user", "parts": [{"text": str(prompt)}]}],
        "generationConfig": {"temperature": 0.25, "maxOutputTokens": 900},
    }
    request = urllib.request.Request(
        f"{_API_ROOT}/{chosen}:streamGenerateContent?alt=sse",
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(int(timeout), 90))) as response:
            for raw_line in response:
                if not raw_line.startswith(b"data:"):
                    continue
                try:
                    item = json.loads(raw_line[5:].strip())
                except (ValueError, UnicodeDecodeError):
                    raise GeminiAPIError("Gemini returned an invalid chat stream") from None
                candidates = item.get("candidates") or []
                if not candidates:
                    continue
                parts = ((candidates[0].get("content") or {}).get("parts") or [])
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        yield str(part["text"])
    except urllib.error.HTTPError as exc:
        raise GeminiAPIError(f"Gemini API returned HTTP {exc.code}", status_code=exc.code) from None
    except TimeoutError:
        raise GeminiAPIError("Gemini chat timed out", status_code=408) from None
    except (urllib.error.URLError, OSError) as exc:
        raise GeminiAPIError(f"Gemini API connection failed: {type(exc).__name__}") from None
