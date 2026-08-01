from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from agent.src.schemas import Summary
from agent.src.prompts import summarize_webpage_prompt
from agent.src.config import settings
# Import your newly separated utilities cleanly
from agent.src.utils.helper import get_today_str

summarization_model = init_chat_model(model="google_genai:gemini-3.1-flash-lite", temperature=0.0, api_key=settings.GOOGLE_API_KEY)

def summarize_webpage_content(webpage_content: str) -> str:
    """Summarize webpage content using the configured summarization model."""
    try:
        structured_model = summarization_model.with_structured_output(Summary)
        summary = structured_model.invoke([
            HumanMessage(content=summarize_webpage_prompt.format(
                webpage_content=webpage_content, 
                date=get_today_str()
            ))
        ])
        return f"<summary>\n{summary.summary}\n</summary>\n\n<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
    except Exception as e:
        return webpage_content[:1000] + "..." if len(webpage_content) > 1000 else webpage_content

def process_search_results(unique_results: dict) -> dict:
    """Process search results by summarizing content where available."""
    summarized_results = {}
    for url, result in unique_results.items():
        content = summarize_webpage_content(result['raw_content']) if result.get("raw_content") else result['content']
        summarized_results[url] = {'title': result['title'], 'content': content}
    return summarized_results

def format_search_output(summarized_results: dict) -> str:
    """Format search results into a well-structured string output."""
    if not summarized_results:
        return "No valid search results found."
    formatted_output = "Search results: \n\n"
    for i, (url, result) in enumerate(summarized_results.items(), 1):
        formatted_output += f"\n\n--- SOURCE {i}: {result['title']} ---\nURL: {url}\n\nSUMMARY:\n{result['content']}\n\n" + "-" * 80 + "\n"
    return formatted_output
