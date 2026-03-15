# app/services/thread_summarizer.py
#
# LLM-based summarization for thread compaction.
#
# When a thread accumulates more messages than THREAD_COMPACTION_THRESHOLD,
# _run_compaction() in chat.py calls this service to summarize older messages
# into a compact text summary. The summary is stored in threads.compaction_summary
# and passed to Agent 1 so it has context from earlier in the conversation.
#
# Design:
#   - Receives LLMProvider via constructor (same pattern as all agents).
#   - Temperature 0.0 for deterministic output.
#   - Supports incremental compaction: if a previous summary exists, it's
#     incorporated so no context is lost across multiple compactions.

import logging

from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

SUMMARIZER_SYSTEM_PROMPT = """You are a conversation summarizer for a pet companion chat application.

Your job is to summarize a conversation between a pet owner and a friendly AI assistant.

Focus on:
- Key pet facts mentioned (health, diet, behavior, symptoms)
- Emotional context (owner's mood, concerns, anxieties)
- Action items or advice given
- Questions that were asked and answered
- Any unresolved concerns

Keep the summary concise (3-5 sentences). Write in plain text, no bullet points or formatting.
If a previous summary is provided, incorporate its key points into the new summary so nothing is lost."""


class ThreadSummarizer:
    """Summarize conversation messages for thread compaction."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def summarize(
        self,
        messages: list[dict],
        existing_summary: str | None = None,
    ) -> str:
        """
        Summarize conversation messages into a compact text summary.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts to summarize.
            existing_summary: Previous summary to incorporate (for incremental compaction).

        Returns:
            Plain text summary string.
        """
        # Build the user prompt with the messages to summarize
        conversation_text = "\n".join(
            f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}"
            for msg in messages
        )

        user_prompt = ""
        if existing_summary:
            user_prompt += (
                f"Previous conversation summary:\n{existing_summary}\n\n---\n\n"
            )
        user_prompt += (
            f"New messages to summarize:\n{conversation_text}\n\n"
            "Please provide an updated summary combining the previous summary "
            "(if any) with the new messages."
        )

        result = await self._llm.complete(
            system_prompt=SUMMARIZER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=400,
        )

        logger.info(
            "Thread summarized — messages=%d existing_summary=%s",
            len(messages),
            "yes" if existing_summary else "no",
        )
        return result.strip()
