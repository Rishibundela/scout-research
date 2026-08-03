
"""Research Agent Implementation.

This module implements a research agent that can perform iterative web searches
and synthesis to answer complex research questions.
"""
import asyncio
from typing_extensions import Literal
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, filter_messages
from langchain.chat_models import init_chat_model
from agent.src.tools import tavily_search, think_tool
from agent.src.prompts import research_agent_prompt, compress_research_system_prompt, compress_research_human_message
from agent.src.utils.helper import get_today_str
from agent.src.state import ResearcherState, ResearcherOutputState 
from agent.src.config import settings
from agent.src.mcp import get_mcp_tools
from agent.src.utils.get_all_tools import get_all_tools

# Initialize models
model = init_chat_model(model="google_genai:gemini-3.5-flash",api_key=settings.GOOGLE_API_KEY)
summarization_model = init_chat_model(model="google_genai:gemini-3.1-flash-lite", api_key=settings.GOOGLE_API_KEY)
compress_model = init_chat_model(model="google_genai:gemini-3.5-flash-lite", max_tokens=32000, api_key=settings.GOOGLE_API_KEY)

# ===== AGENT NODES =====

async def llm_call(state: ResearcherState):
    """Analyze current state and decide on next actions.

    The model analyzes the current conversation state and decides whether to:
    1. Call search tools to gather more information
    2. Provide a final answer based on gathered information

    Returns updated state with the model's response.
    """

    # Add tools to model
    tools = await get_all_tools()
    model_with_tools = model.bind_tools(tools)


    response = await model_with_tools.ainvoke(
        [SystemMessage(content=research_agent_prompt)] + state["researcher_messages"]
    )
    return {
        "researcher_messages": [response]
    }
    
# Node 2: Execute Tools Concurrently
async def tool_node(state: ResearcherState):
    """Execute all tool calls from the previous LLM response.
    
        Executes all tool calls from the previous LLM responses.
        Returns updated state with tool execution results.
    """
    tools = await get_all_tools()
    tools_by_name = {t.name: t for t in tools}

    tool_calls = state["researcher_messages"][-1].tool_calls

    async def execute_tool(call):
        tool = tools_by_name[call["name"]]
        observation = await tool.ainvoke(call["args"])
        return ToolMessage(
            content=str(observation), name=call["name"], tool_call_id=call["id"]
        )

    tool_outputs = await asyncio.gather(
        *(execute_tool(c) for c in tool_calls)
    )
    return {"researcher_messages": tool_outputs}

# Node 3: Compress Research
async def compress_research(state: ResearcherState) -> dict:
    """Compress research findings into a concise summary.

    Takes all the research messages and tool outputs and creates
    a compressed summary suitable for the supervisor's decision-making.
    """

    system_message = compress_research_system_prompt.format(date=get_today_str())
    messages = [SystemMessage(content=system_message)] + state.get("researcher_messages", []) + [HumanMessage(content=compress_research_human_message)]
    response = await compress_model.ainvoke(messages)

    # Extract raw notes from tool and AI messages
    raw_notes = [
        str(m.content) for m in filter_messages(
            state["researcher_messages"], 
            include_types=["tool", "ai"]
        )
    ]

    return {
        "compressed_research": str(response.content),
        "raw_notes": ["\n".join(raw_notes)]
    }

# ===== ROUTING LOGIC =====

def should_continue(state: ResearcherState) -> Literal["tool_node", "compress_research"]:
    """Determine whether to continue research or provide final answer.

    Determines whether the agent should continue the research loop or provide
    a final answer based on whether the LLM made tool calls.

    Returns:
        "tool_node": Continue to tool execution
        "compress_research": Stop and compress research
    """
    messages = state["researcher_messages"]
    last_message = messages[-1]

    # If the LLM makes a tool call, continue to tool execution
    if last_message.tool_calls:
        return "tool_node"
    # Otherwise, we have a final answer
    return "compress_research"

# ===== GRAPH CONSTRUCTION =====

# Build the agent workflow
agent_builder = StateGraph(ResearcherState, output_schema=ResearcherOutputState)

# Add nodes to the graph
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("tool_node", tool_node)
agent_builder.add_node("compress_research", compress_research)

# Add edges to connect nodes
agent_builder.add_edge(START, "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {
        "tool_node": "tool_node", # Continue research loop
        "compress_research": "compress_research", # Provide final answer
    },
)
agent_builder.add_edge("tool_node", "llm_call") # Loop back for more research
agent_builder.add_edge("compress_research", END)

# Compile the agent
researcher_agent = agent_builder.compile()
