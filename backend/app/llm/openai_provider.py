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


class OpenAIProvider(LLMProvider):
    """
    LLMProvider backed by direct OpenAI API (api.openai.com).

    Uses the Responses API. Instantiated once by factory.py and shared
    across all requests via FastAPI app.state.

    Handles reasoning models (gpt-5 family) transparently:
      - Passes reasoning={"effort": "none"} to fully disable reasoning mode
      - For standard models (gpt-4.1 etc.), passes temperature normally
    Agents don't need to know — they call complete() the same way.
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
        """
        model_name = model or self._model
        # Strip messages to only role+content — Responses API rejects unknown
        # fields (e.g. timestamp) that Chat Completions silently ignored.
        clean_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        full_input = [{"role": "system", "content": system_prompt}] + clean_messages
        is_reasoning = _is_reasoning_model(model_name)

        logger.debug(
            "Sending completion. model=%s messages=%d temperature=%s is_reasoning=%s",
            model_name, len(full_input), temperature, is_reasoning,
        )

        try:
            kwargs: dict = {
                "model": model_name,
                "input": full_input,
                "max_output_tokens": max_tokens,
            }

            if is_reasoning:
                # Disable reasoning entirely — no chain-of-thought, no extra cost.
                # temperature is not supported when reasoning is active, so omit it.
                kwargs["reasoning"] = {"effort": "none"}
            else:
                kwargs["temperature"] = temperature

            response = await self._client.responses.create(**kwargs)
            reply = response.output_text or ""
            logger.debug("Completion received. length=%d chars", len(reply))
            return reply

        except RateLimitError as exc:
            logger.warning("OpenAI rate limit hit: %s", exc)
            raise LLMProviderError(
                "Rate limit exceeded — please wait a moment and try again.",
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
