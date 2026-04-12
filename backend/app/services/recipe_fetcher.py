# app/services/recipe_fetcher.py
#
# Recipe MCP Server client — v2 protocol.
#
# v2 change: MCP accepts 4 parameters only (query, species, life_stage, allergens).
# Query building is owned by FoodQueryPlanner (food_query_planner.py) — this file
# only handles allergen assembly, real-time constraint extraction, and the MCP call.
#
# This file:
#   - fetch_for_pets(): entry point — parallel fetches for dog pets only
#   - _fetch_for_pet(): single-pet fetch — allergens + MCP call (private)
#   - _call_mcp_tool(): raw MCP session open + response parse (private)

import asyncio
import json
import logging
import re
import time
from typing import TypedDict

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# Allergen English → Japanese mapping.
# MCP's hard filter does a string match against Japanese ingredient names in the CSV.
# Passing English ("chicken") would never match "鶏肉", so allergic pets would not be protected.
# Pass-through: values already in Japanese are returned unchanged.
_ALLERGEN_JP: dict[str, str] = {
    "chicken":  "鶏肉",
    "pork":     "豚肉",
    "beef":     "牛肉",
    "tuna":     "まぐろ",
    "mackerel": "さば",
    "salmon":   "鮭",
    "egg":      "卵",
    "soy":      "大豆",
    "rice":     "米",
    "garlic":   "にんにく",
}


class RecipeFetchError(Exception):
    """Raised when recipe MCP server is unreachable or returns an error."""
    pass


class RecipeResult(TypedDict):
    """A single recipe result from the MCP server (similarity_score stripped)."""
    id: int
    title_ja: str
    image_url: str
    primary_protein: str | None
    kcal_per_100g: float | None
    description: str
    ingredients_text: str | None
    meal_type: str | None
    cooking_method: str | None
    health_tags: str | None
    allergen_tags: str | None
    species: str | None
    life_stage: str | None
    is_active: str | None


class RecipeFetcher:
    """
    Async client for the Recipe MCP server (v2 — 4-param protocol).

    Callers must supply a pre-built query string (from FoodQueryPlanner).
    This class handles allergen assembly, real-time constraint extraction
    from conversation history, and the MCP call itself.

    Each fetch:
      1. Assembles allergen list from active_profile + real-time regex scan of history
      2. Calls MCP with exactly 4 params: query, species, life_stage, allergens
      3. Strips similarity_score before returning
    """

    def __init__(self, mcp_url: str, timeout: float = 10.0) -> None:
        """
        Args:
            mcp_url: Base URL of the MCP server (e.g. "https://afa.stagingapp.in/mcp")
            timeout: Request timeout in seconds. Defaults to 10.0.
        """
        self._mcp_url = mcp_url.rstrip("/")
        self._timeout = timeout
        logger.debug("RecipeFetcher initialised — mcp_url=%s, timeout=%f",
                     self._mcp_url, self._timeout)

    async def fetch_for_pets(
        self,
        pre_built_query: str,
        pet_profiles: list[dict],
        active_profiles: list[dict],
        conversation_history: list[dict] | None = None,
    ) -> dict[int, tuple[list[RecipeResult], bool]]:
        """
        Fetch recipes for all dog pets in parallel.

        Args:
            pre_built_query:    MCP query string from FoodQueryPlanner (required).
            pet_profiles:       List of pet profiles (pet_id, species, breed, life_stage).
            active_profiles:    List of active_profile dicts, aligned by index with pet_profiles.
            conversation_history: Last N chat turns for real-time constraint extraction (optional).

        Returns:
            Dict keyed by pet_id → (recipes, fallback_flag).
            Non-dog pets are silently skipped (not included in result).
        """
        tasks = {}

        for i, pet_profile in enumerate(pet_profiles):
            if pet_profile.get("species") != "dog":
                logger.debug("Recipe MCP: skipping non-dog pet_id=%s", pet_profile.get("pet_id"))
                continue

            pet_id = pet_profile.get("pet_id")
            active_profile = active_profiles[i] if i < len(active_profiles) else {}

            task = asyncio.create_task(
                self._fetch_for_pet(
                    pre_built_query=pre_built_query,
                    pet_profile=pet_profile,
                    active_profile=active_profile,
                    conversation_history=conversation_history,
                )
            )
            tasks[pet_id] = task

        results = {}
        if tasks:
            task_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for pet_id, task_result in zip(tasks.keys(), task_results):
                if isinstance(task_result, Exception):
                    logger.warning(
                        "Recipe MCP failed for pet_id=%s: %s — returning empty recipes",
                        pet_id, task_result,
                    )
                    results[pet_id] = ([], True)
                else:
                    results[pet_id] = task_result

        return results

    async def _fetch_for_pet(
        self,
        pre_built_query: str,
        pet_profile: dict,
        active_profile: dict,
        conversation_history: list[dict] | None = None,
    ) -> tuple[list[RecipeResult], bool]:
        """
        Fetch recipes for a single pet.

        Args:
            pre_built_query:    MCP query string from FoodQueryPlanner.
            pet_profile:        Dict with keys: species, breed, life_stage, pet_id.
            active_profile:     Dict of ActiveProfileEntry dicts; each has a 'value' key.
            conversation_history: Last N chat turns for real-time constraint extraction.

        Returns:
            Tuple of (recipes, fallback_flag):
            - recipes: list[RecipeResult], up to 3 items, or empty if fallback=true
            - fallback_flag: True if no match found or embedding matrix not loaded

        Raises:
            RecipeFetchError: if MCP server unreachable or JSON parse failure
        """
        # Helper to safely extract value from active_profile entry
        def ap_value(key: str) -> str:
            entry = active_profile.get(key, {})
            if isinstance(entry, dict):
                return str(entry.get("value", "")).strip()
            return str(entry).strip()

        # life_stage: from pet_profile (static) with "adult" fallback
        life_stage = pet_profile.get("life_stage", "") or "adult"

        # allergens: stored in active_profile as free text → split into list[str],
        # then map English names to Japanese so MCP's string filter actually matches.
        allergens_text = ap_value("allergies")
        allergens_list = []
        if allergens_text:
            raw = [a.strip() for a in allergens_text.replace(";", ",").split(",") if a.strip()]
            allergens_list = [_ALLERGEN_JP.get(a.lower(), a) for a in raw]

        # Real-time constraint extraction: scan conversation history for in-session
        # ingredient exclusions ("no chicken", "nothing with garlic", "without X").
        # Compressor runs in background so new constraints may not yet be in active_profile.
        if conversation_history:
            allergens_set = set(allergens_list)
            for turn in conversation_history[-6:]:
                if turn.get("role") != "user":
                    continue
                content_lower = turn["content"].lower()
                for eng, jp in _ALLERGEN_JP.items():
                    if jp in allergens_set:
                        continue  # already covered
                    # Match phrases like "no X", "without X", "nothing with X", "avoid X"
                    if re.search(
                        r"\b(no|without|nothing with|avoid|not)\b.{0,20}\b" + eng + r"\b",
                        content_lower,
                    ):
                        allergens_set.add(jp)
                        logger.info(
                            "Real-time constraint: detected 'no %s' in history → adding %s to allergens",
                            eng, jp,
                        )
            allergens_list = list(allergens_set)

        # v2 protocol: exactly 4 parameters
        tool_args = {
            "query":      pre_built_query,
            "species":    pet_profile.get("species", "dog"),
            "life_stage": life_stage,
            "allergens":  allergens_list,
        }

        logger.info(
            "RecipeFetcher: MCP call — species=%s life_stage=%s allergens=%s query=%r",
            tool_args["species"], tool_args["life_stage"],
            tool_args["allergens"], pre_built_query,
        )

        # Call MCP tool with timeout
        try:
            result = await asyncio.wait_for(
                self._call_mcp_tool(tool_args),
                timeout=self._timeout
            )
            return result
        except asyncio.TimeoutError as exc:
            raise RecipeFetchError(f"Recipe MCP timeout ({self._timeout}s)") from exc
        except RecipeFetchError:
            raise
        except BaseException as exc:
            # Catch all other exceptions (including ExceptionGroup from anyio)
            raise RecipeFetchError(f"Recipe MCP error: {type(exc).__name__}: {exc}") from exc

    async def _call_mcp_tool(self, tool_args: dict) -> tuple[list[RecipeResult], bool]:
        """
        Internal: open MCP session and call the get_recipes tool.

        Returns:
            Tuple of (recipes, fallback_flag)
        """
        response_text = None
        t0 = time.perf_counter()
        try:
            async with streamablehttp_client(self._mcp_url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_recipes", tool_args)
                    response_text = result.content[0].text
        except Exception as exc:
            logger.warning("MCP session/call error: %s", exc)
            raise RecipeFetchError(f"MCP session failed: {exc}") from exc
        logger.info("RecipeFetcher: MCP round-trip %.2fs", time.perf_counter() - t0)

        # Parse response outside context managers to isolate errors
        if response_text is None:
            raise RecipeFetchError("MCP returned no response")

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as exc:
            logger.warning("MCP response not valid JSON: %s", response_text[:100])
            raise RecipeFetchError(f"MCP response not valid JSON: {exc}") from exc

        try:
            # fallback:true → no match found, treat as empty not as error
            fallback = data.get("fallback", False)
            if fallback:
                logger.info("Recipe MCP: fallback=true (no match or embedding not loaded)")
                return [], True

            # Extract recipes and strip similarity_score
            recipes_raw = data.get("recipes", [])
            recipes = []
            for r in recipes_raw:
                recipe = RecipeResult(
                    id=r["id"],
                    title_ja=r["title_ja"],
                    image_url=r["image_url"],
                    primary_protein=r.get("primary_protein"),
                    kcal_per_100g=r.get("kcal_per_100g"),
                    description=r["description"],
                    ingredients_text=r.get("ingredients_text"),
                    meal_type=r.get("meal_type"),
                    cooking_method=r.get("cooking_method"),
                    health_tags=r.get("health_tags"),
                    allergen_tags=r.get("allergen_tags"),
                    species=r.get("species"),
                    life_stage=r.get("life_stage"),
                    is_active=r.get("is_active"),
                    # similarity_score intentionally omitted
                )
                recipes.append(recipe)

            logger.debug("Recipe MCP: returned %d recipe(s)", len(recipes))
            return recipes, False
        except KeyError as exc:
            logger.warning("MCP response missing required field: %s", exc)
            raise RecipeFetchError(f"MCP response missing field: {exc}") from exc
        except (TypeError, ValueError) as exc:
            logger.warning("MCP response format error: %s", exc)
            raise RecipeFetchError(f"MCP response format error: {exc}") from exc

    async def close(self) -> None:
        """Shutdown hook (no-op for stateless MCP; here for API consistency)."""
        logger.info("RecipeFetcher shutdown")
