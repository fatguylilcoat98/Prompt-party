"""Deterministic engine fixtures for Milestone 2+ tests.

Fixed clock and counting id factory make every engine test reproducible:
identical inputs always produce identical ids, timestamps, and event
ledgers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

from app.controller.engine import GameStateController
from app.events.bus import EventBus
from app.events.store import EventStore
from app.persistence.db import build_engine, build_session_factory, init_db

FIXED_TIME = datetime(2026, 7, 27, 17, 0, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    return FIXED_TIME


class CountingIds:
    def __init__(self) -> None:
        self._counter = count(1)

    def __call__(self, prefix: str) -> str:
        return f"{prefix}_{next(self._counter):06d}"


class EngineHarness:
    def __init__(self, db_url: str) -> None:
        self.engine = build_engine(db_url)
        init_db(self.engine)
        self.session_factory = build_session_factory(self.engine)
        self.bus = EventBus()
        self.store = EventStore(self.session_factory, self.bus)
        self.controller = GameStateController(
            self.session_factory,
            self.store,
            clock=fixed_clock,
            id_factory=CountingIds(),
        )

    def close(self) -> None:
        self.engine.dispose()
