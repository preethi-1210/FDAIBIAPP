
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from openai import OpenAI
from .config import settings

logger = logging.getLogger(__name__)

# Per-request record of which engine actually handled each Ask AI stage, so responses and
# audits report the provider that produced the output rather than the configured one.
_trace: ContextVar[list[dict[str, Any]] | None] = ContextVar("ryze_llm_trace", default=None)


@contextmanager
def trace_steps() -> Iterator[list[dict[str, Any]]]:
    steps: list[dict[str, Any]] = []
    token = _trace.set(steps)
    try:
        yield steps
    finally:
        _trace.reset(token)


def record_step(
    stage: str,
    engine: str,
    ok: bool = True,
    detail: str | None = None,
    latency_ms: float | None = None,
) -> None:
    steps = _trace.get()
    if steps is None:
        return
    steps.append({
        "stage": stage,
        "engine": engine,
        "ok": ok,
        "detail": detail,
        "latency_ms": round(latency_ms) if latency_ms is not None else None,
    })


_TRANSIENT_ERRORS = {
    "ServerError",
    "InternalServerError",
    "ServiceUnavailable",
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "TimeoutError",
    "ConnectionError",
    "ReadTimeout",
    "ConnectTimeout",
}
_MAX_ATTEMPTS = 3


def _call_with_retries(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Retry the same provider on transient failures (5xx, 429, timeouts). The provider is never
    switched silently; a persistent failure is raised to the caller and recorded in the trace."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            text = str(exc)
            # Exhausted quotas are not transient; retrying them only burns more calls.
            transient = "RESOURCE_EXHAUSTED" not in text and "quota" not in text.lower() and (
                type(exc).__name__ in _TRANSIENT_ERRORS
                or any(marker in text for marker in ("503", "429", "overloaded", "UNAVAILABLE"))
            )
            if not transient or attempt == _MAX_ATTEMPTS:
                raise
            logger.warning("Transient %s from LLM provider; retrying (attempt %d)", type(exc).__name__, attempt + 1)
            time.sleep(0.75 * attempt)
    raise AssertionError("unreachable")


def last_failure(stage: str) -> str | None:
    """The most recent failure recorded for a stage in the current request, if any."""
    for step in reversed(_trace.get() or []):
        if step["stage"] == stage and not step["ok"]:
            return step.get("detail")
    return None


def _model_name(provider: str) -> str:
    return {
        "google": settings.google_model,
        "openai": settings.openai_model,
        "grok": settings.xai_model,
        "anthropic": settings.anthropic_model,
        "ollama": settings.ollama_model,
    }.get(provider, "")


def _record_llm(stage: str, provider: str | None, ok: bool, started: float, error: str | None = None) -> None:
    engine = f"{provider}:{_model_name(provider)}" if provider else "no LLM provider configured"
    record_step(stage, engine, ok=ok, detail=error, latency_ms=(time.perf_counter() - started) * 1000)


def provider_status() -> dict[str, Any]:
    return {
        "configured": settings.llm_provider,
        "openai": bool(settings.openai_api_key),
        "grok": bool(settings.xai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "anthropic_model": settings.anthropic_model,
        "google": bool(settings.google_api_key),
        "google_model": settings.google_model,
        "ollama": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "active": resolve_provider(),
    }


def resolve_provider(override: str | None = None) -> str | None:
    choice = (override or settings.llm_provider or "auto").lower()

    if choice == "google" and settings.google_api_key:
        return "google"
    if choice == "openai" and settings.openai_api_key:
        return "openai"
    if choice in {"grok", "xai"} and settings.xai_api_key:
        return "grok"
    if choice == "anthropic" and settings.anthropic_api_key:
        return "anthropic"
    if choice == "ollama":
        return "ollama"

    if choice == "auto":
        if settings.anthropic_api_key:
            return "anthropic"
        if settings.openai_api_key:
            return "openai"
        if settings.xai_api_key:
            return "grok"
        if settings.google_api_key:
            return "google"
        if settings.ollama_base_url and settings.ollama_model:
            return "ollama"

    return None


def _client(provider: str) -> tuple[OpenAI, str]:
    if provider == "openai":
        return (
            OpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.llm_timeout_seconds,
            ),
            settings.openai_model,
        )

    if provider == "grok":
        return (
            OpenAI(
                api_key=settings.xai_api_key,
                base_url="https://api.x.ai/v1",
                timeout=settings.llm_timeout_seconds,
            ),
            settings.xai_model,
        )

    if provider == "ollama":
        return (
            OpenAI(
                api_key="ollama",
                base_url=settings.ollama_base_url,
                timeout=settings.llm_timeout_seconds,
            ),
            settings.ollama_model,
        )

    raise ValueError(f"Unsupported OpenAI-compatible provider: {provider}")


def _google_message(
    system: str,
    user: str,
    json_mode: bool = False,
) -> str:
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Install google-genai to use the Google provider"
        ) from exc

    client = genai.Client(api_key=settings.google_api_key)

    config_args: dict[str, Any] = {
        "system_instruction": system,
        "temperature": 0 if json_mode else 0.2,
        "http_options": types.HttpOptions(
            timeout=settings.llm_timeout_seconds * 1000
        ),
    }

    if json_mode:
        config_args["response_mime_type"] = "application/json"

    response = client.models.generate_content(
        model=settings.google_model,
        contents=user,
        config=types.GenerateContentConfig(**config_args),
    )

    return response.text or ""


def _anthropic_message(
    system: str,
    user: str,
    json_mode: bool = False,
) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Install anthropic to use the Anthropic provider"
        ) from exc

    prompt = system
    if json_mode:
        prompt += " Return only a valid JSON object with no markdown fences."

    response = Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.llm_timeout_seconds,
    ).messages.create(
        model=settings.anthropic_model,
        max_tokens=1024 if json_mode else 512,
        temperature=0 if json_mode else 0.2,
        system=prompt,
        messages=[{"role": "user", "content": user}],
    )

    return "".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    )


def llm_available(override: str | None = None) -> bool:
    return resolve_provider(override) is not None


# Providers that reported a non-transient failure (exhausted quota, invalid or denied key)
# are skipped in favour of the fallback provider for a while instead of failing every call.
_HARD_FAILURE_COOLDOWN_SECONDS = 600
_unavailable_until: dict[str, float] = {}
_HARD_FAILURE_MARKERS = ("RESOURCE_EXHAUSTED", "quota", "API_KEY_INVALID", "API key not valid", "PERMISSION_DENIED", "401", "403")


def _fallback_provider(primary: str) -> str | None:
    fallback = (settings.llm_fallback_provider or "").strip().lower()
    if not fallback or fallback == primary:
        return None
    return resolve_provider(fallback)


def _providers_for(override: str | None, stage: str) -> list[str]:
    """The provider order for one call: the selected provider, then the configured fallback."""
    primary = resolve_provider(override)
    if not primary:
        return []
    fallback = _fallback_provider(primary)
    if fallback is None:
        return [primary]
    if _unavailable_until.get(primary, 0) > time.monotonic():
        record_step(
            stage,
            f"{primary}:{_model_name(primary)}",
            ok=False,
            detail=f"Skipped: {primary} recently reported a quota or key failure; using fallback {fallback}.",
        )
        return [fallback]
    return [primary, fallback]


def _note_failure(provider: str, exc: Exception) -> None:
    text = f"{type(exc).__name__}: {exc}"
    if any(marker in text for marker in _HARD_FAILURE_MARKERS):
        _unavailable_until[provider] = time.monotonic() + _HARD_FAILURE_COOLDOWN_SECONDS


def failed_providers(stage: str) -> list[str]:
    """Providers whose calls for a stage failed in the current request, in order."""
    names: list[str] = []
    for step in _trace.get() or []:
        provider = step["engine"].split(":")[0]
        if step["stage"] == stage and not step["ok"] and provider in _MODEL_PROVIDERS and provider not in names:
            names.append(provider)
    return names


_MODEL_PROVIDERS = {"google", "openai", "grok", "anthropic", "ollama"}


def complete_json(
    system: str,
    user: str,
    override: str | None = None,
    stage: str = "llm",
) -> dict[str, Any] | None:
    providers = _providers_for(override, stage)
    if not providers:
        _record_llm(stage, None, False, time.perf_counter(), "No LLM provider is configured for this request.")
        return None
    for provider in providers:
        result = _complete_json_with(provider, system, user, stage)
        if result is not None:
            return result
    return None


def _complete_json_with(provider: str, system: str, user: str, stage: str) -> dict[str, Any] | None:
    started = time.perf_counter()
    try:
        if provider == "google":
            content = _call_with_retries(_google_message, system, user, json_mode=True)

        elif provider == "anthropic":
            content = _call_with_retries(_anthropic_message, system, user, json_mode=True)

        else:
            client, model = _client(provider)

            kwargs: dict[str, Any] = {
                "model": model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            system
                            + "\nReturn only a valid JSON object. "
                            "Escape newlines inside JSON strings."
                        ),
                    },
                    {"role": "user", "content": user},
                ],
            }

            # JSON mode for every OpenAI-compatible provider, Ollama included: without it, small
            # local models wrap SQL in backticks or leave raw line breaks, which is not valid JSON.
            kwargs["response_format"] = {"type": "json_object"}

            response = _call_with_retries(client.chat.completions.create, **kwargs)
            content = response.choices[0].message.content or ""

        start = content.find("{")
        end = content.rfind("}")

        if start < 0 or end <= start:
            logger.warning("LLM %s returned no JSON object for stage %s", provider, stage)
            _record_llm(stage, provider, False, started, "The model did not return a JSON object.")
            return None

        # strict=False accepts raw line breaks/tabs inside strings: models (notably Ollama) often
        # return multi-line SQL unescaped, which strict JSON rejects as "Invalid control character".
        result = json.loads(content[start : end + 1], strict=False)

        if not isinstance(result, dict):
            logger.warning("LLM %s returned non-object JSON for stage %s", provider, stage)
            _record_llm(stage, provider, False, started, "The model did not return a JSON object.")
            return None

        _record_llm(stage, provider, True, started)
        return result

    except Exception as exc:
        logger.warning("LLM %s call failed for stage %s: %s", provider, stage, type(exc).__name__)
        _note_failure(provider, exc)
        _record_llm(stage, provider, False, started, f"{type(exc).__name__}: {str(exc)[:200]}")
        return None


def complete_text(
    system: str,
    user: str,
    override: str | None = None,
    stage: str = "llm",
) -> str | None:
    providers = _providers_for(override, stage)
    if not providers:
        _record_llm(stage, None, False, time.perf_counter(), "No LLM provider is configured for this request.")
        return None
    for provider in providers:
        result = _complete_text_with(provider, system, user, stage)
        if result is not None:
            return result
    return None


def _complete_text_with(provider: str, system: str, user: str, stage: str) -> str | None:
    started = time.perf_counter()
    try:
        if provider == "google":
            content = _call_with_retries(_google_message, system, user)
        elif provider == "anthropic":
            content = _call_with_retries(_anthropic_message, system, user)
        else:
            client, model = _client(provider)
            response = _call_with_retries(
                client.chat.completions.create,
                model=model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = response.choices[0].message.content
        if not content or not content.strip():
            _record_llm(stage, provider, False, started, "The model returned an empty response.")
            return None
        _record_llm(stage, provider, True, started)
        return content

    except Exception as exc:
        logger.warning("LLM %s call failed for stage %s: %s", provider, stage, type(exc).__name__)
        _note_failure(provider, exc)
        _record_llm(stage, provider, False, started, f"{type(exc).__name__}: {str(exc)[:200]}")
        return None
