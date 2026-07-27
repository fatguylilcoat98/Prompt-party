"""Show and round control API (Master Spec section 10 subset for M2).

All mutating endpoints are producer actions and require the producer
token. Errors from the controller map to HTTP statuses; raw exception
detail never leaves the server (Master Spec section 9).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.controller.engine import (
    Actor,
    AuthorityError,
    DuplicateCommandError,
    NotFoundError,
    ShowStateError,
)
from app.games.shared.module import TransitionError
from app.games.shared.phases import Phase
from app.producer.auth import require_producer

router = APIRouter()


def _run(request: Request, fn, *args, **kwargs):
    controller = request.app.state.controller
    try:
        return fn(controller, *args, **kwargs)
    except DuplicateCommandError:
        raise HTTPException(status_code=409, detail="duplicate command_id")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    except (ShowStateError, TransitionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except AuthorityError:
        raise HTTPException(status_code=403, detail="not authorized for this action")


class CreateShowBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    selected_games: list[str] = Field(default_factory=list)
    producer_settings: dict = Field(default_factory=dict)


class EndShowBody(BaseModel):
    reason: str = "normal"


class CreateRoundBody(BaseModel):
    game_id: str = Field(min_length=1)


class TransitionBody(BaseModel):
    new_phase: Phase
    override: bool = False
    reason: str | None = None


@router.post("/shows", status_code=201)
def create_show(
    body: CreateShowBody,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(
        request,
        lambda c: c.create_show(
            body.title, body.selected_games, actor,
            producer_settings=body.producer_settings, command_id=x_command_id,
        ),
    )


@router.post("/shows/{show_id}/start")
def start_show(
    show_id: str,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(request, lambda c: c.start_show(show_id, actor, command_id=x_command_id))


@router.post("/shows/{show_id}/pause")
def pause_show(
    show_id: str,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(request, lambda c: c.pause_show(show_id, actor, command_id=x_command_id))


@router.post("/shows/{show_id}/resume")
def resume_show(
    show_id: str,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(request, lambda c: c.resume_show(show_id, actor, command_id=x_command_id))


@router.post("/shows/{show_id}/end")
def end_show(
    show_id: str,
    body: EndShowBody,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(
        request,
        lambda c: c.end_show(show_id, actor, reason=body.reason, command_id=x_command_id),
    )


@router.post("/shows/{show_id}/rounds", status_code=201)
def create_round(
    show_id: str,
    body: CreateRoundBody,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(
        request,
        lambda c: c.create_round(show_id, body.game_id, actor, command_id=x_command_id),
    )


@router.post("/rounds/{round_id}/transition")
def transition_round(
    round_id: str,
    body: TransitionBody,
    request: Request,
    actor: Actor = Depends(require_producer),
    x_command_id: str | None = Header(default=None),
):
    return _run(
        request,
        lambda c: c.transition_round(
            round_id, body.new_phase, actor,
            command_id=x_command_id, override=body.override, reason=body.reason,
        ),
    )


@router.get("/shows/{show_id}")
def get_show(show_id: str, request: Request):
    return _run(request, lambda c: c.get_show(show_id))


@router.get("/rounds/{round_id}")
def get_round(round_id: str, request: Request):
    return _run(request, lambda c: c.get_round(round_id))


@router.get("/shows/{show_id}/events")
def list_events(
    show_id: str,
    request: Request,
    since: int = 0,
    include_private: bool = False,
    x_producer_token: str | None = Header(default=None),
):
    """Public event feed for broadcast/audience. The full ledger,
    including private events, requires the producer token — the filter is
    enforced here on the server."""
    if include_private:
        require_producer(request, x_producer_token)
    stored = request.app.state.event_store.list_events(
        show_id, public_only=not include_private, since_seq=since
    )
    return {
        "show_id": show_id,
        "events": [
            {"seq": s.seq, **s.envelope.model_dump(mode="json")} for s in stored
        ],
    }
