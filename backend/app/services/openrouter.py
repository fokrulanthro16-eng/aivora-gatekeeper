"""
OpenRouter API guard service.

Responsibilities:
  1. Estimate token cost BEFORE forwarding to OpenRouter (so quota can be
     pre-checked and deducted atomically).
  2. Forward the request to https://openrouter.ai/api/v1/chat/completions
     only after quota has been confirmed.
  3. Map OpenRouter / HTTP errors to structured internal exceptions.

The actual quota check is performed by the route handler (not here) so that
the check-and-forward are always paired: we never call OpenRouter without a
prior successful quota deduction.
"""
from __future__ import annotations

import structlog
from typing import Any

import httpx

from app.core.config import get_settings
from app.utils.cost_estimator import estimate_cost, parse_model_key
from app.utils.token_estimator import estimate_request_cost

log = structlog.get_logger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_ENDPOINT = f"{OPENROUTER_BASE_URL}/chat/completions"

# Hard ceiling on how long we wait for OpenRouter
_OPENROUTER_TIMEOUT_SECONDS = 60.0


class OpenRouterError(Exception):
    """Raised when OpenRouter returns a non-200 response or is unreachable."""

    def __init__(self, status_code: int, message: str, body: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.body = body or {}


def is_openrouter_configured() -> bool:
    return bool(get_settings().OPENROUTER_API_KEY)


def estimate_request_tokens(messages: list[dict[str, Any]], max_tokens: int) -> int:
    """
    Estimate the total token count for a chat request.
    Uses the character-ratio heuristic from token_estimator.py.
    """
    s = get_settings()
    body: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens}
    return estimate_request_cost(
        body,
        chars_per_token=s.TOKEN_CHARS_PER_TOKEN,
        default_cost=s.TOKEN_DEFAULT_COST,
        max_cost=s.TOKEN_MAX_COST,
    )


def estimate_openrouter_cost(
    model_key: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[float, int, str, str]:
    """
    Return (estimated_cost_usd, estimated_tokens, provider, model).

    Splits the model key, counts tokens, and looks up pricing.
    """
    parsed = parse_model_key(model_key)
    tokens = estimate_request_tokens(messages, max_tokens)
    cost   = estimate_cost(parsed.provider, parsed.model, tokens, max_tokens)
    return cost, tokens, parsed.provider, parsed.model


async def call_openrouter(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Forward a chat-completions request to OpenRouter.

    Raises:
        OpenRouterError: on non-200 HTTP status or network failure.
        RuntimeError:    if OPENROUTER_API_KEY is not configured.
    """
    api_key = get_settings().OPENROUTER_API_KEY
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to your .env file to enable proxy calls."
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if extra_body:
        payload.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://aivora.ai",
        "X-Title": "Aivora Gatekeeper",
    }

    log.info("openrouter_request", model=model, max_tokens=max_tokens)

    try:
        async with httpx.AsyncClient(timeout=_OPENROUTER_TIMEOUT_SECONDS) as client:
            response = await client.post(
                OPENROUTER_CHAT_ENDPOINT,
                json=payload,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise OpenRouterError(504, "OpenRouter request timed out.") from exc
    except httpx.RequestError as exc:
        raise OpenRouterError(502, f"OpenRouter network error: {exc}") from exc

    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text[:500]}
        log.error(
            "openrouter_error",
            status=response.status_code,
            body=body,
            model=model,
        )
        raise OpenRouterError(
            response.status_code,
            f"OpenRouter returned HTTP {response.status_code}",
            body,
        )

    result: dict[str, Any] = response.json()
    log.info(
        "openrouter_success",
        model=model,
        usage=result.get("usage"),
    )
    return result
