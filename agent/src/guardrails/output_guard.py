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
    # Credit Card Numbers (restricts to common prefixes and avoids DOI/ISBN path segments)
    (r"(?<![\/\.\d])\b(?:4[0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}|5[1-5][0-9]{2}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}|6011[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}|3[47][0-9]{2}[ -]?[0-9]{6}[ -]?[0-9]{5})\b", "[REDACTED_CARD_NUMBER]"),
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


def heal_latex_delimiters(text: str) -> str:
    """Fixes mismatched LaTeX math delimiters produced by LLMs.

    Uses a sequential scan to find $$ openers and check their closers.
    Common failure modes healed:
    - $$ formula $   (display open, inline close) -> $$ formula $$
    - word$ mid-sentence  (stray orphaned dollar sign removed)
    """
    if not text:
        return text

    # Protect code blocks from modification
    code_blocks = []
    def _save_code(m):
        code_blocks.append(m.group(0))
        return f"__CODE_BLOCK_{len(code_blocks) - 1}__"
    text = re.sub(r"```[\s\S]*?```", _save_code, text)

    # Fix 1: Find $$ ... $ patterns where the closing is a single $
    # Strategy: find all $$ openers, then check if closed by $$ or single $
    result = []
    i = 0
    length = len(text)
    while i < length:
        # Check for $$ (display math opener)
        if i < length - 1 and text[i] == '$' and text[i + 1] == '$':
            # Find the closing delimiter
            j = i + 2
            close_pos = -1
            while j < length:
                if j < length - 1 and text[j] == '$' and text[j + 1] == '$':
                    # Proper $$ close found
                    close_pos = j
                    break
                elif text[j] == '$' and (j + 1 >= length or text[j + 1] != '$'):
                    # Single $ found - check if this looks like a mismatched close
                    # (i.e., the content between $$ and $ contains LaTeX commands)
                    content = text[i + 2:j]
                    if '\\' in content or '\n' in content:
                        # This is likely a mismatched display math close - heal it
                        result.append('$$')
                        result.append(content)
                        result.append('$$')
                        i = j + 1
                        close_pos = -2  # sentinel: already handled
                        break
                j += 1
            if close_pos == -2:
                continue  # already appended
            elif close_pos >= 0:
                # Proper $$ ... $$ block - pass through unchanged
                result.append(text[i:close_pos + 2])
                i = close_pos + 2
            else:
                # No close found at all - pass through as-is
                result.append(text[i:i + 2])
                i += 2
        else:
            result.append(text[i])
            i += 1

    text = ''.join(result)

    # Fix 2: Stray $ attached to word boundaries (e.g., "where n$ is the total")
    # Only strip when the $ is NOT closing a valid inline math span.
    # We check: is there an unmatched opening $ before this position on the same line?
    prose_words = {'is','are','was','were','the','a','an','and','or','of','in','to',
                   'for','that','this','with','from','by','as','at','on','not',
                   'has','have','had','can','will','be','it'}
    def _fix_stray_dollar(m):
        pre_text = text[:m.start()]
        # Count $ signs on the current line before this match
        last_newline = pre_text.rfind('\n')
        line_before = pre_text[last_newline + 1:]
        # Count unescaped $ signs (excluding $$)
        singles = len(re.findall(r'(?<!\$)\$(?!\$)', line_before))
        if singles % 2 == 1:
            # Odd count means there's an open $ waiting to be closed — this $ is the closer
            return m.group(0)  # don't modify
        # Even count means this $ is orphaned — strip it
        return f"{m.group(1)} {m.group(2)}"

    text = re.sub(
        r"(?<!\$)(\w)\$\s+(is|are|was|were|the|a|an|and|or|of|in|to|for|that|this|with|from|by|as|at|on|not|has|have|had|can|will|be|it)\b",
        _fix_stray_dollar,
        text
    )

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", block)

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
        clean_url = url.strip()
        # Clean trailing punctuation characters step by step from the right
        while clean_url:
            last_char = clean_url[-1]
            if last_char not in ")]>,.'\"":
                break
            
            # Special case for trailing parenthesis: only strip if unmatched
            if last_char == ')':
                open_count = clean_url.count('(')
                close_count = clean_url.count(')')
                if open_count >= close_count:
                    # Parentheses are balanced or open count is higher, keep the closing parenthesis
                    break
            
            clean_url = clean_url[:-1]

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
    Unified citation engine that:
    1. Cross-references cited URLs against raw notes memory to verify grounding.
    2. Deduplicates citations pointing to the same canonical URL.
    3. Re-numbers body citations and bibliography sequentially starting from 1 with no gaps.
    4. Ensures consistent *(Unverified Source)* labeling across all citations.
    """
    raw_notes_str = "\n".join(notes) if isinstance(notes, list) else str(notes)
    
    # Extract raw URLs from notes and canonicalize them
    raw_urls = re.findall(r'https?://[^\s\)\>\]]+', raw_notes_str)
    normalized_scraped_urls: Set[str] = {
        canonicalize_url(u, keep_scheme=False) for u in raw_urls if canonicalize_url(u, keep_scheme=False)
    }

    # Find the sources section
    split_match = re.search(r"(\n(?:#+\s+)?(?:Sources|References)\b.*)", report_text, re.IGNORECASE | re.DOTALL)
    if not split_match:
        return report_text, 0
        
    sources_section = split_match.group(1)
    body_text = report_text[:split_match.start()]

    # Parse bibliography line items
    # Typically: [1] Title: URL or [1] Title - URL or [1] URL
    lines = sources_section.split("\n")
    bibliography = []
    
    for line in lines:
        match = re.match(r"^\s*\[(\d+)\]\s+(.+)$", line)
        if match:
            num = int(match.group(1))
            rest = match.group(2).strip()
            
            # Find the URL in the line
            url_match = re.search(r"(https?://[^\s\)\*]+)", rest)
            if url_match:
                url = url_match.group(1)
                # Clean up title by removing the URL and punctuation separators
                title = rest.replace(url, "").strip()
                title = re.sub(r"^:\s*|^-\s*|:\s*$|-\s*$|^\"|\"$", "", title).strip()
                
                # Check if this URL is verified (grounded in the notes)
                norm_url = canonicalize_url(url, keep_scheme=False)
                is_unverified = norm_url not in normalized_scraped_urls
                
                bibliography.append({
                    "original_num": num,
                    "title": title,
                    "url": url,
                    "unverified": is_unverified
                })

    if not bibliography:
        return report_text, 0

    # Deduplicate by canonical URL
    unique_bib = []
    canonical_seen = {}
    original_to_unique_idx = {} # maps original_num -> unique_bib list index
    
    for entry in bibliography:
        canon_url = canonicalize_url(entry["url"], keep_scheme=False)
        if canon_url in canonical_seen:
            # Map duplicate number to the first occurrence
            first_idx = canonical_seen[canon_url]
            original_to_unique_idx[entry["original_num"]] = first_idx
        else:
            new_idx = len(unique_bib)
            canonical_seen[canon_url] = new_idx
            original_to_unique_idx[entry["original_num"]] = new_idx
            unique_bib.append(entry)

    # Map original citations in body to their unique bibliography index placeholder
    def replace_bracket_citation(match):
        orig_num = int(match.group(1))
        if orig_num in original_to_unique_idx:
            idx = original_to_unique_idx[orig_num]
            return f"__CIT__{idx}__"
        return match.group(0)

    # Convert all bracket citations in body
    body_placeholder = re.sub(r"\[(\d+)\]", replace_bracket_citation, body_text)

    # Find unique indices referenced in body
    used_indices = sorted(list({
        int(m) for m in re.findall(r"__CIT__(\d+)__", body_placeholder)
    }))

    # Fallback if none matched
    if not used_indices:
        used_indices = list(range(len(unique_bib)))

    # Re-map the used indices to sequential 1-based reference numbers
    idx_to_new_num = {old_idx: new_num for new_num, old_idx in enumerate(used_indices, 1)}

    # Rewrite bracket citations in body to their new sequential numbers
    def restore_bracket_citation(match):
        idx = int(match.group(1))
        if idx in idx_to_new_num:
            return f"[{idx_to_new_num[idx]}]"
        return ""

    body_final = re.sub(r"__CIT__(\d+)__", restore_bracket_citation, body_placeholder)

    # Clean and sort adjacent brackets, e.g. [2][1] -> [1][2]
    def clean_adjacent_brackets(text):
        pattern = r"((?:\[\d+\])+)"
        def repl(match):
            nums = [int(n) for n in re.findall(r"\d+", match.group(1))]
            unique_sorted_nums = sorted(list(set(nums)))
            return "".join(f"[{n}]" for n in unique_sorted_nums)
        return re.sub(pattern, repl, text)

    body_final = clean_adjacent_brackets(body_final)

    # Also verify any inline markdown links: [Title](URL)
    markdown_link_pattern = r'\[([^\]]+)\]\((https?://[^\s\)]+)\)'
    markdown_matches = re.findall(markdown_link_pattern, body_final)
    for title, url in markdown_matches:
        norm_cited = canonicalize_url(url, keep_scheme=False)
        if norm_cited and norm_cited not in normalized_scraped_urls:
            original_markdown = f"[{title}]({url})"
            if f"{original_markdown} *(Unverified Source)*" not in body_final:
                patched_markdown = f"[{title}]({url}) *(Unverified Source)*"
                body_final = body_final.replace(original_markdown, patched_markdown)

    # Reconstruct ## Sources section
    new_sources_lines = []
    heading_match = re.match(r"^(\s*#+\s+(?:Sources|References)\b.*)", split_match.group(1))
    heading = heading_match.group(1).split("\n")[0] if heading_match else "## Sources"
    new_sources_lines.append(heading)
    
    patched_count = 0
    for old_idx in used_indices:
        new_num = idx_to_new_num[old_idx]
        entry = unique_bib[old_idx]
        
        unverified_suffix = ""
        if entry["unverified"]:
            unverified_suffix = " *(Unverified Source)*"
            patched_count += 1
            
        title_str = f'"{entry["title"]}"' if entry["title"] else f'Source [{new_num}]'
        line_str = f"[{new_num}] {title_str} - {entry['url']}{unverified_suffix}"
        new_sources_lines.append(line_str)

    sources_final = "\n" + "\n".join(new_sources_lines) + "\n"
    return body_final + sources_final, patched_count


def validate_report_structure(report_text: str) -> str:
    """
    Programmatic output guardrail that:
    1. Repairs broken Markdown link spacing (e.g. [Title] (URL)).
    2. Auto-heals Mermaid syntax issues (first-line text intrusions, unquoted subgraphs with spaces/&, special character node labels).
    3. Guarantees top-level header structure.
    """
    if not report_text or len(report_text.strip()) < 100:
        return "# Research Report\n\n*Error: Generated report was incomplete or empty.*"

    # Normalize Windows line endings to Unix format first to ensure regex matches
    report_text = report_text.replace("\r\n", "\n")

    # 1. Clean up Markdown links
    # Fix space between brackets and parentheses: [Link Title] (http://...) -> [Link Title](http://...)
    report_text = re.sub(r'\[([^\]]+)\]\s+\((https?://[^\s\)]+)\)', r'[\1](\2)', report_text)
    # Fix double parentheses: [Link Title]((http://...)) -> [Link Title](http://...)
    report_text = re.sub(r'\[([^\]]+)\]\(\((https?://[^\s\)]+)\)\)', r'[\1](\2)', report_text)

    # 2. Auto-heal Mermaid Code Blocks
    def heal_mermaid_block(match: re.Match) -> str:
        content = match.group(1)
        # Translate experimental architecture-beta to standard flowchart TD
        if "architecture-beta" in content:
            content = content.replace("architecture-beta", "flowchart TD")
            content = re.sub(r'group\s+([a-zA-Z0-9_\-]+)\s*\["([^"]+)"\]', r'subgraph \1["\2"]\nend', content)
            content = re.sub(r'group\s+([a-zA-Z0-9_\-]+)\s*\[([^\]]+)\]', r'subgraph \1["\2"]\nend', content)
            content = re.sub(r'service\s+([a-zA-Z0-9_\-]+)\s*\["([^"]+)"\]', r'\1["\2"]', content)
            content = re.sub(r'service\s+([a-zA-Z0-9_\-]+)\s*\[([^\]]+)\]', r'\1["\2"]', content)
            
        lines = content.split("\n")
        
        # Valid diagram type declarations
        diag_declarations = [
            "graph", "flowchart", "sequenceDiagram", "gantt", 
            "classDiagram", "stateDiagram", "erDiagram", "journey", 
            "pie", "gitGraph", "requirementDiagram"
        ]
        
        first_diag_idx = -1
        for idx, line in enumerate(lines):
            trimmed = line.strip()
            if any(trimmed.startswith(decl) for decl in diag_declarations):
                first_diag_idx = idx
                break
        
        pre_text = ""
        diag_lines = lines
        if first_diag_idx > 0:
            # Found text before diagram declaration inside block! Move it above
            pre_text = "\n".join(lines[:first_diag_idx]).strip() + "\n\n"
            diag_lines = lines[first_diag_idx:]
        elif first_diag_idx == -1:
            # No declaration found. Inject default flowchart TD
            diag_lines = ["flowchart TD"] + lines
            
        cleaned_lines = []
        for line in diag_lines:
            stripped = line.strip()
            if not stripped:
                cleaned_lines.append(line)
                continue

            # Split combined connection and node definitions to ensure compatibility,
            # e.g. source["Source Label"] -- "Edge Text" --> target["Target Label"]
            # becomes:
            # source["Source Label"]
            # target["Target Label"]
            # source -- "Edge Text" --> target
            if "-->" in line or "---" in line:
                # Find all node definitions of the form ID[Label], ID(Label), or ID{Label}
                defs = re.findall(r'([a-zA-Z0-9_\-]+)([(\[\{])([^()\[\]\{\}]+)([)\]\}])', line)
                if defs:
                    indent_match = re.match(r"^(\s*)", line)
                    indent = indent_match.group(1) if indent_match else ""
                    
                    for node_id, open_b, label, close_b in defs:
                        cleaned_lines.append(f"{indent}{node_id}{open_b}{label}{close_b}")
                        original_str = f"{node_id}{open_b}{label}{close_b}"
                        line = line.replace(original_str, node_id)

            # Clean Subgraphs: ensure titles containing spaces or '&' are safely quoted
            subgraph_match = re.match(r"^(\s*subgraph\s+)([^\n]+)$", line)
            if subgraph_match:
                prefix = subgraph_match.group(1)
                body = subgraph_match.group(2).strip()
                
                if body.startswith('"') and body.endswith('"'):
                    pass
                elif '["' in body and body.endswith('"]'):
                    pass
                else:
                    # Parse ID[Label] or ID["Label"] format
                    label_match = re.match(r"^([a-zA-Z0-9_\-]+)\[(.*)\]$", body)
                    if label_match:
                        sub_id = label_match.group(1)
                        label = label_match.group(2).strip().strip('"')
                        body = f'{sub_id}["{label}"]'
                    else:
                        # Raw title: wrap in double quotes if it contains spaces or non-word characters
                        if re.search(r"[^a-zA-Z0-9_\-]", body):
                            clean_body = body.replace('"', "")
                            body = f'"{clean_body}"'
                cleaned_lines.append(f"{prefix}{body}")
                continue
                
            # Clean Node Labels: wrap NodeID[Label] in double quotes to prevent syntax errors
            # Only matches unquoted labels to avoid double-escaping.
            # Example: A[Perception & Planning] -> A["Perception & Planning"]
            line = re.sub(
                r'([a-zA-Z0-9_\-]+)([(\[\{])([^"()\[\]\{\}]+)([)\]\}])',
                r'\1\2"\3"\4',
                line
            )
            cleaned_lines.append(line)
            
        res_content = "\n".join(cleaned_lines)
        return f"{pre_text}```mermaid\n{res_content}\n```"

    report_text = re.sub(r"```mermaid\n([\s\S]*?)```", heal_mermaid_block, report_text)

    # 3. Ensure top-level headers exist
    if not re.search(r"^#+\s+", report_text, re.MULTILINE):
        report_text = f"# Final Research Report\n\n{report_text}"

    return report_text