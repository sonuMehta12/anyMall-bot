# app/llm/factory.py
#
# Factory function: reads the provider name from Settings and returns the
# correct LLMProvider implementation.
#
# Why a factory?
#   main.py (or tests) calls create_llm_provider(settings) once at startup.
#   Every other file just receives an LLMProvider — they never need to know
#   which concrete class it is.
#
#   Adding a new provider in the future:
#     1. Write the new class (e.g. app/llm/openai.py)
#     2. Add one elif branch here
#     3. Done — zero changes anywhere else.

import logging

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.azure_openai import AzureOpenAIProvider

logger = logging.getLogger(__name__)


def create_llm_provider(settings: Settings) -> LLMProvider:
    """
    Instantiate and return the correct LLMProvider for the given settings.

    Args:
        settings: The loaded Settings object (from app.core.config).

    Returns:
        A ready-to-use LLMProvider instance.

    Raises:
        ValueError: if LLM_PROVIDER is unrecognised or required env vars
                    are missing for the chosen provider.
    """
    provider_name = settings.llm_provider.lower().strip()
    logger.info("Creating LLM provider: %s", provider_name)

    if provider_name == "azure":
        # Validate that all Azure-specific vars are present.
        # We check here (not in Settings) because these vars are only required
        # when llm_provider == "azure" — future providers need different vars.
        missing = [
            name
            for name, value in {
                "AZURE_OPENAI_ENDPOINT":    settings.azure_openai_endpoint,
                "AZURE_OPENAI_API_KEY":     settings.azure_openai_api_key,
                "AZURE_OPENAI_API_VERSION": settings.azure_openai_api_version,
                "AZURE_OPENAI_DEPLOYMENT_CHAT": settings.azure_openai_deployment_chat,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                f"LLM_PROVIDER=azure requires these env vars to be set: {missing}"
            )

        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,    # type: ignore[arg-type]
            api_key=settings.azure_openai_api_key,      # type: ignore[arg-type]
            api_version=settings.azure_openai_api_version,
            deployment=settings.azure_openai_deployment_chat,
        )

    # ── Future providers ───────────────────────────────────────────────────────
    # elif provider_name == "openai":
    #     from app.llm.openai import OpenAIProvider
    #     if not settings.openai_api_key:
    #         raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
    #     return OpenAIProvider(
    #         api_key=settings.openai_api_key,
    #         model=settings.openai_model_chat,
    #     )

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER: {provider_name!r}. "
            f"Valid values: 'azure'."
        )
