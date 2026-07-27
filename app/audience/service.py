"""Audience room membership.

Audience members join with a room code and get a server-issued session id.
That session id is required for submissions and votes — and grants no
authority over game state (Authority rules).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.controller.engine import NotFoundError
from app.games.shared.schemas import new_id, utc_now
from app.persistence.db import session_scope
from app.persistence.models import AudienceMember, Show


def room_code_for_show(show_id: str) -> str:
    """Deterministic 6-character room code derived from the show id."""
    return show_id[-6:].upper()


class AudienceService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock
        self._new_id = id_factory

    def join(self, show_id: str, display_name: str) -> dict:
        display_name = display_name.strip()[:60] or "guest"
        session_id = self._new_id("aud")
        with session_scope(self._sessions) as session:
            if session.get(Show, show_id) is None:
                raise NotFoundError(f"show {show_id!r} not found")
            session.add(
                AudienceMember(
                    session_id=session_id,
                    show_id=show_id,
                    display_name=display_name,
                    room_code=room_code_for_show(show_id),
                    rate_limit={},
                    joined_at=self._clock(),
                )
            )
        return {
            "session_id": session_id,
            "show_id": show_id,
            "display_name": display_name,
            "room_code": room_code_for_show(show_id),
        }

    def require_member(self, session: Session, show_id: str, session_id: str) -> AudienceMember:
        member = session.get(AudienceMember, session_id)
        if member is None or member.show_id != show_id:
            raise NotFoundError("unknown audience session for this show")
        return member
