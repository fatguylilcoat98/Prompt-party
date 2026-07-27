"""Deterministic engine fixtures for Milestone 2+ tests.

Fixed clock and counting id factory make every engine test reproducible:
identical inputs always produce identical ids, timestamps, and event
ledgers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

from pathlib import Path

from app.audience.service import AudienceService
from app.audience.voting import VotingService
from app.controller.engine import GameStateController
from app.events.bus import EventBus
from app.events.store import EventStore
from app.games.art_showdown.mock_content import CANNED as ART_SHOWDOWN_CANNED
from app.games.art_showdown.module import ArtShowdownModule
from app.games.art_showdown.orchestrator import ArtShowdownOrchestrator
from app.moderation.service import ModerationService
from app.persistence.db import build_engine, build_session_factory, init_db
from app.providers.mock import MockImageProvider, MockTextProvider
from app.providers.registry import ProviderRegistry

FIXED_TIME = datetime(2026, 7, 27, 17, 0, 0, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    return FIXED_TIME


class CountingIds:
    def __init__(self) -> None:
        self._counter = count(1)

    def __call__(self, prefix: str) -> str:
        return f"{prefix}_{next(self._counter):06d}"


class EngineHarness:
    def __init__(self, db_url: str, media_dir: Path | None = None) -> None:
        self.engine = build_engine(db_url)
        init_db(self.engine)
        self.session_factory = build_session_factory(self.engine)
        self.bus = EventBus()
        self.store = EventStore(self.session_factory, self.bus)
        ids = CountingIds()
        self.controller = GameStateController(
            self.session_factory,
            self.store,
            clock=fixed_clock,
            id_factory=ids,
        )
        self.controller.register_module(ArtShowdownModule())

        if media_dir is None:
            db_path = db_url.removeprefix("sqlite:///")
            media_dir = Path(db_path).parent / "media"
        self.mock_text = MockTextProvider(canned=dict(ART_SHOWDOWN_CANNED))
        self.mock_image = MockImageProvider(media_dir=media_dir)
        self.providers = ProviderRegistry()
        self.providers.register(self.mock_text)
        self.providers.register(self.mock_image)

        self.audience = AudienceService(self.session_factory, clock=fixed_clock, id_factory=ids)
        self.moderation = ModerationService(
            self.session_factory, self.store, self.audience,
            clock=fixed_clock, id_factory=ids,
        )
        self.voting = VotingService(
            self.session_factory, self.store, self.audience,
            clock=fixed_clock, id_factory=ids,
        )
        self.art = ArtShowdownOrchestrator(
            self.session_factory, self.store, self.providers, self.controller,
            clock=fixed_clock, id_factory=ids,
        )

    def close(self) -> None:
        self.engine.dispose()
