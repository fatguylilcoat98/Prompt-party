"""Application factory.

Run in development with:  scripts/dev.sh  (or `make dev`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.audience import router as audience_router
from app.api.broadcast import router as broadcast_router
from app.api.game_actions import router as game_actions_router
from app.api.health import router as health_router
from app.api.shows import router as shows_router
from app.audience.service import AudienceService
from app.audience.voting import VotingService
from app.config import Settings, get_settings
from app.controller.engine import GameStateController
from app.events.bus import EventBus
from app.events.store import EventStore
from app.games.art_showdown.mock_content import CANNED as ART_SHOWDOWN_CANNED
from app.games.art_showdown.module import ArtShowdownModule
from app.games.art_showdown.orchestrator import ArtShowdownOrchestrator
from app.games.ai_court.mock_content import CANNED as AI_COURT_CANNED
from app.games.ai_court.module import AiCourtModule
from app.games.ai_court.orchestrator import AiCourtOrchestrator
from app.games.rap_battle.mock_content import CANNED as RAP_BATTLE_CANNED
from app.games.rap_battle.module import RapBattleModule
from app.games.rap_battle.orchestrator import RapBattleOrchestrator
from app.moderation.service import ModerationService
from app.games.shared.schemas import Capability
from app.persistence.db import build_engine, build_session_factory, init_db
from app.providers.mock import MockImageProvider, MockTextProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.registry import ProviderRegistry


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    """Mocks are always registered so every game can run without external
    API calls; real adapters register on top when configured. Provider
    selection is configuration — the engine never sees provider-specific
    logic (Milestone 4 rule)."""
    registry = ProviderRegistry()
    registry.register(
        MockTextProvider(canned={**ART_SHOWDOWN_CANNED, **RAP_BATTLE_CANNED, **AI_COURT_CANNED})
    )
    registry.register(MockImageProvider(media_dir=settings.media_dir))

    if settings.text_provider == "openai_compatible" and settings.text_provider_base_url:
        registry.register(
            OpenAICompatibleProvider(
                name="openai_text",
                base_url=settings.text_provider_base_url,
                api_key=settings.text_provider_api_key.get_secret_value(),
                model=settings.text_provider_model,
                capabilities=frozenset(
                    {Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT,
                     Capability.VISION_INPUT}
                ),
            )
        )
    if settings.image_provider == "openai_compatible" and settings.image_provider_base_url:
        registry.register(
            OpenAICompatibleProvider(
                name="openai_image",
                base_url=settings.image_provider_base_url,
                api_key=settings.image_provider_api_key.get_secret_value(),
                model=settings.image_provider_model,
                capabilities=frozenset({Capability.IMAGE_GENERATION}),
                media_dir=settings.media_dir,
            )
        )
    return registry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.engine)
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title="Prompt Party",
        version=__version__,
        lifespan=lifespan,
        # Public API docs never include producer secrets; docs stay on in
        # development only.
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = build_engine(settings.database_url)
    app.state.session_factory = build_session_factory(app.state.engine)
    app.state.event_bus = EventBus()
    app.state.event_store = EventStore(app.state.session_factory, app.state.event_bus)
    app.state.controller = GameStateController(
        app.state.session_factory, app.state.event_store
    )
    app.state.providers = build_provider_registry(settings)

    app.state.controller.register_module(ArtShowdownModule())
    app.state.controller.register_module(RapBattleModule())
    app.state.controller.register_module(AiCourtModule())
    app.state.audience = AudienceService(app.state.session_factory)
    app.state.moderation = ModerationService(
        app.state.session_factory, app.state.event_store, app.state.audience
    )
    app.state.voting = VotingService(
        app.state.session_factory, app.state.event_store, app.state.audience
    )
    app.state.art_showdown = ArtShowdownOrchestrator(
        app.state.session_factory,
        app.state.event_store,
        app.state.providers,
        app.state.controller,
    )
    app.state.rap_battle = RapBattleOrchestrator(
        app.state.session_factory,
        app.state.event_store,
        app.state.providers,
        app.state.controller,
    )
    app.state.ai_court = AiCourtOrchestrator(
        app.state.session_factory,
        app.state.event_store,
        app.state.providers,
        app.state.controller,
    )
    #: game_id -> orchestrator, used by the generic action dispatcher.
    app.state.games = {
        "art_showdown": app.state.art_showdown,
        "rap_battle": app.state.rap_battle,
        "ai_court": app.state.ai_court,
    }

    app.include_router(health_router, prefix="/api")
    app.include_router(shows_router, prefix="/api")
    app.include_router(audience_router, prefix="/api")
    app.include_router(game_actions_router, prefix="/api")
    app.include_router(broadcast_router, prefix="/api")

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/broadcast", StaticFiles(directory=web_dir / "broadcast", html=True))
    return app
