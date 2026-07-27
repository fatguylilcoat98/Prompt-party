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


class JudgeBody(BaseModel):
    judge_id: str


class RevealBody(BaseModel):
    artist_ids: list[str] | None = None


class OpenVotesBody(BaseModel):
    choices: list[str]


class OverrideWinnerBody(BaseModel):
    winner_id: str
    reason: str = Field(min_length=1)


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


@router.post("/rounds/{round_id}/actions/request_plans")
async def request_plans(
    round_id: str, request: Request, actor: Actor = Depends(require_producer)
):
    return await _map_errors(request.app.state.art_showdown.request_plans)(round_id, actor)


@router.post("/rounds/{round_id}/actions/start_generation")
async def start_generation(
    round_id: str, request: Request, actor: Actor = Depends(require_producer)
):
    return await _map_errors(request.app.state.art_showdown.start_generation)(round_id, actor)


@router.post("/rounds/{round_id}/actions/retry_generation")
async def retry_generation(
    round_id: str, request: Request, actor: Actor = Depends(require_producer)
):
    return await _map_errors(request.app.state.art_showdown.retry_generation)(round_id, actor)


@router.post("/rounds/{round_id}/actions/request_commentary")
async def request_commentary(
    round_id: str, body: JudgeBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.art_showdown.request_commentary)(
        round_id, actor, body.judge_id
    )


@router.post("/rounds/{round_id}/actions/reveal")
async def reveal(
    round_id: str, body: RevealBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.art_showdown.reveal)(
        round_id, actor, body.artist_ids
    )


@router.post("/rounds/{round_id}/actions/request_scores")
async def request_scores(
    round_id: str, body: JudgeBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.art_showdown.request_scores)(
        round_id, actor, body.judge_id
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
    return await _map_errors(request.app.state.art_showdown.calculate_winner)(
        round_id, actor, request.app.state.voting
    )


@router.post("/rounds/{round_id}/winner/override")
async def override_winner(
    round_id: str, body: OverrideWinnerBody, request: Request,
    actor: Actor = Depends(require_producer),
):
    return await _map_errors(request.app.state.art_showdown.override_winner)(
        round_id, actor, body.winner_id, body.reason
    )


@router.get("/rounds/{round_id}/replay")
async def replay(round_id: str, request: Request):
    return await _map_errors(request.app.state.art_showdown.replay)(round_id)
