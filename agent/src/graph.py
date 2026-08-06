
"""
Full Multi-Agent Research System

This module integrates all components of the research system:
- User clarification and scoping
- Research brief generation  
- Multi-agent research coordination
- Final report generation

The system orchestrates the complete research workflow from initial user
input through final report delivery.
Hardened with model fallbacks, execution timeouts, and top-level error boundaries.
"""

import logging
from typing_extensions import Literal

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, RetryPolicy, TimeoutPolicy
from langgraph.errors import NodeError
from langchain.chat_models import init_chat_model

from agent.src.utils.helper import get_today_str, extract_text_content
from agent.src.prompts import final_report_generation_prompt
from agent.src.state import AgentState, AgentInputState
from agent.src.subgraphs.scoping_graph import clarify_with_user, write_research_brief
from agent.src.subgraphs.supervisor import supervisor_agent
from agent.src.config import settings

logger = logging.getLogger(__name__)

# ===== 1. RELIABLE WRITER MODEL CHAIN =====

primary_writer = init_chat_model(
    model="google_genai:gemini-3.6-flash", 
    api_key=settings.GOOGLE_API_KEY, 
    max_tokens=32000
)
backup_writer = init_chat_model(
    model="google_genai:gemini-3.5-flash", 
    api_key=settings.GOOGLE_API_KEY, 
    max_tokens=32000
)

reliable_writer_model = (
    primary_writer
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
    .with_fallbacks([backup_writer.with_retry(stop_after_attempt=2)])
)

# ===== FINAL REPORT GENERATION =====

async def final_report_generation(state: AgentState):
    """
    Final report generation node.

    Synthesizes all research findings into a comprehensive final report
    """

    notes = state.get("notes", [])

    findings = "\n".join(notes) if isinstance(notes, list) else str(notes)

    final_report_prompt = final_report_generation_prompt.format(
        research_brief=state.get("research_brief", ""),
        findings=findings if findings.strip() else "No specific findings collected.",
        date=get_today_str()
    )

    final_report = await reliable_writer_model.ainvoke([HumanMessage(content=final_report_prompt)])
    report_text = extract_text_content(final_report.content)

    return {
        "final_report": final_report.content, 
        "messages": [AIMessage(content=f"Here is the final report:\n\n{report_text}")],
    }

# ===== 3. TOP-LEVEL ERROR HANDLER =====

def handle_main_error(state: AgentState, error: NodeError) -> Command[Literal["__end__"]]:
    """Top-level error safety net for the entire agent system."""
    logger.error(f"❌ [Main Orchestrator Error] Node '{error.node}' failed: {error.error}")
    
    notes = state.get("notes", [])
    raw_findings = "\n\n".join(notes) if isinstance(notes, list) else str(notes)
    
    fallback_report = (
        f"# Research Report (Partial / Emergency Recovery)\n\n"
        f"**Note:** Final synthesis hit an unrecoverable service timeout. "
        f"Below are the raw findings gathered by research sub-agents:\n\n"
        f"{raw_findings if raw_findings.strip() else 'No findings available.'}"
    )

    return Command(
        goto=END,
        update={
            "final_report": fallback_report,
            "messages": [AIMessage(content=fallback_report)]
        }
    )

# ===== 4. GRAPH CONSTRUCTION =====

main_policy = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    jitter=True
)

deep_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Add workflow nodes
# Add workflow nodes with fault tolerance
deep_researcher_builder.add_node(
    "clarify_with_user", 
    clarify_with_user,
    retry=main_policy,
    timeout=TimeoutPolicy(run_timeout=30),
    error_handler=handle_main_error
)
deep_researcher_builder.add_node(
    "write_research_brief", 
    write_research_brief,
    retry=main_policy,
    timeout=TimeoutPolicy(run_timeout=45),
    error_handler=handle_main_error
)
deep_researcher_builder.add_node("supervisor_subgraph", supervisor_agent)
deep_researcher_builder.add_node(
    "final_report_generation", 
    final_report_generation,
    retry=main_policy,
    timeout=TimeoutPolicy(run_timeout=180),  # 3 minutes for report writing
    error_handler=handle_main_error
)

# Connect workflow edges
deep_researcher_builder.add_edge(START, "clarify_with_user")
deep_researcher_builder.add_edge("write_research_brief", "supervisor_subgraph")
deep_researcher_builder.add_edge("supervisor_subgraph", "final_report_generation")
deep_researcher_builder.add_edge("final_report_generation", END)

# Compile the full workflow
deep_research_agent = deep_researcher_builder.compile()
