import os
from typing import AsyncGenerator, Dict, Any
from langgraph_sdk import get_client

LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://localhost:8123")


class ScoutAgentClient:

    def __init__(self, url: str = LANGGRAPH_URL):
        self.url = url

    def _get_client(self):
        # Create client dynamically so httpx binds to the currently active loop
        return get_client(url=self.url)

    async def create_thread(self) -> str:
        client = self._get_client()
        thread = await client.threads.create()
        return thread["thread_id"]

    async def get_thread_state(self, thread_id: str) -> Dict[str, Any]:
        client = self._get_client()
        return await client.threads.get_state(thread_id)

    async def stream_agent_response(
        self,
        thread_id: str,
        message: str,
        graph_name: str = "deep_research_agent",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        client = self._get_client()
        input_data = {"messages": [{"role": "user", "content": message}]}

        async for event in client.runs.stream(
            thread_id=thread_id,
            assistant_id=graph_name,
            input=input_data,
            stream_mode="updates",
        ):
            yield event