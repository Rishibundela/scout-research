"""Fast Heuristic and Regex Input Guardrail.

Provides zero-latency pre-checks to block prompt injection attacks,
jailbreaks, and system prompt extraction before LLM invocation.
"""

import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# High-precision injection patterns
INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"(?i)system\s+override",
    r"(?i)you\s+are\s+now\s+a\s+(dan|jailbroken)",
    r"(?i)disregard\s+your\s+rules",
    r"(?i)print\s+(your\s+)?system\s+prompt",
    r"(?i)output\s+(your\s+)?initial\s+instructions",
    r"(?i)<\|im_start\|>",
    r"(?i)\[system\]\s*\(",
]


def validate_user_input(text: str) -> Tuple[bool, str]:
    """
    Validates user input against heuristics and regex patterns.
    
    Returns:
        (is_safe: bool, reason_or_cleaned_text: str)
    """
    if not text or not text.strip():
        return False, "Input prompt cannot be empty."

    # Check input length cap (e.g., max 10,000 characters for initial query)
    if len(text) > 10000:
        return False, "Input query exceeds maximum allowed length (10,000 characters)."

    # Scan for prompt injection patterns
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text):
            logger.warning(f"Security Guardrail Triggered! Matched pattern: {pattern}")
            return False, "Input query contains restricted system control phrases or prompt injection patterns."

    return True, text