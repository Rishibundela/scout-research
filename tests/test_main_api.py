import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Patch get_client at import time to prevent any real network requests
mock_sdk_client = MagicMock()
mock_sdk_client.threads.create = AsyncMock(return_value={"thread_id": "test_thread_123"})
mock_sdk_client.threads.get_state = AsyncMock(return_value={"values": {"status": "mocked"}})
mock_sdk_client.threads.delete = AsyncMock(return_value=None)
mock_sdk_client.threads.search = AsyncMock(return_value=[{"thread_id": "test_thread_123", "metadata": {"title": "Mocked Title", "user_id": "user_123"}}])
mock_sdk_client.threads.update = AsyncMock(return_value=None)
mock_sdk_client.runs.cancel = AsyncMock(return_value=True)

with patch("backend.agent_client.get_client", return_value=mock_sdk_client):
    from backend.main import app, research_service

from fastapi.testclient import TestClient

client = TestClient(app)

# Now we mock the research_service methods directly on the global instance
research_service.start_session = AsyncMock(return_value="test_thread_123")
research_service.update_session_title = AsyncMock(return_value=None)
research_service.delete_session = AsyncMock(return_value=None)
research_service.get_state = AsyncMock(return_value={"values": {"status": "mocked"}})
research_service.list_sessions = AsyncMock(return_value=[{"thread_id": "test_thread_123", "title": "Mocked Title"}])
research_service.get_session_history = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
research_service.get_session_report = AsyncMock(return_value="Mocked report markdown")
research_service.cancel = AsyncMock(return_value=True)


def test_create_session():
    research_service.start_session.reset_mock()
    research_service.update_session_title.reset_mock()
    
    response = client.post("/api/research/session", json={"user_id": "user_123", "title": "New Thread"})
    assert response.status_code == 200
    assert response.json() == {"thread_id": "test_thread_123"}
    research_service.start_session.assert_awaited_once_with(user_id="user_123")
    research_service.update_session_title.assert_awaited_once_with("test_thread_123", "New Thread")


def test_list_sessions():
    research_service.list_sessions.reset_mock()
    response = client.get("/api/research/sessions?user_id=user_123&limit=10")
    assert response.status_code == 200
    assert "sessions" in response.json()
    assert response.json()["sessions"] == [{"thread_id": "test_thread_123", "title": "Mocked Title"}]
    research_service.list_sessions.assert_awaited_once_with(user_id="user_123", limit=10)


def test_delete_session():
    research_service.delete_session.reset_mock()
    response = client.delete("/api/research/test_thread_123")
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    research_service.delete_session.assert_awaited_once_with("test_thread_123")


def test_update_session_title():
    research_service.update_session_title.reset_mock()
    response = client.patch("/api/research/test_thread_123/title", json={"title": "Updated Title"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    research_service.update_session_title.assert_awaited_with("test_thread_123", "Updated Title")


def test_get_session_state():
    research_service.get_state.reset_mock()
    response = client.get("/api/research/test_thread_123/state")
    assert response.status_code == 200
    assert response.json() == {"values": {"status": "mocked"}}
    research_service.get_state.assert_awaited_once_with("test_thread_123")


def test_get_session_history():
    research_service.get_session_history.reset_mock()
    response = client.get("/api/research/test_thread_123/history")
    assert response.status_code == 200
    assert response.json() == {"history": [{"role": "user", "content": "hello"}]}
    research_service.get_session_history.assert_awaited_once_with("test_thread_123")


def test_get_session_report():
    research_service.get_session_report.reset_mock()
    response = client.get("/api/research/test_thread_123/report")
    assert response.status_code == 200
    assert response.json() == {"final_report": "Mocked report markdown"}
    research_service.get_session_report.assert_awaited_once_with("test_thread_123")


def test_cancel_run():
    research_service.cancel.reset_mock()
    response = client.post("/api/research/test_thread_123/cancel")
    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    research_service.cancel.assert_awaited_once_with("test_thread_123")


def test_start_research_stream():
    async def mock_execute(thread_id, user_query, on_node_stage, on_token, on_interrupt, on_complete):
        await on_node_stage("search_node")
        await on_token("search_node", "token1")
        await on_interrupt({"message": "clarify"}, "clarification_node")
        await on_complete({"final_report": "Done"})

    research_service.execute_research = mock_execute

    response = client.post("/api/research/stream", json={"user_id": "user_123", "query": "hello"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    lines = [line if isinstance(line, str) else line.decode('utf-8') for line in response.iter_lines() if line]
    
    assert "event: session" in lines
    assert "event: stage" in lines
    assert 'data: {"node": "search_node"}' in lines
    assert "event: token" in lines
    assert 'data: {"node": "search_node", "text": "token1"}' in lines
    assert "event: interrupt" in lines
    assert 'data: {"node": "clarification_node", "data": {"message": "clarify"}}' in lines
    assert "event: complete" in lines
    assert 'data: {"final_report": "Done"}' in lines
    assert "event: done" in lines


def test_resume_research_stream():
    async def mock_resume(thread_id, resume_value, on_node_stage, on_token, on_interrupt, on_complete):
        await on_node_stage("resume_node")
        await on_token("resume_node", "token2")
        await on_complete({"final_report": "Resumed Done"})

    research_service.resume_from_clarification = mock_resume

    response = client.post("/api/research/test_thread_123/resume", json={"answers": {"response": "yes"}})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    lines = [line if isinstance(line, str) else line.decode('utf-8') for line in response.iter_lines() if line]
    assert "event: session" in lines
    assert "event: stage" in lines
    assert 'data: {"node": "resume_node"}' in lines
    assert "event: token" in lines
    assert 'data: {"node": "resume_node", "text": "token2"}' in lines
    assert "event: complete" in lines
    assert 'data: {"final_report": "Resumed Done"}' in lines
    assert "event: done" in lines


def test_resume_checkpoint_stream():
    async def mock_resume_checkpoint(thread_id, on_node_stage, on_token, on_interrupt, on_complete):
        await on_node_stage("checkpoint_node")
        await on_token("checkpoint_node", "token3")
        await on_complete({"final_report": "Checkpoint Done"})

    research_service.resume_from_checkpoint = mock_resume_checkpoint

    response = client.post("/api/research/test_thread_123/resume-checkpoint")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    lines = [line if isinstance(line, str) else line.decode('utf-8') for line in response.iter_lines() if line]
    assert "event: session" in lines
    assert "event: stage" in lines
    assert 'data: {"node": "checkpoint_node"}' in lines
    assert "event: token" in lines
    assert 'data: {"node": "checkpoint_node", "text": "token3"}' in lines
    assert "event: complete" in lines
    assert 'data: {"final_report": "Checkpoint Done"}' in lines
    assert "event: done" in lines
