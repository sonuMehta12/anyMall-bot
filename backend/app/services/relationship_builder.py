# app/services/relationship_builder.py
#
# RelationshipBuilder — builds users.relationship_summary from conversation style data.
#
# What it does:
#   Reads USER STYLE sections from recent threads.compaction_summary entries for a user,
#   merges them with the existing relationship_summary using an LLM, and writes the result
#   back to users.relationship_summary via UserProfileWriter.
#
# Pipeline:
#   threads.compaction_summary (USER STYLE sections, from enhanced ThreadSummarizer)
#     → RelationshipBuilder.build()
#       → users.relationship_summary
#         → Agent 1 reads as relationship_context
#
# Completely separate from HistoryBuilder:
#   HistoryBuilder reads fact_log (structured facts).
#   RelationshipBuilder reads threads.compaction_summary (prose style observations).
#
# Abstraction:
#   UserProfileWriter Protocol decouples RelationshipBuilder from the storage layer.
#   Current impl: UserRepo (direct PostgreSQL write).
#   Future impl: AALDA user API — swap with zero changes to this file.

# ── Standard library ────────────────────────────────────────────────────────
import logging
from typing import TYPE_CHECKING

# ── Our code ────────────────────────────────────────────────────────────────
from app.llm.base import LLMProvider
from app.types import UserProfileWriter

logger = logging.getLogger(__name__)

RELATIONSHIP_SYSTEM_PROMPT = """You are a relationship context builder for a pet companion app.

Your job is to synthesize observations about how a pet owner communicates and write a
concise, useful summary that will help an AI assistant calibrate its responses.

You receive USER STYLE observations from recent conversations, plus an existing summary.

RULES:
1. Synthesize all observations into 2-4 sentences.
2. Focus on actionable characteristics: anxiety level, preferred response length,
   question patterns, emotional tone (worried / curious / practical / etc.).
3. Merge with the existing summary — weight recent observations more than old ones.
4. Be specific and concrete. Avoid vague phrases like "seems engaged".
5. Write in plain text, no bullet points, no headers.
6. If observations are contradictory, note the variance (e.g. "usually calm but occasionally anxious").
7. If there are fewer than 2 style observations and no existing summary, output a generic
   placeholder: "Communication style not yet established."
"""


class RelationshipBuilder:
    """
    Builds users.relationship_summary from USER STYLE sections of compaction summaries.

    Receives LLMProvider and UserProfileWriter via constructor.
    UserProfileWriter is an abstract Protocol — swap UserRepo for AALDA API with no changes here.
    Temperature 0.0 for deterministic output.
    """

    def __init__(self, llm: LLMProvider, user_writer: UserProfileWriter) -> None:
        self._llm = llm
        self._writer = user_writer

    async def build(
        self,
        user_code: str,
        style_observations: list[str],
        existing_summary: str,
    ) -> str:
        """
        Merge USER STYLE observations into an updated relationship_summary.

        Args:
            user_code: For logging only.
            style_observations: List of USER STYLE section texts from recent summaries.
                                Empty strings already filtered out by caller.
            existing_summary: Current users.relationship_summary (may be "").

        Returns:
            Updated relationship_summary string (2-4 plain-text sentences).
        """
        # Build user prompt
        obs_text = "\n".join(
            f"Observation {i + 1}: {obs}"
            for i, obs in enumerate(style_observations)
        )

        user_prompt = ""
        if existing_summary:
            user_prompt += f"Existing relationship summary:\n{existing_summary}\n\n---\n\n"
        user_prompt += (
            f"Recent USER STYLE observations ({len(style_observations)} conversation(s)):\n"
            f"{obs_text}\n\n"
            "Please provide an updated relationship summary."
        )

        result = await self._llm.complete(
            system_prompt=RELATIONSHIP_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=200,
        )

        summary = result.strip()
        logger.info(
            "RelationshipBuilder: built summary for user_code=%s (observations=%d)",
            user_code, len(style_observations),
        )
        return summary

    async def write_summary(self, user_code: str, summary: str) -> None:
        """
        Write a relationship summary via the UserProfileWriter abstraction.

        Public method so callers (nightly.py) never need to access _writer directly.
        Keeping the write here ensures the Protocol abstraction is the only path
        to storage — swap _writer for an AALDA-backed writer with zero caller changes.
        """
        await self._writer.update_relationship_summary(user_code, summary)
