"""Report rendering & export utilities.

`build_report_html`
    Wraps report Markdown in a small standalone HTML page that renders
    GitHub-flavored Markdown (tables, lists, code), math via KaTeX, and
    Mermaid diagrams — all wired up client-side via CDN <script> tags.

    This is the practical Streamlit-compatible equivalent of a
    remark-math / rehype-katex pipeline: Streamlit has no React build step
    to hook `unified`/`rehype` plugins into, so the same underlying
    libraries (marked.js for Markdown, KaTeX for math, Mermaid.js for
    diagrams) are used directly in the browser instead, inside an
    `st.components.v1.html` iframe. Visually equivalent output, no Node
    build pipeline required.

`export_markdown`
    Trivial passthrough to UTF-8 bytes, for a `.md` download button.

`export_pdf`
    Best-effort Markdown -> PDF using pure-Python libraries (`markdown2` +
    `xhtml2pdf`), so it works without native system dependencies (unlike
    e.g. WeasyPrint, which needs Cairo/Pango). Returns `None` if those
    optional packages aren't installed, so callers can degrade gracefully
    instead of crashing. Math and Mermaid are NOT executed in the PDF
    (xhtml2pdf can't run JS) — LaTeX/mermaid source shows as plain text
    there. Use the in-app viewer for full-fidelity rendering; the PDF is
    meant as a portable text+table copy.
"""
from __future__ import annotations

import html
import io
import json
import logging
import re
import ssl
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# NOTE: this is a raw string so JS regex escapes like \n and \s pass through
# to the browser untouched instead of being interpreted by Python.
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  html, body {
    font-family: -apple-system, "Segoe UI", Inter, sans-serif;
    background: #ffffff; color: #0f172a; margin: 0; padding: 1.5rem 1.75rem;
    line-height: 1.65; font-size: 15.5px;
  }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; display: block; overflow-x: auto; }
  th, td { border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }
  th { background: #f1f5f9; }
  code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 0.9em; }
  pre code { display: block; padding: 10px; overflow-x: auto; }
  blockquote { border-left: 3px solid #93c5fd; margin: 0.5rem 0; padding: 0.25rem 1rem; color: #475569; }
  a { color: #2563eb; }
  img { max-width: 100%; }
  .mermaid { text-align: center; margin: 1rem 0; }
  h1, h2, h3 { letter-spacing: -0.01em; }
  #err-banner {
    display: none; background: #fef2f2; border: 1px solid #fecaca; color: #991b1b;
    padding: 8px 12px; border-radius: 8px; margin-bottom: 12px; font-size: 0.85rem;
  }
</style>
</head>
<body>
<div id="err-banner"></div>
<div id="content"></div>
<script>
  const raw = __RAW_MD_JSON__;
  const container = document.getElementById('content');
  const errBanner = document.getElementById('err-banner');

  function showWarning(msg) {
    errBanner.style.display = 'block';
    errBanner.textContent = msg;
  }

  try {
    const displayMath = [];
    const inlineMath = [];
    const mermaidBlocks = [];

    // Split text into paragraphs to isolate mismatched delimiters
    const paragraphs = raw.split('\n\n');
    const processedParagraphs = paragraphs.map((p) => {
      // Extract display math ($$ ... $$)
      let processed = p.replace(/\$\$\s*([\s\S]*?)\s*\$\$/g, (m, code) => {
        const idx = displayMath.push(code) - 1;
        return '<div class="display-math-placeholder" data-idx="' + idx + '"></div>';
      });

      // Extract inline math ($ ... $) with max 150 chars limit and no outer spaces
      processed = processed.replace(/\$([^\s\$](?:[^\$]{0,150}?[^\s\$])?)\$/g, (m, code) => {
        const idx = inlineMath.push(code) - 1;
        return '<span class="inline-math-placeholder" data-idx="' + idx + '"></span>';
      });

      // Extract mermaid blocks
      processed = processed.replace(/```mermaid\n([\s\S]*?)```/g, (m, code) => {
        const idx = mermaidBlocks.push(code) - 1;
        return '<div class="mermaid-placeholder" data-idx="' + idx + '"></div>';
      });

      return processed;
    });

    const processedRaw = processedParagraphs.join('\n\n');

    // Parse Markdown
    marked.setOptions({ gfm: true, breaks: false });
    container.innerHTML = marked.parse(processedRaw);

    // Put mermaid blocks back
    container.querySelectorAll('.mermaid-placeholder').forEach((el) => {
      const idx = el.getAttribute('data-idx');
      const div = document.createElement('div');
      div.className = 'mermaid';
      div.textContent = mermaidBlocks[idx];
      el.replaceWith(div);
    });

    if (window.mermaid) {
      mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });
      mermaid.run({ querySelector: '.mermaid' }).catch(() => showWarning(
        'One or more Mermaid diagrams could not be rendered and are shown as raw text instead.'
      ));
    }

    // Render display math
    container.querySelectorAll('.display-math-placeholder').forEach((el) => {
      const idx = el.getAttribute('data-idx');
      const div = document.createElement('div');
      try {
        katex.render(displayMath[idx], div, { displayMode: true, throwOnError: false });
      } catch (err) {
        div.textContent = '$$' + displayMath[idx] + '$$';
      }
      el.replaceWith(div);
    });

    // Render inline math
    container.querySelectorAll('.inline-math-placeholder').forEach((el) => {
      const idx = el.getAttribute('data-idx');
      const span = document.createElement('span');
      try {
        katex.render(inlineMath[idx], span, { displayMode: false, throwOnError: false });
      } catch (err) {
        span.textContent = '$' + inlineMath[idx] + '$';
      }
      el.replaceWith(span);
    });
  } catch (e) {
    showWarning('This report could not be fully rendered (' + e.message + '). Showing raw text below.');
    container.textContent = raw;
  }
</script>
</body>
</html>
"""


def build_report_html(markdown_text: str, height: int = 720) -> str:
    """Returns a standalone HTML document rendering `markdown_text` with
    GFM tables, KaTeX math (`$...$` / `$$...$$`), and ```mermaid diagrams.
    """
    safe = markdown_text if isinstance(markdown_text, str) and markdown_text.strip() else "*No content.*"
    return _HTML_TEMPLATE.replace("__RAW_MD_JSON__", json.dumps(safe))


def export_markdown(markdown_text: str) -> bytes:
    return (markdown_text or "").encode("utf-8")


def get_mermaid_png_data_uri(mermaid_code: str) -> Optional[str]:
    """Downloads a static PNG from mermaid.ink and returns it as an inline Base64 data URI.
    Uses standard library urllib with bypassed SSL verification to prevent Windows certificate issues
    and a custom User-Agent header to bypass scraper blocker filters.
    """
    import base64
    b64_str = base64.b64encode(mermaid_code.strip().encode("utf-8")).decode("utf-8")
    img_url = f"https://mermaid.ink/img/{b64_str}"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(
            img_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )
        
        with urllib.request.urlopen(req, context=ctx, timeout=15.0) as response:
            if response.status == 200:
                img_b64 = base64.b64encode(response.read()).decode("utf-8")
                return f"data:image/png;base64,{img_b64}"
    except Exception as e:
        logger.warning(f"Failed to fetch static Mermaid image from mermaid.ink: {e}")
    return None


def get_math_png_data_uri(latex_code: str) -> Optional[str]:
    """Downloads a static PNG render of a LaTeX equation from CodeCogs.
    Uses standard library urllib with bypassed SSL verification to prevent Windows certificate issues
    and a custom User-Agent header.
    """
    import base64
    # URL encode the latex formula
    encoded_latex = urllib.parse.quote(latex_code.strip())
    img_url = f"https://latex.codecogs.com/png.image?\\dpi{{150}}\\bg{{white}}{encoded_latex}"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(
            img_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )
        
        with urllib.request.urlopen(req, context=ctx, timeout=12.0) as response:
            if response.status == 200:
                img_b64 = base64.b64encode(response.read()).decode("utf-8")
                return f"data:image/png;base64,{img_b64}"
    except Exception as e:
        logger.warning(f"Failed to fetch static LaTeX image from CodeCogs: {e}")
    return None


def export_pdf(markdown_text: str, title: str = "Research Report") -> Optional[bytes]:
    """Best-effort Markdown -> PDF. Returns None (never raises) if the
    optional PDF dependencies aren't installed, so callers can show a
    friendly hint instead of crashing the app."""
    try:
        import markdown2
        from xhtml2pdf import pisa
    except ImportError:
        logger.warning("markdown2/xhtml2pdf not installed; PDF export unavailable.")
        return None

    try:
        # Replace mermaid blocks with static PNG base64 Data URIs
        def _mermaid_to_image_tag(match):
            code = match.group(1).strip()
            data_uri = get_mermaid_png_data_uri(code)
            if data_uri:
                return f'<p style="text-align: center;"><img src="{data_uri}" style="max-height: 400px;"/></p>'
            return f'<pre><code>{html.escape(code)}</code></pre>'

        # Replace display math ($$ ... $$) with centered static PNG images
        def _display_math_to_image(match):
            formula = match.group(1).strip()
            formula_clean = formula.replace(r"\_", "_")  # Unescape markdown chars for LaTeX compiler
            data_uri = get_math_png_data_uri(formula_clean)
            if data_uri:
                return f'<p style="text-align: center; margin: 15px 0;"><img src="{data_uri}" style="max-height: 120px;"/></p>'
            return f'<p style="text-align: center; font-family: monospace;">$${formula}$$</p>'

        # Replace inline math ($ ... $) with middle-aligned inline PNG images
        def _inline_math_to_image(match):
            formula = match.group(1).strip()
            formula_clean = formula.replace(r"\_", "_")
            data_uri = get_math_png_data_uri(formula_clean)
            if data_uri:
                return f'<img src="{data_uri}" style="vertical-align: middle; height: 13px; margin: 0 2px;"/>'
            return f'${formula}$'

        # Isolate replacements paragraph-by-paragraph to prevent delimiter leaks
        paragraphs = (markdown_text or "").split("\n\n")
        processed_paragraphs = []
        
        for p in paragraphs:
            # 1. Replace display math ($$ ... $$)
            p = re.sub(
                r"\$\$\s*([\s\S]*?)\s*\$\$",
                _display_math_to_image,
                p
            )
            # 2. Replace inline math ($ ... $)
            p = re.sub(
                r"\$([^\s\$](?:[^\$]{0,150}?[^\s\$])?)\$",
                _inline_math_to_image,
                p
            )
            # 3. Replace mermaid blocks
            p = re.sub(
                r"```mermaid\n([\s\S]*?)```",
                _mermaid_to_image_tag,
                p
            )
            processed_paragraphs.append(p)
            
        clean_text = "\n\n".join(processed_paragraphs)

        body_html = markdown2.markdown(
            clean_text,
            extras=["tables", "fenced-code-blocks", "strike", "task_list", "code-friendly"],
        )
        doc = f"""<html><head><meta charset="utf-8"/><style>
            body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11pt; }}
            h1, h2, h3 {{ color: #0f172a; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #cbd5e1; padding: 4px 8px; }}
            code {{ background: #f1f5f9; }}
        </style></head><body><h1>{html.escape(title)}</h1>{body_html}</body></html>"""

        buffer = io.BytesIO()
        result = pisa.CreatePDF(src=doc, dest=buffer)
        if result.err:
            logger.error("xhtml2pdf reported %s error(s) while rendering PDF", result.err)
        return buffer.getvalue()
    except Exception:
        logger.exception("PDF export failed")
        return None