"""Output Guardrail Engine for Deep Research Agent.

Sanitizes secrets/PII, validates URL citations against collected notes,
and enforces structural completeness on synthesized final reports.
"""

import re
import logging
from typing import List, Tuple
from urllib.parse import urlparse  # FIXED: Added missing import

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
    # Credit Card Numbers
    (r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CARD_NUMBER]"),
]


def sanitize_secrets_and_pii(text: str) -> str:
    """Scrubs leaked API credentials, private keys, and sensitive tokens."""
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
    try:
        parsed = urlparse(url.lower().strip())
        domain_and_path = f"{parsed.netloc}{parsed.path}".replace("www.", "").rstrip("/")
        return domain_and_path
    except Exception:
        return url.lower().strip()


def verify_url_grounding(report_text: str, notes: list) -> tuple[str, int]:
    """
    Cross-references cited Markdown URLs [Title](URL) in the report against raw notes.
    If a URL is missing from scraped notes, flags it cleanly OUTSIDE the markdown brackets
    so the link remains valid and clickable.
    """
    raw_notes_str = "\n".join(notes) if isinstance(notes, list) else str(notes)
    
    # Extract all raw URLs from scraped notes and normalize them
    raw_urls = re.findall(r'https?://[^\s\)]+', raw_notes_str)
    normalized_scraped_urls = {normalize_url(u) for u in raw_urls if normalize_url(u)}

    # Regex specifically matching Markdown Links: [Link Title](https://...)
    markdown_link_pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    matches = re.findall(markdown_link_pattern, report_text)
    
    patched_count = 0

    for title, url in matches:
        norm_cited = normalize_url(url)
        
        # Check if the core domain + path exists in raw scraped memory
        if norm_cited and norm_cited not in normalized_scraped_urls:
            original_markdown = f"[{title}]({url})"
            # FIXED: Place [Unverified] OUTSIDE the () so the link syntax stays 100% valid & clickable!
            patched_markdown = f"[{title}]({url}) *(Unverified Source)*"
            
            report_text = report_text.replace(original_markdown, patched_markdown)
            patched_count += 1

    return report_text, patched_count


def validate_report_structure(report_text: str) -> str:
    """Ensures report contains basic structural elements."""
    if not report_text or len(report_text.strip()) < 100:
        return "# Research Report\n\n*Error: Generated report was incomplete or empty.*"

    if not re.search(r"^#+\s+", report_text, re.MULTILINE):
        report_text = f"# Final Research Report\n\n{report_text}"

    return report_text