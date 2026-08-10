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
    global _all_tools_cache, _last_mcp_retry_time

    current_time = time.time()
    should_reload = (
        _all_tools_cache is None or
        force_refresh or
        (not is_mcp_tools_loaded() and (current_time - _last_mcp_retry_time > MCP_RETRY_COOLDOWN))
    )

    if should_reload:
        if not is_mcp_tools_loaded() and _all_tools_cache is not None:
            _last_mcp_retry_time = current_time
            logger.info("Retrying MCP tool discovery after cooldown...")
            
        mcp_tools = await get_mcp_tools()
        # Custom primary tools always included, even if MCP fails
        _all_tools_cache = mcp_tools + [tavily_search, think_tool]

    return _all_tools_cache