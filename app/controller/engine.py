"""Game-state controller (Milestone 2).

The single authority over official game state (Master Spec section 4,
AUTHORITY RULE): only this controller — driven by controller logic or an
authorized producer action — advances a show or round. AI output, audience
input, media callbacks, and provider completions can never advance state:
they are not accepted actor types here.

Every state change is persisted to the ordered event ledger *before* the
in-process bus fans it out, and every command may carry a ``command_id``
for idempotency — duplicates are rejected (Master Spec section 15).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.events.store import EventStore
from app.games.shared.module import TransitionError
from app.games.shared.phases import Phase, phase_index
from app.games.shared.schemas import ActorType, EventEnvelope, new_id, utc_now
from app.persistence.db import session_scope
from app.persistence.models import CommandRecord, ErrorRecord, Round, Show


class ShowStatus(str, Enum):
    CREATED = "created"
    LIVE = "live"
    PAUSED = "paused"
    ENDED = "ended"


#: Actor types allowed to advance official state. AI and audience actors
#: are structurally excluded (Authority rules).
STATE_AUTHORITY = frozenset({ActorType.CONTROLLER, ActorType.PRODUCER})


class AuthorityError(PermissionError):
    """Actor is not allowed to perform this state change."""


class DuplicateCommandError(ValueError):
    """A command with this command_id was already executed."""


class ShowStateError(ValueError):
    """Operation is not valid for the show's current status."""


class NotFoundError(LookupError):
    pass


class Actor:
    __slots__ = ("actor_type", "actor_id")

    def __init__(self, actor_type: ActorType, actor_id: str) -> None:
        self.actor_type = actor_type
        self.actor_id = actor_id


CONTROLLER_ACTOR = Actor(ActorType.CONTROLLER, "controller")


def validate_default_transition(old: Phase, new: Phase) -> None:
    """Default lifecycle rule: exactly one step forward in the shared
    12-phase order. Game modules may restrict further; only an audited
    producer override may do anything else."""
    if phase_index(new) != phase_index(old) + 1:
        raise TransitionError(
            f"illegal transition {old.value} -> {new.value}: "
            "only the next lifecycle phase is allowed without a producer override"
        )


class GameStateController:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_store: EventStore,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._sessions = session_factory
        self._events = event_store
        self._clock = clock
        self._new_id = id_factory
        #: game_id -> GameModule; modules registered here may tighten
        #: transition validation beyond the default rule (Milestone 3+).
        self._modules: dict[str, Any] = {}

    # -- module registry -------------------------------------------------

    def register_module(self, module: Any) -> None:
        self._modules[module.manifest.game_id] = module

    # -- internal helpers ------------------------------------------------

    def _require_authority(self, actor: Actor, *, producer_only: bool = False) -> None:
        if producer_only:
            if actor.actor_type is not ActorType.PRODUCER:
                raise AuthorityError(
                    f"actor_type={actor.actor_type.value} may not perform "
                    "producer-only actions"
                )
            return
        if actor.actor_type not in STATE_AUTHORITY:
            raise AuthorityError(
                f"actor_type={actor.actor_type.value} may not advance game state"
            )

    def _claim_command(
        self, session: Session, command_id: str | None, action: str, actor: Actor,
        show_id: str | None,
    ) -> None:
        if command_id is None:
            return
        session.add(
            CommandRecord(
                command_id=command_id,
                action=action,
                actor_type=actor.actor_type.value,
                actor_id=actor.actor_id,
                show_id=show_id,
                created_at=self._clock(),
            )
        )
        try:
            session.flush()
        except IntegrityError:
            raise DuplicateCommandError(
                f"command_id {command_id!r} was already executed"
            ) from None

    def _emit(
        self,
        session: Session,
        *,
        event_type: str,
        show_id: str,
        actor: Actor,
        public: bool,
        round_id: str | None = None,
        game_id: str | None = None,
        phase: Phase | None = None,
        payload: dict | None = None,
    ) -> None:
        self._events.append(
            session,
            EventEnvelope(
                event_id=self._new_id("evt"),
                event_type=event_type,
                show_id=show_id,
                round_id=round_id,
                game_id=game_id,
                phase=phase,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                timestamp=self._clock(),
                public=public,
                payload=payload or {},
            ),
        )

    def _get_show(self, session: Session, show_id: str) -> Show:
        show = session.get(Show, show_id)
        if show is None:
            raise NotFoundError(f"show {show_id!r} not found")
        return show

    def _get_round(self, session: Session, round_id: str) -> Round:
        round_ = session.get(Round, round_id)
        if round_ is None:
            raise NotFoundError(f"round {round_id!r} not found")
        return round_

    # -- show lifecycle --------------------------------------------------

    def create_show(
        self,
        title: str,
        selected_games: list[str],
        actor: Actor,
        producer_settings: dict | None = None,
        command_id: str | None = None,
    ) -> dict:
        self._require_authority(actor)
        show_id = self._new_id("show")
        with session_scope(self._sessions) as session:
            self._claim_command(session, command_id, "create_show", actor, show_id)
            session.add(
                Show(
                    show_id=show_id,
                    title=title,
                    status=ShowStatus.CREATED.value,
                    selected_games=selected_games,
                    producer_settings=producer_settings or {},
                    created_at=self._clock(),
                    updated_at=self._clock(),
                )
            )
            self._emit(
                session,
                event_type="show.created",
                show_id=show_id,
                actor=actor,
                public=True,
                payload={"title": title, "selected_games": selected_games},
            )
        return {"show_id": show_id, "status": ShowStatus.CREATED.value}

    def _set_show_status(
        self,
        show_id: str,
        actor: Actor,
        *,
        action: str,
        allowed_from: frozenset[ShowStatus],
        to_status: ShowStatus,
        event_type: str,
        command_id: str | None,
        payload: dict | None = None,
    ) -> dict:
        self._require_authority(actor)
        with session_scope(self._sessions) as session:
            self._claim_command(session, command_id, action, actor, show_id)
            show = self._get_show(session, show_id)
            current = ShowStatus(show.status)
            if current not in allowed_from:
                raise ShowStateError(
                    f"cannot {action} show in status {current.value!r}"
                )
            show.status = to_status.value
            show.updated_at = self._clock()
            self._emit(
                session,
                event_type=event_type,
                show_id=show_id,
                actor=actor,
                public=True,
                payload=payload or {},
            )
        return {"show_id": show_id, "status": to_status.value}

    def start_show(self, show_id: str, actor: Actor, command_id: str | None = None) -> dict:
        return self._set_show_status(
            show_id, actor, action="start", command_id=command_id,
            allowed_from=frozenset({ShowStatus.CREATED}),
            to_status=ShowStatus.LIVE, event_type="show.started",
        )

    def pause_show(self, show_id: str, actor: Actor, command_id: str | None = None) -> dict:
        return self._set_show_status(
            show_id, actor, action="pause", command_id=command_id,
            allowed_from=frozenset({ShowStatus.LIVE}),
            to_status=ShowStatus.PAUSED, event_type="show.paused",
        )

    def resume_show(self, show_id: str, actor: Actor, command_id: str | None = None) -> dict:
        return self._set_show_status(
            show_id, actor, action="resume", command_id=command_id,
            allowed_from=frozenset({ShowStatus.PAUSED}),
            to_status=ShowStatus.LIVE, event_type="show.resumed",
        )

    def end_show(
        self,
        show_id: str,
        actor: Actor,
        reason: str = "normal",
        command_id: str | None = None,
    ) -> dict:
        # Emergency stop is an end with reason="emergency_stop"; producers
        # may end from any non-ended status.
        return self._set_show_status(
            show_id, actor, action="end", command_id=command_id,
            allowed_from=frozenset(
                {ShowStatus.CREATED, ShowStatus.LIVE, ShowStatus.PAUSED}
            ),
            to_status=ShowStatus.ENDED, event_type="show.ended",
            payload={"reason": reason},
        )

    # -- rounds and phase transitions ------------------------------------

    def create_round(
        self,
        show_id: str,
        game_id: str,
        actor: Actor,
        cast: list[dict] | None = None,
        command_id: str | None = None,
    ) -> dict:
        self._require_authority(actor)
        round_id = self._new_id("round")
        with session_scope(self._sessions) as session:
            self._claim_command(session, command_id, "create_round", actor, show_id)
            show = self._get_show(session, show_id)
            if ShowStatus(show.status) is not ShowStatus.LIVE:
                raise ShowStateError(
                    f"cannot create a round while show is {show.status!r}"
                )
            session.add(
                Round(
                    round_id=round_id,
                    show_id=show_id,
                    game_id=game_id,
                    phase=Phase.LOBBY.value,
                    cast=cast or [],
                    data={},
                    created_at=self._clock(),
                    updated_at=self._clock(),
                )
            )
            self._emit(
                session,
                event_type="round.created",
                show_id=show_id,
                round_id=round_id,
                game_id=game_id,
                phase=Phase.LOBBY,
                actor=actor,
                public=True,
            )
        return {"round_id": round_id, "show_id": show_id, "phase": Phase.LOBBY.value}

    def transition_round(
        self,
        round_id: str,
        new_phase: Phase,
        actor: Actor,
        command_id: str | None = None,
        override: bool = False,
        reason: str | None = None,
    ) -> dict:
        """Advance a round's phase.

        Normal path: controller/producer actor + next-phase-only rule (plus
        the game module's own validation when registered). Override path:
        producer only, any transition, always audited with a reason.
        """
        self._require_authority(actor, producer_only=override)
        with session_scope(self._sessions) as session:
            self._claim_command(session, command_id, "transition_round", actor, None)
            round_ = self._get_round(session, round_id)
            show = self._get_show(session, round_.show_id)
            if ShowStatus(show.status) is not ShowStatus.LIVE:
                raise ShowStateError(
                    f"cannot transition round while show is {show.status!r}"
                )
            old_phase = Phase(round_.phase)
            if not override:
                validate_default_transition(old_phase, new_phase)
                module = self._modules.get(round_.game_id)
                if module is not None:
                    module.validate_transition(old_phase, new_phase)
            round_.phase = new_phase.value
            round_.updated_at = self._clock()
            payload = {"from": old_phase.value, "to": new_phase.value}
            if override:
                payload["override"] = True
                payload["reason"] = reason or "unspecified"
            self._emit(
                session,
                event_type=(
                    "round.phase_overridden" if override else "round.phase_changed"
                ),
                show_id=round_.show_id,
                round_id=round_id,
                game_id=round_.game_id,
                phase=new_phase,
                actor=actor,
                public=True,
                payload=payload,
            )
        return {"round_id": round_id, "phase": new_phase.value}

    # -- failure recording (Master Spec section 9) -----------------------

    def record_failure(
        self,
        *,
        show_id: str | None,
        round_id: str | None,
        error_type: str,
        detail: str,
        provider: str | None = None,
        model: str | None = None,
        operation: str | None = None,
        retry_count: int = 0,
        recovery_action: str | None = None,
    ) -> str:
        """Record a technical failure. Raw detail goes to the private
        errors ledger and a private event; public surfaces only ever see
        themed failure states derived elsewhere."""
        error_id = self._new_id("err")
        with session_scope(self._sessions) as session:
            session.add(
                ErrorRecord(
                    error_id=error_id,
                    show_id=show_id,
                    round_id=round_id,
                    provider=provider,
                    model=model,
                    operation=operation,
                    error_type=error_type,
                    detail=detail,
                    retry_count=retry_count,
                    recovery_action=recovery_action,
                    created_at=self._clock(),
                )
            )
            if show_id is not None:
                self._emit(
                    session,
                    event_type="failure.recorded",
                    show_id=show_id,
                    round_id=round_id,
                    actor=CONTROLLER_ACTOR,
                    public=False,
                    payload={
                        "error_id": error_id,
                        "error_type": error_type,
                        "provider": provider,
                        "operation": operation,
                        "retry_count": retry_count,
                        "recovery_action": recovery_action,
                        "detail": detail,
                    },
                )
        return error_id

    # -- read views ------------------------------------------------------

    def get_show(self, show_id: str) -> dict:
        with session_scope(self._sessions) as session:
            show = self._get_show(session, show_id)
            return {
                "show_id": show.show_id,
                "title": show.title,
                "status": show.status,
                "selected_games": show.selected_games,
            }

    def get_round(self, round_id: str) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            return {
                "round_id": round_.round_id,
                "show_id": round_.show_id,
                "game_id": round_.game_id,
                "phase": round_.phase,
            }
