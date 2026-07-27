"""AI Roast Battle round orchestrator (Packet 05).

Every roast passes the moderation pipeline (section 7): target validated
against the approved roster, output moderation independent of the
performer persona, block/repair-once/publish, rejected output stored
privately and never shown. No chaos card can touch moderation — there is
structurally no code path from a modifier to the moderation gate.
"""

from __future__ import annotations

import copy
import json

from app.controller.engine import Actor
from app.games.roast_battle.module import RoastBattleModule
from app.games.roast_battle.schemas import (
    APPROVED_STYLES,
    ESCALATION,
    RoastScorecard,
    RoastTarget,
    RoastTurn,
    TargetType,
    moderate_roast,
)
from app.games.shared.orchestrator import ActionError, BaseGameOrchestrator
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType
from app.persistence.db import session_scope
from app.persistence.models import Round
from app.providers.base import Operation, ProviderRequest, RequestStatus

DEFAULT_EXCHANGES = 4


class RoastBattleOrchestrator(BaseGameOrchestrator):
    ACTIONS = frozenset({
        "setup_battle", "request_roast", "apply_modifier", "request_scores",
    })

    def __init__(self, *args, **kwargs) -> None:
        self.module = RoastBattleModule()
        super().__init__(*args, **kwargs)
        self._modifiers = {m.modifier_id: m for m in self.module.manifest.modifiers}

    @staticmethod
    def _battle(round_: Round) -> dict:
        battle = (round_.data or {}).get("battle")
        if not battle:
            raise ActionError("no battle; run setup_battle first")
        return copy.deepcopy(battle)

    # -- setup (sections 2, 5) --------------------------------------------

    def setup_battle(self, round_id: str, actor: Actor, roster: list[dict] | None = None,
                     exchanges: int = DEFAULT_EXCHANGES,
                     first_roaster_id: str | None = None) -> dict:
        if not 4 <= exchanges <= 6:
            raise ActionError("battle configuration allows 4-6 exchanges")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.ROUND_INTRO)
            if (round_.data or {}).get("battle"):
                raise ActionError("battle already set up")
            roasters = [p["participant_id"] for p in self._cast(round_, "contestant")]
            if len(roasters) != 2:
                raise ActionError("roast_battle needs exactly two roasters")

            # Both roasters are always approved AI-participant targets.
            targets: dict[str, dict] = {}
            for p in self._cast(round_, "contestant"):
                target = RoastTarget(
                    target_id=p["participant_id"],
                    target_type=TargetType.AI_PARTICIPANT,
                    display_name=p.get("display_name", p["participant_id"]),
                    approved_topics=["performance style", "prior lines", "fictional persona"],
                    blocked_topics=["protected traits", "private data", "real trauma"],
                )
                targets[target.target_id] = target.model_dump()
            # Additional roster entries are validated through the schema —
            # the target_type enum has no "arbitrary real person" value
            # (TARGET RULE, acceptance 1-2).
            for raw in roster or []:
                try:
                    target = RoastTarget.model_validate(raw)
                except Exception as exc:
                    raise ActionError(f"target rejected by roster validation: {exc}")
                targets[target.target_id] = target.model_dump()

            if first_roaster_id is None:
                first_roaster_id = roasters[
                    int.from_bytes(round_id.encode()[-4:], "big") % 2
                ]
            if first_roaster_id not in roasters:
                raise ActionError(f"{first_roaster_id!r} is not a roaster")
            order = [first_roaster_id] + [r for r in roasters if r != first_roaster_id]
            battle = {
                "order": order, "turn_index": 0, "turns": [],
                "targets": targets, "config": {"exchanges": exchanges},
                "modifier": None, "skip_next": None,
            }
            self._update_data(session, round_, battle=battle)
            self._emit(
                session, round_, "battle.setup",
                actor.actor_type, actor.actor_id, public=True,
                payload={"order": order, "exchanges": exchanges,
                         "targets": sorted(targets)},
            )
        return {"round_id": round_id, "order": order, "targets": sorted(targets)}

    # -- modifiers (controlled power-ups) ----------------------------------

    def apply_modifier(self, round_id: str, actor: Actor, modifier_id: str,
                       params: dict | None = None) -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("modifiers resolve through producer approval")
        modifier = self._modifiers.get(modifier_id)
        if modifier is None:
            # Moderation cannot be touched by any chaos card: there is no
            # modifier that names it (acceptance 5).
            raise ActionError(f"unknown modifier {modifier_id!r}")
        params = params or {}
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, *modifier.allowed_phases)
            battle = self._battle(round_)
            if battle["modifier"] is not None:
                raise ActionError("one active power-up at a time")
            cleaned: dict = {}
            if modifier_id == "target_lock":
                target_id = params.get("target_id", "")
                if target_id not in battle["targets"]:
                    raise ActionError(f"target {target_id!r} is not on the approved roster")
                cleaned["target_id"] = target_id
            elif modifier_id in {"style_mandate", "compliment_only", "silent_treatment"}:
                style = params.get("style", "backhanded_compliment"
                                   if modifier_id == "compliment_only" else "direct")
                if modifier_id == "silent_treatment":
                    style = "direct"
                    cleaned["nonverbal_only"] = True
                if style not in APPROVED_STYLES:
                    raise ActionError("style must come from the approved style enum")
                cleaned["style"] = style
            elif modifier_id == "no_defense":
                # The target (next speaker) skips one response turn.
                battle["skip_next"] = battle["order"][battle["turn_index"] % 2]
            elif modifier_id == "mirror":
                cleaned["mirror"] = True
            elif modifier_id == "everyone_fair_game":
                # Expands only to approved AI participants already in the
                # cast (judges) — never arbitrary audience names.
                for judge in self._cast(round_, "commentator"):
                    target = RoastTarget(
                        target_id=judge["participant_id"],
                        target_type=TargetType.AI_PARTICIPANT,
                        display_name=judge.get("display_name", judge["participant_id"]),
                        approved_topics=["judging style", "prior rulings"],
                        blocked_topics=["protected traits", "private data", "real trauma"],
                    )
                    battle["targets"][target.target_id] = target.model_dump()
                cleaned["added_targets"] = sorted(
                    j["participant_id"] for j in self._cast(round_, "commentator")
                )
            battle["modifier"] = {"modifier_id": modifier_id, "params": cleaned}
            self._update_data(session, round_, battle=battle)
            self._emit(
                session, round_, "modifier.applied",
                actor.actor_type, actor.actor_id, public=True,
                payload={"modifier_id": modifier_id, "params": cleaned},
            )
        return {"round_id": round_id, "modifier_id": modifier_id, "params": cleaned}

    # -- roast turns (sections 6, 7, 9) ------------------------------------

    async def request_roast(self, round_id: str, actor: Actor) -> dict:
        published = blocked_notice = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            battle = self._battle(round_)
            if len(battle["turns"]) >= battle["config"]["exchanges"]:
                raise ActionError("configured exchange count reached")
            roaster_id = battle["order"][battle["turn_index"] % 2]
            if battle["skip_next"] == roaster_id:
                # No Defense: the target skips one response turn.
                battle["skip_next"] = None
                battle["turn_index"] += 1
                self._update_data(session, round_, battle=battle)
                self._emit(
                    session, round_, "roast.turn_skipped",
                    ActorType.CONTROLLER, "controller", public=True,
                    payload={"roaster_id": roaster_id, "reason": "no_defense"},
                )
                roaster_id = battle["order"][battle["turn_index"] % 2]
            roaster = next(
                p for p in self._cast(round_, "contestant")
                if p["participant_id"] == roaster_id
            )
            opponent_id = next(r for r in battle["order"] if r != roaster_id)
            modifier = battle["modifier"] or {}
            mod_params = modifier.get("params", {})
            target_id = mod_params.get("target_id", opponent_id)
            target = battle["targets"].get(target_id)
            if target is None:
                raise ActionError(f"target {target_id!r} is not on the approved roster")
            mirror = bool(mod_params.get("mirror"))
            style = mod_params.get("style", "direct")
            exchanges = battle["config"]["exchanges"]
            escalation = min(3, 1 + (len(battle["turns"]) * 3) // exchanges)
            callbacks = [
                t["turn"]["callback_key"] for t in battle["turns"]
                if t.get("status") == "published" and t["turn"].get("callback_key")
            ]
            last_roast = next(
                (t["turn"]["roast"] for t in reversed(battle["turns"])
                 if t.get("status") == "published"), "",
            )

            provider = self._provider_for(roaster, Operation.TEXT_GENERATION)
            turn = request = response = None
            reject_reason = "provider_failed"
            retries = 0
            for attempt in range(2):  # block, repair once (section 7)
                request = ProviderRequest(
                    request_id=self._new_id("req"),
                    participant_id=roaster_id,
                    operation=Operation.TEXT_GENERATION,
                    template_id="roast_turn",
                    template_version=self.module.manifest.prompts["roast_turn"],
                    system_prompt=f"You are {roaster.get('display_name')} in a playful AI roast battle.",
                    user_prompt=(
                        f"Approved target: {json.dumps(target)}\n"
                        f"Opponent's last line: {last_roast or 'none'}\n"
                        f"Escalation: {ESCALATION[escalation]}\n"
                        "Write one concise roast under 45 words with one callback. "
                        "Attack the performance, fictional persona, or approved "
                        "topic only. Return JSON only."
                    ),
                    params={
                        "target_id": target_id,
                        "target_name": target["display_name"],
                        "available_callbacks": json.dumps(callbacks),
                        "mirror": "true" if mirror else "false",
                        "style": style,
                        "turn_index": str(len(battle["turns"])),
                        "attempt": str(attempt),
                    },
                    timeout_seconds=roaster.get("timeout_seconds", 30),
                )
                response = await provider.generate(request)
                retries = attempt
                if response.status is not RequestStatus.OK:
                    reject_reason = response.error_detail or "provider_failed"
                    continue
                try:
                    candidate = RoastTurn.model_validate_json(response.text)
                except Exception as exc:
                    reject_reason = f"invalid_roast: {exc}"
                    continue
                if candidate.target_id not in battle["targets"]:
                    reject_reason = f"unapproved_target: {candidate.target_id}"
                    continue
                # Output moderation is independent of the roaster persona
                # and runs on every attempt (acceptance 4, 8).
                moderation_reason = moderate_roast(candidate.roast)
                if moderation_reason:
                    reject_reason = f"moderation_blocked: {moderation_reason}"
                    continue
                if mirror and not (candidate.self_roast or "").strip():
                    reject_reason = "mirror_requires_self_roast"
                    continue
                turn = candidate
                break

            self._record_ai_call(
                session, round_id, request, response,
                "ok" if turn is not None else reject_reason, retries,
            )
            if turn is None:
                # Blocked output is stored privately (audit) and never
                # published (acceptance 6); the crowd sees a bench notice.
                raw_text = response.text if response and response.text else ""
                self._record_failure(
                    session, round_, error_type="roast_blocked",
                    detail=f"{reject_reason} | raw: {raw_text[:400]}",
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="bench_message_no_score",
                )
                blocked_notice = {
                    "roaster_id": roaster_id,
                    "notice": f"{roaster.get('display_name', roaster_id)} has been "
                              "bleeped and briefly benched.",
                }
                battle["turns"] = battle["turns"] + [{
                    "turn_id": f"turn_{len(battle['turns'])}",
                    "roaster_id": roaster_id, "status": "blocked",
                    "turn": None, "flags": {"reason_class": "blocked"},
                }]
                battle["turn_index"] += 1
                battle["modifier"] = None
                self._update_data(session, round_, battle=battle)
                self._emit(
                    session, round_, "roast.blocked",
                    ActorType.CONTROLLER, "controller", public=True,
                    payload=blocked_notice,
                )
            else:
                flags = {"escalation": escalation}
                if not turn.callback_key.strip():
                    # Missing callback publishes with a craft penalty flag
                    # — never a false compliance claim (acceptance 3).
                    flags["craft_penalty"] = "missing_callback"
                entry = {
                    "turn_id": f"turn_{len(battle['turns'])}",
                    "roaster_id": roaster_id, "status": "published",
                    "turn": turn.model_dump(), "flags": flags,
                }
                battle["turns"] = battle["turns"] + [entry]
                battle["turn_index"] += 1
                battle["modifier"] = None  # power-ups last one roast
                self._update_data(session, round_, battle=battle)
                self._emit(
                    session, round_, "roast.published",
                    ActorType.AI, roaster_id, public=True, payload=entry,
                )
                published = entry
        if published is not None:
            return {"round_id": round_id, **published}
        return {"round_id": round_id, "blocked": blocked_notice}

    # -- judging (section 8) ----------------------------------------------

    async def request_scores(self, round_id: str, actor: Actor, judge_id: str) -> dict:
        card = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.FINAL_JUDGING)
            judges = {j["participant_id"]: j for j in self._cast(round_, "commentator")}
            if judge_id not in judges:
                raise ActionError(f"{judge_id!r} is not a judge in this round")
            battle = self._battle(round_)
            published = [t for t in battle["turns"] if t["status"] == "published"]
            if not published:
                raise ActionError("no published roasts to judge")
            judge = judges[judge_id]
            roaster_ids = sorted(battle["order"])

            provider = self._provider_for(judge, Operation.TEXT_GENERATION)
            parse_status = "provider_failed"
            request = response = None
            retries = 0
            for attempt in range(2):
                request = ProviderRequest(
                    request_id=self._new_id("req"),
                    participant_id=judge_id,
                    operation=Operation.TEXT_GENERATION,
                    template_id="roast_judge_scorecard",
                    template_version=self.module.manifest.prompts["roast_judge_scorecard"],
                    system_prompt=f"You are {judge.get('display_name')} judging an AI roast battle.",
                    user_prompt="Score Sting, Craft, Crowd as integers 1-10; totals "
                                "must equal sums. Return JSON only.",
                    params={
                        "persona_id": judge.get("persona_id", "judge"),
                        "roaster_ids": json.dumps(roaster_ids),
                        "attempt": str(attempt),
                    },
                    timeout_seconds=judge.get("timeout_seconds", 60),
                )
                response = await provider.generate(request)
                retries = attempt
                if response.status is not RequestStatus.OK:
                    continue
                try:
                    card = RoastScorecard.model_validate_json(response.text)
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
                    recovery_action="judge_too_wounded_reweight_valid",
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
            raise ActionError("judge is too wounded to continue; remaining judges reweighted")
        return {"round_id": round_id, "judge_id": judge_id, "scorecard": card.model_dump()}

    # -- replay -----------------------------------------------------------

    def replay(self, round_id: str) -> dict:
        replay, data = self._replay_base(round_id)
        battle = data.get("battle", {})
        return {
            **replay,
            "order": battle.get("order", []),
            "targets": sorted(battle.get("targets", {})),
            # Blocked turns appear as blocked, contents redacted (they
            # exist only in the private audit ledger).
            "turns": [
                t if t.get("status") == "published"
                else {**t, "turn": None}
                for t in battle.get("turns", [])
            ],
        }
