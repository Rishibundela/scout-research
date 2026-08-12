"""Context Compaction Engine for Long-Running Research Sessions.

Monitors accumulated state notes and message context, automatically compressing
older historical notes into dense executive summaries when threshold bounds are exceeded.
"""

import logging
import re
from typing import List, Tuple
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, trim_messages, AIMessage
from agent.src.state import AgentState

from agent.src.config import settings

logger = logging.getLogger(__name__)

# Lightweight, high-speed model for context compaction
compaction_model = init_chat_model(
    model="google_genai:gemini-3.1-flash-lite",
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)

COMPACTION_PROMPT = """You are a Lead Knowledge Synthesizer.

Your task is to condense a large collection of research findings and search notes into a dense, highly structured Executive Knowledge Base.

CRITICAL CONSTRAINTS:
1. Preserve ALL key numerical data, statistics, dates, percentages, and metrics.
2. ABSOLUTE URL INTEGRITY: Copy every source URL EXACTLY character-for-character as found in the raw text. NEVER drop, truncate, alter, or summarize a `https://...` URL.
3. DIRECT ATTRIBUTION: Attach the exact raw source URL directly alongside the bullet point finding it supports.
4. Remove duplicate search results, boilerplate text, and conversational filler.
5. Structure findings into logical bulleted thematic sections.
"""


def estimate_token_count(text_list: List[str]) -> int:
    """
    A robust offline token estimator calibrated for a mix of English prose,
    JSON payloads, and source code.
    - Standard alphanumeric words: ~4 characters per token
    - Individual punctuation symbols: 1 token each
    - Whitespace (newlines/tabs): 1 token per sequence
    """
    total_tokens = 0
    for text in text_list:
        if not text:
            continue
        # Count formatting/code symbols
        symbols = sum(1 for char in text if char in "{}[]():;.,=+-*/%&|^~<>!?\"'")
        # Count newlines and carriage returns
        newlines = text.count('\n') + text.count('\r')
        # Filter alphanumeric chunks to count standard words
        words = re.findall(r'[a-zA-Z0-9]+', text)
        word_tokens = sum(max(1, len(w) // 4) for w in words)
        
        total_tokens += symbols + newlines + word_tokens
    return total_tokens


async def compact_research_notes(
    notes: List[str],
    token_threshold: int = 12000,
    recent_notes_to_keep: int = 2,
) -> List[str]:
    """
    Compacts accumulated notes if total estimated tokens exceed token_threshold.

    Args:
        notes: List of raw/accumulated research note strings.
        token_threshold: Token count threshold to trigger compaction.
        recent_notes_to_keep: Number of most recent notes to leave uncompressed.

    Returns:
        Compacted list containing [Compressed Executive Summary] + [Recent Uncompressed Notes].
    """
    if not notes:
        return []

    current_tokens = estimate_token_count(notes)
    
    # If token load is within safe limits, return notes unmodified
    if current_tokens < token_threshold or len(notes) <= recent_notes_to_keep:
        logger.debug(f"Compaction check passed: {current_tokens}/{token_threshold} tokens.")
        return notes

    logger.info(f"⚡ [Compaction Gate Triggered] Token load ({current_tokens}) exceeded threshold ({token_threshold}). Compacting...")

    # Partition notes into historical notes to compact vs. recent notes to preserve
    older_notes = notes[:-recent_notes_to_keep]
    recent_notes = notes[-recent_notes_to_keep:]

    uncompacted_text = "\n\n---\n\n".join(older_notes)

    try:
        response = await compaction_model.ainvoke([
            SystemMessage(content=COMPACTION_PROMPT),
            HumanMessage(content=f"Condense these research notes:\n\n{uncompacted_text}")
        ])

        executive_summary = f"=== COMPACTED EXECUTIVE SUMMARY (Prior Iterations) ===\n{response.content}"
        
        # Reconstruct state list: [Executive Summary] + [Preserved Recent Notes]
        compacted_notes = [executive_summary] + recent_notes
        
        new_tokens = estimate_token_count(compacted_notes)
        logger.info(f"Compaction complete! Reduced context size from {current_tokens} to {new_tokens} tokens.")
        
        return compacted_notes

    except Exception as e:
        logger.error(f"Compaction failed: {e}. Falling back to uncompacted notes.")
        return notes

def prepare_compact_thread_context(state: AgentState) -> list:
    """
    Trims chat history to prevent token explosion during multi-turn follow-ups,
    keeping only essential recent exchanges and condensed notes context.
    """
    messages = state.get("messages", [])
    
    # 1. Trim message history to keep the last ~4,000 tokens of conversation
    trimmed_history = trim_messages(
        messages,
        max_tokens=4000,
        strategy="last",
        token_counter=len, # Simple character/token counter fallback
        start_on="human",
        include_system=True
    )
    
    # 2. Extract existing accumulated notes summary
    notes = state.get("notes", [])
    condensed_notes = "\n".join(notes[-10:]) if isinstance(notes, list) else str(notes)
    
    # 3. Inject condensed thread memory as a system message
    context_summary = SystemMessage(
        content=f"PREVIOUS SESSION RESEARCH CONTEXT:\nThe following facts were already gathered in this session:\n{condensed_notes}\n\nDo NOT re-research topics covered above unless explicitly instructed."
    )
    
    return [context_summary] + trimmed_history