# app/llm/azure_openai.py
#
# Azure OpenAI implementation of LLMProvider.
#
# Azure OpenAI uses the same `openai` Python package as direct OpenAI, but
# requires a different client class (AsyncAzureOpenAI instead of AsyncOpenAI)
# and authenticates differently:
#   - endpoint:    your Azure resource URL (not api.openai.com)
#   - api_key:     an Azure-issued key (not an OpenAI key)
#   - api_version: Azure API version string (e.g. "2025-01-01-preview")
#   - deployment:  the name YOU gave the model in Azure portal (e.g. "gpt-4.1")
#                  This is NOT the model name — it is your deployment name.
#
# The rest of the call signature is identical to direct OpenAI.  That is why
# the strategy pattern is valuable: swapping providers needs no agent changes.

import logging

from openai import AsyncAzureOpenAI, APIError, APIConnectionError, RateLimitError

from app.llm.base import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)


class AzureOpenAIProvider(LLMProvider):
    """
    LLMProvider backed by Azure OpenAI.

    Instantiated once by the factory (factory.py) and shared across all
    requests via FastAPI dependency injection.  Thread-safe because
    AsyncAzureOpenAI is designed for concurrent async use.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        api_version: str,
        deployment: str,
    ) -> None:
        """
        Args:
            endpoint:    Azure resource URL, e.g. https://my-resource.openai.azure.com
            api_key:     Azure OpenAI API key.
            api_version: Azure API version string, e.g. "2025-01-01-preview".
            deployment:  The deployment name from Azure portal, e.g. "gpt-4.1".
        """
        self._deployment = deployment
        # AsyncAzureOpenAI is the async client — required because our FastAPI
        # routes are all async def.  Using the sync client inside async code
        # would block the event loop and kill concurrency.
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        logger.info(
            "AzureOpenAIProvider initialised. endpoint=%s deployment=%s",
            endpoint,
            deployment,
        )

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """
        Send a chat completion to Azure OpenAI and return the reply text.

        Builds the full message list by prepending the system prompt, then
        appending the conversation history.  The model sees:
            [{"role": "system", "content": system_prompt},
             {"role": "user",   "content": "..."},
             {"role": "assistant", "content": "..."},
             ...]

        Raises LLMProviderError on any API failure so callers stay
        provider-agnostic.
        """
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        logger.debug(
            "Sending completion. deployment=%s messages=%d temperature=%s",
            self._deployment,
            len(full_messages),
            temperature,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._deployment,   # Azure uses deployment name here
                messages=full_messages,   # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            )
            reply = response.choices[0].message.content or ""
            logger.debug("Completion received. length=%d chars", len(reply))
            return reply

        except RateLimitError as exc:
            logger.warning("Azure OpenAI rate limit hit: %s", exc)
            raise LLMProviderError(
                "Rate limit exceeded — please wait a moment and try again.",
                provider="azure",
                original=exc,
            ) from exc

        except APIConnectionError as exc:
            logger.error("Azure OpenAI connection error: %s", exc)
            raise LLMProviderError(
                "Could not reach Azure OpenAI. Check your network or endpoint.",
                provider="azure",
                original=exc,
            ) from exc

        except APIError as exc:
            logger.error("Azure OpenAI API error: %s", exc)
            raise LLMProviderError(
                f"Azure OpenAI returned an error: {exc.message}",
                provider="azure",
                original=exc,
            ) from exc

    async def health_check(self) -> bool:
        """
        Send a minimal completion to verify the endpoint and key are valid.
        Returns True if we get any reply back, False on any failure.
        """
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
