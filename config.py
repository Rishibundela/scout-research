# config.py
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    # Application Title
    APP_TITLE: str = os.getenv("APP_TITLE", "Deep Research Agent Engine")
    DEFAULT_USER_ID: str = os.getenv("DEFAULT_USER_ID", "default_user")
    # Render API Base URL
    RENDER_URL: str = os.getenv("LANGGRAPH_RENDER_URL", "https://scout-research.onrender.com")
    
    # LangGraph API Key (Set to None if your Render server doesn't require an API Key)
    API_KEY: str | None = os.getenv("LANGGRAPH_API_KEY", None)
    
    # Default Assistant ID configured in langgraph.json
    ASSISTANT_ID: str = os.getenv("LANGGRAPH_ASSISTANT_ID", "deep_research_agent")
    
    # Interval in seconds to poll background runs for updates in Streamlit UI
    POLL_INTERVAL_SECONDS: float = float(os.getenv("POLL_INTERVAL_SECONDS", "1.0"))

settings = Settings()