"""Audience submission pipeline (Master Spec section 8).

1. Audience input lands in a pending queue.
2. Automated screening and rate limits run immediately.
3. The producer sees a readable moderation reason.
4. Producer may approve, reject, or edit.
5. Locking copies the approved text into the immutable round record.
6. Only approved, locked content ever reaches model providers.
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
from app.persistence.models import Round, Submission


class SubmissionError(ValueError):
    """Submission rejected by phase, rate limit, or moderation state."""


#: Automated screening blocklist (Master Spec section 8). Deliberately
#: coarse in v1: a hit flags the submission for the producer with a
#: readable reason; it does not auto-publish anything.
SCREEN_RULES: list[tuple[str, str]] = [
    ("doxx", "possible doxxing reference"),
    ("kill ", "possible threat language"),
    ("address is", "possible private data"),
    ("system prompt", "attempted system-prompt injection"),
    ("ignore previous", "attempted prompt injection"),
    ("ignore all instructions", "attempted prompt injection"),
]

MAX_SUBMISSION_LENGTH = 280
MAX_SUBMISSIONS_PER_SESSION_PER_ROUND = 3


def screen(text: str) -> str | None:
    """Return a readable moderation reason, or None if nothing tripped."""
    lowered = text.lower()
    for needle, reason in SCREEN_RULES:
        if needle in lowered:
            return reason
    if len(text) > MAX_SUBMISSION_LENGTH:
        return f"submission exceeds {MAX_SUBMISSION_LENGTH} characters"
    if not text.strip():
        return "empty submission"
    return None


class ModerationService:
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

    # -- audience side ---------------------------------------------------

    def submit(self, round_id: str, audience_session_id: str, text: str,
               submission_type: str = "scene_prompt") -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            if Phase(round_.phase) is not Phase.SUBMISSION_OPEN:
                raise SubmissionError(
                    f"submissions are closed (phase {round_.phase})"
                )
            self._audience.require_member(session, round_.show_id, audience_session_id)
            existing = (
                session.query(Submission)
                .filter(Submission.round_id == round_id)
                .filter(Submission.audience_session_id == audience_session_id)
                .count()
            )
            if existing >= MAX_SUBMISSIONS_PER_SESSION_PER_ROUND:
                raise SubmissionError("submission rate limit reached for this round")

            reason = screen(text)
            submission_id = self._new_id("sub")
            session.add(
                Submission(
                    submission_id=submission_id,
                    round_id=round_id,
                    audience_session_id=audience_session_id,
                    submission_type=submission_type,
                    original_text=text,
                    moderation_status="flagged" if reason else "pending",
                    moderation_reason=reason,
                    created_at=self._clock(),
                )
            )
            # Private: unapproved content never appears on public surfaces.
            self._emit(
                session, round_, "submission.received",
                ActorType.AUDIENCE, audience_session_id, public=False,
                payload={
                    "submission_id": submission_id,
                    "moderation_status": "flagged" if reason else "pending",
                    "moderation_reason": reason,
                },
            )
        return {"submission_id": submission_id, "status": "flagged" if reason else "pending"}

    # -- producer side ---------------------------------------------------

    def review(self, submission_id: str, actor: Actor, decision: str,
               edited_text: str | None = None) -> dict:
        if decision not in {"approve", "reject"}:
            raise SubmissionError(f"unknown review decision {decision!r}")
        with session_scope(self._sessions) as session:
            submission = session.get(Submission, submission_id)
            if submission is None:
                raise NotFoundError(f"submission {submission_id!r} not found")
            round_ = self._get_round(session, submission.round_id)
            if Phase(round_.phase) not in {Phase.SUBMISSION_OPEN, Phase.SUBMISSION_REVIEW}:
                raise SubmissionError("review window is closed")
            if edited_text is not None:
                submission.edited_text = edited_text
            submission.moderation_status = (
                "approved" if decision == "approve" else "rejected"
            )
            self._emit(
                session, round_, f"submission.{submission.moderation_status}",
                actor.actor_type, actor.actor_id, public=False,
                payload={"submission_id": submission_id},
            )
        return {"submission_id": submission_id, "status": submission.moderation_status}

    def lock(self, round_id: str, submission_id: str, actor: Actor) -> dict:
        """Lock the approved submission as the round's immutable prompt."""
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            if Phase(round_.phase) is not Phase.SUBMISSION_REVIEW:
                raise SubmissionError(
                    f"prompt can only be locked during SUBMISSION_REVIEW "
                    f"(phase is {round_.phase})"
                )
            if round_.locked_prompt is not None:
                raise SubmissionError("round prompt is already locked and immutable")
            submission = session.get(Submission, submission_id)
            if submission is None or submission.round_id != round_id:
                raise NotFoundError(f"submission {submission_id!r} not found in round")
            if submission.moderation_status != "approved":
                raise SubmissionError(
                    "only an approved submission can be locked "
                    f"(status is {submission.moderation_status!r})"
                )
            final_text = submission.edited_text or submission.original_text
            submission.final_locked_text = final_text
            submission.moderation_status = "locked"
            round_.locked_prompt = final_text
            round_.updated_at = self._clock()
            # The locked challenge is public: it is about to be broadcast.
            self._emit(
                session, round_, "prompt.locked",
                actor.actor_type, actor.actor_id, public=True,
                payload={"submission_id": submission_id, "locked_prompt": final_text},
            )
        return {"round_id": round_id, "locked_prompt": final_text}

    def queue(self, round_id: str) -> list[dict]:
        """Producer view of the moderation queue with readable reasons."""
        with session_scope(self._sessions) as session:
            rows = (
                session.query(Submission)
                .filter(Submission.round_id == round_id)
                .order_by(Submission.created_at)
                .all()
            )
            return [
                {
                    "submission_id": r.submission_id,
                    "submission_type": r.submission_type,
                    "original_text": r.original_text,
                    "edited_text": r.edited_text,
                    "moderation_status": r.moderation_status,
                    "moderation_reason": r.moderation_reason,
                }
                for r in rows
            ]
