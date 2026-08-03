from typing import List, Literal
from tavily import AsyncTavilyClient
from agent.src.config import settings
import asyncio

# Initialize AsyncTavilyClient for non-blocking network calls
tavily_client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)

async def tavily_search_multiple(
    search_queries: List[str], 
    max_results: int = 3, 
    topic: Literal["general", "news", "finance"] = "general", 
    include_raw_content: bool = True, 
) -> List[dict]:
    """Perform search using Tavily API for multiple queries.

    Args:
        search_queries: List of search queries to execute
        max_results: Maximum number of results per query
        topic: Topic filter for search results
        include_raw_content: Whether to include raw webpage content

    Returns:
        List of search result dictionaries
    """

    # Execute searches sequentially. Note: you can use AsyncTavilyClient to parallelize this step.
    async def _fetch_single_query(query: str):
        try:
            return await tavily_client.search(
                query,
                max_results=max_results,
                include_raw_content=include_raw_content,
                topic=topic,
            )
        except Exception as e:
            print(f"Error searching query '{query}': {e}")
            return {"results": []}
        
    # Execute all query searches in parallel
    search_docs = await asyncio.gather(
        *(_fetch_single_query(q) for q in search_queries)
    )

    return list(search_docs)  # Ensure the result is a list of dictionaries

def deduplicate_search_results(search_results: List[dict]) -> dict:
    """Deduplicate search results by URL to avoid processing duplicate content.

    Args:
        search_results: List of search result dictionaries

    Returns:
        Dictionary mapping URLs to unique results
    """
    unique_results = {}

    for response in search_results:
        for result in response['results', []]:
            url = result.get('url')
            if url not in unique_results:
                unique_results[url] = result

    return unique_results

