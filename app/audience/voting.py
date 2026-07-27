"""Audience voting (shared by all games).

One vote per audience session, revisable until close (replacement history
kept), late votes rejected, deterministic tally at close (Master Spec
section 15). Opening/closing a vote is a producer/controller action;
casting a vote never advances game state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.audience.service import AudienceService
from app.controller.engine import Actor, NotFoundError
from app.events.store import EventStore
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType, EventEnvelope, new_id, utc_now
from app.persistence.db import session_scope
from app.persistence.models import Round, Vote


class VotingError(ValueError):
    pass


class VotingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_store: EventStore,
        audience: AudienceService,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._sessions = session_factory
        self._events = event_store
        self._audience = audience
        self._clock = clock
        self._new_id = id_factory

    def _get_round(self, session: Session, round_id: str) -> Round:
        round_ = session.get(Round, round_id)
        if round_ is None:
            raise NotFoundError(f"round {round_id!r} not found")
        return round_

    def _emit(self, session: Session, round_: Round, event_type: str,
              actor_type: ActorType, actor_id: str, public: bool, payload: dict) -> None:
        self._events.append(
            session,
            EventEnvelope(
                event_id=self._new_id("evt"),
                event_type=event_type,
                show_id=round_.show_id,
                round_id=round_.round_id,
                game_id=round_.game_id,
                phase=Phase(round_.phase),
                actor_type=actor_type,
                actor_id=actor_id,
                timestamp=self._clock(),
                public=public,
                payload=payload,
            ),
        )

    @staticmethod
    def _voting_state(round_: Round) -> str:
        return (round_.data or {}).get("voting", {}).get("status", "not_opened")

    # -- producer/controller actions -------------------------------------

    def open(self, round_id: str, actor: Actor, choices: list[str],
             countdown_seconds: int | None = None) -> dict:
        """Open the ballot. ``countdown_seconds`` is display guidance for
        audience/broadcast countdowns only — closing remains an explicit
        producer/controller action, so the close stays deterministic."""
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            if Phase(round_.phase) is not Phase.AUDIENCE_VOTING:
                raise VotingError(
                    f"voting can only open during AUDIENCE_VOTING (phase {round_.phase})"
                )
            if self._voting_state(round_) == "open":
                raise VotingError("voting is already open")
            if len(choices) < 2:
                raise VotingError("voting needs at least two choices")
            voting = {"status": "open", "choices": choices}
            if countdown_seconds is not None:
                voting["countdown_seconds"] = max(5, min(int(countdown_seconds), 600))
                voting["opened_at"] = self._clock().isoformat()
            round_.data = {**(round_.data or {}), "voting": voting}
            self._emit(
                session, round_, "vote.opened",
                actor.actor_type, actor.actor_id, public=True,
                payload={"choices": choices,
                         "countdown_seconds": voting.get("countdown_seconds")},
            )
        return {"round_id": round_id, "voting": "open", "choices": choices,
                "countdown_seconds": voting.get("countdown_seconds")}

    def close(self, round_id: str, actor: Actor) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            if self._voting_state(round_) != "open":
                raise VotingError("voting is not open")
            tally = self._tally(session, round_id, (round_.data or {})["voting"]["choices"])
            round_.data = {
                **(round_.data or {}),
                "voting": {**round_.data["voting"], "status": "closed", "tally": tally},
            }
            self._emit(
                session, round_, "vote.closed",
                actor.actor_type, actor.actor_id, public=True,
                payload={"tally": tally},
            )
        return {"round_id": round_id, "voting": "closed", "tally": tally}

    # -- audience actions -------------------------------------------------

    def cast(self, round_id: str, audience_session_id: str, choice: str) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            if self._voting_state(round_) != "open":
                raise VotingError("voting is not open; late votes are rejected")
            choices = (round_.data or {})["voting"]["choices"]
            if choice not in choices:
                raise VotingError(f"choice {choice!r} is not on the ballot")
            self._audience.require_member(session, round_.show_id, audience_session_id)

            current = (
                session.query(Vote)
                .filter(Vote.round_id == round_id)
                .filter(Vote.voter_session_id == audience_session_id)
                .filter(Vote.replaced_vote_id.is_(None))
                .order_by(Vote.created_at.desc())
                .first()
            )
            vote_id = self._new_id("vote")
            replaced = None
            if current is not None:
                # Revising: keep history by marking the old vote replaced.
                current.replaced_vote_id = vote_id
                replaced = current.vote_id
            session.add(
                Vote(
                    vote_id=vote_id,
                    round_id=round_id,
                    voter_session_id=audience_session_id,
                    choice=choice,
                    created_at=self._clock(),
                )
            )
            # Vote contents stay private until the tally; the audience
            # member gets a confirmation, not a public broadcast.
            self._emit(
                session, round_, "vote.cast",
                ActorType.AUDIENCE, audience_session_id, public=False,
                payload={"vote_id": vote_id, "replaced_vote_id": replaced},
            )
        return {"vote_id": vote_id, "choice": choice, "revised": replaced is not None}

    # -- tally -----------------------------------------------------------

    def _tally(self, session: Session, round_id: str, choices: list[str]) -> dict[str, int]:
        rows = (
            session.query(Vote)
            .filter(Vote.round_id == round_id)
            .filter(Vote.replaced_vote_id.is_(None))
            .all()
        )
        tally = {choice: 0 for choice in choices}
        for row in rows:
            if row.choice in tally:
                tally[row.choice] += 1
        return tally

    def tally(self, round_id: str) -> dict[str, int]:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            voting = (round_.data or {}).get("voting", {})
            if voting.get("status") == "closed":
                return voting["tally"]
            return self._tally(session, round_id, voting.get("choices", []))
