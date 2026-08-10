import pytest
import pybreaker
from agent.src.guardrails.output_guard import canonicalize_url
from agent.src.utils.search import get_tavily_circuit_breaker
from agent.src.utils.compaction import estimate_token_count

def test_canonicalize_url_parentheses():
    """Verify that URL canonicalization preserves balanced parentheses but strips unmatched ones."""
    # Balanced parentheses (e.g. Wikipedia) should be preserved
    url_wiki = "https://en.wikipedia.org/wiki/Page_(disambiguation)"
    assert canonicalize_url(url_wiki, keep_scheme=True) == "https://en.wikipedia.org/wiki/page_(disambiguation)"
    assert canonicalize_url(url_wiki, keep_scheme=False) == "en.wikipedia.org/wiki/page_(disambiguation)"

    # Trailing periods outside parentheses should be stripped, keeping balanced paren
    url_wiki_dot = "https://en.wikipedia.org/wiki/Page_(disambiguation)."
    assert canonicalize_url(url_wiki_dot, keep_scheme=True) == "https://en.wikipedia.org/wiki/page_(disambiguation)"

    # Unmatched closing parenthesis should be stripped
    url_unmatched = "https://example.com/page)"
    assert canonicalize_url(url_unmatched, keep_scheme=True) == "https://example.com/page"
    assert canonicalize_url(url_unmatched, keep_scheme=False) == "example.com/page"

    # Multiple trailing unmatched punctuation elements
    url_complex = "https://example.com/path).\""
    assert canonicalize_url(url_complex, keep_scheme=True) == "https://example.com/path"


def test_multi_tenant_circuit_breaker():
    """Verify that circuit breakers are isolated per thread_id, preventing global tenant lockouts."""
    breaker_t1 = get_tavily_circuit_breaker("thread_1")
    breaker_t2 = get_tavily_circuit_breaker("thread_2")

    assert breaker_t1 is not breaker_t2
    assert breaker_t1.current_state == "closed"
    assert breaker_t2.current_state == "closed"

    # Trip breaker for thread_1 by registering 5 consecutive failures
    def failing_func():
        raise Exception("Tavily error mock")

    for _ in range(5):
        try:
            breaker_t1.call(failing_func)
        except Exception:
            pass

    assert breaker_t1.current_state == "open"
    # thread_2's circuit breaker MUST remain closed and healthy
    assert breaker_t2.current_state == "closed"


def test_token_estimation_heuristics():
    """Verify that the token estimator behaves accurately on prose, JSON, and code."""
    # 1. Plain English prose
    prose = ["This is a standard sentence of research results."]
    # Prose token size should be approx character length divided by 4
    prose_len_est = sum(len(x) for x in prose) // 4
    prose_heur_est = estimate_token_count(prose)
    assert abs(prose_heur_est - prose_len_est) < 5

    # 2. JSON data (where total_chars // 4 significantly underestimates token count)
    json_data = ['{"key": "value", "count": 100, "status": "active"}']
    # len is 50 -> total_chars // 4 is 12
    # Our new heuristic counts symbols like {}, "", :, , and words
    json_heur_est = estimate_token_count(json_data)
    # The actual token count is closer to 19-20 tokens
    assert json_heur_est > 15
    assert json_heur_est < 25

    # 3. Python code block
    code_block = ["def foo(x):\n    return x + 1\n"]
    # len is 29 -> total_chars // 4 is 7
    # Our heuristic counts def (1), foo (1), x (1), return (1), x (1), 1 (1) -> 6 words
    # and symbols: ( ) : + -> 4 symbols
    # and newlines -> 2
    # total -> 12 tokens
    code_heur_est = estimate_token_count(code_block)
    assert code_heur_est >= 10
    assert code_heur_est <= 15
