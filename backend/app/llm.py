
import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from openai import OpenAI
from .config import settings
from .services.reliability import BackpressureError, Bulkhead, CircuitBreaker, is_transient, retry_call

logger = logging.getLogger(__name__)

# Per-request record of which engine actually handled each Ask AI stage, so responses and
# audits report the provider that produced the output rather than the configured one.
_trace: ContextVar[list[dict[str, Any]] | None] = ContextVar("ryze_llm_trace", default=None)
_call_info: ContextVar[dict[str, Any] | None] = ContextVar("ryze_llm_call_info", default=None)
# Says why a stage may not use a model under the user's plan; None when it may.
_usage_gate: ContextVar[Callable[[str], str | None] | None] = ContextVar("ryze_llm_usage_gate", default=None)
# Receives progress events while a streamed Ask AI request runs. Never set for ordinary requests.
_progress: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar("ryze_progress", default=None)


@contextmanager
def progress_listener(listener: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    token = _progress.set(listener)
    try:
        yield
    finally:
        _progress.reset(token)


def report_progress(event: dict[str, Any]) -> None:
    """Tell a streaming client that a stage really happened. Progress never affects the answer."""
    listener = _progress.get()
    if listener is None:
        return
    try:
        listener(event)
    except Exception:  # A disconnected client must not fail the analysis.
        logger.debug("Progress listener failed", exc_info=True)


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
    **extra: Any,
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
        **extra,
    })
    # Only the stage name and outcome: engines, models and details stay in the audited trace.
    report_progress({"type": "step", "stage": stage, "ok": ok})


@contextmanager
def usage_gate(check: Callable[[str], str | None] | None) -> Iterator[None]:
    token = _usage_gate.set(check)
    try:
        yield
    finally:
        _usage_gate.reset(token)


def usage_refusal(stage: str) -> str | None:
    check = _usage_gate.get()
    return check(stage) if check is not None else None


def _blocked_by_plan(stage: str) -> bool:
    reason = usage_refusal(stage)
    if reason:
        record_step(stage, "plan allowance", ok=False, detail=reason, plan_blocked=True)
    return bool(reason)


def _call_with_retries(function: Any, *args: Any, **kwargs: Any) -> Any:
    """Retry the same provider on transient failures (5xx, rate limits, timeouts), with jittered
    backoff. Quota, key and validation failures are not retried; fallback happens in the caller."""

    def on_retry(attempt: int, exc: Exception) -> None:
        logger.warning("Transient %s from LLM provider; retrying (attempt %d)", type(exc).__name__, attempt + 1)
        info = _call_info.get()
        if info is not None:
            info["retries"] = info.get("retries", 0) + 1

    return retry_call(function, *args, on_retry=on_retry, **kwargs)


def _note_usage(input_tokens: Any, output_tokens: Any, total_tokens: Any = None) -> None:
    info = _call_info.get()
    if info is not None and (input_tokens is not None or output_tokens is not None or total_tokens is not None):
        info["input_tokens"] = input_tokens
        info["output_tokens"] = output_tokens
        # The provider's own total when it sends one: Gemini's includes thinking tokens.
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        info["total_tokens"] = total_tokens


_RETRY_AFTER = re.compile(r"(?:retryDelay['\"]?\s*:\s*['\"]?|retry in\s+)(\d+)", re.IGNORECASE)


def classify_provider_error(text: str) -> tuple[str | None, int | None]:
    """Only an error that names a quota counts as exhausted: other 429s are capacity or rate limits."""
    retry = _RETRY_AFTER.search(text)
    retry_after = int(retry.group(1)) if retry else None
    if "RESOURCE_EXHAUSTED" in text and "quota" in text.lower() or "exceeded your current quota" in text.lower():
        return "quota_exhausted", retry_after
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower():
        return "rate_limited", retry_after
    return None, retry_after


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
    extra = {key: value for key, value in (_call_info.get() or {}).items() if value}
    record_step(stage, engine, ok=ok, detail=error, latency_ms=(time.perf_counter() - started) * 1000, **extra)


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

    usage = getattr(response, "usage_metadata", None)
    _note_usage(
        getattr(usage, "prompt_token_count", None),
        getattr(usage, "candidates_token_count", None),
        getattr(usage, "total_token_count", None),
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

    usage = getattr(response, "usage", None)
    _note_usage(getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
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
_breakers: dict[str, CircuitBreaker] = {}
_bulkheads: dict[str, Bulkhead] = {}
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
    info = _call_info.get()
    if info is not None:
        info["error_kind"], info["retry_after_seconds"] = classify_provider_error(text)
    if any(marker in text for marker in _HARD_FAILURE_MARKERS):
        _unavailable_until[provider] = time.monotonic() + _HARD_FAILURE_COOLDOWN_SECONDS
    elif is_transient(exc):
        _breaker(provider).record_failure()


def _breaker(provider: str) -> CircuitBreaker:
    return _breakers.setdefault(provider, CircuitBreaker(f"llm:{provider}"))


def _attempt(function: Any, provider: str, system: str, user: str, stage: str) -> Any:
    """One provider attempt behind its circuit breaker and concurrency limit."""
    breaker = _breaker(provider)
    engine = f"{provider}:{_model_name(provider)}"
    if not breaker.allow():
        record_step(stage, engine, ok=False, detail=f"Skipped: {provider} is failing repeatedly (circuit open).")
        return None
    _call_info.set({})
    bulkhead = _bulkheads.setdefault(provider, Bulkhead(f"LLM provider {provider}", settings.llm_max_concurrency))
    try:
        with bulkhead.slot():
            result = function(provider, system, user, stage)
    except BackpressureError as exc:
        record_step(stage, engine, ok=False, detail=str(exc))
        return None
    if result is not None:
        breaker.record_success()
    return result


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
    if _blocked_by_plan(stage):
        return None
    providers = _providers_for(override, stage)
    if not providers:
        _record_llm(stage, None, False, time.perf_counter(), "No LLM provider is configured for this request.")
        return None
    for provider in providers:
        result = _attempt(_complete_json_with, provider, system, user, stage)
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
            usage = getattr(response, "usage", None)
            _note_usage(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
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
    if _blocked_by_plan(stage):
        return None
    providers = _providers_for(override, stage)
    if not providers:
        _record_llm(stage, None, False, time.perf_counter(), "No LLM provider is configured for this request.")
        return None
    for provider in providers:
        result = _attempt(_complete_text_with, provider, system, user, stage)
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
            usage = getattr(response, "usage", None)
            _note_usage(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
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
