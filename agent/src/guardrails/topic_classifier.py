"""Topic Boundary Classifier.

Lightweight, high-speed LLM node that classifies queries for safety,
domain scope, and research intent before spawning sub-agents.
"""

import logging
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage

from agent.src.config import settings
from agent.src.schemas import TopicClassification

logger = logging.getLogger(__name__)

# System instructions for classification
classifier_prompt = """You are a high-speed security and scope classifier for an automated Deep Research Agent.

Analyze the user query and classify it into one of these categories:
1. 'valid_research': Questions requiring web search, technical analysis, market research, history, science, synthesis, or deep investigation.
2. 'out_of_scope': Requests for pure code generation (e.g. 'write a quicksort in C'), creative writing (e.g. 'write a poem about cats'), or math execution without research context.
3. 'harmful_dangerous': Requests involving illegal activities, malware development, physical harm, weapons, dangerous chemical synthesis, or personal harassment.
4. 'casual_chitchat': Conversational greetings like 'hi', 'how are you', 'who made you'.

Set 'is_safe' to False ONLY if category is 'harmful_dangerous'.
Set 'is_research_topic' to True ONLY if category is 'valid_research'.
Provide a concise, polite 'rejection_reason' if the query cannot be processed as a research topic.
"""

primary_classifier_model = init_chat_model(
    model="google_genai:gemini-3.1-flash-lite",
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)

backup_classifier_model = init_chat_model(
    model="google_genai:gemini-3.5-flash-lite",
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)

reliable_classifier = (
    primary_classifier_model.with_structured_output(TopicClassification)
    .with_retry(stop_after_attempt=2)
    .with_fallbacks([
        backup_classifier_model.with_structured_output(TopicClassification).with_retry(stop_after_attempt=2)
    ])
)


async def classify_topic(user_query: str) -> TopicClassification:
    """Classifies user query against safety boundaries and research scope."""
    try:
        response: TopicClassification = await reliable_classifier.ainvoke([
            SystemMessage(content=classifier_prompt),
            HumanMessage(content=user_query)
        ])
        return response
    except Exception as e:
        logger.error(f"⚠️ Topic classification failed: {e}. Defaulting to permissive research mode.")
        # Fail-Open / Graceful Fallback: If classifier fails, allow request to proceed to scoping
        return TopicClassification(
            is_safe=True,
            is_research_topic=True,
            category="valid_research",
            rejection_reason=""
        )