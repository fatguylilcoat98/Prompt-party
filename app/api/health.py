"""Health endpoint: app, database, and provider status.

Exposes no secrets and no raw exception details (Master Spec sections 9
and 12) — failures are reported as component status strings only.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from app import __version__

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings

    database = "ok"
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"

    providers = [
        {"provider": h.provider, "healthy": h.healthy}
        for h in await request.app.state.providers.health()
    ]

    return {
        "app": "prompt-party",
        "version": __version__,
        "environment": settings.environment,
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "providers": providers,
    }
