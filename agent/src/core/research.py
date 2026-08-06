import asyncio
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from agent.src.schemas import Summary
from agent.src.prompts import summarize_webpage_prompt
from agent.src.config import settings
# Import your newly separated utilities cleanly
from agent.src.utils.helper import get_today_str
import logging

logger = logging.getLogger(__name__)

# Summarization Model with Retry & Fallback
primary_sum_model = init_chat_model(
    model="google_genai:gemini-3.1-flash-lite",
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)
backup_sum_model = init_chat_model(
    model="google_genai:gemini-3.5-flash-lite",
    temperature=0.0,
    api_key=settings.GOOGLE_API_KEY,
)

reliable_summarizer = (
    primary_sum_model
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
    .with_fallbacks([backup_sum_model.with_retry(stop_after_attempt=2)])
)

async def summarize_webpage_content(webpage_content: str) -> str:
    """
    Summarize webpage content using reliable model chain with XML prompt isolation.
    """
    try:
        # TIER 2 DEFENSE: Wrap scraped content in <source_data> XML tags
        isolated_content = f"<source_data>\n{webpage_content}\n</source_data>"
        
        structured_model = reliable_summarizer.with_structured_output(Summary)
        summary: Summary = await structured_model.ainvoke([
            HumanMessage(content=summarize_webpage_prompt.format(
                webpage_content=isolated_content, 
                date=get_today_str()
            ))
        ])
        return f"<summary>\n{summary.summary}\n</summary>\n\n<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
    except Exception as e:
        logger.warning(f"⚠️ Summarizer failed, using raw excerpt fallback: {e}")
        safe_excerpt = webpage_content[:1000] if webpage_content else "No content retrieved."
        return f"<summary>\n{safe_excerpt}...\n</summary>"

async def process_search_results(unique_results: dict) -> dict:
    """Process search results by summarizing content concurrently."""
    urls = list(unique_results.keys())
    tasks = []

    for url in urls:
        result = unique_results[url]
        if result.get("raw_content"):
            tasks.append(summarize_webpage_content(result["raw_content"]))
        else:
            # Wrap pre-summarized/short content in an immediate async result
            async def _instant_content(c=result.get("content", "No content available.")):
                return c
            tasks.append(_instant_content())
    summaries = await asyncio.gather(*tasks)

    summarized_results = {}
    for url, summary_text in zip(urls, summaries):
        summarized_results[url] = {
            "title": unique_results[url].get("title", "No Title"),
            "content": summary_text
        }
    return summarized_results

async def format_search_output(summarized_results: dict) -> str:
    """Format search results into a well-structured string output."""
    if not summarized_results:
        return "No valid search results found."
    formatted_output = "Search results: \n\n"
    for i, (url, result) in enumerate(summarized_results.items(), 1):
        formatted_output += (
            f"--- SOURCE {i}: {result['title']} ---\n"
            f"URL: {url}\n\n"
            f"SUMMARY:\n{result['content']}\n\n"
            + "-" * 80
            + "\n\n"
        )
    return formatted_output
