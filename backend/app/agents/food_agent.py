# app/agents/food_agent.py
#
# FoodAgent — specialized agent for food-related intents.
#
# Replaces ConversationAgent for food_recipes_info and food_info intents.
# Handles the full food pipeline:
#   Mode 2 (food_recipes_info): MCP + web search IN PARALLEL → LLM → reply + recipe cards
#   Mode 3 (food_info):         web search → LLM → reply only (no recipe cards)
#
# Mode 1 (food_recipes) never reaches FoodAgent — chat.py returns MCP results directly.
#
# Design rules:
#   - Mode 2: MCP call and Tavily web search run concurrently (asyncio.gather).
#     Both only need pet context + user message — neither depends on the other's result.
#     The LLM receives both outputs once both complete.
#   - Tavily query is built by FoodQueryPlanner (nano LLM call) before the parallel step.
#   - LLM receives the full recipe payload (all fields except image_url; no truncation).
#   - LLM must reference recipe names explicitly (Mode 2) and cite web sources.
#   - Never invent ingredients, steps, or health claims not present in the inputs.
#   - FoodAgent returns both the message text AND recipes_by_pet so chat.py can
#     build a complete ChatResponse without a second MCP call.

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from app.llm.base import LLMProvider, LLMProviderError
from app.services.food_query_planner import plan_food_queries
from app.services.recipe_fetcher import RecipeFetcher
from app.services.web_searcher import WebSearcher

logger = logging.getLogger(__name__)

# Per-agent model override. None = provider default (set in .env).
_MODEL: str | None = None


@dataclass
class FoodAgentResult:
    """
    Return value from FoodAgent.run().

    Contains both the text reply AND the recipes fetched from MCP (for Mode 2),
    so chat.py can build a complete ChatResponse without a second MCP call.

    `effective_mode` is the mode actually used — may differ from the requested mode
    when MCP returns 0 recipes and the agent downgrades food_recipes_info → food_info.

    The `.text` property makes this compatible with test code that calls
    `getattr(result, "text", str(result))`.
    """
    message: str
    recipes_by_pet: dict = field(default_factory=dict)  # {pet_id: [recipe_dict, ...]}
    effective_mode: str = ""  # actual mode used after any internal downgrade

    @property
    def text(self) -> str:
        return self.message


class FoodAgent:
    """
    Specialized food intent agent.

    Created once at startup (main.py lifespan). Shared across all requests.
    Thread-safe — all state is in local variables within run().
    """

    def __init__(self, llm: LLMProvider, web_searcher: WebSearcher) -> None:
        self._llm = llm
        self._web_searcher = web_searcher
        logger.info("FoodAgent initialised.")

    async def run(
        self,
        mode: str,
        user_message: str,
        recipe_fetcher: RecipeFetcher,
        pet_profiles: list[dict],
        active_profiles: list[dict],
        session_messages: list[dict],
        language: str,
    ) -> FoodAgentResult:
        """
        Generate a food-focused response.

        Args:
            mode:             "food_recipes_info" or "food_info"
            user_message:     Current user message.
            recipe_fetcher:   RecipeFetcher instance (used for MCP call in Mode 2).
            pet_profiles:     List of pet profile dicts (species, breed, life_stage, ...).
            active_profiles:  List of active_profile dicts (one per pet, parallel to pet_profiles).
            session_messages: Full session history for context (last 20 turns used).
            language:         "JA" or "EN" — determines response language.

        Returns:
            FoodAgentResult with .message (reply text) and .recipes_by_pet (for ChatResponse).
        """
        t_total_start = time.perf_counter()

        primary_pet = pet_profiles[0] if pet_profiles else {}
        primary_active = active_profiles[0] if active_profiles else {}

        recipes_by_pet: dict[int, list[dict]] = {}
        flat_recipes: list[dict] = []
        effective_mode = mode

        # ── Query planning — single nano LLM call, both queries coherent ─────────
        # Generates only the queries needed for this mode:
        #   food_recipes_info → mcp_query + tavily_query
        #   food_info         → tavily_query only
        t_plan_start = time.perf_counter()
        query_plan = await plan_food_queries(
            llm=self._llm,
            mode=mode,
            user_message=user_message,
            pet_profile=primary_pet,
            active_profile=primary_active,
            conversation_history=list(session_messages[-6:]),
            language=language,
        )
        logger.info(
            "FoodAgent: query plan in %.2fs — mcp=%r tavily=%r",
            time.perf_counter() - t_plan_start,
            query_plan.mcp_query, query_plan.tavily_query,
        )

        if mode == "food_recipes_info":
            # ── Mode 2: MCP + web search IN PARALLEL ─────────────────────────
            # MCP uses the planner's mcp_query; Tavily uses the planner's tavily_query.
            # Both queries are generated from the same context — coherent by design.
            t_parallel_start = time.perf_counter()

            mcp_task = asyncio.create_task(
                recipe_fetcher.fetch_for_pets(
                    pre_built_query=query_plan.mcp_query,
                    pet_profiles=pet_profiles,
                    active_profiles=active_profiles,
                    conversation_history=list(session_messages[-6:]),
                )
            )
            web_task = asyncio.create_task(self._web_searcher.search(query_plan.tavily_query))

            mcp_outcome, web_outcome = await asyncio.gather(
                mcp_task, web_task, return_exceptions=True
            )

            t_parallel_end = time.perf_counter()
            logger.info(
                "FoodAgent: MCP + web search (parallel) completed in %.2fs",
                t_parallel_end - t_parallel_start,
            )

            # ── Handle MCP result ──────────────────────────────────────────────
            if isinstance(mcp_outcome, Exception):
                logger.warning(
                    "FoodAgent: MCP fetch failed — proceeding without recipes: %s", mcp_outcome
                )
            else:
                for pid, (rs, _fallback) in mcp_outcome.items():
                    as_dicts = [dict(r) for r in rs]
                    if as_dicts:
                        recipes_by_pet[int(pid)] = as_dicts

            # Flatten ALL dogs' recipes into the prompt so a "food for both" request
            # gets an explanation covering every pet, not just the primary.
            # recipes_by_pet already contains only dog pets (hamster/cat skipped by
            # fetch_for_pets), so no species-mismatch risk here.
            flat_recipes = [r for rs in recipes_by_pet.values() for r in rs]

            if not flat_recipes:
                # MCP returned nothing — downgrade to food_info so the prompt never
                # instructs the LLM to reference recipe names.
                effective_mode = "food_info"
                logger.info(
                    "FoodAgent Mode 2→3 fallback: MCP returned 0 recipes — "
                    "switching to food_info mode",
                )
            else:
                logger.info(
                    "FoodAgent Mode 2: %d recipe(s) fetched across %d pet(s)",
                    len(flat_recipes), len(recipes_by_pet),
                )

            # ── Handle web result ──────────────────────────────────────────────
            if isinstance(web_outcome, Exception):
                logger.warning("FoodAgent: web search failed: %s", web_outcome)
                web_results: list[dict] = []
            else:
                web_results = web_outcome

        else:
            # ── Mode 3: web search only ───────────────────────────────────────
            t_web_start = time.perf_counter()
            web_results = await self._web_searcher.search(query_plan.tavily_query)
            logger.info(
                "FoodAgent: web search completed in %.2fs",
                time.perf_counter() - t_web_start,
            )

        logger.info("FoodAgent: %d web result(s) from Tavily", len(web_results))

        # ── Build system prompt ──────────────────────────────────────────────
        t_prompt_start = time.perf_counter()
        system_prompt = _build_system_prompt(
            mode=effective_mode,
            pet_profiles=pet_profiles,
            active_profiles=active_profiles,
            recipes=flat_recipes,
            web_results=web_results,
            session_messages=session_messages,
            language=language,
        )
        logger.info(
            "FoodAgent: prompt built in %.3fs (len=%d)",
            time.perf_counter() - t_prompt_start, len(system_prompt),
        )

        # ── LLM call ─────────────────────────────────────────────────────────
        t_llm_start = time.perf_counter()
        try:
            reply = await self._llm.complete(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                temperature=0.7,
                max_tokens=2048,
                model=_MODEL,
            )
            reply = reply.strip()
        except LLMProviderError as exc:
            logger.error("FoodAgent: LLM call failed — %s", exc)
            # Wrap in the same HTML structure the frontend always expects for food modes.
            # A plain-text fallback would cause FoodResponseCard to render an empty card
            # because it looks for .fr-for-you / .fr-for-vet sections.
            fallback_body = (
                "申し訳ありませんが、現在情報を取得できません。もう一度お試しください。"
                if language == "JA"
                else "I'm having trouble getting food information right now. Please try again."
            )
            reply = (
                '<div class="food-response">'
                f'<div class="fr-section fr-for-you">'
                f'<div class="fr-section-title">🍽️ {"For You" if language != "JA" else "あなたへ"}</div>'
                f'<div class="fr-body"><p>{fallback_body}</p></div>'
                "</div>"
                "</div>"
            )
        logger.info(
            "FoodAgent: LLM call completed in %.2fs (reply_len=%d)",
            time.perf_counter() - t_llm_start, len(reply),
        )

        t_total = time.perf_counter() - t_total_start
        logger.info(
            "FoodAgent: TOTAL=%.2fs | requested_mode=%s effective_mode=%s "
            "recipe_count=%d web_results=%d",
            t_total, mode, effective_mode, len(flat_recipes), len(web_results),
        )

        return FoodAgentResult(
            message=reply,
            recipes_by_pet=recipes_by_pet,
            effective_mode=effective_mode,
        )

    async def close(self) -> None:
        await self._web_searcher.close()


# ── System prompt builder ──────────────────────────────────────────────────────

def _build_system_prompt(
    mode: str,
    pet_profiles: list[dict],
    active_profiles: list[dict],
    recipes: list[dict],
    web_results: list[dict],
    session_messages: list[dict],
    language: str,
) -> str:
    """Build the FoodAgent system prompt from all available context."""

    lang_instruction = (
        "Respond in Japanese (日本語). Use natural, warm Japanese appropriate for a pet care app."
        if language == "JA"
        else "Respond in English. Use warm, accessible language appropriate for a pet care app."
    )

    def _ap_val(active: dict, key: str) -> str:
        entry = active.get(key, {})
        if isinstance(entry, dict):
            return str(entry.get("value", "")).strip()
        return str(entry).strip()

    # Pet context
    pet_lines = []
    for i, pet in enumerate(pet_profiles):
        active = active_profiles[i] if i < len(active_profiles) else {}
        ap_val = lambda key: _ap_val(active, key)

        lines = [
            f"Name: {pet.get('name', 'Unknown')}",
            f"Species: {pet.get('species', 'dog')}",
            f"Breed: {pet.get('breed', 'Unknown')}",
            f"Life stage: {pet.get('life_stage', 'adult')}",
        ]
        if ap_val("chronic_illness"):
            lines.append(f"Conditions: {ap_val('chronic_illness')}")
        if ap_val("allergies"):
            lines.append(f"Allergies: {ap_val('allergies')}")
        if ap_val("symptoms"):
            lines.append(f"Active symptoms: {ap_val('symptoms')}")
        if ap_val("body_condition_score"):
            lines.append(f"BCS: {ap_val('body_condition_score')}/9")
        if ap_val("activity_level"):
            lines.append(f"Activity: {ap_val('activity_level')}/5")
        if ap_val("weight_trend"):
            lines.append(f"Weight trend: {ap_val('weight_trend')}")
        pet_lines.append(f"PET {i + 1}:\n" + "\n".join(f"  {l}" for l in lines))

    pet_section = "\n\n".join(pet_lines)

    # Recipes section (Mode 2 only).
    # Full payload sent to LLM — no truncation — so it can give accurate ingredient
    # and nutritional details. image_url is excluded (not useful for text generation).
    #
    # Mixed-species guard: recipes from MCP are always dog recipes. If there are
    # non-dog pets in the session, label the section clearly and add a hard warning
    # so the LLM never applies dog recipes to a hamster, cat, rabbit, etc.
    recipes_section = ""
    if recipes:
        dog_pets = [p for p in pet_profiles if p.get("species") == "dog"]
        non_dog_pets = [p for p in pet_profiles if p.get("species") != "dog"]
        dog_label = ", ".join(p.get("name", "Unknown") for p in dog_pets) or "dog"

        recipe_lines = [
            f"RECIPES FETCHED FROM MCP (formulated for dogs only — fetched for: {dog_label}):"
        ]
        for j, r in enumerate(recipes, 1):
            line = f"{j}. {r.get('title_ja', 'Unknown')}"
            if r.get("meal_type"):
                line += f" [{r['meal_type']}]"
            if r.get("primary_protein"):
                line += f"\n   Protein: {r['primary_protein']}"
            if r.get("kcal_per_100g"):
                line += f" | {r['kcal_per_100g']} kcal/100g"
            if r.get("cooking_method"):
                line += f" | Cooking: {r['cooking_method']}"
            if r.get("health_tags"):
                line += f"\n   Health tags: {r['health_tags']}"
            if r.get("allergen_tags"):
                line += f"\n   Allergens: {r['allergen_tags']}"
            if r.get("ingredients_text"):
                line += f"\n   Ingredients: {r['ingredients_text']}"
            if r.get("description"):
                line += f"\n   Description: {r['description']}"
            recipe_lines.append(line)

        recipes_section = "\n".join(recipe_lines) + "\n\n"

        if non_dog_pets:
            non_dog_labels = ", ".join(
                f"{p.get('name', 'Unknown')} ({p.get('species', 'unknown')})"
                for p in non_dog_pets
            )
            recipes_section += (
                f"SPECIES MISMATCH WARNING: The recipes above are formulated for dogs only. "
                f"They are NOT safe or suitable for {non_dog_labels}. "
                f"Do NOT recommend, reference, or discuss these recipes in the context of {non_dog_labels}. "
                f"For {non_dog_labels}, provide dietary guidance from general veterinary knowledge only.\n\n"
            )

    # Web search section — pre-compute favicon URLs so LLM gets them ready-made
    web_section = ""
    source_anchors_html = ""
    if web_results:
        web_lines = ["WEB SEARCH RESULTS (cite sources inline; use the exact anchor HTML below in your sources section):"]
        anchor_parts = []
        for w in web_results:
            title = w.get("title", "")
            url = w.get("url", "")
            content = w.get("content", "")[:300]
            web_lines.append(f"- [{title}]({url}): {content}")
            # Pre-compute favicon URL from domain so LLM never has to construct URLs
            try:
                domain = urlparse(url).netloc
                favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=16"
            except Exception:
                favicon_url = ""
            favicon_img = (
                f'<img class="fr-favicon" src="{favicon_url}" '
                f'onerror="this.style.display=\'none\'" alt="" />'
                if favicon_url else ""
            )
            anchor_parts.append(
                f'<a class="fr-source-link" href="{url}" target="_blank" rel="noopener">'
                f'{favicon_img}<span>{title}</span></a>'
            )
        web_section = "\n".join(web_lines) + "\n\n"
        source_anchors_html = "\n".join(anchor_parts)

    # Recent history (last 20 turns for context).
    # Strip Mode 1 synthetic messages ("[Recipe cards shown — ...]") so the LLM
    # never reads recipe titles it cannot reference in this response.
    history_section = ""
    if session_messages:
        filtered_turns = [
            t for t in session_messages[-20:]
            if not t.get("content", "").startswith("[Recipe cards shown")
        ]
        if filtered_turns:
            history_lines = ["RECENT CONVERSATION HISTORY:"]
            for turn in filtered_turns:
                role = "User" if turn.get("role") == "user" else "Assistant"
                history_lines.append(f"{role}: {turn.get('content', '')[:200]}")
            history_section = "\n".join(history_lines) + "\n\n"

    # Sources block — injected into prompt so LLM copies it verbatim
    sources_block_instruction = ""
    if source_anchors_html:
        sources_block_instruction = f"""\
SOURCES HTML (copy this VERBATIM into your <div class="fr-sources"> block — do not modify URLs or attributes):
{source_anchors_html}
"""

    # Mode-specific instructions
    if mode == "food_recipes_info":
        mode_instructions = """\
You are a food expert for pet owners. You must respond with valid HTML only — no markdown, no plain text.

OUTPUT FORMAT (return exactly this structure — sources row comes FIRST, before the sections):
<div class="food-response">
  <div class="fr-sources">
    <!-- Paste the SOURCES HTML exactly as provided above. Omit this div entirely if no sources. -->
  </div>
  <div class="fr-section fr-for-you">
    <div class="fr-section-title">🍽️ For You</div>
    <div class="fr-body">
      <!-- Warm explanation: reference ALL recipe titles by name (from RECIPES section above),
           explain WHY each fits this pet. Use <p>, <ul><li>, <strong> for recipe names. -->
    </div>
  </div>
  <div class="fr-section fr-for-vet">
    <div class="fr-section-title">🩺 For Your Vet</div>
    <div class="fr-body">
      <!-- Clinical details: kcal, protein sources, health tags, allergen status per recipe.
           Use <p> and <ul><li>. -->
    </div>
  </div>
</div>

CRITICAL RULES:
- Only reference recipe names that appear in the "RECIPES FETCHED FROM MCP" section above.
  Do NOT reference any recipe names from the conversation history — those are from a previous
  search and are NOT available in this response.
- Never invent ingredients, cooking steps, or health claims not in the recipe data.
- If no web sources, omit the fr-sources div entirely.
- Output ONLY the HTML block — no explanation before or after it."""
    else:  # food_info (and food_recipes_info downgraded when MCP returned nothing)
        mode_instructions = """\
You are a food and nutrition expert for pet owners. You must respond with valid HTML only — no markdown, no plain text.

OUTPUT FORMAT (return exactly this structure — sources row comes FIRST, before the sections):
<div class="food-response">
  <div class="fr-sources">
    <!-- Paste the SOURCES HTML exactly as provided above. Omit this div entirely if no sources. -->
  </div>
  <div class="fr-section fr-for-you">
    <div class="fr-section-title">🍽️ For You</div>
    <div class="fr-body">
      <!-- Warm, practical answer the owner can act on today.
           Use <p> for paragraphs, <ul><li> for lists. -->
    </div>
  </div>
  <div class="fr-section fr-for-vet">
    <div class="fr-section-title">🩺 For Your Vet</div>
    <div class="fr-body">
      <!-- Clinical nuances worth discussing at the next vet visit.
           Use <p> and <ul><li>. -->
    </div>
  </div>
</div>

CRITICAL RULES:
- Do NOT reference any recipe names from the conversation history — no recipe cards are
  being shown in this response, so referencing them would be misleading.
- Never invent facts not in the web results or established veterinary knowledge.
- If no web sources, omit the fr-sources div entirely.
- Output ONLY the HTML block — no explanation before or after it."""

    prompt = f"""\
{lang_instruction}

{mode_instructions}

PET INFORMATION:
{pet_section}

{recipes_section}{web_section}{sources_block_instruction}{history_section}\
Today's date: {date.today().isoformat()}
"""
    return prompt.strip()
