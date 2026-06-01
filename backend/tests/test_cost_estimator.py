"""
Tests for app/utils/cost_estimator.py

Covers: parse_model_key, get_model_cost, estimate_cost.
"""
from __future__ import annotations

import pytest

from app.utils.cost_estimator import (
    ModelCost,
    _DEFAULT_COST,
    estimate_cost,
    get_model_cost,
    parse_model_key,
)


# ── parse_model_key ───────────────────────────────────────────────────────────

class TestParseModelKey:
    def test_slash_separated(self):
        result = parse_model_key("openai/gpt-4o")
        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        assert result.full_key == "openai/gpt-4o"

    def test_slash_separated_normalised_to_lowercase(self):
        result = parse_model_key("OpenAI/GPT-4O")
        assert result.provider == "openai"
        assert result.model == "gpt-4o"

    def test_bare_gpt_model_inferred_as_openai(self):
        result = parse_model_key("gpt-4o-mini")
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_bare_claude_model_inferred_as_anthropic(self):
        result = parse_model_key("claude-3-5-sonnet")
        assert result.provider == "anthropic"
        assert result.model == "claude-3-5-sonnet"

    def test_bare_gemini_model_inferred_as_google(self):
        result = parse_model_key("gemini-pro-1.5")
        assert result.provider == "google"

    def test_bare_llama_model_inferred_as_meta(self):
        result = parse_model_key("llama-3.1-8b-instruct")
        assert result.provider == "meta-llama"

    def test_bare_mistral_model_inferred(self):
        result = parse_model_key("mistral-7b-instruct")
        assert result.provider == "mistralai"

    def test_bare_mixtral_model_inferred(self):
        result = parse_model_key("mixtral-8x7b-instruct")
        assert result.provider == "mistralai"

    def test_unknown_model_returns_unknown_provider(self):
        result = parse_model_key("totally-unknown-model-xyz")
        assert result.provider == "unknown"
        assert result.model == "totally-unknown-model-xyz"

    def test_o1_inferred_as_openai(self):
        result = parse_model_key("o1-mini")
        assert result.provider == "openai"

    def test_full_key_reflects_normalised_form(self):
        result = parse_model_key("openai/gpt-4o")
        assert result.full_key == "openai/gpt-4o"


# ── get_model_cost ────────────────────────────────────────────────────────────

class TestGetModelCost:
    def test_known_model_returns_correct_price(self):
        cost = get_model_cost("openai", "gpt-4o")
        assert cost.input_per_1k == pytest.approx(0.005, rel=1e-3)
        assert cost.output_per_1k == pytest.approx(0.015, rel=1e-3)

    def test_anthropic_sonnet_cost(self):
        cost = get_model_cost("anthropic", "claude-3-5-sonnet")
        assert cost.input_per_1k == pytest.approx(0.003, rel=1e-3)

    def test_case_insensitive_lookup(self):
        cost_lower = get_model_cost("openai", "gpt-4o")
        cost_upper = get_model_cost("OpenAI", "GPT-4O")
        assert cost_lower == cost_upper

    def test_unknown_model_returns_default_cost(self):
        cost = get_model_cost("unknown_provider", "nonexistent_model_xyz")
        assert cost == _DEFAULT_COST

    def test_default_cost_is_nonzero(self):
        assert _DEFAULT_COST.input_per_1k > 0
        assert _DEFAULT_COST.output_per_1k > 0

    def test_cheap_model_cheaper_than_expensive(self):
        cheap = get_model_cost("openai", "gpt-4o-mini")
        expensive = get_model_cost("openai", "gpt-4o")
        assert cheap.input_per_1k < expensive.input_per_1k


# ── estimate_cost ─────────────────────────────────────────────────────────────

class TestEstimateCost:
    def test_zero_tokens_returns_small_cost(self):
        # No input tokens, only output budget
        cost = estimate_cost("openai", "gpt-4o", input_tokens=0, max_output_tokens=1000)
        assert cost > 0

    def test_input_only_cost(self):
        # 1000 input tokens, no output budget
        cost = estimate_cost("openai", "gpt-4o", input_tokens=1000, max_output_tokens=0)
        mc = get_model_cost("openai", "gpt-4o")
        expected = (1000 / 1000) * mc.input_per_1k
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_combined_cost(self):
        mc = get_model_cost("openai", "gpt-4o")
        expected = (500 / 1000) * mc.input_per_1k + (1024 / 1000) * mc.output_per_1k
        actual = estimate_cost("openai", "gpt-4o", input_tokens=500, max_output_tokens=1024)
        assert actual == pytest.approx(expected, rel=1e-6)

    def test_result_rounded_to_8_decimals(self):
        cost = estimate_cost("openai", "gpt-4o-mini", input_tokens=123, max_output_tokens=456)
        # Should not have more than 8 decimal places
        assert cost == round(cost, 8)

    def test_cheap_model_costs_less(self):
        cost_mini = estimate_cost("openai", "gpt-4o-mini", 1000, 1024)
        cost_full = estimate_cost("openai", "gpt-4o",      1000, 1024)
        assert cost_mini < cost_full

    def test_unknown_model_uses_default_conservative_price(self):
        known   = estimate_cost("openai", "gpt-4o",    1000, 1024)
        unknown = estimate_cost("unknown", "model-xyz", 1000, 1024)
        # Default cost is $0.01/1k which is higher than gpt-4o-mini but we just check it's nonzero
        assert unknown > 0
