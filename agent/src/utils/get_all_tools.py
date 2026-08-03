"""Tool aggregator module."""

from agent.src.mcp import get_mcp_tools
from agent.src.tools import tavily_search, think_tool


async def get_all_tools() -> list:
    """Gather all MCP tools and custom LangChain tools into a single list."""
    mcp_tools = await get_mcp_tools()
    return mcp_tools + [tavily_search, think_tool]