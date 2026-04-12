# app/core/config.py
#
# Reads all environment variables and exposes them as a typed Settings object.
#
# Why pydantic-settings?
#   - It reads from .env automatically — no manual os.getenv() calls scattered
#     around the codebase.
#   - It validates types at startup.  A missing required variable raises a clear
#     error immediately instead of a cryptic crash 10 minutes later.
#   - It gives you autocomplete on Settings fields in your IDE.
#
# How to use elsewhere:
#   from app.core.config import settings
#   print(settings.azure_openai_endpoint)
#
# NEVER import os or dotenv directly in agent/service/route files.
# Always go through settings.

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    All configuration for the AnyMall-chan backend.

    Fields are read from the .env file (or real environment variables).
    The field names here are lowercase; the .env keys are UPPER_CASE —
    pydantic-settings handles the mapping automatically.

    Required fields have no default value — the app will refuse to start if
    they are missing.  Optional fields have a default of None or a safe value.
    """

    # ── LLM Provider ──────────────────────────────────────────────────────────
    # Which LLM backend to use.  Matches LLM_PROVIDER in .env.
    # Valid values: "azure" | "openai"
    llm_provider: str = "azure"

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    # All four are required when llm_provider == "azure".
    # They have defaults of None so the Settings object can be instantiated
    # even when we are running tests with a mock provider — but the factory
    # (app/llm/factory.py) will raise a clear error if they are None at runtime.
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2025-01-01-preview"
    azure_openai_deployment_chat: str = "gpt-4.1"

    # ── Direct OpenAI ──────────────────────────────────────────────────────
    # Used when llm_provider == "openai".
    # OPENAI_API_KEY is required. OPENAI_MODEL_CHAT sets the default model
    # for all agents (overridable per-agent via _MODEL constant in each file).
    openai_api_key: str | None = None
    openai_model_chat: str = "gpt-5.4"

    # ── Database (Phase 1C) ────────────────────────────────────────────────
    # PostgreSQL connection string for async SQLAlchemy.
    # The "+asyncpg" driver suffix is mandatory for async mode.
    # Default None so tests without a database still instantiate Settings.
    # Lifespan crashes with a clear error if this is None at runtime.
    database_url: str | None = None

    # ── AALDA API ──────────────────────────────────────────────────────────
    # External API for real pet data (profiles, diets, vaccinations).
    # Backend calls this on every chat request (cached in Valkey for 5 min).
    aalda_api_url: str = "https://anymall-api.stagingapp.in/api/v1"
    aalda_timeout_seconds: float = 10.0  # httpx timeout for AALDA API calls

    # ── Valkey cache (ft-005) ──────────────────────────────────────────────
    # Connection URL for the Valkey (Redis-compatible) in-memory cache.
    # Default points to the local Docker container defined in docker-compose.yml.
    # Format: valkey://:password@host:port/db
    valkey_url: str = "valkey://:valkey_dev@localhost:6379/0"

    # ── Recipe MCP Server (Phase 3 — Recipe Integration) ────────────────────
    # External MCP server for recipe recommendations based on pet profiles.
    # Runs on the Streamable HTTP protocol; one pet per call.
    recipe_mcp_url: str = "https://afa.stagingapp.in/mcp"
    # slightly shorter than AALDA to fail fast
    recipe_mcp_timeout_seconds: float = 8.0

    # ── Tavily Web Search (Food AI — Phase 4) ────────────────────────────────
    # API key for Tavily web search, used by FoodAgent in modes 2 and 3.
    # If empty, web search is skipped (FoodAgent still runs but without web context).
    tavily_api_key: str = ""

    # ── pydantic-settings configuration ──────────────────────────────────────
    # env_file: which file to read from disk.
    # env_file_encoding: always utf-8.
    # extra="ignore": unknown keys in .env are silently skipped (safe).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the single shared Settings instance.

    @lru_cache means this function runs exactly once no matter how many times
    it is called.  The Settings object is created on first call and reused
    forever — cheap, thread-safe, and avoids re-reading .env repeatedly.

    FastAPI dependency injection usage:
        from fastapi import Depends
        from app.core.config import get_settings, Settings

        @app.get("/health")
        async def health(s: Settings = Depends(get_settings)):
            return {"provider": s.llm_provider}
    """
    s = Settings()
    logger.info("Settings loaded. LLM provider: %s", s.llm_provider)
    return s


# Module-level shortcut — lets files do `from app.core.config import settings`
# without having to call get_settings() themselves.
settings: Settings = get_settings()
