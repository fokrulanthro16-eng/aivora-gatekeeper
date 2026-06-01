"""
Async circuit breaker with CLOSED → OPEN → HALF_OPEN state machine.

Trips on:
  • Any exception raised by the wrapped coroutine (including HTTP 5xx / 429
    that the caller translates into a Python exception before calling .call()).

Recovery:
  • After CB_RECOVERY_TIMEOUT_SECONDS in OPEN state, transitions to HALF_OPEN.
  • CB_HALF_OPEN_MAX_ATTEMPTS consecutive successes close the circuit.
  • Any failure in HALF_OPEN re-opens immediately.

All state mutations happen inside an asyncio.Lock so the breaker is safe for
concurrent FastAPI request handlers.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"        # Normal — all calls go through
    OPEN = "open"            # Tripped — all calls use fallback immediately
    HALF_OPEN = "half_open"  # Recovery probe — limited calls go through


@dataclass
class CircuitBreakerConfig:
    name: str = "default"
    failure_threshold: int = 5      # consecutive failures before opening
    recovery_timeout: float = 60.0  # seconds before transitioning OPEN → HALF_OPEN
    half_open_max_attempts: int = 2  # successes needed in HALF_OPEN to close


@dataclass
class _State:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    half_open_successes: int = 0
    opened_at: float | None = None
    last_failure_at: float | None = None
    # Lifetime counters
    total_calls: int = 0
    total_fallbacks: int = 0
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreaker:
    """
    Usage::

        result = await cb.call(
            fn=supabase_rpc,          # the protected coroutine
            fallback=degraded_quota,  # used when OPEN or after re-opening
            user_uuid=uid,
            cost=10,
        )
    """

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._cfg = config
        self._s = _State()
        self._lock = asyncio.Lock()

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._s.state

    async def call(
        self,
        fn: Callable[..., Coroutine[Any, Any, T]],
        fallback: Callable[..., Coroutine[Any, Any, T]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        async with self._lock:
            current_state = self._resolve_state()

        if current_state == CircuitState.OPEN:
            async with self._lock:
                self._s.total_fallbacks += 1
            return await fallback(*args, **kwargs)

        # CLOSED or HALF_OPEN — attempt the real call.
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            async with self._lock:
                self._s.total_calls += 1
                self._s.total_failures += 1
                self._s.last_failure_at = time.monotonic()
                self._on_failure_unsafe()
                # If the failure just opened the circuit, use fallback now.
                just_opened = self._s.state == CircuitState.OPEN
                if just_opened:
                    self._s.total_fallbacks += 1
            if just_opened:
                return await fallback(*args, **kwargs)
            raise

        async with self._lock:
            self._s.total_calls += 1
            self._s.total_successes += 1
            self._on_success_unsafe()

        return result

    def snapshot(self) -> dict[str, Any]:
        s = self._s
        return {
            "name": self._cfg.name,
            "state": s.state.value,
            "failure_count": s.failure_count,
            "half_open_successes": s.half_open_successes,
            "opened_at": s.opened_at,
            "last_failure_at": s.last_failure_at,
            "total_calls": s.total_calls,
            "total_successes": s.total_successes,
            "total_failures": s.total_failures,
            "total_fallbacks": s.total_fallbacks,
        }

    # ── Internal — must be called while self._lock is held ────────────────────

    def _resolve_state(self) -> CircuitState:
        """Transition OPEN → HALF_OPEN if the recovery window has elapsed."""
        if (
            self._s.state == CircuitState.OPEN
            and self._s.opened_at is not None
            and time.monotonic() - self._s.opened_at >= self._cfg.recovery_timeout
        ):
            self._s.state = CircuitState.HALF_OPEN
            self._s.failure_count = 0
            self._s.half_open_successes = 0
        return self._s.state

    def _on_success_unsafe(self) -> None:
        if self._s.state == CircuitState.HALF_OPEN:
            self._s.half_open_successes += 1
            if self._s.half_open_successes >= self._cfg.half_open_max_attempts:
                self._s.state = CircuitState.CLOSED
                self._s.failure_count = 0
                self._s.opened_at = None
        elif self._s.state == CircuitState.CLOSED:
            self._s.failure_count = 0

    def _on_failure_unsafe(self) -> None:
        if self._s.state == CircuitState.HALF_OPEN:
            # Any failure in HALF_OPEN re-opens immediately.
            self._s.state = CircuitState.OPEN
            self._s.opened_at = time.monotonic()
        elif self._s.state == CircuitState.CLOSED:
            self._s.failure_count += 1
            if self._s.failure_count >= self._cfg.failure_threshold:
                self._s.state = CircuitState.OPEN
                self._s.opened_at = time.monotonic()


# ── Module-level singleton ─────────────────────────────────────────────────────

_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        from app.core.config import get_settings

        s = get_settings()
        _breaker = CircuitBreaker(
            CircuitBreakerConfig(
                name="supabase_token_bucket",
                failure_threshold=s.CB_FAILURE_THRESHOLD,
                recovery_timeout=s.CB_RECOVERY_TIMEOUT_SECONDS,
                half_open_max_attempts=s.CB_HALF_OPEN_MAX_ATTEMPTS,
            )
        )
    return _breaker
