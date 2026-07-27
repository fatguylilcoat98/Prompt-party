"""Live event streaming hub (Phase 2, Priority 1).

Design: notify-then-read. The persisted event ledger remains the single
source of truth — no state is duplicated into stream buffers. Each SSE
connection registers a waker; when the bus publishes an event for a show,
matching wakers fire and each connection reads the new rows from the
EventStore past its own last-sent seq. Consequences:

- Event IDs are ledger seqs, so ``Last-Event-ID`` reconnect is exact.
- Memory per connection is one read batch, regardless of backlog size.
- Public/private filtering happens in the store query, on the server.

Bus publishes can come from worker threads (sync endpoints run in a
threadpool), so wakers are fired via ``loop.call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from time import time

from app.events.bus import EventBus, Visibility
from app.events.store import StoredEvent
from app.games.shared.schemas import EventEnvelope


class StreamHub:
    def __init__(self, bus: EventBus) -> None:
        self._wakers: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = (
            defaultdict(set)
        )
        self.started_at = time()
        bus.subscribe(Visibility.ALL, self._on_event)

    def _on_event(self, envelope: EventEnvelope) -> None:
        for loop, waker in list(self._wakers.get(envelope.show_id, ())):
            loop.call_soon_threadsafe(waker.set)

    def register(self, show_id: str) -> tuple[asyncio.AbstractEventLoop, asyncio.Event]:
        entry = (asyncio.get_running_loop(), asyncio.Event())
        self._wakers[show_id].add(entry)
        return entry

    def unregister(self, show_id: str,
                   entry: tuple[asyncio.AbstractEventLoop, asyncio.Event]) -> None:
        self._wakers.get(show_id, set()).discard(entry)
        if show_id in self._wakers and not self._wakers[show_id]:
            del self._wakers[show_id]

    def viewer_counts(self) -> dict[str, int]:
        return {show_id: len(entries) for show_id, entries in self._wakers.items()}

    def total_viewers(self) -> int:
        return sum(len(entries) for entries in self._wakers.values())


def format_sse(stored: StoredEvent) -> str:
    """One ledger row as an SSE frame: id is the ledger seq."""
    envelope = stored.envelope
    data = json.dumps(
        {"seq": stored.seq, **envelope.model_dump(mode="json")},
        separators=(",", ":"),
    )
    return f"id: {stored.seq}\nevent: {envelope.event_type}\ndata: {data}\n\n"
