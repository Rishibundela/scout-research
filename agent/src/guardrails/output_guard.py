"""Output Guardrail Engine for Deep Research Agent.

Sanitizes secrets/PII, validates URL citations against collected notes,
and enforces structural completeness on synthesized final reports.
"""

import re
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# High-confidence Regex Patterns for Secrets and Sensitive PII
SECRET_PATTERNS = [
    # AWS Access Keys
    (r"(?i)\b(AKIA|ASIA)[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]"),
    # Generic API Keys / Bearer Tokens
    (r"(?i)\b(sk-[a-zA-Z0-9]{32,64})\b", "[REDACTED_API_KEY]"),
    (r"(?i)\b(bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*)\b", "[REDACTED_BEARER_TOKEN]"),
    # Private Keys
    (r"-----BEGIN (RSA|EC|PGP|PRIVATE) KEY-----[\s\S]+?-----END \1 KEY-----", "[REDACTED_PRIVATE_KEY]"),
    # Credit Card Numbers (Basic Luhn check range)
    (r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CARD_NUMBER]"),
]


def sanitize_secrets_and_pii(text: str) -> str:
    """
    Scubs leaked API credentials, private keys, and sensitive tokens
    from generated report text.
    """
    sanitized_text = text
    for pattern, replacement in SECRET_PATTERNS:
        sanitized_text = re.sub(pattern, replacement, sanitized_text)
    return sanitized_text


def verify_url_grounding(report_text: str, accumulated_notes: List[str]) -> Tuple[str, int]:
    """
    Verifies that URLs cited in the final report actually exist in the collected research notes.
    Replaces hallucinated URLs with a warning flag.
    """
    # Extract all http/https URLs from the report
    url_pattern = r"https?://[^\s\)\>\]]+"
    cited_urls = set(re.findall(url_pattern, report_text))

    if not cited_urls:
        return report_text, 0

    # Combine all notes to check source grounding
    ground_truth_context = "\n".join(accumulated_notes)
    
    hallucinated_count = 0
    cleaned_report = report_text

    for url in cited_urls:
        # Clean trailing punctuation from regex capture
        clean_url = url.rstrip(".,;")
        if clean_url not in ground_truth_context:
            hallucinated_count += 1
            logger.warning(f"⚠️ [Hallucinated Citation Detected]: {clean_url}")
            # Replace hallucinated URL with warning placeholder
            cleaned_report = cleaned_report.replace(
                clean_url, 
                f"[Unverified Source: {clean_url}]"
            )

    return cleaned_report, hallucinated_count


def validate_report_structure(report_text: str) -> str:
    """
    Ensures report contains basic structural elements (headers, non-empty content).
    """
    if not report_text or len(report_text.strip()) < 100:
        return "# Research Report\n\n*Error: Generated report was incomplete or empty.*"

    # Ensure there is at least one H1 or H2 markdown header
    if not re.search(r"^#+\s+", report_text, re.MULTILINE):
        report_text = f"# Final Research Report\n\n{report_text}"

    return report_text