
"""Pydantic Schemas for Research Scoping.

This defines structured schemas used for
the research agent scoping workflow, including researcher state management and output schemas.
"""
from pydantic import BaseModel, Field
from typing import Literal

# ===== STRUCTURED OUTPUT SCHEMAS =====

class ClarifyWithUser(BaseModel):
    """Schema for user clarification decision and questions."""

    need_clarification: bool = Field(
        description="Whether the user needs to be asked a clarifying question.",
    )
    question: str = Field(
        description="A question to ask the user to clarify the report scope",
    )
    verification: str = Field(
        description="Verify message that we will start research after the user has provided the necessary information.",
    )

class ResearchQuestion(BaseModel):
    """Schema for structured research brief generation."""

    research_brief: str = Field(
        description="A research question that will be used to guide the research.",
    )

class Summary(BaseModel):
    """Schema for webpage content summarization."""
    summary: str = Field(description="Concise summary of the webpage content")
    key_excerpts: str = Field(description="Important quotes and excerpts from the content")


class ConductResearch(BaseModel):
    """Tool for delegating a research task to a specialized sub-agent."""
    research_topic: str = Field(
        description="The topic to research. Should be a single topic, and should be described in high detail (at least a paragraph).",
    )

class ResearchComplete(BaseModel):
    """Tool for indicating that the research process is complete."""
    status: str = Field(
        default="complete", 
        description="Must be set to 'complete' when research is finished."
    )

class TopicClassification(BaseModel):
    """Schema for classifying incoming user requests before execution."""
    
    category: Literal["valid_research", "general_chitchat", "harmful_dangerous"] = Field(
        description="Categorize the query into deep research, general chitchat/QA, or harmful content."
    )
    rejection_reason: str = Field(
        default="",
        description="Reason if the topic is harmful or dangerous."
    )