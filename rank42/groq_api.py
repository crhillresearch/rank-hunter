"""Small Groq Chat Completions client for Rank Hunter AI features."""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from rank42 import __version__


KEY_ENV = "GROQ_API_KEY"
KEY_RELATIVE_PATH = Path(".rank42-ui") / "groq-api-key"
DEFAULT_MODEL = "openai/gpt-oss-120b"
AVAILABLE_MODELS = (DEFAULT_MODEL, "openai/gpt-oss-20b", "qwen/qwen3.8-27b")
_API_ROOT = "https://api.groq.com/openai/v1"
_USER_AGENT = f"RankHunter/{__version__}"


class GroqAPIError(RuntimeError):
    """An API failure without credential or submitted prompt content."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def key_path(project_root) -> Path:
    return Path(project_root).resolve() / KEY_RELATIVE_PATH


def _clean_key(value) -> str:
    key = str(value or "").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("Groq API key must be one non-empty line")
    return key


def key_source(project_root, *, environ=None) -> str | None:
    env = os.environ if environ is None else environ
    if str(env.get(KEY_ENV) or "").strip():
        return "environment"
    path = key_path(project_root)
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return "local file"
    return None


def read_key(project_root, *, environ=None) -> str:
    env = os.environ if environ is None else environ
    value = str(env.get(KEY_ENV) or "").strip()
    if value:
        return _clean_key(value)
    path = key_path(project_root)
    return _clean_key(path.read_text(encoding="utf-8")) if path.is_file() else ""


def write_key(project_root, value) -> Path:
    key = _clean_key(value)
    target = key_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".groq-key-", dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(key + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target


def delete_local_key(project_root) -> bool:
    try:
        key_path(project_root).unlink()
        return True
    except FileNotFoundError:
        return False


def _model(value) -> str:
    chosen = str(value or DEFAULT_MODEL).strip()
    if chosen not in AVAILABLE_MODELS:
        raise ValueError("unsupported Groq model")
    return chosen


def _http_error(exc: urllib.error.HTTPError) -> GroqAPIError:
    body = b""
    try:
        body = exc.read(4096)
        details = json.loads(body).get("error")
        details = details if isinstance(details, dict) else {}
    except (ValueError, UnicodeDecodeError, OSError, AttributeError):
        details = {}
    code = str(details.get("code") or "")
    if exc.code == 401:
        message = "Groq rejected the API key. Check Settings → Services → Groq."
    elif exc.code == 403:
        if b"error code: 1010" in body:
            message = "Groq's Cloudflare edge blocked this HTTP client (error 1010)."
        elif code == "model_permission_blocked_org":
            message = "Groq model is blocked for this organization. Check Groq Console → Limits."
        elif code == "model_permission_blocked_project":
            message = "Groq model is blocked for this project. Check Groq Console → Project Limits."
        else:
            message = "Groq denied access to this model or account."
    elif exc.code == 404:
        message = "Groq model is unavailable. Choose another model in Settings → General."
    elif exc.code == 429:
        retry = str(exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
        delay = f" Retry after {retry} seconds." if retry.isdigit() else " Try again later."
        message = "Groq rate limit reached; check your account's free-plan limits." + delay
    else:
        message = f"Groq API returned HTTP {exc.code}"
    return GroqAPIError(message, status_code=exc.code)


def _request(path, key, *, payload=None, timeout=30, attempts=2):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{_API_ROOT}{path}", data=body,
        headers={"Authorization": f"Bearer {_clean_key(key)}",
                 "Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST" if body is not None else "GET",
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {500, 502, 503, 504} and attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise _http_error(exc) from None
        except TimeoutError:
            raise GroqAPIError("Groq API timed out", status_code=408) from None
        except (urllib.error.URLError, OSError) as exc:
            raise GroqAPIError(f"Groq API connection failed: {type(exc).__name__}") from None
        except (ValueError, json.JSONDecodeError):
            raise GroqAPIError("Groq API returned an invalid response") from None


def check_connection(project_root, *, model=DEFAULT_MODEL) -> str:
    key = read_key(project_root)
    if not key:
        raise GroqAPIError("Add a Groq API key in Settings → Services first")
    chosen = _model(model)
    result = _request("/models", key, timeout=15)
    available = {str(item.get("id")) for item in result.get("data") or [] if isinstance(item, dict)}
    if chosen not in available:
        raise GroqAPIError(f"Groq model {chosen} is not available to this account")
    return chosen


def generate_json(project_root, prompt, *, model=DEFAULT_MODEL, timeout=90) -> dict:
    key = read_key(project_root)
    if not key:
        raise GroqAPIError("Add a Groq API key in Settings → Services first")
    chosen = _model(model)
    response = _request("/chat/completions", key, payload={
        "model": chosen,
        "messages": [
            {"role": "system", "content": "Return exactly one valid JSON object. Do not add Markdown."},
            {"role": "user", "content": str(prompt)},
        ],
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
        "max_completion_tokens": 4096,
        "stream": False,
    }, timeout=max(1, min(int(timeout), 120)))
    choices = response.get("choices") or []
    output = str(((choices[0].get("message") or {}).get("content") or "")) if choices else ""
    if not output:
        raise GroqAPIError("Groq returned no JSON response")
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError:
        raise GroqAPIError("Groq returned invalid JSON") from None
    if not isinstance(decoded, dict):
        raise GroqAPIError("Groq response must be a JSON object")
    decoded["_model_used"] = chosen
    return decoded


def stream_text(project_root, prompt, *, system_instruction, model=DEFAULT_MODEL, timeout=60):
    key = read_key(project_root)
    if not key:
        raise GroqAPIError("Add a Groq API key in Settings → Services first")
    chosen = _model(model)
    payload = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": str(system_instruction)},
            {"role": "user", "content": str(prompt)},
        ],
        "reasoning_effort": "low",
        "max_completion_tokens": 2500,
        "stream": True,
    }
    request = urllib.request.Request(
        f"{_API_ROOT}/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(int(timeout), 90))) as response:
            for raw_line in response:
                if not raw_line.startswith(b"data:"):
                    continue
                data = raw_line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    event = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    raise GroqAPIError("Groq returned an invalid chat stream") from None
                if event.get("error"):
                    raise GroqAPIError("Groq chat generation failed")
                for choice in event.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        yield str(content)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    except TimeoutError:
        raise GroqAPIError("Groq chat timed out", status_code=408) from None
    except (urllib.error.URLError, OSError) as exc:
        raise GroqAPIError(f"Groq API connection failed: {type(exc).__name__}") from None
