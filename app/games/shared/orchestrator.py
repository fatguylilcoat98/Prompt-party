"""Base game orchestrator: shared helpers every game module's flow uses.

Orchestrators execute AI actions *within* phases; advancing the official
phase remains a controller/producer action (Authority rules). Failures are
recorded in-transaction with raw detail kept private (Master Spec
section 9).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.audience.voting import VotingService
from app.controller.engine import Actor, GameStateController, NotFoundError
from app.events.store import EventStore
from app.games.shared.module import RoundRecord
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType, EventEnvelope, new_id, utc_now
from app.persistence.db import session_scope
from app.persistence.models import AIRequestRecord, ErrorRecord, Round
from app.providers.base import Operation, ProviderRequest
from app.providers.registry import ProviderRegistry


class ActionError(ValueError):
    """Game action rejected (wrong phase, missing prerequisite, limits)."""


class BaseGameOrchestrator:
    #: Producer actions callable via POST /rounds/{id}/actions/{action_id}.
    ACTIONS: frozenset[str] = frozenset()

    #: Game module instance; subclasses set this in __init__ before super().
    module = None

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_store: EventStore,
        providers: ProviderRegistry,
        controller: GameStateController,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._sessions = session_factory
        self._events = event_store
        self._providers = providers
        self._controller = controller
        self._clock = clock
        self._new_id = id_factory

    @property
    def game_id(self) -> str:
        return self.module.manifest.game_id

    # -- round access ----------------------------------------------------

    def _get_round(self, session: Session, round_id: str) -> Round:
        round_ = session.get(Round, round_id)
        if round_ is None or round_.game_id != self.game_id:
            raise NotFoundError(f"{self.game_id} round {round_id!r} not found")
        return round_

    def _require_phase(self, round_: Round, *phases: Phase) -> None:
        if Phase(round_.phase) not in phases:
            allowed = "/".join(p.value for p in phases)
            raise ActionError(
                f"action requires phase {allowed} (round is in {round_.phase})"
            )

    @staticmethod
    def _cast(round_: Round, seat_type: str) -> list[dict]:
        return [
            p for p in (round_.cast or [])
            if p.get("seat_type") == seat_type and p.get("enabled", True)
        ]

    def _update_data(self, session: Session, round_: Round, **changes) -> None:
        round_.data = {**(round_.data or {}), **changes}
        round_.updated_at = self._clock()

    # -- events, AI-call ledger, failures --------------------------------

    def _emit(self, session: Session, round_: Round, event_type: str,
              actor_type: ActorType, actor_id: str, public: bool, payload: dict) -> None:
        self._events.append(
            session,
            EventEnvelope(
                event_id=self._new_id("evt"), event_type=event_type,
                show_id=round_.show_id, round_id=round_.round_id,
                game_id=self.game_id, phase=Phase(round_.phase),
                actor_type=actor_type, actor_id=actor_id,
                timestamp=self._clock(), public=public, payload=payload,
            ),
        )

    def _record_ai_call(self, session: Session, round_id: str, request: ProviderRequest,
                        response, parse_status: str | None, retry_count: int) -> None:
        session.add(
            AIRequestRecord(
                request_id=request.request_id, round_id=round_id,
                participant_id=request.participant_id, operation=request.operation.value,
                template_id=request.template_id, template_version=request.template_version,
                provider=response.provider, model=response.model,
                status=response.status.value, parse_status=parse_status,
                latency_ms=response.latency_ms, usage=response.usage,
                retry_count=retry_count, created_at=self._clock(),
            )
        )

    def _record_failure(self, session: Session, round_: Round, *, error_type: str,
                        detail: str, provider: str | None = None, model: str | None = None,
                        operation: str | None = None, retry_count: int = 0,
                        recovery_action: str | None = None) -> str:
        error_id = self._new_id("err")
        session.add(
            ErrorRecord(
                error_id=error_id, show_id=round_.show_id, round_id=round_.round_id,
                provider=provider, model=model, operation=operation,
                error_type=error_type, detail=detail, retry_count=retry_count,
                recovery_action=recovery_action, created_at=self._clock(),
            )
        )
        self._emit(
            session, round_, "failure.recorded",
            ActorType.CONTROLLER, "controller", public=False,
            payload={"error_id": error_id, "error_type": error_type,
                     "operation": operation, "retry_count": retry_count,
                     "recovery_action": recovery_action, "detail": detail},
        )
        return error_id

    # -- providers -------------------------------------------------------

    def _provider_for(self, participant: dict, operation: Operation):
        """Use the participant's provider when it supports the operation,
        else the first registered provider that does (see README
        deviations)."""
        preferred = self._providers.get(participant["provider"])
        if preferred.supports(operation):
            return preferred
        return self._providers.first_supporting(operation)

    # -- winner / override / replay skeleton ------------------------------

    def _round_record(self, round_: Round) -> RoundRecord:
        data = round_.data or {}
        return RoundRecord(
            round_id=round_.round_id, show_id=round_.show_id, game_id=self.game_id,
            phase=Phase(round_.phase), locked_prompt=round_.locked_prompt,
            votes=data.get("voting", {}).get("tally", {}),
            judge_scores=data.get("judge_scores", {}),
        )

    def calculate_winner(self, round_id: str, actor: Actor, voting: VotingService) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            vote_state = (round_.data or {}).get("voting", {})
            if vote_state.get("status") != "closed":
                raise ActionError("voting must be closed before calculating the winner")
            score = self.module.calculate_score(self._round_record(round_))
            result = {
                "winner_id": score.winner_id, "is_draw": score.is_draw,
                "components": score.components, "tie_break_used": score.tie_break_used,
                "official": True,
            }
            round_.result = result
            round_.updated_at = self._clock()
            self._emit(
                session, round_, "round.winner_calculated",
                actor.actor_type, actor.actor_id, public=True, payload=result,
            )
        return {"round_id": round_id, **result}

    def override_winner(self, round_id: str, actor: Actor, winner_id: str, reason: str) -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("winner override is producer-only")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            previous = round_.result or {}
            round_.result = {
                **previous, "winner_id": winner_id, "is_draw": False,
                "overridden": True, "override_reason": reason, "official": True,
            }
            round_.updated_at = self._clock()
            self._emit(
                session, round_, "round.winner_overridden",
                actor.actor_type, actor.actor_id, public=True,
                payload={"winner_id": winner_id, "reason": reason,
                         "previous_winner_id": previous.get("winner_id")},
            )
        return {"round_id": round_id, "winner_id": winner_id, "overridden": True}

    def _replay_base(self, round_id: str) -> tuple[dict, dict]:
        """Common public-safe replay skeleton; returns (replay, round data)
        for the game to extend with its own outputs."""
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            data = round_.data or {}
            failures = [
                {
                    "error_type": e.error_type, "operation": e.operation,
                    "retry_count": e.retry_count, "recovery_action": e.recovery_action,
                }
                for e in session.query(ErrorRecord)
                .filter(ErrorRecord.round_id == round_id)
                .order_by(ErrorRecord.created_at)
                .all()
            ]
            timeline = [
                {"seq": s.seq, "event_type": s.envelope.event_type,
                 "actor_id": s.envelope.actor_id, "payload": s.envelope.payload}
                for s in self._events.list_events(round_.show_id, public_only=True)
                if s.envelope.round_id == round_id
            ]
        replay = {
            "round_id": round_id, "game_id": self.game_id, "phase": round_.phase,
            "locked_prompt": round_.locked_prompt, "cast": round_.cast,
            "votes": data.get("voting", {}).get("tally", {}),
            "result": round_.result, "failures": failures, "timeline": timeline,
        }
        return replay, data
