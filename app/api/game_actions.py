"""Producer game-action API for the vertical slice (Master Spec section 10).

Submissions review, prompt lock, game actions, voting open/close, winner
calculate/override, replay. All mutating routes are producer-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.audience.voting import VotingError
from app.controller.engine import Actor, NotFoundError
from app.games.art_showdown.orchestrator import ActionError
from app.moderation.service import SubmissionError
from app.producer.auth import require_producer

router = APIRouter()


def _map_errors(fn):
    async def run(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            return result
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
        except (ActionError, SubmissionError, VotingError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return run


class ReviewBody(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    edited_text: str | None = None


class LockBody(BaseModel):
    submission_id: str


class OpenVotesBody(BaseModel):
    choices: list[str]


class OverrideWinnerBody(BaseModel):
    winner_id: str
    reason: str = Field(min_length=1)


def _orchestrator_for(request: Request, round_id: str):
    """Dispatch by the round's game module (one engine, many games)."""
    try:
        round_ = request.app.state.controller.get_round(round_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    orchestrator = request.app.state.games.get(round_["game_id"])
    if orchestrator is None:
        raise HTTPException(
            status_code=422, detail=f"no module registered for {round_['game_id']}"
        )
    return orchestrator


@router.post("/rounds/{round_id}/actions/{action_id}")
async def run_action(
    round_id: str, action_id: str, request: Request,
    body: dict | None = None,
    actor: Actor = Depends(require_producer),
):
    """Generic producer game action (Master Spec section 10). Only actions
    the game module explicitly exposes are callable."""
    orchestrator = _orchestrator_for(request, round_id)
    if action_id not in getattr(orchestrator, "ACTIONS", frozenset()):
        raise HTTPException(
            status_code=404, detail=f"unknown action {action_id!r} for this game"
        )
    method = getattr(orchestrator, action_id)
    try:
        return await _map_errors(method)(round_id, actor, **(body or {}))
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"bad action parameters: {exc}")


@router.get("/rounds/{round_id}/submissions")
async def moderation_queue(
    round_id: str, request: Request, actor: Actor = Depends(require_producer)
):
    return await _map_errors(request.app.state.moderation.queue)(round_id)


@router.post("/submissions/{submission_id}/review")
async def review_submission(
    submission_id: str, body: ReviewBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.moderation.review)(
        submission_id, actor, body.decision, body.edited_text
    )


@router.post("/rounds/{round_id}/lock")
async def lock_prompt(
    round_id: str, body: LockBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.moderation.lock)(
        round_id, body.submission_id, actor
    )


@router.post("/rounds/{round_id}/votes/open")
async def open_votes(
    round_id: str, body: OpenVotesBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.voting.open)(round_id, actor, body.choices)


@router.post("/rounds/{round_id}/votes/close")
async def close_votes(
    round_id: str, request: Request, actor: Actor = Depends(require_producer)
):
    return await _map_errors(request.app.state.voting.close)(round_id, actor)


@router.post("/rounds/{round_id}/winner/calculate")
async def calculate_winner(
    round_id: str, request: Request, actor: Actor = Depends(require_producer)
):
    orchestrator = _orchestrator_for(request, round_id)
    return await _map_errors(orchestrator.calculate_winner)(
        round_id, actor, request.app.state.voting
    )


@router.post("/rounds/{round_id}/winner/override")
async def override_winner(
    round_id: str, body: OverrideWinnerBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    orchestrator = _orchestrator_for(request, round_id)
    return await _map_errors(orchestrator.override_winner)(
        round_id, actor, body.winner_id, body.reason
    )


@router.get("/rounds/{round_id}/replay")
async def replay(round_id: str, request: Request):
    orchestrator = _orchestrator_for(request, round_id)
    return await _map_errors(orchestrator.replay)(round_id)
