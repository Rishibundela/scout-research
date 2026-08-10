import logging
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient
from agent.src.files import get_current_dir

logger = logging.getLogger(__name__)

_client: MultiServerMCPClient | None = None
_cached_mcp_tools: list | None = None

def get_mcp_client() -> MultiServerMCPClient:
    """Instantiate and return the global singleton MultiServerMCPClient lazily."""
    global _client
    if _client is None:
        # Lazy directory creation on first access
        files_dir = get_current_dir() / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        mcp_config = {
            "filesystem": {
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    files_dir.as_posix(),
                ],
                "transport": "stdio",
            }
        }
        _client = MultiServerMCPClient(mcp_config)
    return _client


async def get_mcp_tools() -> list:
    """Get LangChain tools from MCP client with caching and fault isolation."""
    global _cached_mcp_tools

    # 1. Return cached tools if already discovered
    if _cached_mcp_tools is not None:
        return _cached_mcp_tools

    # 2. Resilient Fetch: If MCP fails, fallback gracefully to empty list
    try:
        client = get_mcp_client()
        _cached_mcp_tools = await client.get_tools()
        logger.info(f"Loaded {len(_cached_mcp_tools)} tools from MCP server.")
        return _cached_mcp_tools
    except Exception as e:
        logger.error(
            f"Failed to connect to MCP server or load tools: {e}. Proceeding without MCP tools."
        )
        return []

def is_mcp_tools_loaded() -> bool:
    """Check if MCP tools have been successfully fetched and cached."""
    return _cached_mcp_tools is not None