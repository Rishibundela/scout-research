"""Low-level LangGraph SDK wrapper.

This is the only file that talks to `langgraph_sdk` directly. Everything
above it (research_service, agent_runtime) works with plain Python
primitives so the rest of the app doesn't need to know SDK version details.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, Optional

from langgraph_sdk import get_client

from config import settings


class LangGraphSDKClient:
    """Thin wrapper around the LangGraph SDK client, bound to one thread."""

    def __init__(self, url: str = settings.RENDER_URL, api_key: Optional[str] = settings.API_KEY):
        self.url = url
        self.api_key = api_key
        self.client = self._get_client()

    def _get_client(self):
        """Creates a fresh SDK client bound to the current running event loop."""
        return get_client(url=self.url, api_key=self.api_key)

    async def create_thread(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self.client.threads.create(metadata=metadata or {})

    async def get_thread_state(self, thread_id: str) -> Dict[str, Any]:
        return await self.client.threads.get_state(thread_id)

    async def delete_thread(self, thread_id: str) -> None:
        await self.client.threads.delete(thread_id)

    async def search_threads(self, limit: int = 50, metadata: Optional[Dict[str, Any]] = None) -> list[Dict[str, Any]]:
        return await self.client.threads.search(limit=limit, metadata=metadata)

    async def update_thread(self, thread_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        return await self.client.threads.update(thread_id, metadata=metadata)

    async def stream_run(
        self,
        thread_id: str,
        assistant_id: Optional[str] = None,
        input_data: Optional[Dict[str, Any]] = None,
        command: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Any, None]:
        """Streams one run. Tries to include nested subgraph node updates
        (`stream_subgraphs=True`) so progress from e.g. `supervisor_subgraph`
        shows up too; falls back gracefully on older SDK versions that don't
        accept that kwarg.
        """
        assistant_id = assistant_id or settings.ASSISTANT_ID
        base_kwargs = dict(
            thread_id=thread_id,
            assistant_id=assistant_id,
            input=input_data,
            command=command,
            stream_mode=["updates", "values", "messages"],
        )
        try:
            stream = self.client.runs.stream(**base_kwargs, stream_subgraphs=True)
        except TypeError:
            stream = self.client.runs.stream(**base_kwargs)

        async for chunk in stream:
            yield chunk

    async def cancel_run(self, thread_id: str, run_id: str) -> bool:
        """Best-effort server-side cancellation. Returns False (instead of
        raising) if the installed SDK version doesn't support it or the run
        has already finished, so callers can degrade to local-only stop."""
        try:
            await self.client.runs.cancel(thread_id, run_id)
            return True
        except Exception:
            return False