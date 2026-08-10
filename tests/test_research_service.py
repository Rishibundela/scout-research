import pytest
from unittest.mock import AsyncMock, MagicMock
from research_service import ResearchService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.create_thread = AsyncMock(return_value={"thread_id": "test_thread_999"})
    client.get_thread_state = AsyncMock(return_value={"values": {"query": "Quantum Computing"}})
    client.delete_thread = AsyncMock(return_value=None)
    return client


async def test_start_and_close_session(mock_client):
    service = ResearchService(client=mock_client)
    
    thread_id = await service.start_session("user_123")
    assert thread_id == "test_thread_999"
    mock_client.create_thread.assert_awaited_once_with(metadata={"user_id": "user_123"})

    await service.close_session(thread_id)
    mock_client.delete_thread.assert_awaited_once_with("test_thread_999")


async def test_execute_research_callbacks(mock_client):
    # Mocking stream chunks for node updates, tokens, and final values
    async def mock_stream(*args, **kwargs):
        # 1. Update event
        chunk1 = MagicMock()
        chunk1.event = "updates"
        chunk1.data = {"search_node": {"status": "searching"}}
        yield chunk1

        # 2. Messages event
        chunk2 = MagicMock()
        chunk2.event = "messages"
        chunk2.data = ({"content": "Quantum computing is..."}, {})
        yield chunk2

        # 3. Final values event
        chunk3 = MagicMock()
        chunk3.event = "values"
        chunk3.data = {"final_report": "Complete report on quantum."}
        yield chunk3

    mock_client.stream_run = mock_stream
    service = ResearchService(client=mock_client)

    nodes_visited = []
    tokens_received = []
    final_output = []

    async def async_on_node(node):
        nodes_visited.append(node)

    def sync_on_token(node, token):
        tokens_received.append(token)

    def sync_on_complete(data):
        final_output.append(data)

    await service.execute_research(
        thread_id="test_thread_999",
        user_query="Quantum Computing",
        on_node_stage=async_on_node,
        on_token=sync_on_token,
        on_complete=sync_on_complete,
    )

    assert nodes_visited == ["search_node"]
    assert tokens_received == ["Quantum computing is..."]
    assert final_output[0]["final_report"] == "Complete report on quantum."


async def test_interrupt_handling(mock_client):
    async def mock_stream(*args, **kwargs):
        chunk = MagicMock()
        chunk.event = "updates"
        chunk.data = {
            "clarify_node": {
                "type": "clarification_request",
                "question": "Which topic specifically?"
            }
        }
        yield chunk

    mock_client.stream_run = mock_stream
    service = ResearchService(client=mock_client)

    interrupt_payload = []
    def on_interrupt(data, node):
        interrupt_payload.append(data)

    await service.execute_research(
        thread_id="test_thread_999",
        user_query="Vague query",
        on_interrupt=on_interrupt
    )

    assert len(interrupt_payload) == 1
    assert interrupt_payload[0]["question"] == "Which topic specifically?"


@pytest.mark.integration
async def test_live_research_service_flow():
    """Hits live Render deployment via ResearchService."""
    service = ResearchService()
    
    thread_id = await service.start_session("integration_user")
    print(f"\n[LIVE TEST] Started Session: {thread_id}")

    try:
        nodes = []
        tokens = []

        await service.execute_research(
            thread_id=thread_id,
            user_query="Briefly summarize AI agents in 2 sentences.",
            on_node_stage=lambda node: nodes.append(node),
            on_token=lambda node, t: tokens.append(t),
        )

        print(f"[LIVE TEST] Stages hit: {nodes}")
        print(f"[LIVE TEST] Total tokens/chunks received: {len(tokens)}")
        assert len(nodes) > 0 or len(tokens) > 0

    finally:
        await service.close_session(thread_id)
        print(f"[LIVE TEST] Closed Session: {thread_id}")