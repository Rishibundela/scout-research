import json
import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from research_service import ResearchService

app = FastAPI(title="Research Agent API")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

research_service = ResearchService()

# --- Request Schemas ---

class StartResearchRequest(BaseModel):
    user_id: Optional[str] = "default_user"
    query: str

class ResumeResearchRequest(BaseModel):
    answers: Dict[str, Any]

# --- Helper Utilities ---

def format_sse(event: str, data: Any) -> str:
    """Formats payload according to the Server-Sent Events spec."""
    payload = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    return f"event: {event}\ndata: {payload}\n\n"

async def _stream_queue(
    queue: asyncio.Queue, 
    background_task: asyncio.Task,
    thread_id: str
):
    """
    Async generator yielding formatted SSE strings from the queue 
    until execution completes or throws an exception.
    """
    # Send initial session acknowledgment
    yield format_sse("session", {"thread_id": thread_id})

    try:
        while True:
            # Wait for the next event from the callbacks
            event_type, data = await queue.get()
            yield format_sse(event_type, data)
            queue.task_done()

            # Terminal event condition
            if event_type in ("done", "error"):
                break

    except asyncio.CancelledError:
        # Triggered if client closes the tab / disconnects
        if not background_task.done():
            background_task.cancel()
        raise

# --- API Endpoints ---

@app.post("/api/research/stream")
async def start_research_stream(request: StartResearchRequest):
    """Initializes a session and streams research execution events."""
    thread_id = await research_service.start_session(user_id=request.user_id)
    queue: asyncio.Queue = asyncio.Queue()

    # Define callbacks that bridge service events into the queue
    async def on_stage(node: str):
        await queue.put(("stage", {"node": node}))

    async def on_token(token: str):
        await queue.put(("token", {"text": token}))

    async def on_interrupt(data: dict):
        await queue.put(("interrupt", data))

    async def on_complete(data: dict):
        await queue.put(("complete", data))

    # Background task executor
    async def run_pipeline():
        try:
            await research_service.execute_research(
                thread_id=thread_id,
                user_query=request.query,
                on_node_stage=on_stage,
                on_token=on_token,
                on_interrupt=on_interrupt,
                on_complete=on_complete,
            )
        except Exception as e:
            await queue.put(("error", {"message": str(e)}))
        finally:
            await queue.put(("done", {"thread_id": thread_id}))

    task = asyncio.create_task(run_pipeline())

    return StreamingResponse(
        _stream_queue(queue, task, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disables response buffering in Nginx/Render
        },
    )


@app.post("/api/research/{thread_id}/resume")
async def resume_research_stream(thread_id: str, request: ResumeResearchRequest):
    """Resumes an interrupted session with user clarification answers."""
    queue: asyncio.Queue = asyncio.Queue()

    async def on_stage(node: str):
        await queue.put(("stage", {"node": node}))

    async def on_token(token: str):
        await queue.put(("token", {"text": token}))

    async def on_interrupt(data: dict):
        await queue.put(("interrupt", data))

    async def on_complete(data: dict):
        await queue.put(("complete", data))

    async def run_resume():
        try:
            await research_service.resume_from_clarification(
                thread_id=thread_id,
                clarification_answers=request.answers,
                on_node_stage=on_stage,
                on_token=on_token,
                on_interrupt=on_interrupt,
                on_complete=on_complete,
            )
        except Exception as e:
            await queue.put(("error", {"message": str(e)}))
        finally:
            await queue.put(("done", {"thread_id": thread_id}))

    task = asyncio.create_task(run_resume())

    return StreamingResponse(
        _stream_queue(queue, task, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )