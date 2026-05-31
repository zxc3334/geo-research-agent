"""Interactive research endpoints — user input injection during research."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.interactive_bus import interactive_bus
from app.core.progress_bus import progress_bus

router = APIRouter()


class UserInputRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="用户的补充指令或问题")


@router.post("/tasks/{task_id}/input")
async def submit_user_input(task_id: str, req: UserInputRequest):
    """Submit user input during an active research task.

    The input will be injected into the next subtask's context,
    influencing the research direction.
    """
    # Check if the task exists and is running
    latest = progress_bus.get_latest(task_id)
    if not latest:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or no progress data")

    if latest.get("status") in ("completed", "failed"):
        raise HTTPException(status_code=400, detail="Task already finished, cannot inject input")

    # Submit to interactive bus
    interactive_bus.submit_input(task_id, req.text, task_id=task_id)

    # Also publish to progress bus so SSE clients see it
    progress_bus.publish(task_id, {
        "phase": "user_input_received",
        "status": "running",
        "user_input": req.text[:100],
    })

    return {
        "status": "accepted",
        "message": f"Input will be injected into the next subtask context: '{req.text[:60]}...'",
    }
