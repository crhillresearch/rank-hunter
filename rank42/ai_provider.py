"""One provider choice for Rank Hunter Agent and AI Hunt."""

from __future__ import annotations

from rank42 import gemini_api, groq_api, openai_api
from rank42.settings_registry import setting_value


SERVICES = ("Gemini", "OpenAI", "Groq")
AVAILABLE_MODELS = gemini_api.AVAILABLE_MODELS + openai_api.AVAILABLE_MODELS + groq_api.AVAILABLE_MODELS
DEFAULT_SERVICE = "Gemini"


def selected_service(db) -> str:
    if db is None:
        return DEFAULT_SERVICE
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_settings'").fetchone() is None:
        return DEFAULT_SERVICE
    service = str(setting_value(db, "default_ai_service", DEFAULT_SERVICE) or DEFAULT_SERVICE)
    return service if service in SERVICES else DEFAULT_SERVICE


def default_model(service: str, *, db=None) -> str:
    if service == "Gemini":
        fallback = gemini_api.DEFAULT_MODEL
        key = "gemini_default_model"
        options = gemini_api.AVAILABLE_MODELS
    elif service == "OpenAI":
        fallback = openai_api.DEFAULT_MODEL
        key = "openai_default_model"
        options = openai_api.AVAILABLE_MODELS
    elif service == "Groq":
        fallback = groq_api.DEFAULT_MODEL
        key = "groq_default_model"
        options = groq_api.AVAILABLE_MODELS
    else:
        raise ValueError("unsupported AI service")
    if db is None or db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_settings'"
    ).fetchone() is None:
        return fallback
    value = str(setting_value(db, key, fallback) or fallback)
    return value if value in options else fallback


def models_for_service(service: str) -> tuple[str, ...]:
    if service == "Gemini":
        return gemini_api.AVAILABLE_MODELS
    if service == "OpenAI":
        return openai_api.AVAILABLE_MODELS
    if service == "Groq":
        return groq_api.AVAILABLE_MODELS
    raise ValueError("unsupported AI service")


def service_for_model(model: str) -> str:
    if model in gemini_api.AVAILABLE_MODELS:
        return "Gemini"
    if model in openai_api.AVAILABLE_MODELS:
        return "OpenAI"
    if model in groq_api.AVAILABLE_MODELS:
        return "Groq"
    raise ValueError("unsupported AI model")


def key_source(project_root, service: str) -> str | None:
    if service == "Gemini":
        return gemini_api.key_source(project_root)
    if service == "OpenAI":
        return openai_api.key_source(project_root)
    if service == "Groq":
        return groq_api.key_source(project_root)
    raise ValueError("unsupported AI service")


def generate_json(project_root, prompt, *, db=None, model=None, timeout=90):
    chosen = str(model or default_model(selected_service(db), db=db))
    provider = {"Gemini": gemini_api, "OpenAI": openai_api, "Groq": groq_api}[service_for_model(chosen)]
    return provider.generate_json(project_root, prompt, model=chosen, timeout=timeout)


def stream_text(project_root, prompt, *, system_instruction, db=None, model=None, timeout=60):
    chosen = str(model or default_model(selected_service(db), db=db))
    provider = {"Gemini": gemini_api, "OpenAI": openai_api, "Groq": groq_api}[service_for_model(chosen)]
    return provider.stream_text(project_root, prompt, system_instruction=system_instruction,
                                model=chosen, timeout=timeout)
