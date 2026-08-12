"""Background execution runtime for streaming agent turns.

Streamlit's script model is single-threaded per rerun, so a plain
`asyncio.run(...)` call would block the *entire* UI — sidebar, Stop button,
everything — for as long as a research run takes (sometimes minutes). To
keep the UI responsive, stoppable mid-run, and resumable across page
reloads, each turn runs in its own background thread with its own asyncio
event loop. The main Streamlit thread never awaits the agent directly; it
polls a small, lock-protected `RunHandle` snapshot on a short timer (via
`st.fragment(run_every=...)` in app.py) and renders whatever has
accumulated so far.

Bubble grouping
----------------
Tokens are attributed to the node that produced them. Nodes are bucketed
into "bubble groups" so the chat view gets one message per logical turn of
the conversation rather than one per graph node:

    clarify_with_user      -> "clarify"        (its own chat bubble)
    general_assistant      -> "general"        (its own chat bubble)
    final_report_generation-> "draft_report"   (its own chat bubble; the
                                                 guardrail-finalized report
                                                 later overwrites this bubble's
                                                 text before it's finalized)
    everything else         -> "progress"      (shown only in the trace log,
    (write_research_brief,                      never as a chat bubble)
     supervisor_subgraph and
     its nested nodes, output_guardrail,
     __error_handler__* nodes)

Whenever the active bubble group changes, the previous bubble is finalized
(appended to history) before a new one opens — this is what guarantees an
earlier message (e.g. the clarifying question) is never overwritten by a
later one (e.g. the final report).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
import re
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from research_service import ResearchService, RunCancelled

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Node -> bubble group mapping (specific to the Scout research graph)
# --------------------------------------------------------------------------
_GROUP_KEYWORDS = {
    "clarify": "clarify",
    "general_assistant": "general",
    "final_report_generation": "draft_report",
    "report": "draft_report",
    "draft": "draft_report",
    "writer": "draft_report",
    "output_guardrail": "draft_report",
}
BUBBLE_GROUPS = {"clarify", "general", "draft_report"}


EXCLUDED_NODES = {"input_guardrail", "validate_input", "intent_classifier", "guardrail"}


def node_group(node_name: str) -> str | None:
    if not node_name or node_name in EXCLUDED_NODES:
        return None
    
    key = node_name.lower()
    if key.startswith("__error_handler__"):
        return "progress"  # recovery nodes are trace-only, never their own bubble
    for token, group in _GROUP_KEYWORDS.items():
        if token in key:
            return group
    return "progress"


def _clean_clarify_token(raw_text: str) -> str:
    """Extract clean conversational text from structured JSON outputs."""
    if not raw_text or not raw_text.strip():
        return ""

    # Search for "verification" or "question" field values
    matches = list(re.finditer(r'"(?:verification|question)"\s*:\s*"([^"]*)', raw_text))
    if matches:
        extracted = matches[-1].group(1)
        # Unescape double quotes for beautiful rendering in UI
        return extracted.replace('\\"', '"')

    # Fallback for plain conversational text
    if not raw_text.strip().startswith("{"):
        return raw_text

    return ""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Segment:
    """One contiguous chat bubble, built from tokens of the same group."""
    group: str
    text: str = ""
    is_report: bool = False
    finalized: bool = False


@dataclass
class RunHandle:
    run_id: str
    thread_id: str
    mode: str
    status: str = "running"  # running | done | error | interrupt | cancelled
    segments: List[Segment] = field(default_factory=list)
    progress: List[str] = field(default_factory=list)
    active_node: Optional[str] = None
    interrupt_payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> "RunHandle":
        """A shallow, lock-protected copy that's safe to read from the UI
        thread without racing the background writer thread."""
        with self.lock:
            return RunHandle(
                run_id=self.run_id,
                thread_id=self.thread_id,
                mode=self.mode,
                status=self.status,
                segments=[Segment(s.group, s.text, s.is_report, s.finalized) for s in self.segments],
                progress=list(self.progress),
                active_node=self.active_node,
                interrupt_payload=self.interrupt_payload,
                error=self.error,
                started_at=self.started_at,
                finished_at=self.finished_at,
            )


# Registry of in-flight / recently finished runs, keyed by run_id. Lives at
# module (process) scope so it survives Streamlit reruns within a session.
# If the whole process restarts, this registry is naturally empty again —
# app.py detects that case (an `active_run_id` recorded on disk with no
# matching entry here) and offers a "Resume" button instead.
ACTIVE_RUNS: Dict[str, RunHandle] = {}


def _finalize_current_segment(handle: RunHandle) -> None:
    if handle.segments and not handle.segments[-1].finalized:
        seg = handle.segments[-1]
        if not seg.text.strip():
            handle.segments.pop()  # drop empty bubbles (e.g. silent auto-proceed)
        else:
            seg.finalized = True


def _open_segment(handle: RunHandle, group: str) -> None:
    handle.segments.append(Segment(group=group))


def start_run(mode: str, thread_id: str, **kwargs: Any) -> RunHandle:
    """Kicks off a background thread running one streaming agent turn."""
    run_id = str(uuid.uuid4())
    handle = RunHandle(run_id=run_id, thread_id=thread_id, mode=mode)
    ACTIVE_RUNS[run_id] = handle

    worker = threading.Thread(
        target=_worker_entrypoint,
        args=(handle, mode, kwargs),
        daemon=True,
        name=f"scout-run-{run_id[:8]}",
    )
    worker.start()
    return handle


def request_cancel(run_id: str) -> None:
    handle = ACTIVE_RUNS.get(run_id)
    if handle:
        handle.cancel_event.set()


def get_run(run_id: str) -> Optional[RunHandle]:
    return ACTIVE_RUNS.get(run_id)


def forget_run(run_id: str) -> None:
    ACTIVE_RUNS.pop(run_id, None)


# --------------------------------------------------------------------------
# Worker thread
# --------------------------------------------------------------------------
def _worker_entrypoint(handle: RunHandle, mode: str, kwargs: dict) -> None:
    try:
        asyncio.run(_worker(handle, mode, kwargs))
    except RunCancelled:
        with handle.lock:
            _finalize_current_segment(handle)
            handle.status = "cancelled"
            handle.finished_at = time.time()
    except Exception as exc:  # noqa: BLE001 - surface everything to the UI, never crash the thread silently
        logger.exception("Background run %s failed", handle.run_id)
        with handle.lock:
            _finalize_current_segment(handle)
            handle.status = "error"
            handle.error = str(exc)
            handle.finished_at = time.time()


async def _worker(handle: RunHandle, mode: str, kwargs: dict) -> None:
    service = ResearchService()
    raw_buffers = {}
    last_node = [None]

    def on_node_stage(node_name: str) -> None:
        with handle.lock:
            handle.progress.append(node_name)
            handle.active_node = node_name
            group = node_group(node_name)
            if group in BUBBLE_GROUPS:
                current = handle.segments[-1] if handle.segments else None
                if current is None or current.finalized or current.group != group:
                    _finalize_current_segment(handle)
                    _open_segment(handle, group)

    def on_token(node_name: str, token: str) -> None:
        with handle.lock:
            group = node_group(node_name)
            if group not in BUBBLE_GROUPS:
                return
            
            # If the node name changed, reset the buffer for this group
            if last_node[0] != node_name:
                raw_buffers[group] = ""
                last_node[0] = node_name

            current = handle.segments[-1] if handle.segments else None
            if current is None or current.finalized or current.group != group:
                _finalize_current_segment(handle)
                _open_segment(handle, group)
                current = handle.segments[-1]
            
            raw_buffers[group] = raw_buffers.get(group, "") + token
            if group in ("clarify", "general"):
                current.text = _clean_clarify_token(raw_buffers[group])
            else:
                current.text = raw_buffers[group]

    def on_interrupt(payload: Any, node_name: str) -> None:
        with handle.lock:
            _finalize_current_segment(handle)
            handle.interrupt_payload = payload if isinstance(payload, dict) else {"message": str(payload)}
            handle.status = "interrupt"

    def on_complete(final_state: dict) -> None:
        with handle.lock:
            report = final_state.get("final_report")
            if isinstance(report, str) and report.strip():
                current = handle.segments[-1] if handle.segments else None
                if current is None or current.finalized or current.group != "draft_report":
                    _open_segment(handle, "draft_report")
                    current = handle.segments[-1]
                current.text = report  # replace the raw draft with the guardrail-finalized version
                current.is_report = True

    try:
        if mode == "execute":
            await service.execute_research(
                thread_id=handle.thread_id,
                user_query=kwargs["query"],
                on_node_stage=on_node_stage, on_token=on_token,
                on_interrupt=on_interrupt, on_complete=on_complete,
                cancel_event=handle.cancel_event,
            )
        elif mode == "resume":
            await service.resume_from_clarification(
                thread_id=handle.thread_id,
                resume_value=kwargs["resume_value"],
                on_node_stage=on_node_stage, on_token=on_token,
                on_interrupt=on_interrupt, on_complete=on_complete,
                cancel_event=handle.cancel_event,
            )
        elif mode == "checkpoint":
            await service.resume_from_checkpoint(
                thread_id=handle.thread_id,
                on_node_stage=on_node_stage, on_token=on_token,
                on_interrupt=on_interrupt, on_complete=on_complete,
                cancel_event=handle.cancel_event,
            )
        else:
            raise ValueError(f"Unknown run mode: {mode}")
    finally:
        with handle.lock:
            _finalize_current_segment(handle)
            if handle.status == "running":
                handle.status = "done"
            handle.finished_at = time.time()