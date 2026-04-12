# app/services/web_searcher.py
#
# Thin async wrapper around the Tavily web search API.
#
# Used by FoodAgent (modes 2 and 3) to fetch veterinary / nutrition information
# from authoritative web sources. Results are injected into FoodAgent's prompt
# so it can cite real sources rather than generating unsupported claims.
#
# Design decisions:
#   - AsyncTavilyClient: opens per-call, no persistent connection needed
#   - search_depth="advanced": better quality for medical/nutrition queries
#   - max_results=3: enough context, controls latency
#   - Timeout handled by Tavily client internally; errors → empty list (graceful)
#   - Errors are logged as WARNING and return [], so FoodAgent still runs

import logging

from tavily import AsyncTavilyClient

logger = logging.getLogger(__name__)


class WebSearcher:
    """
    Async Tavily web search client.

    Created once at startup (main.py lifespan). Injected into FoodAgent.
    Falls back to empty results on any error — FoodAgent runs without web context.
    """

    def __init__(self, api_key: str, timeout: float = 5.0) -> None:
        """
        Args:
            api_key: Tavily API key (from settings.tavily_api_key).
            timeout: Search timeout in seconds. Tavily is fast — 5s is generous.
        """
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        """
        Search the web for veterinary/nutrition information.

        Args:
            query:       Search query (usually user message + "dog nutrition" suffix).
            max_results: Max results to return. Default 3 — enough context, low latency.

        Returns:
            List of result dicts with keys: title, url, content.
            Empty list if API key missing, network error, or timeout.
        """
        if not self._api_key:
            logger.debug("WebSearcher: no API key configured — skipping search")
            return []

        try:
            client = AsyncTavilyClient(api_key=self._api_key)
            result = await client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
                timeout=self._timeout,
            )
            results = result.get("results", [])
            logger.info("WebSearcher: %d result(s) for query %r", len(results), query[:60])
            return results
        except Exception as exc:
            logger.warning("WebSearcher: search failed — %s", exc)
            return []

    async def close(self) -> None:
        """No-op — AsyncTavilyClient has no persistent connection to close."""
        pass
