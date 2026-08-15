import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.agent_client import LangGraphSDKClient

# Mark all async tests automatically
pytestmark = pytest.mark.asyncio


# =====================================================================
# UNIT TESTS (Isolated with Mocks - No Network/Render Connection)
# =====================================================================

@pytest.fixture
def mock_sdk_client():
    """Mocks the underlying langgraph_sdk.get_client object."""
    mock_client = MagicMock()
    
    # Async methods on threads
    mock_client.threads.create = AsyncMock(return_value={"thread_id": "test_thread_123"})
    mock_client.threads.get_state = AsyncMock(return_value={"values": {"messages": []}})
    mock_client.threads.delete = AsyncMock(return_value=None)
    
    # Mocking generator for stream
    async def mock_stream(*args, **kwargs):
        chunk1 = MagicMock()
        chunk1.event = "updates"
        chunk1.data = {"node": "step_1"}
        yield chunk1

        chunk2 = MagicMock()
        chunk2.event = "values"
        chunk2.data = {"node": "step_2"}
        yield chunk2

    mock_client.runs.stream = mock_stream
    return mock_client


async def test_create_thread_success(mock_sdk_client):
    """Verify thread creation sends correct metadata and returns response."""
    with patch("backend.agent_client.get_client", return_value=mock_sdk_client):
        wrapper = LangGraphSDKClient(url="http://fake-url", api_key="fake-key")
        
        result = await wrapper.create_thread(metadata={"user_id": "unit_tester"})
        
        assert result == {"thread_id": "test_thread_123"}
        mock_sdk_client.threads.create.assert_awaited_once_with(
            metadata={"user_id": "unit_tester"}
        )


async def test_get_thread_state_success(mock_sdk_client):
    """Verify thread state retrieval."""
    with patch("backend.agent_client.get_client", return_value=mock_sdk_client):
        wrapper = LangGraphSDKClient(url="http://fake-url", api_key="fake-key")
        
        state = await wrapper.get_thread_state("test_thread_123")
        
        assert "values" in state
        mock_sdk_client.threads.get_state.assert_awaited_once_with("test_thread_123")


async def test_stream_run_yielding(mock_sdk_client):
    """Verify stream_run yields streamed chunks correctly."""
    with patch("backend.agent_client.get_client", return_value=mock_sdk_client):
        wrapper = LangGraphSDKClient(url="http://fake-url", api_key="fake-key")
        
        chunks = []
        async for chunk in wrapper.stream_run(
            thread_id="test_thread_123",
            input_data={"messages": [{"role": "user", "content": "Hello"}]}
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].event == "updates"
        assert chunks[1].event == "values"


# =====================================================================
# INTEGRATION TEST (Hits Live Render Server - Run conditionally)
# =====================================================================

@pytest.mark.integration
async def test_live_agent_flow():
    """
    Live Integration Test: Run against your actual Render environment.
    Executes a complete thread lifecycle (Create -> Stream -> State -> Delete).
    """
    client = LangGraphSDKClient()

    # 1. Create a Thread
    thread = await client.create_thread(metadata={"test": "integration_run"})
    thread_id = thread["thread_id"]
    assert thread_id is not None
    print(f"\n[LIVE TEST] Created Thread ID: {thread_id}")

    try:
        # 2. Stream a Run
        chunks = []
        async for chunk in client.stream_run(
            thread_id=thread_id,
            input_data={"messages": [{"role": "user", "content": "Hi! Say 'test successful' and nothing else."}]}
        ):
            chunks.append(chunk)

        assert len(chunks) > 0
        print(f"[LIVE TEST] Received {len(chunks)} stream chunks")

        # 3. Check State
        state = await client.get_thread_state(thread_id)
        assert state is not None
        print("[LIVE TEST] Thread state retrieved successfully")

    finally:
        # 4. Cleanup/Delete Thread
        await client.delete_thread(thread_id)
        print(f"[LIVE TEST] Cleaned up Thread ID: {thread_id}")