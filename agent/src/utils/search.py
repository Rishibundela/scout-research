from typing import List, Literal
from tavily import AsyncTavilyClient
from agent.src.config import settings
import asyncio
import logging
import pybreaker

logger = logging.getLogger(__name__)

# Initialize AsyncTavilyClient
tavily_client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)

import threading
from collections import OrderedDict

# LRU Cache dictionary for circuit breakers
class LRUDict(OrderedDict):
    def __init__(self, maxsize=1000):
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)

_breakers_lock = threading.Lock()
_thread_circuit_breakers = LRUDict(maxsize=1000)

def get_tavily_circuit_breaker(thread_id: str) -> pybreaker.CircuitBreaker:
    """Retrieve or create a circuit breaker scoped to the specific thread_id."""
    with _breakers_lock:
        if thread_id not in _thread_circuit_breakers:
            _thread_circuit_breakers[thread_id] = pybreaker.CircuitBreaker(
                fail_max=5,
                reset_timeout=60,
                name=f"TavilySearchBreaker_{thread_id}"
            )
        else:
            # Move key to end to maintain LRU order
            _thread_circuit_breakers.move_to_end(thread_id)
        return _thread_circuit_breakers[thread_id]

# ===== HARDENED SEARCH HELPERS =====

async def _fetch_single_query(
    query: str, 
    max_results: int, 
    topic: str, 
    include_raw_content: bool,
    thread_id: str
) -> dict:
    """Fetch search results for a single query with circuit breaker & timeout protection."""
    
    # Ensure topic matches Tavily API accepted values ('general' or 'news')
    tavily_topic = topic if topic in ["general", "news"] else "general"

    breaker = get_tavily_circuit_breaker(thread_id)

    try:
        # Put wait_for INSIDE the breaker call so timeouts trip the circuit breaker
        async def _call_tavily():
            return await asyncio.wait_for(
                tavily_client.search(
                    query,
                    max_results=max_results,
                    include_raw_content=include_raw_content,
                    topic=tavily_topic,
                ),
                timeout=10.0
            )

        # Wrap search in Circuit Breaker call
        return await breaker.call_async(_call_tavily)

    except pybreaker.CircuitBreakerError:
        logger.error(f"⚡ Circuit Breaker OPEN for thread '{thread_id}': Skipping Tavily call for query '{query}'.")
        return {"results": []}
    except asyncio.TimeoutError:
        logger.warning(f"Tavily search timed out for query '{query}'.")
        return {"results": []}
    except Exception as e:
        logger.error(f"Error searching query '{query}': {e}")
        return {"results": []}

    
async def tavily_search_multiple(
    search_queries: List[str], 
    max_results: int = 3, 
    topic: Literal["general", "news", "finance"] = "general", 
    include_raw_content: bool = True, 
    thread_id: str = "global"
) -> List[dict]:
    """Perform parallel searches using Tavily API across multiple queries."""
    tasks = [
        _fetch_single_query(q, max_results, topic, include_raw_content, thread_id) 
        for q in search_queries
    ]
    search_docs = await asyncio.gather(*tasks)
    return list(search_docs)


def deduplicate_search_results(search_results: List[dict]) -> dict:
    """Deduplicate search results by URL."""
    unique_results = {}

    for response in search_results:
        results_list = response.get('results', [])
        for result in results_list:
            url = result.get('url')
            if url and url not in unique_results:
                unique_results[url] = result

    return unique_results