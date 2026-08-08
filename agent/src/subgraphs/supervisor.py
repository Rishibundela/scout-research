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
    temperature=0.4,
    api_key=settings.GOOGLE_API_KEY
)
backup_supervisor_model = init_chat_model(
    model="google_genai:gemini-3.6-flash", 
    temperature=0.4,
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


# ===== 2. SUPERVISOR NODES =====

async def supervisor(state: SupervisorState) -> Command[Literal["supervisor_tools"]]:
    """Coordinate research activities and decide next steps."""
    supervisor_messages = state.get("supervisor_messages", [])

    system_message = lead_researcher_prompt.format(
        date=get_today_str(), 
        max_concurrent_research_units=max_concurrent_researchers,
        max_researcher_iterations=max_researcher_iterations
    )
    messages = [SystemMessage(content=system_message)] + supervisor_messages

    response = await reliable_supervisor_model.ainvoke(messages)

    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1
        }
    )


async def supervisor_tools(state: SupervisorState) -> Command[Literal["supervisor", "__end__"]]:
    """Execute supervisor decisions - either conduct research or end the process."""
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1] if supervisor_messages else None

    # Initialize variables
    tool_messages = []
    all_raw_notes = []
    next_step = "supervisor"
    should_end = False

    # Check exit criteria
    exceeded_iterations = research_iterations >= max_researcher_iterations
    no_tool_calls = not most_recent_message or not getattr(most_recent_message, "tool_calls", None)
    
    tool_calls = getattr(most_recent_message, "tool_calls", []) or []
    research_complete = any(tc["name"] == "ResearchComplete" for tc in tool_calls)

    if exceeded_iterations or no_tool_calls or research_complete:
        should_end = True
        next_step = END
    else:
        try:
            # 1. Group tool calls by type
            think_tool_calls = [tc for tc in tool_calls if tc["name"] == "think_tool"]
            conduct_research_calls = [tc for tc in tool_calls if tc["name"] == "ConductResearch"]
            
            # Catch any unrecognized tool calls
            unhandled_calls = [
                tc for tc in tool_calls 
                if tc["name"] not in ["think_tool", "ConductResearch", "ResearchComplete"]
            ]

            # 2. Process unhandled tool calls gracefully
            for tc in unhandled_calls:
                tool_messages.append(
                    ToolMessage(
                        content=f"Error: Tool '{tc['name']}' is not recognized by the supervisor.",
                        name=tc["name"],
                        tool_call_id=tc["id"]
                    )
                )

            # 3. Handle think_tool calls
            for tc in think_tool_calls:
                observation = await think_tool.ainvoke(tc["args"])
                tool_messages.append(
                    ToolMessage(
                        content=str(observation),
                        name=tc["name"],
                        tool_call_id=tc["id"]
                    )
                )

            # 4. Handle ConductResearch calls with parallel subagents
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
                        logger.error(f"Sub-agent failed for topic '{topic}': {e}")
                        return {
                            "compressed_research": f"Sub-agent research failed for topic '{topic}': {str(e)}",
                            "raw_notes": []
                        }

                coros = [safe_run_subagent(tc) for tc in conduct_research_calls]
                tool_results = await asyncio.gather(*coros)

                for result, tc in zip(tool_results, conduct_research_calls):
                    compressed_content = result.get("compressed_research", "Error synthesizing research report")
                    tool_messages.append(
                        ToolMessage(
                            content=compressed_content,
                            name=tc["name"],
                            tool_call_id=tc["id"]
                        )
                    )
                    
                    subagent_raw = result.get("raw_notes", [])
                    if subagent_raw:
                        all_raw_notes.extend(subagent_raw)

        except Exception as e:
            logger.error(f"Error in supervisor tools node: {e}")
            should_end = True
            next_step = END

    all_history = supervisor_messages + tool_messages
    extracted_notes = get_notes_from_tool_calls(all_history)
    combined_raw_notes = extracted_notes + all_raw_notes

    compacted_notes = await compact_research_notes(
        notes=combined_raw_notes,
        token_threshold=10000,
        recent_notes_to_keep=2
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
    logger.error(f"[Supervisor Error] Node '{error.node}' failed: {error.error}")
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
    timeout=TimeoutPolicy(run_timeout=180),
    error_handler=handle_supervisor_error
)

supervisor_builder.add_edge(START, "supervisor")

supervisor_agent = supervisor_builder.compile()