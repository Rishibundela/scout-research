from typing import List, Literal
from tavily import AsyncTavilyClient
from agent.src.config import settings
import asyncio
import logging
import pybreaker

logger = logging.getLogger(__name__)

# Initialize AsyncTavilyClient
tavily_client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)

# Circuit Breaker: Trips if Tavily fails 5 times consecutively. Stays open for 60s.
tavily_circuit_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="TavilySearchBreaker"
)

# ===== 2. HARDENED SEARCH HELPERS =====

async def _fetch_single_query(
    query: str, 
    max_results: int, 
    topic: str, 
    include_raw_content: bool
) -> dict:
    """Fetch search results for a single query with circuit breaker & timeout protection."""
    try:
        # Wrap search in Circuit Breaker call
        async def _call_tavily():
            return await tavily_client.search(
                query,
                max_results=max_results,
                include_raw_content=include_raw_content,
                topic=topic,
            )

        # Enforce hard 10-second timeout on single query fetch
        return await asyncio.wait_for(
            tavily_circuit_breaker.call_async(_call_tavily),
            timeout=10.0
        )

    except pybreaker.CircuitBreakerError:
        logger.error(f"⚡ Circuit Breaker OPEN: Skipping Tavily call for query '{query}'.")
        return {"results": []}
    except asyncio.TimeoutError:
        logger.warning(f"⏳ Tavily search timed out for query '{query}'.")
        return {"results": []}
    except Exception as e:
        logger.error(f"❌ Error searching query '{query}': {e}")
        return {"results": []}

    
async def tavily_search_multiple(
    search_queries: List[str], 
    max_results: int = 3, 
    topic: Literal["general", "news", "finance"] = "general", 
    include_raw_content: bool = True, 
) -> List[dict]:
    """Perform parallel searches using Tavily API across multiple queries."""
    tasks = [
        _fetch_single_query(q, max_results, topic, include_raw_content) 
        for q in search_queries
    ]
    search_docs = await asyncio.gather(*tasks)
    return list(search_docs)

def deduplicate_search_results(search_results: List[dict]) -> dict:
    """Deduplicate search results by URL."""
    unique_results = {}

    for response in search_results:
        # FIXED: Correct dictionary .get() access
        results_list = response.get('results', [])
        for result in results_list:
            url = result.get('url')
            if url and url not in unique_results:
                unique_results[url] = result

    return unique_results