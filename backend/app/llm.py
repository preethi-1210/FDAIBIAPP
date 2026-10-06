import json
from typing import Any

from openai import OpenAI

from .config import settings


def provider_status() -> dict[str, Any]:
    return {
        "configured": settings.llm_provider,
        "openai": bool(settings.openai_api_key),
        "grok": bool(settings.xai_api_key),
        "ollama": settings.ollama_base_url,
        "active": resolve_provider(),
    }


def resolve_provider(override: str | None = None) -> str | None:
    choice = (override or settings.llm_provider or "auto").lower()
    if choice == "openai" and settings.openai_api_key:
        return "openai"
    if choice in {"grok", "xai"} and settings.xai_api_key:
        return "grok"
    if choice == "ollama":
        return "ollama"
    if choice == "auto":
        if settings.openai_api_key:
            return "openai"
        if settings.xai_api_key:
            return "grok"
        return None
    return None


def _client(provider: str) -> tuple[OpenAI, str]:
    if provider == "openai":
        return OpenAI(api_key=settings.openai_api_key), settings.openai_model
    if provider == "grok":
        return OpenAI(api_key=settings.xai_api_key, base_url="https://api.x.ai/v1"), settings.xai_model
    return (
        OpenAI(api_key="ollama", base_url=settings.ollama_base_url),
        settings.ollama_model,
    )


def llm_available(override: str | None = None) -> bool:
    return resolve_provider(override) is not None


def complete_json(system: str, user: str, override: str | None = None) -> dict[str, Any] | None:
    provider = resolve_provider(override)
    if not provider:
        return None
    try:
        client, model = _client(provider)
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system + " Return only JSON."},
                {"role": "user", "content": user},
            ],
        }
        if provider != "ollama":
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or "{}"
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start : end + 1]
        return json.loads(content)
    except Exception:
        return None


def complete_text(system: str, user: str, override: str | None = None) -> str | None:
    provider = resolve_provider(override)
    if not provider:
        return None
    try:
        client, model = _client(provider)
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content
    except Exception:
        return None
