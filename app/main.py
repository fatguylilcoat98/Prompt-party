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
from app.moderation.service import ModerationService
from app.persistence.db import build_engine, build_session_factory, init_db
from app.providers.mock import MockImageProvider, MockTextProvider
from app.providers.registry import ProviderRegistry


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()
    # Milestone 4 adds real adapters selected by settings.text_provider /
    # settings.image_provider; mocks are always registered so every game can
    # run without external API calls. The canned generators produce
    # schema-valid, deterministic game content.
    registry.register(MockTextProvider(canned=dict(ART_SHOWDOWN_CANNED)))
    registry.register(MockImageProvider(media_dir=settings.media_dir))
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

    app.include_router(health_router, prefix="/api")
    app.include_router(shows_router, prefix="/api")
    app.include_router(audience_router, prefix="/api")
    app.include_router(game_actions_router, prefix="/api")
    app.include_router(broadcast_router, prefix="/api")

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/broadcast", StaticFiles(directory=web_dir / "broadcast", html=True))
    return app
