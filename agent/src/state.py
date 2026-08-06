"""State Definitions for Research Scoping.

This defines the state objects used for the research agent scoping workflow,
hardened with custom deduplicating reducers for idempotent state updates.
"""

from typing import TypedDict, List
from typing_extensions import Optional, Annotated, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages


# ===== CUSTOM IDEMPOTENT REDUCER =====

def deduplicate_list(left: list[str] | None, right: list[str] | None) -> list[str]:
    """
    State Reducer: Merges string list updates while preserving insertion order
    and filtering out exact duplicate text blocks.
    """
    if not left:
        left = []
    if not right:
        right = []

    combined = left + right
    seen = set()
    unique_items = []

    for item in combined:
        if item and item not in seen:
            seen.add(item)
            unique_items.append(item)

    return unique_items


# ===== STATE DEFINITIONS =====

class AgentInputState(MessagesState):
    """Input state for the full agent - contains user input messages."""
    pass


class AgentState(MessagesState):
    """Main state for the full multi-agent research system."""

    # Research brief generated from user conversation history
    research_brief: Optional[str]
    # Messages exchanged with the supervisor agent (add_messages deduplicates by message ID)
    supervisor_messages: Annotated[Sequence[BaseMessage], add_messages]
    # Idempotent collection of raw and processed research notes
    raw_notes: Annotated[list[str], deduplicate_list]
    notes: Annotated[list[str], deduplicate_list]
    # Final formatted research report
    final_report: str


class ResearcherState(TypedDict):
    """State for the research agent containing message history and research metadata."""

    researcher_messages: Annotated[Sequence[BaseMessage], add_messages]
    tool_call_iterations: int
    research_topic: str
    compressed_research: str
    raw_notes: Annotated[List[str], deduplicate_list]


class ResearcherOutputState(TypedDict):
    """Output state for the research agent containing final research results."""

    compressed_research: str
    raw_notes: Annotated[List[str], deduplicate_list]
    researcher_messages: Annotated[Sequence[BaseMessage], add_messages]


class SupervisorState(TypedDict):
    """State for the multi-agent research supervisor."""

    supervisor_messages: Annotated[Sequence[BaseMessage], add_messages]
    research_brief: str
    notes: Annotated[list[str], deduplicate_list]
    research_iterations: int
    raw_notes: Annotated[list[str], deduplicate_list]

