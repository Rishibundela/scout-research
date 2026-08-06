"""Research Agent Implementation.

This module implements a hardened research agent that performs iterative web searches,
tool execution, and synthesis with native LangGraph fault tolerance, model fallbacks,
and graceful tool error handling.
"""

import asyncio
import logging
from typing_extensions import Literal

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, filter_messages
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
from langgraph.errors import NodeError

from agent.src.config import settings
from agent.src.prompts import (
    research_agent_prompt,
    compress_research_system_prompt,
    compress_research_human_message,
)
from agent.src.utils.helper import get_today_str
from agent.src.state import ResearcherState, ResearcherOutputState
from agent.src.utils.get_all_tools import get_all_tools

logger = logging.getLogger(__name__)

# ===== 1. MODEL SETUP WITH RETRIES & FALLBACKS =====

# Primary Model for Reasoning & Tool Calling
primary_model = init_chat_model(
    model="google_genai:gemini-2.0-flash",
    api_key=settings.GOOGLE_API_KEY,
)
backup_model = init_chat_model(
    model="google_genai:gemini-1.5-pro",
    api_key=settings.GOOGLE_API_KEY,
)

# Compression Model with Large Context Window
primary_compress_model = init_chat_model(
    model="google_genai:gemini-3.1-flash-lite",
    max_tokens=32000,
    api_key=settings.GOOGLE_API_KEY,
)
backup_compress_model = init_chat_model(
    model="google_genai:gemini-3.5-flash-lite",
    max_tokens=32000,
    api_key=settings.GOOGLE_API_KEY,
)


def get_reliable_model():
    """Model chain with retries and failover for main reasoning."""
    primary_retry = primary_model.with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
    backup_retry = backup_model.with_retry(stop_after_attempt=2, wait_exponential_jitter=True)
    return primary_retry.with_fallbacks([backup_retry])


def get_reliable_compress_model():
    """Model chain with retries and failover for compression."""
    primary_retry = primary_compress_model.with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
    backup_retry = backup_compress_model.with_retry(stop_after_attempt=2, wait_exponential_jitter=True)
    return primary_retry.with_fallbacks([backup_retry])

# ===== 2. AGENT WORKFLOW NODES =====

async def llm_call(state: ResearcherState) -> dict:
    """Analyze current state and decide on next actions."""
    tools = await get_all_tools()
    reliable_model = get_reliable_model()
    model_with_tools = reliable_model.bind_tools(tools)

    response = await model_with_tools.ainvoke(
        [SystemMessage(content=research_agent_prompt)] + state["researcher_messages"]
    )
    return {"researcher_messages": [response]}
    
async def tool_node(state: ResearcherState) -> dict:
    """
    Execute all tool calls concurrently with graceful per-tool error isolation.
    A single tool failure will NEVER crash the node or stop other tools.
    """
    tools = await get_all_tools()
    tools_by_name = {t.name: t for t in tools}

    last_message = state["researcher_messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", [])

    async def safe_execute_tool(call):
        tool_name = call["name"]
        tool_call_id = call["id"]

        if tool_name not in tools_by_name:
            return ToolMessage(
                content=f"Error: Tool '{tool_name}' is not recognized.",
                name=tool_name,
                tool_call_id=tool_call_id,
            )

        try:
            tool = tools_by_name[tool_name]
            # Wrap tool execution in an isolated try/except block
            observation = await tool.ainvoke(call["args"])
            return ToolMessage(
                content=str(observation),
                name=tool_name,
                tool_call_id=tool_call_id,
            )
        except Exception as e:
            logger.warning(f"⚠️ Tool '{tool_name}' failed during execution: {e}")
            # Graceful Degradation: Feed error back to LLM so it can adjust its query
            return ToolMessage(
                content=f"Tool execution failed: {str(e)}. Try refining your query or arguments.",
                name=tool_name,
                tool_call_id=tool_call_id,
            )

    # Execute all tool calls concurrently with per-tool fault isolation
    tool_outputs = await asyncio.gather(*(safe_execute_tool(c) for c in tool_calls))
    return {"researcher_messages": tool_outputs}

async def compress_research(state: ResearcherState) -> dict:
    """Compress research findings into a concise summary."""
    system_message = compress_research_system_prompt.format(date=get_today_str())
    messages = (
        [SystemMessage(content=system_message)]
        + state.get("researcher_messages", [])
        + [HumanMessage(content=compress_research_human_message)]
    )

    compress_chain = get_reliable_compress_model()
    response = await compress_chain.ainvoke(messages)

    # Extract raw notes from tool and AI messages
    raw_notes_list = [
        str(m.content)
        for m in filter_messages(
            state.get("researcher_messages", []),
            include_types=["tool", "ai"],
        )
    ]

    return {
        "compressed_research": str(response.content),
        "raw_notes": ["\n---\n".join(raw_notes_list)],
    }

# ===== 3. ROUTING LOGIC =====

def should_continue(state: ResearcherState) -> Literal["tool_node", "compress_research"]:
    """Determine whether to continue research or compress findings."""
    messages = state.get("researcher_messages", [])
    if not messages:
        return "compress_research"

    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
        return "tool_node"
    return "compress_research"

# ===== 4. LANGGRAPH ERROR HANDLER =====

def handle_research_error(state: ResearcherState, error: NodeError) -> Command[Literal["__end__"]]:
    """
    Fires if a node exhausts all retries.
    Returns safe fallback output to prevent complete workflow crashes.
    """
    logger.error(f"❌ [Research Agent Error] Node '{error.node}' failed: {error.error}")
    
    fallback_summary = f"Research execution hit an unrecoverable limit at step '{error.node}'. Partial findings saved."
    return Command(
        goto=END,
        update={
            "compressed_research": fallback_summary,
            "raw_notes": ["Partial research saved due to system error."],
        },
    )

# ===== 5. GRAPH CONSTRUCTION WITH FAULT TOLERANCE =====

agent_policy = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    jitter=True,
)

agent_builder = StateGraph(ResearcherState, output_schema=ResearcherOutputState)

# Add nodes with RetryPolicy, Timeout, and Error Handler
agent_builder.add_node(
    "llm_call",
    llm_call,
    retry=agent_policy,
    timeout=TimeoutPolicy(run_timeout=45),
    error_handler=handle_research_error,
)

agent_builder.add_node(
    "tool_node",
    tool_node,
    retry=agent_policy,
    timeout=TimeoutPolicy(run_timeout=60),  # Tools get 60s for parallel web operations
    error_handler=handle_research_error,
)

agent_builder.add_node(
    "compress_research",
    compress_research,
    retry=agent_policy,
    timeout=TimeoutPolicy(run_timeout=45),
    error_handler=handle_research_error,
)

# Connect Edges
agent_builder.add_edge(START, "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {
        "tool_node": "tool_node",
        "compress_research": "compress_research",
    },
)
agent_builder.add_edge("tool_node", "llm_call")
agent_builder.add_edge("compress_research", END)

# Compile Agent
researcher_agent = agent_builder.compile()