"""Broadcast surface: public state projection and controlled media URLs.

Broadcast state contains only public-safe fields (Master Spec section 3:
never API keys, raw exceptions, private moderation, or hidden prompts).
Media is served only for validated, approved assets through controlled
URLs (Master Spec section 12).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.games.shared.phases import Phase
from app.persistence.db import session_scope
from app.persistence.models import MediaAsset, Round, Show

router = APIRouter()


def _game_public_state(game_id: str, data: dict) -> dict:
    """Public-safe per-game projection for broadcast/audience surfaces.
    Blocked/unpublished content is redacted here on the server."""
    if game_id == "rap_battle":
        battle = data.get("battle") or {}
        return {
            "order": battle.get("order", []),
            "verses": battle.get("verses", []),
            "exchanges": battle.get("config", {}).get("exchanges"),
        }
    if game_id == "roast_battle":
        battle = data.get("battle") or {}
        return {
            "order": battle.get("order", []),
            "turns": [
                t if t.get("status") == "published" else {**t, "turn": None}
                for t in battle.get("turns", [])
            ],
            "targets": sorted(battle.get("targets", {})),
        }
    if game_id == "ai_court":
        trial = data.get("trial") or {}
        return {
            "fictional_notice": True,
            "case": data.get("case"),
            "stage": trial.get("stage"),
            "turns": trial.get("turns", []),
            "objections": trial.get("objections", []),
            "final_ruling": data.get("final_ruling"),
            "precedent": data.get("precedent"),
        }
    if game_id == "improv":
        performance = data.get("performance") or {}
        return {
            "scene": data.get("scene"),
            "turns": performance.get("turns", []),
            "bells": performance.get("bells", []),
            "ended": performance.get("ended", False),
        }
    return {}


@router.get("/shows/{show_id}/broadcast-state")
def broadcast_state(show_id: str, request: Request):
    with session_scope(request.app.state.session_factory) as session:
        show = session.get(Show, show_id)
        if show is None:
            raise HTTPException(status_code=404, detail="show not found")
        round_ = (
            session.query(Round)
            .filter(Round.show_id == show_id)
            .order_by(Round.created_at.desc())
            .first()
        )
        state = {
            "show_id": show_id,
            "title": show.title,
            "status": show.status,
            "round": None,
        }
        if round_ is not None:
            data = round_.data or {}
            generation = data.get("generation", {})
            revealed = set(data.get("revealed", []))
            state["round"] = {
                "round_id": round_.round_id,
                "game_id": round_.game_id,
                "phase": round_.phase,
                "locked_prompt": round_.locked_prompt,
                "cast": [
                    {
                        "participant_id": p.get("participant_id"),
                        "display_name": p.get("display_name"),
                        "seat_type": p.get("seat_type"),
                        "avatar": p.get("avatar", ""),
                    }
                    for p in (round_.cast or [])
                ],
                "plans": data.get("plans", {})
                if Phase(round_.phase) not in {Phase.LOBBY, Phase.SUBMISSION_OPEN}
                else {},
                # Themed labels only — never raw provider detail.
                "generation": {
                    artist: {"status": g.get("status"), "label": g.get("label")}
                    for artist, g in generation.items()
                },
                "revealed": [
                    {"artist_id": artist, "url": f"/api/media/{generation[artist]['asset_id']}"}
                    for artist in revealed
                    if artist in generation and "asset_id" in generation[artist]
                ],
                "commentary": data.get("commentary", []),
                "judge_scores": data.get("judge_scores", {}),
                "voting": {
                    "status": data.get("voting", {}).get("status", "not_opened"),
                    "choices": data.get("voting", {}).get("choices", []),
                    "tally": data.get("voting", {}).get("tally"),
                    "countdown_seconds": data.get("voting", {}).get("countdown_seconds"),
                    "opened_at": data.get("voting", {}).get("opened_at"),
                },
                "result": round_.result,
                "game_state": _game_public_state(round_.game_id, data),
            }
        return state


@router.get("/media/{asset_id}")
def media(asset_id: str, request: Request):
    with session_scope(request.app.state.session_factory) as session:
        asset = session.get(MediaAsset, asset_id)
        if asset is None or asset.moderation_status != "approved":
            raise HTTPException(status_code=404, detail="media not available")
        path = Path(asset.path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="media not available")
        return FileResponse(path, media_type=asset.mime_type)
