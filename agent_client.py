# agent_client.py snippet
import os
import uuid
from langgraph_sdk import get_client


class ScoutAgentClient:

    def __init__(self, url: str = None):
        self.url = url or os.getenv("LANGGRAPH_URL", "http://localhost:8123")

    def _get_client(self):
        return get_client(url=self.url)

    async def stream_agent_response(
        self, thread_id: str, message: str, graph_name: str
    ):
        client = self._get_client()
        # Ensure thread exists in backend database
        await client.threads.create(thread_id=thread_id, if_exists="do_nothing")

        input_data = {"messages": [{"role": "user", "content": message}]}

        async for event in client.runs.stream(
            thread_id=thread_id,
            assistant_id=graph_name,
            input=input_data,
            stream_mode="values",
        ):
            yield event

    async def delete_thread(self, thread_id: str):
        client = self._get_client()
        try:
            await client.threads.delete(thread_id)
        except Exception as e:
            print(f"Error deleting thread {thread_id} on server: {e}")