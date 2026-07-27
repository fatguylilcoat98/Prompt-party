"""AI Improv round orchestrator (Packet 04).

The controller owns the scene-state ledger: dialogue proposes updates,
the producer validates them into official state (STATE RULE), facts never
disappear, twists resolve through approved modifier objects, and only
the host/producer rings the bell or ends the scene.
"""

from __future__ import annotations

import copy
import json

from app.controller.engine import Actor
from app.games.improv.module import ImprovModule
from app.games.improv.schemas import (
    ImprovTurn,
    ProposedUpdate,
    SceneCharacter,
    SceneFact,
    SceneState,
    yes_and_recommendations,
)
from app.games.shared.orchestrator import ActionError, BaseGameOrchestrator
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType
from app.persistence.db import session_scope
from app.persistence.models import Round
from app.providers.base import Operation, ProviderRequest, RequestStatus

MAX_EXCHANGES = 12
CONTEXT_LINES = 3


class ImprovOrchestrator(BaseGameOrchestrator):
    ACTIONS = frozenset({
        "setup_scene", "request_line", "apply_state_updates", "apply_modifier",
        "ring_bell", "end_scene", "host_recap",
    })

    def __init__(self, *args, **kwargs) -> None:
        self.module = ImprovModule()
        super().__init__(*args, **kwargs)
        self._modifiers = {m.modifier_id: m for m in self.module.manifest.modifiers}

    # -- state access -----------------------------------------------------

    @staticmethod
    def _scene(round_: Round) -> SceneState:
        raw = (round_.data or {}).get("scene")
        if not raw:
            raise ActionError("no scene; run setup_scene first")
        return SceneState.model_validate(raw)

    @staticmethod
    def _performance(round_: Round) -> dict:
        perf = (round_.data or {}).get("performance")
        if not perf:
            raise ActionError("scene has not started")
        return copy.deepcopy(perf)

    # -- setup (section 5) ------------------------------------------------

    def setup_scene(self, round_id: str, actor: Actor, location: str, genre: str,
                    characters: list[dict]) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.ROUND_INTRO)
            if not round_.locked_prompt:
                raise ActionError("no locked scene suggestion; lock a submission first")
            if (round_.data or {}).get("scene"):
                raise ActionError("scene already set up")
            player_ids = {p["participant_id"] for p in self._cast(round_, "contestant")}
            cast_chars = []
            for c in characters:
                if c.get("participant_id") not in player_ids:
                    raise ActionError(
                        f"character assigned to unknown player {c.get('participant_id')!r}"
                    )
                cast_chars.append(SceneCharacter.model_validate(c))
            scene = SceneState(
                scene_id=self._new_id("scene"),
                location=location, genre=genre, characters=cast_chars,
            )
            performance = {
                "turns": [], "ended": False, "bells": [],
                "assignments": {c.name: c.participant_id for c in cast_chars},
                "twist": None, "repair_for": None, "genre_shift": None,
            }
            self._update_data(session, round_, scene=scene.model_dump(),
                              performance=performance)
            self._emit(
                session, round_, "scene.setup",
                actor.actor_type, actor.actor_id, public=True,
                payload={"scene": scene.compact()},
            )
        return {"round_id": round_id, "scene": scene.model_dump()}

    # -- scene play (sections 6, 7) ----------------------------------------

    async def request_line(self, round_id: str, actor: Actor) -> dict:
        entry = None
        timeout_entry = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            scene = self._scene(round_)
            perf = self._performance(round_)
            if perf["ended"]:
                raise ActionError("the scene has ended")
            if len(perf["turns"]) >= MAX_EXCHANGES:
                raise ActionError("maximum exchanges reached; end the scene")

            characters = scene.characters
            if perf["repair_for"]:
                character = next(c for c in characters if c.name == perf["repair_for"])
                is_repair = True
            else:
                character = characters[len(perf["turns"]) % len(characters)]
                is_repair = False
            participant_id = perf["assignments"][character.name]
            participant = next(
                p for p in self._cast(round_, "contestant")
                if p["participant_id"] == participant_id
            )

            effective_genre = (
                perf["genre_shift"]["genre"]
                if perf["genre_shift"] and perf["genre_shift"]["turns_left"] > 0
                else scene.genre
            )
            compact = {**scene.compact(), "genre": effective_genre}
            recent = [
                {"character": t["character"], "line": t["turn"]["spoken_line"]}
                for t in perf["turns"][-CONTEXT_LINES:]
            ]
            twist = perf["twist"]["instruction"] if perf["twist"] else ""
            last_offer = (
                perf["turns"][-1]["turn"]["new_offer"] if perf["turns"] else ""
            )

            provider = self._provider_for(participant, Operation.TEXT_GENERATION)
            turn = request = response = None
            parse_status = "provider_failed"
            retries = 0
            for attempt in range(2):
                request = ProviderRequest(
                    request_id=self._new_id("req"),
                    participant_id=participant_id,
                    operation=Operation.TEXT_GENERATION,
                    template_id="improv_turn",
                    template_version=self.module.manifest.prompts["improv_turn"],
                    system_prompt=f"You are {character.name} in an AI improv scene.",
                    user_prompt=(
                        f"Scene state: {json.dumps(compact)}\n"
                        f"Recent dialogue: {json.dumps(recent)}\n"
                        f"Active twist: {twist or 'none'}\n"
                        "Respond in character in 1-3 sentences. Accept the previous "
                        "offer, add one playable detail, reference established "
                        "reality. Return JSON."
                    ),
                    params={
                        "character_name": character.name,
                        "scene_state": json.dumps(compact),
                        "last_offer": last_offer,
                        "twist": twist,
                        "turn_index": str(len(perf["turns"])),
                        "attempt": str(attempt),
                    },
                    timeout_seconds=participant.get("timeout_seconds", 30),
                )
                response = await provider.generate(request)
                retries = attempt
                if response.status is not RequestStatus.OK:
                    parse_status = response.error_detail or "provider_failed"
                    continue
                try:
                    turn = ImprovTurn.model_validate_json(response.text)
                    parse_status = "ok"
                    break
                except Exception as exc:
                    parse_status = f"invalid_turn: {exc}"
            self._record_ai_call(session, round_id, request, response, parse_status, retries)

            if turn is None:
                # Timeout recovery never fabricates dialogue (acceptance 9):
                # the character freezes; the host narrates the absence.
                self._record_failure(
                    session, round_, error_type="line_failed", detail=parse_status,
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="character_freezes_no_fabricated_line",
                )
                timeout_entry = {
                    "character": character.name,
                    "note": f"{character.name} freezes dramatically; the scene continues.",
                }
                self._emit(
                    session, round_, "scene.player_frozen",
                    ActorType.CONTROLLER, "controller", public=True,
                    payload=timeout_entry,
                )
            else:
                recommendations = yes_and_recommendations(
                    turn,
                    has_prior_line=bool(perf["turns"]),
                    has_facts=bool(scene.established_facts),
                )
                entry = {
                    "turn_id": f"turn_{len(perf['turns'])}",
                    "character": character.name,
                    "participant_id": participant_id,
                    "turn": turn.model_dump(),
                    "twist_applied": twist or None,
                    "updates_applied": False,
                    "repair": is_repair,
                    "violation": None,
                }
                perf["turns"] = perf["turns"] + [entry]
                if is_repair:
                    perf["repair_for"] = None
                if perf["twist"]:
                    perf["twist"] = None  # twist consumed by this line
                if perf["genre_shift"] and perf["genre_shift"]["turns_left"] > 0:
                    perf["genre_shift"]["turns_left"] -= 1
                self._update_data(session, round_, performance=perf)
                self._emit(
                    session, round_, "scene.line",
                    ActorType.AI, participant_id, public=True, payload=entry,
                )
                if recommendations:
                    # Advisory only: the producer/host decides on the bell.
                    self._emit(
                        session, round_, "scene.rule_recommendation",
                        ActorType.CONTROLLER, "controller", public=False,
                        payload={"turn_id": entry["turn_id"],
                                 "recommendations": recommendations},
                    )
        if timeout_entry is not None:
            return {"round_id": round_id, "frozen": timeout_entry}
        return {"round_id": round_id, **entry}

    def apply_state_updates(self, round_id: str, actor: Actor, turn_id: str,
                            approved_indexes: list[int] | None = None) -> dict:
        """Producer validation makes proposed updates official (STATE RULE,
        acceptance 2). Facts are append-only (acceptance 3)."""
        applied = []
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            scene = self._scene(round_)
            perf = self._performance(round_)
            entry = next((t for t in perf["turns"] if t["turn_id"] == turn_id), None)
            if entry is None:
                raise ActionError(f"no turn {turn_id!r}")
            if entry["updates_applied"]:
                raise ActionError("updates for this turn were already validated")
            proposals = [
                ProposedUpdate.model_validate(u)
                for u in entry["turn"]["proposed_state_updates"]
            ]
            chosen = (
                proposals if approved_indexes is None
                else [proposals[i] for i in approved_indexes if 0 <= i < len(proposals)]
            )
            for update in chosen:
                if update.type == "fact":
                    scene.established_facts.append(
                        SceneFact(
                            fact_id=f"fact_{len(scene.established_facts) + 1}",
                            text=update.value,
                        )
                    )
                elif update.type == "object":
                    scene.active_objects.append(update.value)
                elif update.type == "relationship":
                    scene.relationships.append(update.value)
                elif update.type == "thread":
                    scene.unresolved_threads.append(update.value)
                elif update.type == "emotion":
                    name, _, emotion = update.value.partition(":")
                    for character in scene.characters:
                        if character.name == name.strip():
                            character.emotion = emotion.strip() or character.emotion
                elif update.type == "time":
                    scene.established_facts.append(
                        SceneFact(
                            fact_id=f"fact_{len(scene.established_facts) + 1}",
                            text=f"Time anchor: {update.value}",
                        )
                    )
                applied.append(update.model_dump())
            entry["updates_applied"] = True
            scene.turn_index = len(perf["turns"])
            self._update_data(session, round_, scene=scene.model_dump(), performance=perf)
            self._emit(
                session, round_, "scene.state_updated",
                actor.actor_type, actor.actor_id, public=True,
                payload={"turn_id": turn_id, "applied": applied},
            )
        return {"round_id": round_id, "turn_id": turn_id, "applied": applied}

    # -- twists and modifiers ----------------------------------------------

    def apply_modifier(self, round_id: str, actor: Actor, modifier_id: str,
                       params: dict | None = None) -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("modifiers resolve through producer approval")
        modifier = self._modifiers.get(modifier_id)
        if modifier is None:
            raise ActionError(f"unknown modifier {modifier_id!r}")
        params = params or {}
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, *modifier.allowed_phases)
            scene = self._scene(round_)
            perf = self._performance(round_)
            if perf["ended"]:
                raise ActionError("scene is ending; modifier rejected")
            if perf["twist"] is not None:
                raise ActionError("one active power-up at a time")
            payload: dict = {"modifier_id": modifier_id}

            if modifier_id == "freeze_replace":
                name = params.get("character_name", "")
                new_player = params.get("new_participant_id", "")
                if name not in perf["assignments"]:
                    raise ActionError(f"unknown character {name!r}")
                players = {p["participant_id"] for p in self._cast(round_, "contestant")}
                if new_player not in players:
                    raise ActionError(f"unknown player {new_player!r}")
                # Swap takes effect at the next turn boundary (acceptance 6).
                perf["assignments"] = {**perf["assignments"], name: new_player}
                payload["assignment"] = {name: new_player}
            elif modifier_id == "genre_shift":
                genre = params.get("genre", "")
                if not genre or len(genre) > 60:
                    raise ActionError("genre_shift needs an approved genre under 60 chars")
                # Genre changes; established facts remain true (acceptance 7).
                perf["genre_shift"] = {"genre": genre, "turns_left": 2}
                perf["twist"] = {"modifier_id": modifier_id,
                                 "instruction": f"genre shift: {genre}"}
                payload["genre"] = genre
            elif modifier_id == "object_endowment":
                obj = params.get("object", "")
                if not obj or len(obj) > 60:
                    raise ActionError("object_endowment needs an approved object")
                scene.active_objects.append(obj)
                perf["twist"] = {"modifier_id": modifier_id,
                                 "instruction": f"the {obj} is now real"}
                payload["object"] = obj
            elif modifier_id == "emotion_injection":
                name = params.get("character_name", "")
                emotion = params.get("emotion", "")
                if name not in perf["assignments"] or not emotion:
                    raise ActionError("emotion_injection needs a character and emotion")
                for character in scene.characters:
                    if character.name == name:
                        character.emotion = emotion
                perf["twist"] = {"modifier_id": modifier_id,
                                 "instruction": f"{name} feels {emotion}"}
                payload["emotion"] = {name: emotion}
            elif modifier_id == "time_jump":
                interval = params.get("interval", "")
                if not interval or len(interval) > 60:
                    raise ActionError("time_jump needs an approved interval")
                scene.established_facts.append(
                    SceneFact(
                        fact_id=f"fact_{len(scene.established_facts) + 1}",
                        text=f"Time anchor: {interval}",
                    )
                )
                perf["twist"] = {"modifier_id": modifier_id,
                                 "instruction": f"time jump: {interval}"}
                payload["interval"] = interval
            elif modifier_id == "one_word":
                perf["twist"] = {"modifier_id": modifier_id,
                                 "instruction": "one word per line"}
            elif modifier_id == "scene_hijack":
                new_player = params.get("participant_id", "")
                on_stage = set(perf["assignments"].values())
                players = {p["participant_id"] for p in self._cast(round_, "contestant")}
                if new_player not in players - on_stage:
                    raise ActionError("scene_hijack requires an available off-stage player")
                name = params.get("name", f"Hijacker {new_player}")
                scene.characters.append(SceneCharacter(
                    participant_id=new_player, name=name,
                    role=params.get("role", "uninvited guest"), emotion="charged",
                ))
                perf["assignments"] = {**perf["assignments"], name: new_player}
                payload["new_character"] = name

            self._update_data(session, round_, scene=scene.model_dump(), performance=perf)
            self._emit(
                session, round_, "modifier.applied",
                actor.actor_type, actor.actor_id, public=True, payload=payload,
            )
        return {"round_id": round_id, **payload}

    # -- host controls (section 8, acceptance 5) ---------------------------

    def ring_bell(self, round_id: str, actor: Actor, turn_id: str, violation: str,
                  note: str = "") -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("only the host/producer rings the bell")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            perf = self._performance(round_)
            entry = next((t for t in perf["turns"] if t["turn_id"] == turn_id), None)
            if entry is None:
                raise ActionError(f"no turn {turn_id!r}")
            entry["violation"] = {"violation": violation, "note": note[:200]}
            perf["bells"] = perf["bells"] + [{"turn_id": turn_id, "violation": violation}]
            perf["repair_for"] = entry["character"]
            self._update_data(session, round_, performance=perf)
            self._emit(
                session, round_, "scene.bell",
                actor.actor_type, actor.actor_id, public=True,
                payload={"turn_id": turn_id, "violation": violation, "note": note[:200]},
            )
        return {"round_id": round_id, "turn_id": turn_id, "repair_for": entry["character"]}

    def end_scene(self, round_id: str, actor: Actor, reason: str = "button_found") -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("only the host/producer ends the scene")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            perf = self._performance(round_)
            perf["ended"] = True
            self._update_data(session, round_, performance=perf)
            self._emit(
                session, round_, "scene.ended",
                actor.actor_type, actor.actor_id, public=True,
                payload={"reason": reason},
            )
        return {"round_id": round_id, "ended": True}

    async def host_recap(self, round_id: str, actor: Actor) -> dict:
        """Host recap built only from established scene facts."""
        recap = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            scene = self._scene(round_)
            hosts = self._cast(round_, "host")
            if not hosts:
                raise ActionError("no AI host in the cast; producer recaps manually")
            host = hosts[0]
            provider = self._provider_for(host, Operation.TEXT_GENERATION)
            request = ProviderRequest(
                request_id=self._new_id("req"),
                participant_id=host["participant_id"],
                operation=Operation.TEXT_GENERATION,
                template_id="improv_recap",
                template_version=self.module.manifest.prompts["improv_recap"],
                system_prompt="You are the improv host. Recap only established facts.",
                user_prompt=f"Scene state: {json.dumps(scene.compact())}",
                params={"scene_state": json.dumps(scene.compact())},
                timeout_seconds=host.get("timeout_seconds", 30),
            )
            response = await provider.generate(request)
            self._record_ai_call(
                session, round_id, request, response,
                "ok" if response.status is RequestStatus.OK else response.error_detail, 0,
            )
            if response.status is not RequestStatus.OK:
                raise ActionError("host recap failed; producer recaps manually")
            recap = json.loads(response.text)["spoken_line"]
            self._emit(
                session, round_, "host.recap",
                ActorType.AI, host["participant_id"], public=True,
                payload={"recap": recap},
            )
        return {"round_id": round_id, "recap": recap}

    # -- replay -----------------------------------------------------------

    def replay(self, round_id: str) -> dict:
        replay, data = self._replay_base(round_id)
        perf = data.get("performance", {})
        return {
            **replay,
            "scene": data.get("scene"),
            "turns": perf.get("turns", []),
            "bells": perf.get("bells", []),
            "ended": perf.get("ended", False),
        }
