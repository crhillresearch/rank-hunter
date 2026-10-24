"""Small OpenAI Responses API client for Rank Hunter AI features."""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


KEY_ENV = "OPENAI_API_KEY"
KEY_RELATIVE_PATH = Path(".rank42-ui") / "openai-api-key"
DEFAULT_MODEL = "gpt-6.1-sol"
AVAILABLE_MODELS = (DEFAULT_MODEL, "gpt-6-luna", "gpt-6-astra")
_API_ROOT = "https://api.openai.com/v1"


class OpenAIAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def key_path(project_root) -> Path:
    return Path(project_root).resolve() / KEY_RELATIVE_PATH


def _clean_key(value) -> str:
    key = str(value or "").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("OpenAI API key must be one non-empty line")
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
    fd, temporary = tempfile.mkstemp(prefix=".openai-key-", dir=str(target.parent))
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
        raise ValueError("unsupported OpenAI model")
    return chosen


def _http_error(exc: urllib.error.HTTPError) -> OpenAIAPIError:
    """Turn API error codes into safe guidance without echoing response bodies."""
    try:
        payload = json.loads(exc.read(4096))
        details = payload.get("error") if isinstance(payload, dict) else None
        details = details if isinstance(details, dict) else {}
    except (ValueError, UnicodeDecodeError, OSError):
        details = {}
    code = str(details.get("code") or "").strip().lower()
    kind = str(details.get("type") or "").strip().lower()
    if exc.code == 429:
        if code == "credit_balance_exhausted":
            message = ("OpenAI reports credit_balance_exhausted for the organization tied to this "
                       "API key. Check that key's organization and API Billing balance; "
                       "ChatGPT/Codex credits are separate from API credits.")
        elif code in {"organization_usage_limit_exceeded", "organization_spend_limit_exceeded",
                      "project_spend_limit_exceeded", "billing_hard_limit_reached"}:
            message = "OpenAI API usage or spend limit reached. Check your project and organization limits."
        elif code == "insufficient_quota" or kind == "insufficient_quota":
            message = "OpenAI API quota or billing limit reached. Check API credits and project limits."
        elif code in {"rate_limit_exceeded", "slow_down"} or kind == "rate_limit_error":
            retry = str(exc.headers.get("Retry-After") or "").strip() if exc.headers else ""
            delay = f" Wait at least {retry} seconds before retrying." if retry.isdigit() else " Try again after a short pause."
            message = "OpenAI API rate limit reached." + delay
        else:
            message = "OpenAI API returned 429. Check API credits, spend limits, and rate limits."
    elif exc.code == 401:
        message = "OpenAI API rejected the key. Check Settings → Services → OpenAI API."
    elif exc.code == 403:
        message = "OpenAI API access denied. Check this project's model permissions."
    else:
        message = f"OpenAI API returned HTTP {exc.code}"
    return OpenAIAPIError(message, status_code=exc.code)


def _request(path, key, *, payload=None, timeout=30, attempts=2):
    headers = {"Authorization": f"Bearer {_clean_key(key)}",
               "Content-Type": "application/json"}
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{_API_ROOT}{path}", data=body,
                                     headers=headers, method="POST" if body else "GET")
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
            raise OpenAIAPIError("OpenAI API timed out", status_code=408) from None
        except (urllib.error.URLError, OSError) as exc:
            raise OpenAIAPIError(f"OpenAI API connection failed: {type(exc).__name__}") from None
        except (ValueError, json.JSONDecodeError):
            raise OpenAIAPIError("OpenAI API returned an invalid response") from None


def check_connection(project_root, *, model=DEFAULT_MODEL) -> str:
    key = read_key(project_root)
    if not key:
        raise OpenAIAPIError("Add an OpenAI API key in Settings → Services first")
    chosen = _model(model)
    result = _request(f"/models/{chosen}", key, timeout=15)
    return str(result.get("id") or chosen)


def _output_text(response) -> str:
    parts = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def generate_json(project_root, prompt, *, model=DEFAULT_MODEL, timeout=90) -> dict:
    key = read_key(project_root)
    if not key:
        raise OpenAIAPIError("Add an OpenAI API key in Settings → Services first")
    chosen = _model(model)
    response = _request("/responses", key, payload={
        "model": chosen,
        "instructions": "Return exactly one valid JSON object. Do not add Markdown.",
        "input": str(prompt),
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_object"}},
        "max_output_tokens": 4096,
        "store": False,
    }, timeout=max(1, min(int(timeout), 120)))
    if response.get("status") not in (None, "completed"):
        raise OpenAIAPIError(f"OpenAI response was {str(response['status'])[:40]}")
    output = _output_text(response)
    if not output:
        raise OpenAIAPIError("OpenAI returned no JSON response")
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError:
        raise OpenAIAPIError("OpenAI returned invalid JSON") from None
    if not isinstance(decoded, dict):
        raise OpenAIAPIError("OpenAI response must be a JSON object")
    decoded["_model_used"] = chosen
    return decoded


def stream_text(project_root, prompt, *, system_instruction, model=DEFAULT_MODEL, timeout=60):
    key = read_key(project_root)
    if not key:
        raise OpenAIAPIError("Add an OpenAI API key in Settings → Services first")
    chosen = _model(model)
    payload = {"model": chosen, "instructions": str(system_instruction),
               "input": str(prompt), "reasoning": {"effort": "low"},
               "max_output_tokens": 2500, "store": False, "stream": True}
    request = urllib.request.Request(
        f"{_API_ROOT}/responses", data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(int(timeout), 90))) as response:
            for raw_line in response:
                if not raw_line.startswith(b"data:"):
                    continue
                if raw_line[5:].strip() == b"[DONE]":
                    break
                try:
                    event = json.loads(raw_line[5:].strip())
                except (ValueError, UnicodeDecodeError):
                    raise OpenAIAPIError("OpenAI returned an invalid chat stream") from None
                if event.get("type") == "response.output_text.delta" and event.get("delta"):
                    yield str(event["delta"])
                elif event.get("type") in {"response.failed", "response.incomplete", "error"}:
                    raise OpenAIAPIError("OpenAI chat generation failed")
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    except TimeoutError:
        raise OpenAIAPIError("OpenAI chat timed out", status_code=408) from None
    except (urllib.error.URLError, OSError) as exc:
        raise OpenAIAPIError(f"OpenAI API connection failed: {type(exc).__name__}") from None
