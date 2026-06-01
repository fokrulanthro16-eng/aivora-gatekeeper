"""
Dynamic token cost estimator.

Handles three request body shapes:
  • OpenAI chat completions  — { messages: [{role, content}], max_tokens? }
  • OpenAI completions       — { prompt: str, max_tokens? }
  • Anthropic messages       — { system?: str, messages: [...], max_tokens? }
  • Unknown / empty          — returns TOKEN_DEFAULT_COST

The character-per-token ratio defaults to 4.0, which is a reasonable average
for English prose.  The estimator intentionally over-estimates on edge cases
(very short tokens, code, non-Latin scripts) to be conservative.
"""
from __future__ import annotations

import math
from typing import Any


def _chars_to_tokens(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text.strip()) / chars_per_token))


def _extract_text_from_content(
    content: Any,
    chars_per_token: float,
) -> int:
    """Handles both plain string and multi-modal content block arrays."""
    if isinstance(content, str):
        return _chars_to_tokens(content, chars_per_token)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                # text block: { type: "text", text: "..." }
                total += _chars_to_tokens(block.get("text", ""), chars_per_token)
                # tool_result / tool_use blocks are counted at a flat rate
                if block.get("type") in ("tool_use", "tool_result"):
                    total += 10
        return total
    return 0


def estimate_request_cost(
    body: dict[str, Any] | None,
    *,
    chars_per_token: float = 4.0,
    default_cost: int = 10,
    max_cost: int = 10_000,
) -> int:
    """
    Return an integer token cost estimate for *body*.

    The estimate covers:
      - Input tokens (prompt / messages)
      - Requested output tokens (max_tokens), if declared

    The result is clamped to [1, max_cost].
    """
    if not body:
        return default_cost

    total = 0

    # ── OpenAI / Anthropic messages array ─────────────────────────────────────
    messages = body.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            total += _extract_text_from_content(content, chars_per_token)
            total += 4  # per-message overhead (role, delimiters)

    # ── Anthropic system prompt ────────────────────────────────────────────────
    system = body.get("system")
    if isinstance(system, str):
        total += _chars_to_tokens(system, chars_per_token)

    # ── OpenAI legacy completions prompt ──────────────────────────────────────
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        total += _chars_to_tokens(prompt, chars_per_token)
    elif isinstance(prompt, list):
        for p in prompt:
            if isinstance(p, str):
                total += _chars_to_tokens(p, chars_per_token)

    # ── Declared output budget ─────────────────────────────────────────────────
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or body.get("max_output_tokens")
    if isinstance(max_tokens, (int, float)) and max_tokens > 0:
        total += int(max_tokens)

    return max(1, min(total if total > 0 else default_cost, max_cost))


def cost_from_text(
    text: str,
    *,
    chars_per_token: float = 4.0,
    max_cost: int = 10_000,
) -> int:
    """Shorthand for plain-text inputs (no structured body)."""
    return max(1, min(_chars_to_tokens(text, chars_per_token), max_cost))
