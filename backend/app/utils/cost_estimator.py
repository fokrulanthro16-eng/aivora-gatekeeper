"""
Provider cost estimator for OpenRouter models.

Provides two functions:
  estimate_cost()   — pre-call dollar estimate (input + max_output budget)
  parse_model_key() — split "provider/model" strings into (provider, model)

The hardcoded PROVIDER_COSTS table mirrors 008_provider_costs.sql and is used
as a fast in-process fallback when a Supabase query is not available.  When
Supabase IS available, the provider_costs table is the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

# ── Cost table ────────────────────────────────────────────────────────────────
# USD per 1 000 tokens.  Mirrors database/migrations/008_provider_costs.sql.

@dataclass(frozen=True, slots=True)
class ModelCost:
    input_per_1k: float   # USD
    output_per_1k: float  # USD

_COSTS: dict[tuple[str, str], ModelCost] = {
    # OpenAI
    ("openai", "gpt-4o"):                       ModelCost(0.005000,  0.015000),
    ("openai", "gpt-4o-mini"):                  ModelCost(0.000150,  0.000600),
    ("openai", "gpt-4-turbo"):                  ModelCost(0.010000,  0.030000),
    ("openai", "o1"):                           ModelCost(0.015000,  0.060000),
    ("openai", "o1-mini"):                      ModelCost(0.003000,  0.012000),
    # Anthropic
    ("anthropic", "claude-3-5-sonnet"):         ModelCost(0.003000,  0.015000),
    ("anthropic", "claude-3-5-haiku"):          ModelCost(0.000800,  0.004000),
    ("anthropic", "claude-3-opus"):             ModelCost(0.015000,  0.075000),
    # Google
    ("google", "gemini-pro-1.5"):               ModelCost(0.001250,  0.005000),
    ("google", "gemini-flash-1.5"):             ModelCost(0.0000750, 0.000300),
    ("google", "gemini-flash-2.0"):             ModelCost(0.0000750, 0.000300),
    # Meta
    ("meta-llama", "llama-3.1-8b-instruct"):    ModelCost(0.0000550, 0.0000550),
    ("meta-llama", "llama-3.1-70b-instruct"):   ModelCost(0.000520,  0.000750),
    ("meta-llama", "llama-3.1-405b-instruct"):  ModelCost(0.002700,  0.002700),
    # Mistral
    ("mistralai", "mistral-7b-instruct"):       ModelCost(0.0000550, 0.0000550),
    ("mistralai", "mixtral-8x7b-instruct"):     ModelCost(0.000240,  0.000240),
    ("mistralai", "mistral-large"):             ModelCost(0.002000,  0.006000),
}

# Fallback cost used when the model isn't in the table (conservative estimate)
_DEFAULT_COST = ModelCost(0.010000, 0.010000)


class ParsedModel(NamedTuple):
    provider: str
    model: str
    full_key: str  # "provider/model" as used by OpenRouter


def parse_model_key(model_key: str) -> ParsedModel:
    """
    Split an OpenRouter model key into (provider, model).
    Accepts both "provider/model" and bare "model" formats.

    Examples:
        "openai/gpt-4o"  → ParsedModel("openai", "gpt-4o", "openai/gpt-4o")
        "gpt-4o"         → ParsedModel("openai", "gpt-4o", "openai/gpt-4o")  # guessed
    """
    if "/" in model_key:
        provider, model = model_key.split("/", 1)
        return ParsedModel(provider.lower(), model.lower(), f"{provider.lower()}/{model.lower()}")

    # Bare model name — try to infer provider from well-known prefixes
    m = model_key.lower()
    if m.startswith(("gpt-", "o1", "o3", "davinci", "curie", "babbage")):
        return ParsedModel("openai", m, f"openai/{m}")
    if m.startswith("claude"):
        return ParsedModel("anthropic", m, f"anthropic/{m}")
    if m.startswith("gemini"):
        return ParsedModel("google", m, f"google/{m}")
    if m.startswith(("llama", "meta")):
        return ParsedModel("meta-llama", m, f"meta-llama/{m}")
    if m.startswith("mistral") or m.startswith("mixtral"):
        return ParsedModel("mistralai", m, f"mistralai/{m}")
    # Unknown — treat as openai-compatible
    return ParsedModel("unknown", m, m)


def get_model_cost(provider: str, model: str) -> ModelCost:
    """Return the ModelCost for (provider, model), defaulting if unknown."""
    return _COSTS.get((provider.lower(), model.lower()), _DEFAULT_COST)


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    max_output_tokens: int = 1024,
) -> float:
    """
    Estimate the USD cost of a single request before it is sent.

    The estimate is intentionally conservative:
      cost = (input_tokens / 1000 × input_rate) + (max_output_tokens / 1000 × output_rate)

    The actual cost may be lower if the model generates fewer tokens, but we
    charge the estimate up-front so that quota enforcement is deterministic.

    Returns a float in USD, rounded to 8 decimal places.
    """
    mc = get_model_cost(provider, model)
    input_cost  = (input_tokens       / 1000) * mc.input_per_1k
    output_cost = (max_output_tokens  / 1000) * mc.output_per_1k
    return round(input_cost + output_cost, 8)
