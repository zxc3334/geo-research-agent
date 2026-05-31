"""Interactive context bus — allows user input to be injected between subtasks.

Flow:
  1. Orchestrator dispatches subtask → publishes progress via progress_bus
  2. After subtask completes, checks interactive_bus for pending user input
  3. If user input exists → inject into next subtask's context
  4. If no input → auto-continue after timeout
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserInput:
    """A piece of user input injected during research."""
    text: str
    timestamp: float = field(default_factory=time.time)
    task_id: str = ""  # Which task was running when user spoke


class InteractiveBus:
    """Manages user input injection between subtasks."""

    def __init__(self, auto_continue_seconds: float = 8.0):
        self._inputs: dict[str, list[UserInput]] = {}  # task_run_id -> inputs
        self._wait_events: dict[str, asyncio.Event] = {}
        self._auto_continue_seconds = auto_continue_seconds

    def submit_input(self, run_id: str, text: str, task_id: str = ""):
        """User submits input during a research run."""
        if run_id not in self._inputs:
            self._inputs[run_id] = []
        self._inputs[run_id].append(UserInput(text=text, task_id=task_id))
        # Signal the waiting orchestrator
        if run_id in self._wait_events:
            self._wait_events[run_id].set()

    def get_pending_inputs(self, run_id: str) -> list[UserInput]:
        """Get and clear pending user inputs for a run."""
        inputs = self._inputs.get(run_id, [])
        self._inputs[run_id] = []
        return inputs

    def has_pending_input(self, run_id: str) -> bool:
        """Check if there's pending user input."""
        return bool(self._inputs.get(run_id))

    async def wait_for_input_or_timeout(self, run_id: str, timeout: float | None = None) -> bool:
        """Wait for user input or timeout. Returns True if input arrived."""
        timeout = timeout or self._auto_continue_seconds
        event = asyncio.Event()
        self._wait_events[run_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._wait_events.pop(run_id, None)

    def clear(self, run_id: str):
        """Clean up a completed run."""
        self._inputs.pop(run_id, None)
        self._wait_events.pop(run_id, None)


# Global singleton
interactive_bus = InteractiveBus()
