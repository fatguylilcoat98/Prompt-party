"""Audience API: join, submit, vote.

These routes carry no producer authority — an audience session can only
add submissions to the moderation queue and cast votes. Nothing here can
advance game state (Authority rules).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.audience.voting import VotingError
from app.controller.engine import NotFoundError
from app.moderation.service import SubmissionError

router = APIRouter()


class JoinBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)


class SubmissionBody(BaseModel):
    session_id: str
    text: str = Field(min_length=1, max_length=2000)
    submission_type: str = "scene_prompt"


class VoteBody(BaseModel):
    session_id: str
    choice: str


@router.post("/shows/{show_id}/audience/join", status_code=201)
def join(show_id: str, body: JoinBody, request: Request):
    try:
        return request.app.state.audience.join(show_id, body.display_name)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="show not found")


@router.post("/rounds/{round_id}/submissions", status_code=201)
def submit(round_id: str, body: SubmissionBody, request: Request):
    try:
        return request.app.state.moderation.submit(
            round_id, body.session_id, body.text, body.submission_type
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="unknown round or session")
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/rounds/{round_id}/votes", status_code=201)
def vote(round_id: str, body: VoteBody, request: Request):
    try:
        return request.app.state.voting.cast(round_id, body.session_id, body.choice)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="unknown round or session")
    except VotingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
