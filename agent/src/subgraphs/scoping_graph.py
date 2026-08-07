"""User Clarification and Research Brief Generation.

This module implements the scoping phase of the research workflow, where we:
1. Assess if the user's request needs clarification
2. Generate a detailed research brief from the conversation

The workflow uses structured output to make deterministic decisions about
whether sufficient context exists to proceed with research.
"""

import logging
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, get_buffer_string
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
from langgraph.errors import NodeError  # FIXED: Added missing import!

from agent.src.config import settings
from agent.src.guardrails.topic_classifier import classify_topic
from agent.src.schemas import ClarifyWithUser, ResearchQuestion
from agent.src.prompts import (
    clarify_with_user_instructions,
    transform_messages_into_research_topic_prompt,
    general_assistant_prompt,
)
from agent.src.state import AgentState, AgentInputState
from agent.src.utils.helper import get_today_str
from agent.src.guardrails.input_guard import validate_user_input

logger = logging.getLogger(__name__)


# ===== 1. MODEL DEFINITIONS =====

primary_model = init_chat_model(
    model="google_genai:gemini-3.1-flash-lite",
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)

backup_model = init_chat_model(
    model="google_genai:gemini-3.5-flash-lite",  # Ensure valid backup model identifier
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)

general_llm = init_chat_model(
    model="google_genai:gemini-3.1-flash-lite", 
    api_key=settings.GOOGLE_API_KEY
)


def get_reliable_structured_model(pydantic_schema):
    """
    Complete Chain:
    1. Try Primary Model (Retry up to 3 times on transient blips)
    2. If primary fails all 3 attempts -> Fall back to Backup Model (Retry up to 2 times)
    """
    primary_structured = (
        primary_model
        .with_structured_output(pydantic_schema)
        .with_retry(
            stop_after_attempt=3,
            wait_exponential_jitter=True,
        )
    )

    backup_structured = (
        backup_model
        .with_structured_output(pydantic_schema)
        .with_retry(
            stop_after_attempt=2,
            wait_exponential_jitter=True,
        )
    )

    return primary_structured.with_fallbacks([backup_structured])


# ===== 2. WORKFLOW NODES =====

async def clarify_with_user(
    state: AgentState,
) -> Command[Literal["write_research_brief", "__end__"]]:
    """
    Determine if the user's request contains sufficient information to proceed with research.

    Uses structured output to make deterministic decisions and avoid hallucination.
    Routes to either research brief generation or ends with a clarification question.
    """
    # 1. Fast Zero-Latency Regex Guardrail Check
    user_query = get_buffer_string(state["messages"])
    is_safe_regex, regex_message = validate_user_input(user_query)
    
    if not is_safe_regex:
        # Instantly halt and return safety rejection message
        return Command(goto=END, update={"messages": [AIMessage(content=f"Request Blocked: {regex_message}")]})
    
    # 2. Topic Classification Guardrail
    classification = await classify_topic(user_query)

    # Route based on classification
    if classification.category == "harmful_dangerous":
        return Command(goto=END, update={"messages": [AIMessage(content=f"Safety Notice: {classification.rejection_reason}")]})

    elif classification.category == "general_chitchat":
        # Route to general chitchat assistant node
        return Command(goto="general_assistant")
    
    reliable_model = get_reliable_structured_model(ClarifyWithUser)

    prompt = HumanMessage(
        content=clarify_with_user_instructions.format(
            messages=get_buffer_string(messages=state["messages"]),
            date=get_today_str(),
        )
    )

    response: ClarifyWithUser = await reliable_model.ainvoke([prompt])

    # Route based on clarification need
    if response.need_clarification:
        return Command(
            goto=END,
            update={"messages": [AIMessage(content=response.question)]},
        )
    else:
        return Command(
            goto="write_research_brief",
            update={"messages": [AIMessage(content=response.verification)]},
        )

async def general_assistant_node(state: AgentState) -> dict:
    """Handles chitchat and general non-research questions fast and cheaply."""
    user_query = state["messages"][-1].content
    response = await general_llm.ainvoke([
        SystemMessage(content=general_assistant_prompt),
        HumanMessage(content=user_query)
    ])
    
    return {
        "messages": [AIMessage(content=response.content)]
    }

async def write_research_brief(state: AgentState) -> dict:
    """
    Transform the conversation history into a comprehensive research brief.

    Uses structured output to ensure the brief follows the required format
    and contains all necessary details for effective research.
    """
    reliable_model = get_reliable_structured_model(ResearchQuestion)

    prompt = HumanMessage(
        content=transform_messages_into_research_topic_prompt.format(
            messages=get_buffer_string(state.get("messages", [])),
            date=get_today_str(),
        )
    )
    response: ResearchQuestion = await reliable_model.ainvoke([prompt])

    return {
        "research_brief": response.research_brief,
        "supervisor_messages": [HumanMessage(content=f"{response.research_brief}")],
    }


# ===== 3. LANGGRAPH NATIVE ERROR HANDLER NODE =====

def handle_scoping_error(
    state: AgentState, error: NodeError
) -> Command[Literal["__end__"]]:
    """
    LangGraph native error handler.
    Fires automatically when a node's RetryPolicy is exhausted.
    Receives injected `NodeError` containing node name and exception context.
    """
    logger.error(
        f"❌ Node '{error.node}' exhausted all retries! Underlying error: {error.error}"
    )

    if error.node == "clarify_with_user":
        fallback_msg = "I encountered a service issue while processing your request. Could you please rephrase?"
        return Command(
            goto=END,
            update={"messages": [AIMessage(content=fallback_msg)]},
        )

    # Fallback for write_research_brief
    raw_context = get_buffer_string(state.get("messages", []))
    return Command(
        goto=END,
        update={
            "research_brief": raw_context,
            "messages": [
                AIMessage(content=f"Emergency Brief Generated:\n{raw_context}")
            ],
        },
    )


# ===== 4. GRAPH CONSTRUCTION WITH NATIVE POLICIES =====

scoping_policy = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    jitter=True,
)

scoping_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Wire nodes with RetryPolicy, Timeout, AND error_handler natively!
scoping_builder.add_node(
    "clarify_with_user",
    clarify_with_user,
    retry=scoping_policy,
    timeout=TimeoutPolicy(run_timeout=30),  # Caps single attempt execution at 30 seconds
    error_handler=handle_scoping_error,
)

scoping_builder.add_node(
    "write_research_brief",
    write_research_brief,
    retry=scoping_policy,
    timeout=TimeoutPolicy(run_timeout=45),  # Caps brief generation at 45 seconds
    error_handler=handle_scoping_error,
)

scoping_builder.add_node("general_assistant", general_assistant_node)

# Add workflow edges
scoping_builder.add_edge(START, "clarify_with_user")
scoping_builder.add_edge("general_assistant", END)
scoping_builder.add_edge("write_research_brief", END)

# Compile the workflow
scope_research = scoping_builder.compile()