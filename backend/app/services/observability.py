"""Request metrics and structured logs for Ask AI. Telemetry carries identifiers, routes, engines
and timings; question text and result rows are never recorded."""

import json
import logging
from collections import Counter
from contextvars import ContextVar
from threading import Lock
from typing import Any

from ..sensitive_data import redact_text

logger = logging.getLogger(__name__)
# Set per HTTP request and returned as X-Request-ID, so a log line can be tied to one request.
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
_lock = Lock()
_counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_LATENCY_BUCKETS_SECONDS = (0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)


def increment(name: str, value: float = 1, **labels: str) -> None:
    with _lock:
        _counters[(name, tuple(sorted(labels.items())))] += value


def observe_request(
    *,
    audit_id: str | None,
    conversation_id: Any,
    username: str,
    domain: str,
    route: str | None,
    status: str,
    trace: list[dict[str, Any]],
    duration_seconds: float,
) -> None:
    providers = sorted({
        step["engine"].split(":")[0]
        for step in trace
        if step.get("ok") and ":" in step.get("engine", "") and step["stage"] in {"planner", "sql_generation", "synthesis"}
    })
    input_tokens = sum(step.get("input_tokens") or 0 for step in trace)
    output_tokens = sum(step.get("output_tokens") or 0 for step in trace)
    retries = sum(step.get("retries") or 0 for step in trace)
    failures = [step["stage"] for step in trace if not step.get("ok")]

    increment("ryze_ask_requests_total", route=route or "unknown", status=status, domain=domain)
    increment("ryze_ask_duration_seconds_sum", duration_seconds, route=route or "unknown")
    increment("ryze_ask_duration_seconds_count", route=route or "unknown")
    for bucket in _LATENCY_BUCKETS_SECONDS:
        if duration_seconds <= bucket:
            increment("ryze_ask_duration_seconds_bucket", route=route or "unknown", le=str(bucket))
    increment("ryze_llm_input_tokens_total", input_tokens)
    increment("ryze_llm_output_tokens_total", output_tokens)
    increment("ryze_llm_retries_total", retries)
    for stage in failures:
        increment("ryze_ask_stage_failures_total", stage=stage)

    logger.info(
        "ask_ai %s",
        json.dumps({
            "audit_id": audit_id,
            "request_id": current_request_id.get(),
            "conversation_id": str(conversation_id) if conversation_id else None,
            "user": username,
            "domain": domain,
            "route": route,
            "status": status,
            "providers": providers,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "retries": retries,
            "failed_stages": failures,
            "duration_ms": round(duration_seconds * 1000),
        }),
    )


def render_prometheus() -> str:
    with _lock:
        items = sorted(_counters.items())
    lines = []
    for (name, labels), value in items:
        label_text = ",".join(f'{key}="{val}"' for key, val in labels)
        lines.append(f"{name}{{{label_text}}} {value}" if label_text else f"{name} {value}")
    return "\n".join(lines) + "\n"


class RedactingFilter(logging.Filter):
    """Last line of defence: sensitive values never reach log output, whatever logged them."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": current_request_id.get(),
        }
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload)
