# app/llm/base.py
#
# Abstract base class for all LLM providers.
#
# Why an abstract base?
#   Agent 1 (conversation.py) should never know whether it is talking to
#   Azure OpenAI, direct OpenAI, or a mock in tests.  It just calls complete()
#   and gets a string back.
#
#   To swap providers: change one env var (LLM_PROVIDER).
#   Zero changes in agent code.  Zero changes in route code.
#
# Every concrete provider (azure_openai.py, future openai.py, future mock.py)
# must inherit from LLMProvider and implement both methods.

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """
    Contract that every LLM backend must satisfy.

    Concrete implementations live in this same package:
      - AzureOpenAIProvider  (azure_openai.py)   ← current
      - OpenAIProvider       (openai.py)          ← Phase 1
      - MockLLMProvider      (mock.py)            ← future tests

    Agent 1 receives an LLMProvider instance via dependency injection.
    It only calls complete() — never anything provider-specific.
    """

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """
        Send a chat completion request and return the assistant's reply as a
        plain string.

        Args:
            system_prompt:  The system message (Agent 1's persona + context).
                            Injected as the first message with role="system".
            messages:       The conversation history so far.
                            Each dict has exactly two keys: "role" and "content".
                            Roles are "user" or "assistant" (never "system" —
                            the system message is handled separately above).
            temperature:    Controls randomness.  0.7 is warm but not chaotic.
                            Range: 0.0 (deterministic) to 1.0 (creative).
            max_tokens:     Hard ceiling on reply length.  512 is enough for
                            a conversational response.  Prevents runaway bills.

        Returns:
            The assistant reply text.  Plain string, no wrapping object.

        Raises:
            LLMProviderError: if the API call fails for any reason.
                              Callers catch this and return a safe fallback.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Verify that the provider is reachable and the credentials are valid.

        Returns True if the provider is healthy, False otherwise.
        Called by GET /health to give ops teams a live signal.

        Should complete quickly — use a minimal API call (e.g. complete a
        one-token prompt) rather than a full request.
        """
        ...


class LLMProviderError(Exception):
    """
    Raised when an LLM provider call fails.

    Wraps the underlying exception so callers do not need to import
    provider-specific exception types.

    Attributes:
        provider:   Which provider raised it ("azure", "openai", etc.)
        message:    Human-readable description of what went wrong.
        original:   The underlying exception, if any.
    """

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        original: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message
        self.original = original

    def __repr__(self) -> str:
        return f"LLMProviderError(provider={self.provider!r}, message={self.message!r})"
