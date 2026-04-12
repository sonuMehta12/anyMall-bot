# app/llm/openai_provider.py
#
# Direct OpenAI implementation of LLMProvider.
#
# Uses the Responses API (client.responses.create) — OpenAI's newer standard.
# Authenticates with a regular OpenAI API key from platform.openai.com.
#
# Why Responses API instead of Chat Completions?
#   The Responses API supports reasoning={"effort": "none"} which fully
#   disables chain-of-thought on gpt-5 family models.
#   Chat Completions only supports "minimal"/"low"/"medium"/"high" — no "none".
#
# Reasoning model handling (gpt-5, gpt-5.4, gpt-5.4-mini, etc.):
#   Pass reasoning={"effort": "none"} to disable reasoning mode entirely.
#   This gives fast, cheap output — no chain-of-thought token cost.
#   For non-reasoning models (gpt-4.1, etc.), temperature is passed normally.
#   Agents don't need to know — they call complete() the same way.
#
# Backpressure (T1-03):
#   _LLM_SEMAPHORE  — limits concurrent OpenAI calls to 30 at once. Requests
#                     beyond the limit wait here instead of all firing at once.
#   _call_with_retry — retries up to 3 times (1s → 2s → 4s) on RateLimitError.
#   asyncio.wait_for — each individual call has a 10s hard timeout. If OpenAI
#                     takes longer, the user gets a polite "busy" message.

import asyncio
import logging

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError

from app.llm.base import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)


# ── Reasoning model detection ────────────────────────────────────────────────
# gpt-5 family are reasoning models by default. Without disabling reasoning,
# temperature is rejected and chain-of-thought tokens add cost + latency.
# We detect by prefix so gpt-5.4, gpt-5.4-mini, gpt-5-turbo etc. all match.

_REASONING_PREFIXES = ("gpt-5",)


def _is_reasoning_model(model_name: str) -> bool:
    """Return True if the model is a gpt-5 family reasoning model."""
    return any(model_name.startswith(p) for p in _REASONING_PREFIXES)


# ── Backpressure constants ────────────────────────────────────────────────────

# Max concurrent LLM calls across all requests. Requests beyond this wait
# in the semaphore queue instead of hammering OpenAI simultaneously.
_LLM_SEMAPHORE = asyncio.Semaphore(30)

# How long (seconds) to wait for a single OpenAI call before giving up.
_LLM_TIMEOUT = 30.0

# Retry delays in seconds: attempt 1 → wait 1s → attempt 2 → wait 2s →
# attempt 3 → wait 4s → attempt 4 → give up.
_RETRY_DELAYS = [1, 2, 4]

# Message returned to the user when the LLM is overloaded or too slow.
_BUSY_MESSAGE = "I'm a little busy right now — please try again in a moment."


class OpenAIProvider(LLMProvider):
    """
    LLMProvider backed by direct OpenAI API (api.openai.com).

    Uses the Responses API. Instantiated once by factory.py and shared
    across all requests via FastAPI app.state.

    Handles reasoning models (gpt-5 family) transparently:
      - Passes reasoning={"effort": "none"} to fully disable reasoning mode
      - For standard models (gpt-4.1 etc.), passes temperature normally
    Agents don't need to know — they call complete() the same way.

    Backpressure (T1-03):
      - Semaphore limits concurrent calls to 30
      - Retries up to 3x on RateLimitError with exponential backoff
      - 10s hard timeout per call — returns busy message if exceeded
    """

    def __init__(self, api_key: str, model: str = "gpt-4.1") -> None:
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key)
        logger.info("OpenAIProvider initialised. model=%s", model)

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
        model: str | None = None,
    ) -> str:
        """Send a request to OpenAI Responses API and return the reply text.

        For reasoning models (gpt-5 family): reasoning={"effort": "none"} is
        set to disable chain-of-thought. Temperature is not sent (ignored in
        reasoning mode).
        For standard models: temperature is passed normally.
        Agents call complete() the same way regardless of model.

        Backpressure: waits for the semaphore if 30 calls are already running,
        then retries on rate limit, then enforces a 10s timeout per attempt.
        """
        model_name = model or self._model
        # Strip messages to only role+content — Responses API rejects unknown
        # fields (e.g. timestamp) that Chat Completions silently ignored.
        clean_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        full_input = [{"role": "system", "content": system_prompt}] + clean_messages
        is_reasoning = _is_reasoning_model(model_name)

        kwargs: dict = {
            "model": model_name,
            "input": full_input,
            "max_output_tokens": max_tokens,
        }
        if is_reasoning:
            kwargs["reasoning"] = {"effort": "none"}
        else:
            kwargs["temperature"] = temperature

        logger.debug(
            "Sending completion. model=%s messages=%d temperature=%s is_reasoning=%s",
            model_name, len(full_input), temperature, is_reasoning,
        )

        # Wait here if 30 other calls are already in flight. This prevents
        # flooding OpenAI with hundreds of simultaneous requests.
        async with _LLM_SEMAPHORE:
            return await self._call_with_retry(kwargs)

    async def _call_with_retry(self, kwargs: dict) -> str:
        """Call OpenAI with up to 3 retries on rate limit errors.

        Retry schedule: immediate → wait 1s → wait 2s → wait 4s → give up.
        Each attempt has a 10s hard timeout.
        On timeout or all retries exhausted: raises LLMProviderError with
        a user-friendly busy message.
        """
        last_exc: Exception | None = None

        for attempt, delay in enumerate([0] + _RETRY_DELAYS, start=1):
            if delay:
                logger.warning(
                    "Rate limit hit. Retrying in %ds (attempt %d/%d).",
                    delay, attempt, len(_RETRY_DELAYS) + 1,
                )
                await asyncio.sleep(delay)

            try:
                response = await asyncio.wait_for(
                    self._client.responses.create(**kwargs),
                    timeout=_LLM_TIMEOUT,
                )
                reply = response.output_text or ""
                logger.debug("Completion received. length=%d chars", len(reply))
                return reply

            except asyncio.TimeoutError:
                logger.warning(
                    "OpenAI call timed out after %.1fs (attempt %d).",
                    _LLM_TIMEOUT, attempt,
                )
                raise LLMProviderError(
                    _BUSY_MESSAGE,
                    provider="openai",
                )

            except RateLimitError as exc:
                last_exc = exc
                if attempt <= len(_RETRY_DELAYS):
                    continue  # next iteration will sleep then retry
                # All retries exhausted.
                logger.error("OpenAI rate limit persists after %d attempts.", attempt)
                raise LLMProviderError(
                    _BUSY_MESSAGE,
                    provider="openai",
                    original=exc,
                ) from exc

            except APIConnectionError as exc:
                logger.error("OpenAI connection error: %s", exc)
                raise LLMProviderError(
                    "Could not reach OpenAI. Check your network or API key.",
                    provider="openai",
                    original=exc,
                ) from exc

            except APIError as exc:
                logger.error("OpenAI API error: %s", exc)
                raise LLMProviderError(
                    f"OpenAI returned an error: {exc.message}",
                    provider="openai",
                    original=exc,
                ) from exc

        # Should never reach here, but satisfies the type checker.
        raise LLMProviderError(_BUSY_MESSAGE, provider="openai", original=last_exc)

    async def health_check(self) -> bool:
        """Send a minimal request to verify the API key is valid."""
        try:
            reply = await self.complete(
                system_prompt="You are a health check.",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return bool(reply)
        except LLMProviderError:
            return False
