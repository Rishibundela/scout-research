"""Output Guardrail Engine for Deep Research Agent.

Sanitizes secrets/PII, cleans up raw LaTeX units, validates URL citations 
against collected notes, and enforces structural completeness on final reports.
"""

import re
import logging
from typing import List, Tuple, Set
from urllib.parse import urlparse, urlunparse

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


def sanitize_latex_units(text: str) -> str:
    """
    Cleans raw LaTeX math delimiters, degree symbols, and chemistry formulas
    into plain Markdown for flawless UI rendering.
    """
    if not text:
        return text

    # 1. Strip raw LaTeX text wrappers: $\text{Unit}$ or \text{Unit} -> Unit
    text = re.sub(r'\$\s*\\text\{([^}]+)\}\s*\$', r'\1', text)
    text = re.sub(r'\\text\{([^}]+)\}', r'\1', text)

    # 2. Fix degree Celsius formatting: -60^\circC, -60^\circ C, \circC -> -60°C
    text = re.sub(r'\\circ\s*C?', '°C', text)
    text = re.sub(r'\^\s*°C', '°C', text)

    # 3. Strip stray math enclosure dollars around simple text/ranges:
    # e.g., $dew point < -60°C$ -> dew point < -60°C
    text = re.sub(r'\$([a-zA-Z0-9\s°C<>\-\/_%]+)\$', r'\1', text)

    # 4. Clean up exponent scientific notation: 10^{-3} S/cm -> 10⁻³ S/cm
    superscripts = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '-': '⁻'}
    
    def replace_super(match):
        val = match.group(1)
        return ''.join(superscripts.get(c, c) for c in val)

    text = re.sub(r'10\^\{([^}]+)\}', replace_super, text)

    return text


# =====================================================================
# 1. HERE IS THE CANONICALIZE FUNCTION (Replaces normalize_url)
# =====================================================================
def canonicalize_url(url: str, keep_scheme: bool = False) -> str:
    """
    Strips query parameters (?utm=...), tracking fragments (#...), www., 
    trailing slashes, and stray punctuation from URLs.
    
    - keep_scheme=False -> Returns 'domain.com/path' (Best for lookup comparison)
    - keep_scheme=True  -> Returns 'https://domain.com/path' (Best for clean links)
    """
    if not url:
        return ""
    try:
        # Strip stray closing brackets/quotes captured by broad regexes
        clean_url = url.strip().rstrip(")]>,.'\"")
        parsed = urlparse(clean_url.lower())
        
        clean_netloc = parsed.netloc.replace("www.", "")
        clean_path = parsed.path.rstrip("/")
        
        # For set matching, ignore http vs https
        if not keep_scheme:
            return f"{clean_netloc}{clean_path}"
            
        scheme = parsed.scheme if parsed.scheme in ["http", "https"] else "https"
        return urlunparse((scheme, clean_netloc, clean_path, "", "", ""))
    except Exception:
        return url.lower().strip().rstrip("/")


def verify_url_grounding(report_text: str, notes: list) -> tuple[str, int]:
    """
    Cross-references cited URLs in the report against raw notes memory.
    Handles both Markdown links `[Title](URL)` and list links `[1] Title: URL`.
    Places unverified tags OUTSIDE parentheses so URLs remain 100% clickable.
    """
    raw_notes_str = "\n".join(notes) if isinstance(notes, list) else str(notes)
    
    # Extract raw URLs from notes and canonicalize them into a lookup set
    raw_urls = re.findall(r'https?://[^\s\)\>\]]+', raw_notes_str)
    
    # 📌 LOCATION 1: Canonicalizing scraped notes URLs
    normalized_scraped_urls: Set[str] = {
        canonicalize_url(u, keep_scheme=False) for u in raw_urls if canonicalize_url(u, keep_scheme=False)
    }

    patched_count = 0
    processed_urls: Set[str] = set()

    # 1. Match Markdown Links: [Link Title](https://...)
    markdown_link_pattern = r'\[([^\]]+)\]\((https?://[^\s\)]+)\)'
    markdown_matches = re.findall(markdown_link_pattern, report_text)
    
    for title, url in markdown_matches:
        if url in processed_urls:
            continue
            
        norm_cited = canonicalize_url(url, keep_scheme=False)
        
        if norm_cited and norm_cited not in normalized_scraped_urls:
            original_markdown = f"[{title}]({url})"
            if f"{original_markdown} *(Unverified Source)*" not in report_text:
                patched_markdown = f"[{title}]({url}) *(Unverified Source)*"
                report_text = report_text.replace(original_markdown, patched_markdown)
                patched_count += 1
        processed_urls.add(url)

    # 2. Match Line Item Sources: [1] Title: https://...
    line_item_pattern = r'(\[\d+\]\s+[^:\n]+:\s*)(https?://[^\s]+)'
    line_matches = re.findall(line_item_pattern, report_text)

    for prefix, url in line_matches:
        if url in processed_urls:
            continue

        
        norm_cited = canonicalize_url(url, keep_scheme=False)
        
        if norm_cited and norm_cited not in normalized_scraped_urls:
            original_line = f"{prefix}{url}"
            if "*(Unverified Source)*" not in original_line:
                patched_line = f"{prefix}{url} *(Unverified Source)*"
                report_text = report_text.replace(original_line, patched_line)
                patched_count += 1
        processed_urls.add(url)

    return report_text, patched_count


def validate_report_structure(report_text: str) -> str:
    """Ensures report contains basic structural elements."""
    if not report_text or len(report_text.strip()) < 100:
        return "# Research Report\n\n*Error: Generated report was incomplete or empty.*"

    if not re.search(r"^#+\s+", report_text, re.MULTILINE):
        report_text = f"# Final Research Report\n\n{report_text}"

    return report_text