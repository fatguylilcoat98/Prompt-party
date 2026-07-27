"""Game catalog and show listing for the web consoles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.controller.engine import Actor
from app.games.catalog import catalog
from app.persistence.db import session_scope
from app.persistence.models import Round, Show
from app.producer.auth import require_producer

router = APIRouter()


@router.get("/games")
def games():
    """Public game catalog: manifests and default casts (no secrets)."""
    return {"games": catalog()}


@router.get("/shows")
def list_shows(request: Request, actor: Actor = Depends(require_producer)):
    """Producer console: recent shows with their rounds."""
    with session_scope(request.app.state.session_factory) as session:
        shows = (
            session.query(Show).order_by(Show.created_at.desc()).limit(25).all()
        )
        results = []
        for show in shows:
            rounds = (
                session.query(Round)
                .filter(Round.show_id == show.show_id)
                .order_by(Round.created_at.desc())
                .all()
            )
            results.append({
                "show_id": show.show_id,
                "title": show.title,
                "status": show.status,
                "selected_games": show.selected_games,
                "created_at": show.created_at.isoformat(),
                "rounds": [
                    {"round_id": r.round_id, "game_id": r.game_id, "phase": r.phase}
                    for r in rounds
                ],
            })
    return {"shows": results}
