import os
from pathlib import Path
from dotenv import load_dotenv

# Calculate absolute path to root .env file relative to this file (src/config.py)
# src is 1 level down from agent, agent is 1 level down from root -> 2 parents up
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"

# Force load variables explicitly from the fixed absolute disk address
load_dotenv(dotenv_path=ENV_PATH)

class Settings:
    """Centralized workspace configuration settings."""
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
    
    # LangSmith Tracing Parameters
    LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "false")
    LANGCHAIN_ENDPOINT: str = os.getenv("LANGCHAIN_ENDPOINT", "https://langchain.com")
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "scout-research")

# Instantiate a single global settings object
settings = Settings()

# # Export settings for use in other modules
# __all__ = ["settings"]