"""Pydantic Schema Enforcement with Error Reflection Loops.

When an LLM fails Pydantic schema validation, this module catches the error,
constructs a diagnostic reflection prompt, and instructs the model to fix its response.
"""

import logging
from typing import Type, TypeVar, List
from pydantic import BaseModel, ValidationError
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


async def invoke_with_schema_reflection(
    model: BaseChatModel,
    pydantic_schema: Type[T],
    messages: List[BaseMessage],
    max_reflections: int = 3,
) -> T:
    """
    Invokes an LLM with structured output and automated reflection feedback loops.

    Args:
        model: The base ChatModel instance.
        pydantic_schema: The target Pydantic class to parse into.
        messages: The conversation history/prompt messages.
        max_reflections: Maximum attempt count before raising/falling back.

    Returns:
        Validated instance of pydantic_schema.
    """
    # Create raw model binder to inspect raw text if structured output fails
    structured_model = model.with_structured_output(pydantic_schema)
    current_messages = list(messages)

    for attempt in range(1, max_reflections + 1):
        try:
            logger.debug(f"Attempt {attempt}/{max_reflections} for schema '{pydantic_schema.__name__}'")
            response: T = await structured_model.ainvoke(current_messages)
            return response

        except (ValidationError, Exception) as exc:
            logger.warning(
                f"⚠️ Schema validation failed on attempt {attempt}/{max_reflections} for {pydantic_schema.__name__}: {exc}"
            )

            if attempt == max_reflections:
                logger.error(f"❌ Max reflections ({max_reflections}) exhausted for {pydantic_schema.__name__}.")
                raise exc

            # Construct Reflection Feedback Prompt
            error_feedback = (
                f"Your previous response failed validation for the schema '{pydantic_schema.__name__}'.\n\n"
                f"**Validation Error Details:**\n{str(exc)}\n\n"
                f"Please regenerate your response adhering STRICTLY to the target JSON schema. "
                f"Ensure all required fields are present and correctly typed."
            )

            # Append error feedback to messages so LLM sees its mistake in context
            current_messages.append(
                HumanMessage(content=error_feedback)
            )