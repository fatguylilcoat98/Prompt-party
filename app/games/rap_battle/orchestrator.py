"""AI Rap Battle round orchestrator (Packet 02).

Turn structure (section 6): recorded opening draw, alternating verse
requests with bounded context (previous 1-3 turns, section 10), verse
validation (direct response, forced words, duplicate bars), judging, and
the 60/40 winner flow. Weapons resolve only through manifest-defined
modifier objects with structured params — never raw prompt injection.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.audience.voting import VotingService
from app.controller.engine import Actor, GameStateController, NotFoundError
from app.events.store import EventStore
from app.games.art_showdown.orchestrator import ActionError
from app.games.rap_battle.module import GAME_ID, RapBattleModule
from app.games.rap_battle.schemas import (
    DEFAULT_BAR_RANGE,
    SPEED_ROUND_BAR_RANGE,
    RapScorecard,
    RapVerse,
    forced_words_missing,
    normalize_word,
)
from app.games.shared.module import RoundRecord
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType, EventEnvelope, new_id, utc_now
from app.persistence.db import session_scope
from app.persistence.models import AIRequestRecord, ErrorRecord, Round
from app.providers.base import Operation, ProviderRequest, RequestStatus
from app.providers.registry import ProviderRegistry

CONTEXT_TURNS = 3  # section 10: send the previous 1-3 turns, not the session
DEFAULT_EXCHANGES = 4  # verses total; boundaries allow 4-6


class RapBattleOrchestrator:
    #: Producer actions callable via POST /rounds/{id}/actions/{action_id}.
    ACTIONS = frozenset({
        "opening_draw", "request_verse", "apply_modifier", "request_scores",
    })

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
        self.module = RapBattleModule()
        self._modifiers = {m.modifier_id: m for m in self.module.manifest.modifiers}

    # -- helpers (mirror art orchestrator conventions) --------------------

    def _get_round(self, session: Session, round_id: str) -> Round:
        round_ = session.get(Round, round_id)
        if round_ is None or round_.game_id != GAME_ID:
            raise NotFoundError(f"rap_battle round {round_id!r} not found")
        return round_

    @staticmethod
    def _require_phase(round_: Round, *phases: Phase) -> None:
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

    def _emit(self, session: Session, round_: Round, event_type: str,
              actor_type: ActorType, actor_id: str, public: bool, payload: dict) -> None:
        self._events.append(
            session,
            EventEnvelope(
                event_id=self._new_id("evt"), event_type=event_type,
                show_id=round_.show_id, round_id=round_.round_id, game_id=GAME_ID,
                phase=Phase(round_.phase), actor_type=actor_type, actor_id=actor_id,
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

    def _update_data(self, session: Session, round_: Round, **changes) -> None:
        round_.data = {**(round_.data or {}), **changes}
        round_.updated_at = self._clock()

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

    def _provider_for(self, participant: dict, operation: Operation):
        preferred = self._providers.get(participant["provider"])
        if preferred.supports(operation):
            return preferred
        return self._providers.first_supporting(operation)

    # -- opening draw (section 6) ----------------------------------------

    def opening_draw(self, round_id: str, actor: Actor, first_rapper_id: str | None = None,
                     exchanges: int = DEFAULT_EXCHANGES) -> dict:
        """Record turn order before any generation. Producer may select the
        opener; otherwise the draw is deterministic from the round id."""
        if not 4 <= exchanges <= 6:
            raise ActionError("battle configuration allows 4-6 exchanges")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.ROUND_INTRO)
            if (round_.data or {}).get("battle"):
                raise ActionError("opening draw already recorded")
            rappers = [p["participant_id"] for p in self._cast(round_, "contestant")]
            if len(rappers) != 2:
                raise ActionError("rap_battle needs exactly two rappers in the cast")
            if first_rapper_id is None:
                first_rapper_id = rappers[
                    int.from_bytes(round_id.encode()[-4:], "big") % 2
                ]
            if first_rapper_id not in rappers:
                raise ActionError(f"{first_rapper_id!r} is not a rapper in this round")
            order = [first_rapper_id] + [r for r in rappers if r != first_rapper_id]
            battle = {
                "order": order, "turn_index": 0, "verses": [],
                "config": {"exchanges": exchanges},
            }
            self._update_data(session, round_, battle=battle)
            self._emit(
                session, round_, "battle.opening_draw",
                actor.actor_type, actor.actor_id, public=True,
                payload={"order": order, "exchanges": exchanges},
            )
        return {"round_id": round_id, "order": order, "exchanges": exchanges}

    # -- weapons (section: controlled power-ups) --------------------------

    ALLOWED_MODIFIER_PARAMS = {
        "rhyme_robbery": "words",
        "topic_bomb": "topic",
        "speed_round": None,
        "mic_feedback": "style",
        "beat_switch": "flow",
    }

    def apply_modifier(self, round_id: str, actor: Actor, modifier_id: str,
                       params: dict | None = None) -> dict:
        """Arm a weapon for the next verse. Only manifest-defined modifier
        objects are accepted, and params are validated per modifier —
        audience/producer choices select approved values; nothing is
        injected raw into model prompts (Packet 02 acceptance 3)."""
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("modifiers resolve through producer approval")
        modifier = self._modifiers.get(modifier_id)
        if modifier is None or modifier_id not in self.ALLOWED_MODIFIER_PARAMS:
            raise ActionError(f"unknown modifier {modifier_id!r}")
        params = params or {}
        cleaned: dict = {}
        if modifier_id == "rhyme_robbery":
            words = params.get("words", [])
            if not (isinstance(words, list) and 1 <= len(words) <= 3):
                raise ActionError("rhyme_robbery needs 1-3 approved words")
            for word in words:
                if not (isinstance(word, str) and word and normalize_word(word) == word.lower()
                        and len(word) <= 20 and " " not in word):
                    raise ActionError(f"forced word {word!r} is not a single safe word")
            cleaned["words"] = words
        elif modifier_id == "topic_bomb":
            topic = params.get("topic", "")
            if not (isinstance(topic, str) and 0 < len(topic) <= 60):
                raise ActionError("topic_bomb needs an approved topic under 60 chars")
            cleaned["topic"] = topic
        elif modifier_id in {"mic_feedback", "beat_switch"}:
            key = self.ALLOWED_MODIFIER_PARAMS[modifier_id]
            value = params.get(key, "")
            approved = {"whisper", "opera", "double_time", "half_time", "spoken_word"}
            if value not in approved:
                raise ActionError(f"{modifier_id} requires an approved style enum")
            cleaned[key] = value

        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, *modifier.allowed_phases)
            if (round_.data or {}).get("active_modifier"):
                raise ActionError("one active power-up at a time")
            self._update_data(
                session, round_,
                active_modifier={"modifier_id": modifier_id, "params": cleaned},
            )
            self._emit(
                session, round_, "modifier.applied",
                actor.actor_type, actor.actor_id, public=True,
                payload={"modifier_id": modifier_id, "params": cleaned},
            )
        return {"round_id": round_id, "modifier_id": modifier_id, "params": cleaned}

    # -- verses (sections 6, 7, 10) ---------------------------------------

    async def request_verse(self, round_id: str, actor: Actor) -> dict:
        published: dict | None = None
        recovery: dict | None = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            battle = (round_.data or {}).get("battle")
            if not battle:
                raise ActionError("no opening draw recorded; run opening_draw first")
            if not round_.locked_prompt:
                raise ActionError("no locked battle topic")
            verses = battle["verses"]
            if len(verses) >= battle["config"]["exchanges"]:
                raise ActionError("configured exchange count reached")

            order = battle["order"]
            rapper_id = order[battle["turn_index"] % 2]
            rapper = next(
                p for p in self._cast(round_, "contestant")
                if p["participant_id"] == rapper_id
            )
            opponent_verses = [v for v in verses if v["rapper_id"] != rapper_id]
            opponent_last = (
                " / ".join(opponent_verses[-1]["verse"]["bars"][:2])
                if opponent_verses else ""
            )
            recent_turns = [
                {"rapper_id": v["rapper_id"], "bars": v["verse"]["bars"][:2]}
                for v in verses[-CONTEXT_TURNS:]
            ]
            modifier = (round_.data or {}).get("active_modifier") or {}
            forced_words = (
                modifier.get("params", {}).get("words", [])
                if modifier.get("modifier_id") == "rhyme_robbery" else []
            )
            bar_range = (
                SPEED_ROUND_BAR_RANGE
                if modifier.get("modifier_id") == "speed_round" else DEFAULT_BAR_RANGE
            )

            verse, request, response, parse_status, retries, flags = await self._verse_for(
                rapper, round_, opponent_last, recent_turns, forced_words, bar_range,
                previous_bars={bar for v in verses for bar in v["verse"]["bars"]},
                turn_index=len(verses),
            )
            self._record_ai_call(session, round_id, request, response, parse_status, retries)

            if verse is None:
                # Failure-as-content (section: failure recovery): record the
                # real failure, publish an honest in-character recovery, and
                # pass the mic.
                self._record_failure(
                    session, round_, error_type="verse_failed",
                    detail=parse_status or response.error_detail or "",
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="recovery_line_and_pass_mic",
                )
                recovery = {
                    "rapper_id": rapper_id,
                    "line": f"{rapper.get('display_name', rapper_id)} lost the beat "
                            "and waves the crowd on — no verse this turn.",
                }
                battle = {**battle, "turn_index": battle["turn_index"] + 1}
                self._update_data(session, round_, battle=battle)
                self._emit(
                    session, round_, "verse.recovery",
                    ActorType.AI, rapper_id, public=True, payload=recovery,
                )
            else:
                entry = {
                    "rapper_id": rapper_id,
                    "verse": verse.model_dump(),
                    "flags": flags,
                    "turn": len(verses),
                }
                battle = {
                    **battle,
                    "verses": verses + [entry],
                    "turn_index": battle["turn_index"] + 1,
                }
                changes = {"battle": battle}
                if modifier:
                    changes["active_modifier"] = None  # weapons last one verse
                self._update_data(session, round_, **changes)
                self._emit(
                    session, round_, "verse.published",
                    ActorType.AI, rapper_id, public=True,
                    payload={"verse": verse.model_dump(), "flags": flags, "turn": entry["turn"]},
                )
                published = entry
        if published is not None:
            return {"round_id": round_id, **published}
        return {"round_id": round_id, "recovery": recovery}

    async def _verse_for(self, rapper: dict, round_: Round, opponent_last: str,
                         recent_turns: list[dict], forced_words: list[str],
                         bar_range: tuple[int, int], previous_bars: set[str],
                         turn_index: int):
        provider = self._provider_for(rapper, Operation.TEXT_GENERATION)
        retries = 0
        request = response = None
        flags: dict = {}
        last_error = "provider_failed"
        for attempt in range(2):  # one repair/revision (section 7 + failure table)
            request = ProviderRequest(
                request_id=self._new_id("req"),
                participant_id=rapper["participant_id"],
                operation=Operation.TEXT_GENERATION,
                template_id="rap_verse",
                template_version=self.module.manifest.prompts["rap_verse"],
                system_prompt=f"You are {rapper.get('display_name')} in a playful AI rap battle.",
                user_prompt=(
                    f"Topic: {round_.locked_prompt}\n"
                    f"Opponent's last verse: {opponent_last or 'none'}\n"
                    f"Write {bar_range[0]}-{bar_range[1]} bar-equivalent lines. "
                    "Answer the opponent when a prior verse exists. Honor all "
                    "approved forced words. Return JSON only."
                ),
                params={
                    "persona_id": rapper.get("persona_id", "rapper"),
                    "locked_topic": round_.locked_prompt or "",
                    "opponent_last": opponent_last,
                    "recent_turns": json.dumps(recent_turns),
                    "forced_words": json.dumps(forced_words),
                    "bar_cap": str(bar_range[0]),
                    "turn_index": str(turn_index),
                    "attempt": str(attempt),
                },
                timeout_seconds=rapper.get("timeout_seconds", 45),
            )
            response = await provider.generate(request)
            retries = attempt
            if response.status is not RequestStatus.OK:
                last_error = response.error_detail or "provider_failed"
                continue
            try:
                verse = RapVerse.model_validate_json(response.text)
            except Exception as exc:
                last_error = f"invalid_verse: {exc}"
                continue
            if not bar_range[0] <= len(verse.bars) <= bar_range[1]:
                last_error = (
                    f"bar_count_{len(verse.bars)}_outside_{bar_range[0]}_{bar_range[1]}"
                )
                continue
            if verse.safety_self_check != "pass":
                last_error = "safety_self_check_failed"
                continue
            if opponent_last and not verse.response_to.strip():
                # Direct response required after the opening verse.
                last_error = "missing_direct_response"
                continue
            duplicates = [bar for bar in verse.bars if bar in previous_bars]
            if duplicates:
                # Exact duplicate bars are rejected and regenerated once.
                last_error = f"duplicate_bars: {duplicates[:2]}"
                continue
            missing = forced_words_missing(verse.bars, forced_words)
            if missing:
                if attempt == 0:
                    last_error = f"forced_words_missing: {missing}"
                    continue
                # After the one revision: publish honestly flagged; judges
                # may deduct craft (failure table: no false compliance claim).
                flags["forced_word_miss"] = missing
                verse = verse.model_copy(
                    update={"forced_words_used": [
                        w for w in forced_words if w not in missing
                    ]}
                )
            return verse, request, response, "ok", retries, flags
        return None, request, response, last_error, retries, flags

    # -- judging (section 9) ----------------------------------------------

    async def request_scores(self, round_id: str, actor: Actor, judge_id: str) -> dict:
        card = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.FINAL_JUDGING)
            judges = {j["participant_id"]: j for j in self._cast(round_, "commentator")}
            if judge_id not in judges:
                raise ActionError(f"{judge_id!r} is not a judge in this round")
            battle = (round_.data or {}).get("battle") or {}
            if not battle.get("verses"):
                raise ActionError("no verses to judge")
            judge = judges[judge_id]
            rapper_ids = sorted({v["rapper_id"] for v in battle["verses"]})

            provider = self._provider_for(judge, Operation.TEXT_GENERATION)
            retries = 0
            request = response = None
            parse_status = "provider_failed"
            for attempt in range(2):
                request = ProviderRequest(
                    request_id=self._new_id("req"),
                    participant_id=judge_id,
                    operation=Operation.TEXT_GENERATION,
                    template_id="rap_judge_scorecard",
                    template_version=self.module.manifest.prompts["rap_judge_scorecard"],
                    system_prompt=f"You are {judge.get('display_name')} judging an AI rap battle.",
                    user_prompt=(
                        f"Topic: {round_.locked_prompt}\n"
                        "Score wordplay, aggression, entertainment as integers 1-10; "
                        "totals must equal sums. Return JSON only."
                    ),
                    params={
                        "persona_id": judge.get("persona_id", "judge"),
                        "rapper_ids": json.dumps(rapper_ids),
                        "attempt": str(attempt),
                    },
                    timeout_seconds=judge.get("timeout_seconds", 60),
                )
                response = await provider.generate(request)
                retries = attempt
                if response.status is not RequestStatus.OK:
                    continue
                try:
                    card = RapScorecard.model_validate_json(response.text)
                    parse_status = "ok"
                    break
                except Exception as exc:
                    parse_status = f"invalid_scorecard: {exc}"
            self._record_ai_call(session, round_id, request, response, parse_status, retries)
            if card is None:
                self._record_failure(
                    session, round_, error_type="scorecard_failed", detail=parse_status,
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="reweight_remaining_valid_judges",
                )
            else:
                judge_scores = dict((round_.data or {}).get("judge_scores", {}))
                judge_scores[judge_id] = card.model_dump()
                self._update_data(session, round_, judge_scores=judge_scores)
                self._emit(
                    session, round_, "judge.scorecard_created",
                    ActorType.AI, judge_id, public=True,
                    payload={"scorecard": card.model_dump()},
                )
        if card is None:
            raise ActionError("scorecard failed; remaining judges are reweighted")
        return {"round_id": round_id, "judge_id": judge_id, "scorecard": card.model_dump()}

    # -- winner and replay (mirrors art flow) ------------------------------

    def calculate_winner(self, round_id: str, actor: Actor, voting: VotingService) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            data = round_.data or {}
            vote_state = data.get("voting", {})
            if vote_state.get("status") != "closed":
                raise ActionError("voting must be closed before calculating the winner")
            record = RoundRecord(
                round_id=round_id, show_id=round_.show_id, game_id=GAME_ID,
                phase=Phase(round_.phase), locked_prompt=round_.locked_prompt,
                votes=vote_state.get("tally", {}),
                judge_scores=data.get("judge_scores", {}),
            )
            score = self.module.calculate_score(record)
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

    def replay(self, round_id: str) -> dict:
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
        battle = data.get("battle") or {}
        return {
            "round_id": round_id, "game_id": GAME_ID, "phase": round_.phase,
            "locked_prompt": round_.locked_prompt, "cast": round_.cast,
            "order": battle.get("order", []),
            "verses": battle.get("verses", []),
            "judge_scores": data.get("judge_scores", {}),
            "votes": data.get("voting", {}).get("tally", {}),
            "result": round_.result, "failures": failures, "timeline": timeline,
        }
