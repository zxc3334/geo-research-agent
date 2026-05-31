"""Lightweight in-process pub/sub for SSE progress streaming.

Uses asyncio.Event + shared dict. For production, swap with Redis pub/sub.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


class ProgressBus:
    """In-process event bus for task progress updates."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._latest: dict[str, dict] = {}

    def publish(self, task_id: str, data: dict[str, Any]):
        """Publish a progress update for a task."""
        self._latest[task_id] = data
        for queue in self._subscribers.get(task_id, []):
            queue.put_nowait(data)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a task. Returns an async queue."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(queue)
        # Send latest state immediately if available
        if task_id in self._latest:
            queue.put_nowait(self._latest[task_id])
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        """Remove a subscriber."""
        subs = self._subscribers.get(task_id, [])
        if queue in subs:
            subs.remove(queue)

    def get_latest(self, task_id: str) -> dict | None:
        """Get the latest progress state for a task."""
        return self._latest.get(task_id)


# Global singleton
progress_bus = ProgressBus()
