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
from app.games.ai_court.mock_content import CANNED as AI_COURT_CANNED
from app.games.ai_court.module import AiCourtModule
from app.games.ai_court.orchestrator import AiCourtOrchestrator
from app.games.improv.mock_content import CANNED as IMPROV_CANNED
from app.games.improv.module import ImprovModule
from app.games.improv.orchestrator import ImprovOrchestrator
from app.games.rap_battle.mock_content import CANNED as RAP_BATTLE_CANNED
from app.games.roast_battle.mock_content import CANNED as ROAST_BATTLE_CANNED
from app.games.roast_battle.module import RoastBattleModule
from app.games.roast_battle.orchestrator import RoastBattleOrchestrator
from app.games.rap_battle.module import RapBattleModule
from app.games.rap_battle.orchestrator import RapBattleOrchestrator
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
        self.controller.register_module(RapBattleModule())
        self.controller.register_module(AiCourtModule())
        self.controller.register_module(ImprovModule())
        self.controller.register_module(RoastBattleModule())

        if media_dir is None:
            db_path = db_url.removeprefix("sqlite:///")
            media_dir = Path(db_path).parent / "media"
        self.mock_text = MockTextProvider(
            canned={**ART_SHOWDOWN_CANNED, **RAP_BATTLE_CANNED, **AI_COURT_CANNED,
                    **IMPROV_CANNED, **ROAST_BATTLE_CANNED}
        )
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
        self.rap = RapBattleOrchestrator(
            self.session_factory, self.store, self.providers, self.controller,
            clock=fixed_clock, id_factory=ids,
        )
        self.court = AiCourtOrchestrator(
            self.session_factory, self.store, self.providers, self.controller,
            clock=fixed_clock, id_factory=ids,
        )
        self.improv = ImprovOrchestrator(
            self.session_factory, self.store, self.providers, self.controller,
            clock=fixed_clock, id_factory=ids,
        )
        self.roast = RoastBattleOrchestrator(
            self.session_factory, self.store, self.providers, self.controller,
            clock=fixed_clock, id_factory=ids,
        )

    def close(self) -> None:
        self.engine.dispose()
