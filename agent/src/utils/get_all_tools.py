"""Tool Aggregator Module with In-Memory Caching."""

import logging
from agent.src.mcp import get_mcp_tools
from agent.src.tools import tavily_search, think_tool

logger = logging.getLogger(__name__)

_all_tools_cache: list | None = None


async def get_all_tools(force_refresh: bool = False) -> list:
    """Gather all MCP tools and custom LangChain tools into a single cached list."""
    global _all_tools_cache

    if _all_tools_cache is None or force_refresh:
        mcp_tools = await get_mcp_tools()
        # Custom primary tools always included, even if MCP fails
        _all_tools_cache = mcp_tools + [tavily_search, think_tool]

    return _all_tools_cache