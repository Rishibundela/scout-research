"""Business-logic orchestrator for the Scout research graph.

Wraps `LangGraphSDKClient` and turns its raw chunk stream into a small set
of well-defined callbacks the UI layer reacts to:

    on_node_stage(node_name)             -> a graph node started/updated
    on_token(node_name, token)           -> a text token was produced by node_name
    on_interrupt(payload, node_name)     -> the graph paused, needs human input
    on_complete(final_state)             -> terminal state values were emitted

Two interrupt conventions are supported, since graph authors sometimes use
LangGraph's native `interrupt()` and sometimes a custom state-flag pattern:

  1. Native LangGraph interrupts surface as an "updates" chunk whose data
     dict contains a "__interrupt__" key (a tuple/list of interrupt info,
     each with a `.value` attribute or `["value"]` key).
  2. A convention where a node's output dict carries
     `{"type": "clarification_request", ...}`.

Both are normalized into the same `on_interrupt(payload, node_name)` call,
so the UI never needs to know which convention the graph actually uses.

Nodes named `__error_handler__*` are treated as non-fatal recovery steps:
they're still reported via on_node_stage (so the UI can show "recovering")
but never raise.
"""
from __future__ import annotations

import inspect
import logging
import threading
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from backend.agent_client import LangGraphSDKClient
from backend.config import settings

logger = logging.getLogger(__name__)

CallbackType = Union[Callable[..., None], Callable[..., Awaitable[None]]]

ERROR_HANDLER_PREFIX = "__error_handler__"


class RunCancelled(Exception):
    """Raised when a run is cooperatively cancelled via a cancel_event."""


def _extract_text_content(content: Any) -> str:
    """Recursively converts message content (dict, list, or string) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if "text" in part and isinstance(part["text"], str):
                    parts.append(part["text"])
        return "".join(parts)
    return str(content) if content is not None else ""


class ResearchService:
    """High-level business logic orchestrator for research workflows."""

    def __init__(self, client: Optional[LangGraphSDKClient] = None):
        self.client = client or LangGraphSDKClient()
        self.assistant_id = settings.ASSISTANT_ID
        self.last_run_id: Optional[str] = None

    async def _invoke(self, callback: Optional[CallbackType], *args: Any) -> None:
        if callback is None:
            return
        if inspect.iscoroutinefunction(callback):
            await callback(*args)
        else:
            callback(*args)

    # ---- session management ------------------------------------------------
    async def start_session(self, user_id: str = "default_user") -> str:
        """Initializes a new persistent thread session."""
        thread = await self.client.create_thread(metadata={"user_id": user_id})
        return thread["thread_id"]

    async def close_session(self, thread_id: str) -> None:
        """Purges a research thread server-side."""
        await self.client.delete_thread(thread_id)

    async def get_state(self, thread_id: str) -> Dict[str, Any]:
        return await self.client.get_thread_state(thread_id)

    async def list_sessions(self, user_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        from backend.repository import ThreadRepository
        repo = ThreadRepository(self.client)
        return await repo.list_sessions(user_id, limit=limit)

    async def delete_session(self, thread_id: str) -> None:
        await self.client.delete_thread(thread_id)

    async def update_session_title(self, thread_id: str, title: str) -> None:
        await self.client.update_thread(thread_id, metadata={"title": title})

    async def get_session_history(self, thread_id: str) -> list[Dict[str, Any]]:
        """Fetch and format the message history of an existing thread from Supabase."""
        try:
            state = await self.get_state(thread_id)
            values = state.get("values") or {}
            raw_messages = values.get("messages") or []
            formatted_messages = []
            
            for msg in raw_messages:
                role = "assistant"
                content = ""
                
                if isinstance(msg, dict):
                    role = "user" if msg.get("type") == "human" or msg.get("role") == "user" else "assistant"
                    content = msg.get("content") or ""
                else:
                    role = "user" if msg.__class__.__name__.replace('Message', '').lower() == 'human' else "assistant"
                    content = msg.content or ""
                
                # If content is a list of dictionary blocks, extract the text strings
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                parts.append(item.get("text") or "")
                            elif "text" in item:
                                parts.append(item["text"] or "")
                        elif isinstance(item, str):
                            parts.append(item)
                    content = "".join(parts)
                
                if content:
                    formatted_messages.append({"role": role, "content": content})
                    
            # Remove intermediate draft reports if the finalized report is present
            has_finalized = any("finalized research report" in m["content"].lower() for m in formatted_messages)
            if has_finalized:
                filtered = []
                for m in formatted_messages:
                    if m["role"] == "assistant" and "here is the final report" in m["content"].lower():
                        continue
                    filtered.append(m)
                formatted_messages = filtered
                
            return formatted_messages
        except Exception:
            return []

    async def get_session_report(self, thread_id: str) -> Optional[str]:
        """Retrieve the final report generated in a thread from Supabase, if any."""
        try:
            state = await self.get_state(thread_id)
            values = state.get("values") or {}
            return values.get("final_report")
        except Exception:
            return None

    # ---- public entry points -----------------------------------------------
    async def execute_research(
        self,
        thread_id: str,
        user_query: str,
        **callbacks: Any,
    ) -> None:
        """Starts a brand-new turn from a fresh user query."""
        input_payload = {
            "messages": [
                {"role": "user", "content": user_query}
            ]
        }
        await self._process_stream(thread_id, input_payload, None, **callbacks)

    async def resume_from_clarification(
        self,
        thread_id: str,
        resume_value: Any,
        **callbacks: Any,
    ) -> None:
        """Resumes an interrupted run with the user's clarification answer(s)."""
        await self._process_stream(thread_id, None, {"resume": resume_value}, **callbacks)

    async def resume_from_checkpoint(self, thread_id: str, **callbacks: Any) -> None:
        """Continues an existing thread from its last saved checkpoint.

        Unlike re-sending the original query (which re-enters the graph at
        START), this passes no input/command so LangGraph's own checkpointer
        continues wherever it last persisted state. Use this to recover a
        run that was interrupted by a dropped connection, an app restart,
        or anything other than a deliberate `interrupt()` pause.
        """
        await self._process_stream(thread_id, None, None, **callbacks)

    async def cancel(self, thread_id: str) -> bool:
        """Best-effort server-side cancellation of the most recent run."""
        if not self.last_run_id:
            return False
        return await self.client.cancel_run(thread_id, self.last_run_id)

    # ---- internal ------------------------------------------------------------
    async def _process_stream(
        self,
        thread_id: str,
        input_data: Optional[Dict[str, Any]],
        command: Optional[Dict[str, Any]],
        on_node_stage: Optional[CallbackType] = None,
        on_token: Optional[CallbackType] = None,
        on_interrupt: Optional[CallbackType] = None,
        on_complete: Optional[CallbackType] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        message_metadata = {}
        last_text_lengths = {}
        processed_message_ids = set()
        final_state = None
        try:
            async for chunk in self.client.stream_run(
                thread_id=thread_id,
                assistant_id=self.assistant_id,
                input_data=input_data,
                command=command,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    await self.cancel(thread_id)
                    raise RunCancelled(f"Run on thread {thread_id} was stopped by the user.")

                event = getattr(chunk, "event", None)
                data = getattr(chunk, "data", None)

                if event == "metadata" and isinstance(data, dict):
                    self.last_run_id = data.get("run_id") or self.last_run_id
                    continue

                if event == "updates":
                    await self._handle_updates(data, on_node_stage, on_interrupt)

                elif event == "messages/metadata":
                    if isinstance(data, dict):
                        for msg_id, msg_meta in data.items():
                            if isinstance(msg_meta, dict) and "metadata" in msg_meta:
                                node_name = msg_meta["metadata"].get("langgraph_node")
                                if node_name:
                                    message_metadata[msg_id] = node_name

                elif event == "messages/partial":
                    if isinstance(data, list) and data:
                        msg = data[0]
                        if isinstance(msg, dict):
                            msg_id = msg.get("id")
                            node_name = message_metadata.get(msg_id, "unknown")
                            content = msg.get("content")
                            full_text = _extract_text_content(content)

                            if full_text and msg_id:
                                last_len = last_text_lengths.get(msg_id, 0)
                                if len(full_text) > last_len:
                                    delta = full_text[last_len:]
                                    last_text_lengths[msg_id] = len(full_text)
                                    processed_message_ids.add(msg_id)
                                    if delta and on_token:
                                        await self._invoke(on_token, node_name, delta)

                elif event == "messages":
                    if isinstance(data, (tuple, list)) and data:
                        message = data[0]
                        msg_id = message.get("id") if isinstance(message, dict) else getattr(message, "id", None)
                        if not msg_id or msg_id not in processed_message_ids:
                            node_name = "unknown"
                            if len(data) > 1 and isinstance(data[1], dict):
                                    node_name = data[1].get("langgraph_node", node_name)
                            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
                            if content:
                                text_str = _extract_text_content(content)
                                if text_str and on_token:
                                    await self._invoke(on_token, node_name, text_str)

                elif event == "values":
                    if isinstance(data, dict):
                        final_state = data

            if final_state and on_complete:
                await self._invoke(on_complete, final_state)

        except RunCancelled:
            raise
        except Exception:
            logger.exception("Stream error on thread %s", thread_id)
            raise

    async def _handle_updates(
        self,
        data: Any,
        on_node_stage: Optional[CallbackType],
        on_interrupt: Optional[CallbackType],
    ) -> bool:
        """Returns True if an interrupt was surfaced this chunk."""
        if not data or not isinstance(data, dict):
            return False

        # --- Convention 1: native LangGraph interrupt() ---
        if "__interrupt__" in data:
            raw = data["__interrupt__"]
            items = raw if isinstance(raw, (list, tuple)) else [raw]
            for item in items:
                payload = getattr(item, "value", None)
                if payload is None and isinstance(item, dict):
                    payload = item.get("value", item)
                await self._invoke(on_interrupt, payload, "clarify_with_user")
            return True

        for node_name, node_output in data.items():
            await self._invoke(on_node_stage, node_name)

            # --- Convention 2: custom state-flag interrupt ---
            if isinstance(node_output, dict) and node_output.get("type") == "clarification_request":
                await self._invoke(on_interrupt, node_output, node_name)
                return True
        return False
            