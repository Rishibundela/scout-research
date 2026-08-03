"""Research Utilities.

This module provides search and content processing utilities for the research agent.
"""

from datetime import datetime

# ===== UTILITY FUNCTIONS =====

def get_today_str() -> str:
    """Return today's date formatted as a human-readable string (e.g., 'August 03, 2026')."""
    return datetime.now().strftime("%B %d, %Y")

def extract_text_content(content) -> str:
    """
    Safely extract and merge raw text out of any LangChain message content format.
    
    Handles primitive strings, multi-part multimodal lists, and dictionaries 
    natively returned by modern Google GenAI models.
    """
    if not content:
        return ""
        
    if isinstance(content, str):
        return content
        
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                # Handle standard OpenAI/Gemini block formats
                if "text" in part:
                    text_parts.append(part["text"])
                elif part.get("type") == "text" and "text" in part:
                    text_parts.append(part["text"])
        return "".join(text_parts)
        
    if isinstance(content, dict):
        return content.get("text", str(content))
        
    return str(content)