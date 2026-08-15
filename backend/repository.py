from typing import List, Dict, Any, Optional
from backend.agent_client import LangGraphSDKClient

class ThreadRepository:
    """Repository pattern wrapping the LangGraph SDK client for persistence operations."""
    
    def __init__(self, client: Optional[LangGraphSDKClient] = None):
        self.client = client or LangGraphSDKClient()
        
    async def create_session(self, user_id: str, title: Optional[str] = None) -> str:
        """Create a new chat thread session with user metadata."""
        metadata = {"user_id": user_id}
        if title:
            metadata["title"] = title
        thread = await self.client.create_thread(metadata=metadata)
        return thread["thread_id"]
        
    async def list_sessions(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """List threads filtered by user metadata."""
        threads = await self.client.search_threads(limit=limit, metadata={"user_id": user_id})
        sessions = []
        for t in threads:
            meta = t.get("metadata") or {}
            # Ignore connection check mock threads
            if meta.get("user_id") == "__connection_test__":
                continue
            sessions.append({
                "thread_id": t["thread_id"],
                "title": meta.get("title") or f"Chat {t['thread_id'][:8]}",
                "user_id": meta.get("user_id"),
                "created_at": t.get("created_at")
            })
        sessions.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return sessions
    async def delete_session(self, thread_id: str) -> None:
        """Delete a persistent thread from the database."""
        await self.client.delete_thread(thread_id)

    async def update_session_title(self, thread_id: str, title: str) -> None:
        """Update a thread's metadata title."""
        await self.client.update_thread(thread_id, metadata={"title": title})
        
    async def get_session_state(self, thread_id: str) -> Dict[str, Any]:
        """Fetch the current full state of the thread session."""
        return await self.client.get_thread_state(thread_id)
