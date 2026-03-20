# app/llm/openai_provider.py
#
# Direct OpenAI implementation of LLMProvider.
#
# Uses the same `openai` Python package as Azure, but with the standard
# AsyncOpenAI client (not AsyncAzureOpenAI).  Authenticates with a regular
# OpenAI API key from platform.openai.com.
#
# The call signature is identical to Azure — only the client class and
# auth differ.  Model is passed as a real model name (e.g. "gpt-4.1")
# instead of an Azure deployment name.

import logging

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError

from app.llm.base import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """
    LLMProvider backed by direct OpenAI API (api.openai.com).

    Instantiated once by the factory (factory.py) and shared across all
    requests via FastAPI dependency injection.
    """

    def __init__(self, api_key: str, model: str = "gpt-4.1") -> None:
        """
        Args:
            api_key: OpenAI API key from platform.openai.com.
            model:   Model name, e.g. "gpt-4.1", "gpt-4o".
        """
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key)
        logger.info("OpenAIProvider initialised. model=%s", model)

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """Send a chat completion to OpenAI and return the reply text."""
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        logger.debug(
            "Sending completion. model=%s messages=%d temperature=%s",
            self._model,
            len(full_messages),
            temperature,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=full_messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            )
            reply = response.choices[0].message.content or ""
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
        """Send a minimal completion to verify the API key is valid."""
        try:
            reply = await self.complete(
                system_prompt="You are a health check.",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0.0,
            )
            return bool(reply)
        except LLMProviderError:
            return False
