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


def normalize_url(url: str) -> str:
    """
    Normalizes a URL string by converting to lowercase, removing protocols, 
    stripping 'www.', and dropping trailing slashes for robust comparison.
    """
    if not url:
        return ""
    parsed = urlparse(url.lower().strip())
    # Extract domain and path, stripping www. and trailing slashes
    domain_and_path = f"{parsed.netloc}{parsed.path}".replace("www.", "").rstrip("/")
    return domain_and_path

def verify_url_grounding(report_text: str, notes: list) -> tuple[str, int]:
    """
    Cross-references cited URLs in the final report against raw notes,
    redacting or tagging URLs that do not exist anywhere in the scraped context.
    """
    raw_notes_str = "\n".join(notes) if isinstance(notes, list) else str(notes)
    
    # Extract all raw URLs from scraped notes and normalize them
    raw_urls = re.findall(r'https?://[^\s\)]+', raw_notes_str)
    normalized_scraped_urls = {normalize_url(u) for u in raw_urls if normalize_url(u)}

    # Extract all markdown links in the generated report: [Title](URL)
    report_urls = re.findall(r'https?://[^\s\)]+', report_text)
    patched_count = 0

    for cited_url in report_urls:
        norm_cited = normalize_url(cited_url)
        
        # Check if the core domain + path exists in the raw scraped memory
        if norm_cited and norm_cited not in normalized_scraped_urls:
            # Only tag as unverified if the URL is completely absent from raw scrapes
            report_text = report_text.replace(cited_url, f"{cited_url} [Unverified]")
            patched_count += 1

    return report_text, patched_count


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