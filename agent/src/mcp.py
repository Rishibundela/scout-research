from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient
from agent.src.files import get_current_dir

# Ensure target directory exists
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
        "transport": "stdio",  # Fixed invalid transport "studio" -> "stdio"
    }
}

_client: MultiServerMCPClient | None = None


def get_mcp_client() -> MultiServerMCPClient:
    """Instantiate and return the global singleton MultiServerMCPClient."""
    global _client
    if _client is None:
        _client = MultiServerMCPClient(mcp_config)
    return _client


async def get_mcp_tools() -> list:
    """Get the list of LangChain-compatible tools available from the MCP client."""
    client = get_mcp_client()
    return await client.get_tools()

