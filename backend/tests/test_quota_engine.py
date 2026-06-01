"""
Tests for the quota engine: usage service + workspace budget checks.

Covers:
  - _fallback_check_usage in DEMO_MODE (fail-open)
  - _fallback_check_usage in production mode (fail-closed)
  - check_ai_usage when circuit breaker is OPEN (fallback path)
  - check_ai_usage with mocked RPC returning allowed=True
  - check_ai_usage with mocked RPC returning workspace_budget_exceeded
  - check_ai_usage with mocked RPC returning account_suspended
  - Anomaly detection task is triggered only on workspace+allowed response

All tests mock Supabase; no network calls are made.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState


# ── Fallback behaviour ────────────────────────────────────────────────────────

class TestFallbackCheckUsage:
    async def test_demo_mode_fallback_allows(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        from app.services.usage import _fallback_check_usage
        result = await _fallback_check_usage(
            "user-uuid", "openai", "gpt-4o-mini", 100, 0.001
        )
        assert result["allowed"] is True
        assert result["reason"] == "circuit_open_degraded_mode"

    async def test_production_fallback_rejects(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")
        from app.services.usage import _fallback_check_usage
        result = await _fallback_check_usage(
            "user-uuid", "openai", "gpt-4o-mini", 100, 0.001
        )
        assert result["allowed"] is False
        assert result["reason"] == "supabase_unavailable"

    async def test_fallback_preserves_provider_and_model(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        from app.services.usage import _fallback_check_usage
        result = await _fallback_check_usage(
            "u", "anthropic", "claude-3-5-sonnet", 500, 0.05
        )
        assert result["provider"] == "anthropic"
        assert result["model"] == "claude-3-5-sonnet"
        assert result["estimated_cost"] == 0.05


# ── check_ai_usage with circuit breaker in OPEN state ────────────────────────

class TestCheckAiUsageCircuitOpen:
    @staticmethod
    async def _pre_open_breaker() -> CircuitBreaker:
        """Return a CircuitBreaker already in OPEN state."""
        cb = CircuitBreaker(
            CircuitBreakerConfig(name="test", failure_threshold=2, recovery_timeout=9999.0)
        )

        async def fail(*a, **k):
            raise RuntimeError("db down")

        async def noop(*a, **k):
            return None

        # First failure: re-raises (threshold not yet reached)
        with pytest.raises(RuntimeError):
            await cb.call(fail, noop)
        # Second failure: opens circuit, returns fallback (does NOT raise)
        await cb.call(fail, noop)
        assert cb.state == CircuitState.OPEN
        return cb

    async def test_open_circuit_returns_fallback_production(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")
        cb = await self._pre_open_breaker()

        with patch("app.services.circuit_breaker.get_circuit_breaker", return_value=cb):
            from app.services import usage as usage_mod
            result = await usage_mod.check_ai_usage(
                "u", "openai", "gpt-4o-mini", 100, 0.001
            )
        assert result["allowed"] is False
        assert result["reason"] == "supabase_unavailable"

    async def test_open_circuit_demo_mode_allows(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        cb = await self._pre_open_breaker()

        with patch("app.services.circuit_breaker.get_circuit_breaker", return_value=cb):
            from app.services import usage as usage_mod
            result = await usage_mod.check_ai_usage(
                "u", "openai", "gpt-4o-mini", 100, 0.001
            )
        assert result["allowed"] is True


# ── check_ai_usage with mocked RPC ───────────────────────────────────────────

class TestCheckAiUsageMockedRPC:
    def _allowed_rpc_response(self) -> dict:
        return {
            "allowed":              True,
            "reason":               "allowed",
            "remaining_messages":   950,
            "remaining_budget_usd": 17.82,
            "estimated_cost":       0.002,
            "provider":             "openai",
            "model":                "gpt-4o-mini",
            "workspace_id":         None,
            "workspace_spend_usd":  0,
        }

    async def test_allowed_response_passes_through(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")

        execute_mock = AsyncMock(return_value=MagicMock(data=self._allowed_rpc_response()))
        rpc_builder  = MagicMock(execute=execute_mock)
        client_mock  = MagicMock(rpc=MagicMock(return_value=rpc_builder))

        with (
            patch("app.services.usage.get_supabase_client", return_value=client_mock),
            patch("app.services.circuit_breaker.get_circuit_breaker",
                  return_value=CircuitBreaker(CircuitBreakerConfig(name="t"))),
            patch("app.services.usage.asyncio.create_task"),  # suppress anomaly task
        ):
            from app.services import usage as usage_mod
            result = await usage_mod.check_ai_usage(
                "00000000-0000-0000-0000-000000000001",
                "openai", "gpt-4o-mini", 100, 0.002,
            )
        assert result["allowed"] is True
        assert result["reason"]  == "allowed"

    async def test_workspace_budget_exceeded_passes_through(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")

        rpc_data = {
            "allowed":               False,
            "reason":                "workspace_budget_exceeded",
            "remaining_messages":    0,
            "remaining_budget_usd":  0.45,
            "estimated_cost":        1.00,
            "provider":              "openai",
            "model":                 "gpt-4o",
            "workspace_id":          "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "workspace_budget_usd":  100.00,
            "workspace_spend_usd":   99.55,
            "workspace_remaining_usd": 0.45,
        }
        execute_mock = AsyncMock(return_value=MagicMock(data=rpc_data))
        rpc_builder  = MagicMock(execute=execute_mock)
        client_mock  = MagicMock(rpc=MagicMock(return_value=rpc_builder))

        with (
            patch("app.services.usage.get_supabase_client", return_value=client_mock),
            patch("app.services.circuit_breaker.get_circuit_breaker",
                  return_value=CircuitBreaker(CircuitBreakerConfig(name="t"))),
        ):
            from app.services import usage as usage_mod
            result = await usage_mod.check_ai_usage(
                "u", "openai", "gpt-4o", 500, 1.00
            )
        assert result["allowed"] is False
        assert result["reason"]  == "workspace_budget_exceeded"

    async def test_account_suspended_passes_through(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")

        rpc_data = {
            "allowed":              False,
            "reason":               "account_suspended",
            "remaining_messages":   0,
            "remaining_budget_usd": 0,
            "estimated_cost":       0.001,
            "provider":             "openai",
            "model":                "gpt-4o-mini",
            "workspace_id":         None,
            "workspace_spend_usd":  0,
        }
        execute_mock = AsyncMock(return_value=MagicMock(data=rpc_data))
        rpc_builder  = MagicMock(execute=execute_mock)
        client_mock  = MagicMock(rpc=MagicMock(return_value=rpc_builder))

        with (
            patch("app.services.usage.get_supabase_client", return_value=client_mock),
            patch("app.services.circuit_breaker.get_circuit_breaker",
                  return_value=CircuitBreaker(CircuitBreakerConfig(name="t"))),
        ):
            from app.services import usage as usage_mod
            result = await usage_mod.check_ai_usage(
                "u", "openai", "gpt-4o-mini", 50, 0.001
            )
        assert result["allowed"] is False
        assert result["reason"]  == "account_suspended"


# ── Anomaly detection task scheduling ────────────────────────────────────────

class TestAnomalyTaskScheduling:
    async def test_anomaly_task_created_on_workspace_allow(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")

        rpc_data = {
            "allowed":              True,
            "reason":               "allowed",
            "remaining_messages":   900,
            "remaining_budget_usd": 15.0,
            "estimated_cost":       0.005,
            "provider":             "openai",
            "model":                "gpt-4o-mini",
            "workspace_id":         "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "workspace_spend_usd":  45.0,
        }
        execute_mock = AsyncMock(return_value=MagicMock(data=rpc_data))
        rpc_builder  = MagicMock(execute=execute_mock)
        client_mock  = MagicMock(rpc=MagicMock(return_value=rpc_builder))

        task_created: list[str] = []

        def _fake_create_task(coro):
            task_created.append("created")
            # Consume the coroutine to avoid ResourceWarning
            async def _consume():
                try:
                    await coro
                except Exception:
                    pass
            return asyncio.ensure_future(_consume())

        with (
            patch("app.services.usage.get_supabase_client", return_value=client_mock),
            patch("app.services.circuit_breaker.get_circuit_breaker",
                  return_value=CircuitBreaker(CircuitBreakerConfig(name="t"))),
            patch("app.services.usage.asyncio.create_task", side_effect=_fake_create_task),
        ):
            from app.services import usage as usage_mod
            await usage_mod.check_ai_usage("u", "openai", "gpt-4o-mini", 50, 0.005)

        assert len(task_created) == 1, "Expected one anomaly detection task to be scheduled"

    async def test_no_anomaly_task_without_workspace(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")

        rpc_data = {
            "allowed":              True,
            "reason":               "allowed",
            "remaining_messages":   900,
            "remaining_budget_usd": 15.0,
            "estimated_cost":       0.005,
            "provider":             "openai",
            "model":                "gpt-4o-mini",
            "workspace_id":         None,  # no workspace
            "workspace_spend_usd":  0,
        }
        execute_mock = AsyncMock(return_value=MagicMock(data=rpc_data))
        rpc_builder  = MagicMock(execute=execute_mock)
        client_mock  = MagicMock(rpc=MagicMock(return_value=rpc_builder))
        task_created: list = []

        with (
            patch("app.services.usage.get_supabase_client", return_value=client_mock),
            patch("app.services.circuit_breaker.get_circuit_breaker",
                  return_value=CircuitBreaker(CircuitBreakerConfig(name="t"))),
            patch("app.services.usage.asyncio.create_task",
                  side_effect=lambda c: task_created.append(c)),
        ):
            from app.services import usage as usage_mod
            await usage_mod.check_ai_usage("u", "openai", "gpt-4o-mini", 50, 0.005)

        assert len(task_created) == 0, "No anomaly task should fire without a workspace"
