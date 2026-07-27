"""Producer authority for the HTTP surface.

Producer-only endpoints require the configured producer token via the
``X-Producer-Token`` header, compared in constant time. A valid token
yields a PRODUCER actor; there is no HTTP path that yields a CONTROLLER,
AI, or AUDIENCE actor, so audience and AI traffic structurally cannot
advance game state (Authority rules).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from app.controller.engine import Actor
from app.games.shared.schemas import ActorType


def require_producer(
    request: Request,
    x_producer_token: str | None = Header(default=None),
) -> Actor:
    expected = request.app.state.settings.producer_token.get_secret_value()
    supplied = x_producer_token or ""
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="producer token required")
    return Actor(ActorType.PRODUCER, "producer")
