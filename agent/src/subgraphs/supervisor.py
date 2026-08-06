
"""Multi-agent supervisor for coordinating research across multiple specialized agents.

This module implements a supervisor pattern where:
1. A supervisor agent coordinates research activities and delegates tasks
2. Multiple researcher agents work on specific sub-topics independently
3. Results are aggregated and compressed for final reporting

The supervisor uses parallel research execution to improve efficiency while
maintaining isolated context windows for each research topic.

Hardened with model fallbacks, per-subagent exception isolation,
timeouts, and native LangGraph fault tolerance.
"""

import asyncio
import logging
from typing_extensions import Literal
from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    HumanMessage, 
    BaseMessage, 
    SystemMessage, 
    ToolMessage,
    filter_messages
)
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
from langgraph.errors import NodeError

from agent.src.prompts import lead_researcher_prompt
from agent.src.subgraphs.research_graph import researcher_agent
from agent.src.state import SupervisorState
from agent.src.schemas import ConductResearch, ResearchComplete
from agent.src.utils.helper import get_today_str
from agent.src.tools import think_tool
from agent.src.config import settings
from agent.src.utils.compaction import compact_research_notes

logger = logging.getLogger(__name__)

def get_notes_from_tool_calls(messages: list[BaseMessage]) -> list[str]:
    """Extract research notes from ToolMessage objects in supervisor message history."""
    return [str(tool_msg.content) for tool_msg in filter_messages(messages, include_types="tool")]


# ===== 1. CONFIGURATION & RELIABLE MODEL CHAIN =====

primary_supervisor_model = init_chat_model(
    model="google_genai:gemini-3.5-flash-lite", 
    api_key=settings.GOOGLE_API_KEY
)
backup_supervisor_model = init_chat_model(
    model="google_genai:gemini-3.6-flash", 
    api_key=settings.GOOGLE_API_KEY
)

supervisor_tools_list = [ConductResearch, ResearchComplete, think_tool]

# Reliable Model Chain with retries and failover
reliable_supervisor_model = (
    primary_supervisor_model
    .bind_tools(supervisor_tools_list)
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
    .with_fallbacks([
        backup_supervisor_model.bind_tools(supervisor_tools_list).with_retry(stop_after_attempt=2)
    ])
)

# System constants
max_researcher_iterations = 6
max_concurrent_researchers = 3


# ===== CONFIGURATION =====

supervisor_tools = [ConductResearch, ResearchComplete, think_tool]
supervisor_model = init_chat_model(model="google_genai:gemini-3.5-flash-lite", api_key=settings.GOOGLE_API_KEY)
supervisor_model_with_tools = supervisor_model.bind_tools(supervisor_tools)


# ===== SUPERVISOR NODES =====

async def supervisor(state: SupervisorState) -> Command[Literal["supervisor_tools"]]:
    """Coordinate research activities.

    Analyzes the research brief and current progress to decide:
    - What research topics need investigation
    - Whether to conduct parallel research
    - When research is complete

    Args:
        state: Current supervisor state with messages and research progress

    Returns:
        Command to proceed to supervisor_tools node with updated state
    """
    supervisor_messages = state.get("supervisor_messages", [])

    # Prepare system message with current date and constraints
    system_message = lead_researcher_prompt.format(
        date=get_today_str(), 
        max_concurrent_research_units=max_concurrent_researchers,
        max_researcher_iterations=max_researcher_iterations
    )
    messages = [SystemMessage(content=system_message)] + supervisor_messages

    # Make decision about next research steps
    response = await reliable_supervisor_model.ainvoke(messages)

    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1
        }
    )

async def supervisor_tools(state: SupervisorState) -> Command[Literal["supervisor", "__end__"]]:
    """Execute supervisor decisions - either conduct research or end the process.

    Handles:
    - Executing think_tool calls for strategic reflection
    - Launching parallel research agents for different topics
    - Aggregating research results
    - Determining when research is complete

    Args:
        state: Current supervisor state with messages and iteration count

    Returns:
        Command to continue supervision, end process, or handle errors
    """
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1]

    # Initialize variables for single return pattern
    tool_messages = []
    all_raw_notes = []
    next_step = "supervisor"  # Default next step
    should_end = False

    # Check exit criteria first
    exceeded_iterations = research_iterations >= max_researcher_iterations
    no_tool_calls = not getattr(most_recent_message, "tool_calls", None)
    research_complete = any(
        tool_call["name"] == "ResearchComplete" 
        for tool_call in (getattr(most_recent_message, "tool_calls", []) or [])
    )

    if exceeded_iterations or no_tool_calls or research_complete:
        should_end = True
        next_step = END

    else:
        # Execute ALL tool calls before deciding next step
        try:
            # Separate think_tool calls from ConductResearch calls
            think_tool_calls = [
                tool_call for tool_call in most_recent_message.tool_calls 
                if tool_call["name"] == "think_tool"
            ]

            conduct_research_calls = [
                tool_call for tool_call in most_recent_message.tool_calls 
                if tool_call["name"] == "ConductResearch"
            ]

            # 1. Handle think_tool calls (async)
            for tool_call in think_tool_calls:
                observation = await think_tool.ainvoke(tool_call["args"])
                tool_messages.append(
                    ToolMessage(
                        content=observation,
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"]
                    )
                )

            # # 2. Handle ConductResearch calls with per-subagent fault isolation (parallel async subgraphs)
            if conduct_research_calls:
                async def safe_run_subagent(tool_call):
                    topic = tool_call["args"].get("research_topic", "General Research")
                    try:
                        result = await researcher_agent.ainvoke({
                            "researcher_messages": [HumanMessage(content=topic)],
                            "research_topic": topic
                        })
                        return result
                    except Exception as e:
                        logger.error(f"⚠️ Sub-agent failed for topic '{topic}': {e}")
                        return {
                            "compressed_research": f"Sub-agent research failed for topic '{topic}': {str(e)}",
                            "raw_notes": []
                        }
                    
                # Launch parallel research agents
                coros = [safe_run_subagent(tc) for tc in conduct_research_calls]

                # Wait for all research to complete
                tool_results = await asyncio.gather(*coros)

                # Format research results as tool messages
                # Each sub-agent returns compressed research findings in result["compressed_research"]
                # We write this compressed research as the content of a ToolMessage, which allows
                # the supervisor to later retrieve these findings via get_notes_from_tool_calls()
                research_tool_messages = [
                    ToolMessage(
                        content=result.get("compressed_research", "Error synthesizing research report"),
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"]
                    ) for result, tool_call in zip(tool_results, conduct_research_calls)
                ]

                tool_messages.extend(research_tool_messages)

                # Aggregate raw notes from all research
                all_raw_notes = [
                    "\n".join(result.get("raw_notes", [])) 
                    for result in tool_results
                    if result.get("raw_notes")
                ]

        except Exception as e:
            logger.error(f"❌ Error in supervisor tools node: {e}")
            should_end = True
            next_step = END

    all_history = supervisor_messages + tool_messages
    raw_notes_collected = get_notes_from_tool_calls(all_history)

    # Apply Context Compaction Gate when updating notes
    compacted_notes = await compact_research_notes(
        notes=raw_notes_collected,
        token_threshold=10000,      # Trigger compaction if notes exceed ~10k tokens
        recent_notes_to_keep=2       # Always preserve the 2 most recent research findings
    )

    if should_end:
        return Command(
            goto=END,
            update={
                "notes": compacted_notes,
                "research_brief": state.get("research_brief", "")
            }
        )
    else:
        return Command(
            goto=next_step,
            update={
                "supervisor_messages": tool_messages,
                "raw_notes": all_raw_notes,
                "notes": compacted_notes
            }
        )

# ===== 3. LANGGRAPH ERROR HANDLER =====

def handle_supervisor_error(state: SupervisorState, error: NodeError) -> Command[Literal["__end__"]]:
    """Fires if supervisor nodes exhaust all retries."""
    logger.error(f"❌ [Supervisor Error] Node '{error.node}' failed: {error.error}")
    all_history = state.get("supervisor_messages", [])
    return Command(
        goto=END,
        update={
            "notes": get_notes_from_tool_calls(all_history),
            "research_brief": state.get("research_brief", "Research terminated prematurely due to supervisor error.")
        }
    )
# ===== 4. GRAPH CONSTRUCTION WITH FAULT TOLERANCE =====

supervisor_policy = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    jitter=True
)

supervisor_builder = StateGraph(SupervisorState)

supervisor_builder.add_node(
    "supervisor", 
    supervisor,
    retry=supervisor_policy,
    timeout=TimeoutPolicy(run_timeout=60),
    error_handler=handle_supervisor_error
)

supervisor_builder.add_node(
    "supervisor_tools", 
    supervisor_tools,
    retry=supervisor_policy,
    timeout=TimeoutPolicy(run_timeout=180),  # 3 minutes cap for parallel sub-agent execution
    error_handler=handle_supervisor_error
)

supervisor_builder.add_edge(START, "supervisor")

supervisor_agent = supervisor_builder.compile()