
"""
Full Multi-Agent Research System

This module integrates all components of the research system:
- User clarification and scoping
- Research brief generation  
- Multi-agent research coordination
- Final report generation

The system orchestrates the complete research workflow from initial user
input through final report delivery.
"""

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END

from agent.src.utils.helper import get_today_str, extract_text_content
from agent.src.prompts import final_report_generation_prompt
from agent.src.state import AgentState, AgentInputState
from agent.src.subgraphs.scoping_graph import clarify_with_user, write_research_brief
from agent.src.subgraphs.supervisor import supervisor_agent
from agent.src.config import settings
from agent.src.state import AgentState

# ===== Config =====

from langchain.chat_models import init_chat_model
writer_model = init_chat_model(model="google_genai:gemini-3.6-flash", api_key=settings.GOOGLE_API_KEY, max_tokens=32000)

# ===== FINAL REPORT GENERATION =====



async def final_report_generation(state: AgentState):
    """
    Final report generation node.

    Synthesizes all research findings into a comprehensive final report
    """

    notes = state.get("notes", [])

    findings = "\n".join(notes)

    final_report_prompt = final_report_generation_prompt.format(
        research_brief=state.get("research_brief", ""),
        findings=findings,
        date=get_today_str()
    )

    final_report = await writer_model.ainvoke([HumanMessage(content=final_report_prompt)])

    report_text = extract_text_content(final_report.content)

    return {
        "final_report": final_report.content, 
        "messages": [f"Here is the final report: {report_text}"],
    }

# ===== GRAPH CONSTRUCTION =====
# Build the overall workflow
deep_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Add workflow nodes
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)
deep_researcher_builder.add_node("supervisor_subgraph", supervisor_agent)
deep_researcher_builder.add_node("final_report_generation", final_report_generation)

# Add workflow edges
deep_researcher_builder.add_edge(START, "clarify_with_user")
deep_researcher_builder.add_edge("write_research_brief", "supervisor_subgraph")
deep_researcher_builder.add_edge("supervisor_subgraph", "final_report_generation")
deep_researcher_builder.add_edge("final_report_generation", END)

# Compile the full workflow
deep_research_agent = deep_researcher_builder.compile()
