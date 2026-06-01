"""
Tests for app/services/circuit_breaker.py

Tests the full CLOSED → OPEN → HALF_OPEN → CLOSED state machine, counter
behaviour, and fallback invocation.  Time-dependent transitions mock
time.monotonic to avoid real sleeps.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _succeed(*_args, **_kwargs) -> str:
    return "ok"


async def _fail(*_args, **_kwargs) -> str:
    raise RuntimeError("boom")


async def _fallback(*_args, **_kwargs) -> str:
    return "fallback"


def _make_breaker(threshold: int = 3, recovery: float = 60.0, half_open: int = 2):
    return CircuitBreaker(
        CircuitBreakerConfig(
            name="test",
            failure_threshold=threshold,
            recovery_timeout=recovery,
            half_open_max_attempts=half_open,
        )
    )


async def _trip_breaker(cb: CircuitBreaker) -> None:
    """
    Open cb by driving exactly failure_threshold failures through it.

    Key behaviour of CircuitBreaker.call():
      - Failures 1 .. (threshold-1): re-raise the exception.
      - Failure N (= threshold): opens the circuit AND returns the fallback
        result (does NOT raise).  This is by design so callers always get
        a usable result on the transition call.
    """
    t = cb._cfg.failure_threshold
    for _ in range(t - 1):
        with pytest.raises(RuntimeError):
            await cb.call(_fail, _fallback)
    # The threshold-th failure opens the circuit and returns fallback
    result = await cb.call(_fail, _fallback)
    assert result == "fallback"
    assert cb.state == CircuitState.OPEN


# ── Initial state ─────────────────────────────────────────────────────────────

class TestInitialState:
    async def test_starts_closed(self):
        cb = _make_breaker()
        assert cb.state == CircuitState.CLOSED

    async def test_snapshot_initial_counters_zero(self):
        cb = _make_breaker()
        snap = cb.snapshot()
        assert snap["total_calls"]     == 0
        assert snap["total_successes"] == 0
        assert snap["total_failures"]  == 0
        assert snap["total_fallbacks"] == 0
        assert snap["failure_count"]   == 0
        assert snap["state"]           == "closed"


# ── CLOSED → OPEN transition ──────────────────────────────────────────────────

class TestClosedToOpen:
    async def test_opens_after_threshold_failures(self):
        cb = _make_breaker(threshold=3)
        await _trip_breaker(cb)   # helper asserts OPEN state
        assert cb.state == CircuitState.OPEN

    async def test_single_success_resets_failure_count(self):
        cb = _make_breaker(threshold=3)
        # Two failures (< threshold, so still CLOSED and re-raises)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail, _fallback)
        # One success — failure counter resets to 0
        await cb.call(_succeed, _fallback)
        assert cb.state == CircuitState.CLOSED
        assert cb.snapshot()["failure_count"] == 0

    async def test_total_failure_counter_accumulates(self):
        cb = _make_breaker(threshold=5)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(_fail, _fallback)
        assert cb.snapshot()["total_failures"] == 3

    async def test_fallback_called_when_open(self):
        cb = _make_breaker(threshold=2)
        await _trip_breaker(cb)
        # Now OPEN — any call must use the fallback
        result = await cb.call(_succeed, _fallback)
        assert result == "fallback"

    async def test_fallback_counter_increments(self):
        cb = _make_breaker(threshold=2)
        await _trip_breaker(cb)
        # The trip itself already recorded one fallback; add three more
        for _ in range(3):
            await cb.call(_succeed, _fallback)
        assert cb.snapshot()["total_fallbacks"] >= 4

    async def test_opened_at_timestamp_set(self):
        cb = _make_breaker(threshold=2)
        before = time.monotonic()
        await _trip_breaker(cb)
        after = time.monotonic()
        opened = cb.snapshot()["opened_at"]
        assert opened is not None
        assert before <= opened <= after


# ── OPEN → HALF_OPEN transition ───────────────────────────────────────────────

class TestOpenToHalfOpen:
    async def test_transitions_to_half_open_after_recovery(self):
        cb = _make_breaker(threshold=2, recovery=30.0)
        await _trip_breaker(cb)
        # Fake that 31 seconds have elapsed
        cb._s.opened_at = time.monotonic() - 31.0
        # Next call probes in HALF_OPEN and succeeds
        result = await cb.call(_succeed, _fallback)
        assert result == "ok"

    async def test_half_open_failure_reopens_immediately(self):
        cb = _make_breaker(threshold=2, recovery=30.0)
        await _trip_breaker(cb)
        cb._s.opened_at = time.monotonic() - 31.0
        # In HALF_OPEN, a failure re-opens immediately (returns fallback)
        result = await cb.call(_fail, _fallback)
        assert result == "fallback"
        assert cb.state == CircuitState.OPEN

    async def test_closes_after_required_half_open_successes(self):
        cb = _make_breaker(threshold=2, recovery=30.0, half_open=2)
        await _trip_breaker(cb)
        cb._s.opened_at = time.monotonic() - 31.0
        # First probe — HALF_OPEN
        await cb.call(_succeed, _fallback)
        # Second probe — should close
        await cb.call(_succeed, _fallback)
        assert cb.state == CircuitState.CLOSED


# ── Success path ──────────────────────────────────────────────────────────────

class TestSuccessPath:
    async def test_success_returns_result(self):
        cb = _make_breaker()
        result = await cb.call(_succeed, _fallback)
        assert result == "ok"

    async def test_success_increments_counters(self):
        cb = _make_breaker()
        for _ in range(5):
            await cb.call(_succeed, _fallback)
        snap = cb.snapshot()
        assert snap["total_calls"]     == 5
        assert snap["total_successes"] == 5
        assert snap["total_failures"]  == 0


# ── Snapshot integrity ────────────────────────────────────────────────────────

class TestSnapshot:
    async def test_snapshot_includes_all_fields(self):
        cb = _make_breaker()
        snap = cb.snapshot()
        expected_keys = {
            "name", "state", "failure_count", "half_open_successes",
            "opened_at", "last_failure_at",
            "total_calls", "total_successes", "total_failures", "total_fallbacks",
        }
        assert expected_keys == set(snap.keys())

    async def test_snapshot_name_matches_config(self):
        cb = CircuitBreaker(CircuitBreakerConfig(name="my_service"))
        assert cb.snapshot()["name"] == "my_service"
