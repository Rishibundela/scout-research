"""Tool Aggregator Module with In-Memory Caching."""

import logging
import time
from agent.src.mcp import get_mcp_tools, is_mcp_tools_loaded
from agent.src.tools import tavily_search, think_tool

logger = logging.getLogger(__name__)

_all_tools_cache: list | None = None
_last_mcp_retry_time: float = 0.0
MCP_RETRY_COOLDOWN: float = 60.0  # seconds cached retry cooldown


async def get_all_tools(force_refresh: bool = False) -> list:
    """Gather all MCP tools and custom LangChain tools into a single cached list."""
    # MCP tool loading disabled for now to focus on native operations
    return [tavily_search, think_tool]