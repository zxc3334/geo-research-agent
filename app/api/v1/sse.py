"""SSE (Server-Sent Events) endpoint for real-time task progress."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.progress_bus import progress_bus

router = APIRouter()


@router.get("/tasks/{task_id}/stream")
async def stream_task_progress(task_id: str):
    """Stream real-time progress updates for a research task.

    Returns Server-Sent Events (SSE) with progress data.
    Each event has the format:
        data: {"phase": "researching", "completed_subtasks": 2, ...}

    The stream ends when the task reaches "completed" or "failed" status.
    """
    async def event_generator():
        queue = progress_bus.subscribe(task_id)
        try:
            # Send initial connection event
            yield f"data: {json.dumps({'event': 'connected', 'task_id': task_id})}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    # End stream on terminal states
                    if data.get("status") in ("completed", "failed"):
                        yield f"data: {json.dumps({'event': 'done'})}\n\n"
                        break
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
        finally:
            progress_bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
