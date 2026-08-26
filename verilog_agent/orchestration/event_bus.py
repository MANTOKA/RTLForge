from __future__ import annotations

from collections.abc import Callable

from verilog_agent.domain.models import Event
from verilog_agent.orchestration.storage import SQLiteTaskStore

EventHandler = Callable[[Event], None]


class EventBus:
    """In-process typed delivery with SQLite persistence before notification."""

    def __init__(self, store: SQLiteTaskStore) -> None:
        self.store = store
        self._subscribers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._subscribers.append(handler)

    def publish(self, event: Event) -> Event:
        stored = self.store.append_event(event)
        for subscriber in tuple(self._subscribers):
            subscriber(stored)
        return stored
