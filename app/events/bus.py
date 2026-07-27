"""In-process event bus with public/private separation.

Events are EventEnvelope objects (Master Spec 5.2). Subscribers declare a
visibility: PUBLIC subscribers (broadcast/audience SSE feeds) never receive
private events — the filter lives here on the server, not in front-end code
(Master Spec section 12). Persistence of events into the ordered ledger is
wired in Milestone 2.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from app.games.shared.schemas import EventEnvelope

Subscriber = Callable[[EventEnvelope], None]


class Visibility(str, Enum):
    PUBLIC = "public"
    ALL = "all"  # producer/replay surfaces


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[Visibility, Subscriber]] = []

    def subscribe(self, visibility: Visibility, callback: Subscriber) -> Callable[[], None]:
        entry = (visibility, callback)
        self._subscribers.append(entry)

        def unsubscribe() -> None:
            if entry in self._subscribers:
                self._subscribers.remove(entry)

        return unsubscribe

    def publish(self, event: EventEnvelope) -> None:
        for visibility, callback in list(self._subscribers):
            if visibility is Visibility.PUBLIC and not event.public:
                continue
            callback(event)
