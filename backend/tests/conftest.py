"""
Shared pytest fixtures for the Aivora Gatekeeper test suite.

All async tests use pytest-asyncio in auto mode (configured in pyproject.toml).
External services (Supabase, OpenRouter) are never called from unit tests;
any function that would hit the network must be mocked.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Circuit breaker fixtures ──────────────────────────────────────────────────

@pytest.fixture
def cb_config_strict():
    """Circuit breaker with a low threshold for fast-tripping in tests."""
    from app.services.circuit_breaker import CircuitBreakerConfig
    return CircuitBreakerConfig(
        name="test_strict",
        failure_threshold=2,
        recovery_timeout=60.0,
        half_open_max_attempts=2,
    )


@pytest.fixture
def fresh_breaker(cb_config_strict):
    """A fresh CircuitBreaker instance (not the module singleton)."""
    from app.services.circuit_breaker import CircuitBreaker
    return CircuitBreaker(cb_config_strict)


# ── Supabase mock helpers ─────────────────────────────────────────────────────

def make_rpc_response(data: Any) -> MagicMock:
    """Build a fake supabase RPC execute() coroutine result."""
    mock = MagicMock()
    mock.data = data
    return mock


def make_supabase_mock(rpc_data: Any = None) -> MagicMock:
    """
    Return a mock Supabase AsyncClient where:
      client.rpc(name, params).execute() → awaitable yielding make_rpc_response(rpc_data)
    """
    execute_mock = AsyncMock(return_value=make_rpc_response(rpc_data))
    rpc_builder = MagicMock()
    rpc_builder.execute = execute_mock
    client = MagicMock()
    client.rpc = MagicMock(return_value=rpc_builder)
    return client


# ── Settings override ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_settings_cache():
    """
    Clear the @lru_cache on get_settings() before every test so that env-var
    overrides applied inside a test (via monkeypatch.setenv) take effect.
    """
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
