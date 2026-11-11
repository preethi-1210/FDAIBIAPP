"""Reliability primitives for LLM, warehouse and tool calls: retry classification, bounded
retries with jittered backoff, circuit breakers, concurrency limits and request deadlines."""

import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import BoundedSemaphore, Lock
from typing import Any, Callable, Iterator

_TRANSIENT_ERROR_NAMES = {
    "ServerError",
    "InternalServerError",
    "ServiceUnavailable",
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "TimeoutError",
    "ConnectionError",
    "ConnectTimeout",
    "ReadTimeout",
    "RequestError",
    "OperationalError",
}
_TRANSIENT_MARKERS = ("503", "429", "overloaded", "UNAVAILABLE", "temporarily")
# Exhausted quotas, bad keys and rejected statements fail the same way every time.
_PERMANENT_MARKERS = ("RESOURCE_EXHAUSTED", "quota", "API_KEY_INVALID", "PERMISSION_DENIED", "UNRESOLVED_COLUMN", "PARSE_SYNTAX_ERROR")


class CircuitOpenError(RuntimeError):
    pass


class BackpressureError(RuntimeError):
    pass


class DeadlineExceededError(RuntimeError):
    pass


def is_transient(exc: Exception) -> bool:
    text = str(exc)
    if any(marker.lower() in text.lower() for marker in _PERMANENT_MARKERS):
        return False
    return type(exc).__name__ in _TRANSIENT_ERROR_NAMES or any(marker in text for marker in _TRANSIENT_MARKERS)


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential backoff with jitter, so concurrent callers do not retry in lockstep."""
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return random.uniform(ceiling / 2, ceiling)


def retry_call(
    function: Callable[..., Any],
    *args: Any,
    attempts: int = 3,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Call with bounded retries. Only transient failures retry, and never past the deadline."""
    for attempt in range(1, attempts + 1):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            delay = backoff_delay(attempt)
            limit = remaining_seconds()
            if limit is not None and limit <= delay:
                raise
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(delay)
    raise AssertionError("unreachable")


class CircuitBreaker:
    """Stops calling an unhealthy dependency, then lets one probe through after a cool-off."""

    def __init__(self, name: str, failure_threshold: int = 5, reset_seconds: float = 30.0) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if time.monotonic() - self._opened_at >= self.reset_seconds else "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at >= self.reset_seconds:
                # Only this probe passes; a failure restarts the cool-off.
                self._opened_at = time.monotonic()
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        self.record_success()


class Bulkhead:
    """Limits concurrent calls to a dependency and rejects instead of queueing forever."""

    def __init__(self, name: str, limit: int, wait_seconds: float = 30.0) -> None:
        self.name = name
        self.wait_seconds = wait_seconds
        self._semaphore = BoundedSemaphore(max(1, limit))

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._semaphore.acquire(timeout=self.wait_seconds):
            raise BackpressureError(f"{self.name} is at its concurrency limit; try again shortly.")
        try:
            yield
        finally:
            self._semaphore.release()


_deadline: ContextVar[float | None] = ContextVar("ryze_request_deadline", default=None)


@contextmanager
def request_deadline(seconds: float) -> Iterator[None]:
    token = _deadline.set(time.monotonic() + seconds if seconds > 0 else None)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_seconds() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def check_deadline(stage: str) -> None:
    limit = remaining_seconds()
    if limit is not None and limit <= 0:
        raise DeadlineExceededError(f"The request ran out of time before {stage}. No further work was started.")
