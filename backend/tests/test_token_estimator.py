"""
Tests for app/utils/token_estimator.py

Covers: estimate_request_cost, cost_from_text, and the private helpers for
multi-modal content and message parsing.
"""
from __future__ import annotations

import pytest

from app.utils.token_estimator import cost_from_text, estimate_request_cost


# ── estimate_request_cost ─────────────────────────────────────────────────────

class TestEstimateRequestCostEmptyInput:
    def test_none_body_returns_default(self):
        assert estimate_request_cost(None) == 10

    def test_empty_dict_returns_default(self):
        assert estimate_request_cost({}) == 10

    def test_custom_default_cost(self):
        assert estimate_request_cost(None, default_cost=42) == 42


class TestEstimateRequestCostOpenAIMessages:
    def test_single_user_message(self):
        body = {"messages": [{"role": "user", "content": "Hello world!"}]}
        cost = estimate_request_cost(body, chars_per_token=4.0)
        # "Hello world!" = 12 chars → ceil(12/4) = 3 tokens + 4 overhead = 7
        assert cost == 7

    def test_multi_message_conversation(self):
        body = {
            "messages": [
                {"role": "system",    "content": "You are a helpful assistant."},
                {"role": "user",      "content": "What is 2+2?"},
                {"role": "assistant", "content": "4"},
            ]
        }
        cost = estimate_request_cost(body, chars_per_token=4.0)
        # system: "You are a helpful assistant." = 28 chars → ceil(28/4)=7 tokens + 4 overhead = 11
        # user:   "What is 2+2?"                = 12 chars → ceil(12/4)=3 tokens + 4 overhead = 7
        # asst:   "4"                            =  1 char  → ceil(1/4)=1  token  + 4 overhead = 5
        # total: 11 + 7 + 5 = 23
        assert cost == 23

    def test_max_tokens_added_to_estimate(self):
        body = {
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        }
        base = estimate_request_cost({"messages": [{"role": "user", "content": "Hi"}]})
        with_max = estimate_request_cost(body)
        assert with_max == base + 100

    def test_max_completion_tokens_alias(self):
        body_a = {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 50}
        body_b = {"messages": [{"role": "user", "content": "Hi"}], "max_completion_tokens": 50}
        assert estimate_request_cost(body_a) == estimate_request_cost(body_b)

    def test_max_output_tokens_alias(self):
        body = {"messages": [{"role": "user", "content": "Hi"}], "max_output_tokens": 50}
        expected = estimate_request_cost(
            {"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 50}
        )
        assert estimate_request_cost(body) == expected

    def test_cost_clamped_to_max(self):
        # Very long message should be clamped at max_cost
        body = {"messages": [{"role": "user", "content": "x" * 100_000}], "max_tokens": 5000}
        assert estimate_request_cost(body, max_cost=10_000) == 10_000

    def test_minimum_cost_is_one(self):
        # Even empty messages must cost at least 1
        body = {"messages": [{"role": "user", "content": ""}]}
        assert estimate_request_cost(body) >= 1


class TestEstimateRequestCostAnthropicFormat:
    def test_system_prompt_counted(self):
        body_no_system = {"messages": [{"role": "user", "content": "Hi"}]}
        body_with_system = {
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        assert estimate_request_cost(body_with_system) > estimate_request_cost(body_no_system)

    def test_multimodal_text_block(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image."}
                    ],
                }
            ]
        }
        cost = estimate_request_cost(body, chars_per_token=4.0)
        # "Describe this image." = 20 chars → 5 tokens + 4 overhead = 9
        assert cost == 9

    def test_tool_use_block_adds_flat_rate(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_use", "id": "x", "name": "fn", "input": {}}],
                }
            ]
        }
        cost = estimate_request_cost(body)
        # tool_use adds 10 flat + 4 overhead
        assert cost == 14


class TestEstimateRequestCostLegacyCompletions:
    def test_string_prompt(self):
        body = {"prompt": "Once upon a time", "max_tokens": 50}
        cost = estimate_request_cost(body, chars_per_token=4.0)
        # "Once upon a time" = 16 chars → 4 tokens + 50 = 54
        assert cost == 54

    def test_prompt_list(self):
        body = {"prompt": ["Hello", "World"]}
        cost_single = estimate_request_cost({"prompt": "HelloWorld"})
        cost_list = estimate_request_cost(body)
        # Both should produce equal or very similar estimates
        assert abs(cost_single - cost_list) <= 2


# ── cost_from_text ────────────────────────────────────────────────────────────

class TestCostFromText:
    def test_basic_estimate(self):
        cost = cost_from_text("Hello world!", chars_per_token=4.0)
        # 12 chars → ceil(12/4) = 3
        assert cost == 3

    def test_empty_string(self):
        # Empty text returns minimum 1
        assert cost_from_text("") >= 1

    def test_clamped_to_max(self):
        assert cost_from_text("x" * 100_000, max_cost=500) == 500

    def test_custom_chars_per_token(self):
        # With 2 chars per token, 8 chars → 4 tokens
        assert cost_from_text("abcdefgh", chars_per_token=2.0) == 4
