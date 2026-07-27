"""AI Court round orchestrator (Packet 03).

Cast convention (v1): participant ids are the courtroom roles —
``judge``, ``prosecutor``, ``defense``, and witness ids ``w1``/``w2``
matching the case record. The engine maintains the case record so nobody
can silently rewrite reality: lawyers argue only from the admitted
record, unapproved evidence stays out of it, contradictions are recorded
visibly, and the audience jury verdict is never replaced by the AI judge.
"""

from __future__ import annotations

import copy
import json

from app.controller.engine import Actor
from app.games.ai_court.module import AiCourtModule
from app.games.ai_court.schemas import (
    CaseRecord,
    Evidence,
    EvidenceRuling,
    EvidenceStatus,
    Fact,
    FinalRuling,
    ObjectionRuling,
    ObjectionType,
    Precedent,
    RulingCode,
    TurnOutput,
    Witness,
)
from app.games.shared.orchestrator import ActionError, BaseGameOrchestrator
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType
from app.persistence.db import session_scope
from app.persistence.models import Round
from app.providers.base import Operation, ProviderRequest, RequestStatus

STAGES = ["opening", "examination", "closing", "done"]

LAWYER_ROLES = {"prosecution": "prosecutor", "defense": "defense"}


class AiCourtOrchestrator(BaseGameOrchestrator):
    ACTIONS = frozenset({
        "build_case", "advance_stage", "request_turn", "raise_objection",
        "rule_objection", "propose_evidence", "rule_evidence",
        "flag_contradiction", "recuse", "final_ruling", "create_precedent",
    })

    def __init__(self, *args, **kwargs) -> None:
        self.module = AiCourtModule()
        super().__init__(*args, **kwargs)

    # -- internal --------------------------------------------------------

    @staticmethod
    def _participant(round_: Round, participant_id: str) -> dict:
        for p in round_.cast or []:
            if p.get("participant_id") == participant_id and p.get("enabled", True):
                return p
        raise ActionError(f"cast has no enabled participant {participant_id!r}")

    @staticmethod
    def _case(round_: Round) -> CaseRecord:
        raw = (round_.data or {}).get("case")
        if not raw:
            raise ActionError("no case record; run build_case first")
        return CaseRecord.model_validate(raw)

    @staticmethod
    def _trial(round_: Round) -> dict:
        trial = (round_.data or {}).get("trial")
        if not trial:
            raise ActionError("trial has not started")
        # Deep copy: mutations must never touch the ORM-held value, or
        # SQLAlchemy sees old == new and skips the UPDATE.
        return copy.deepcopy(trial)

    def _ruling_judge(self, round_: Round) -> dict:
        """The seat currently allowed to issue rulings. A recused judge
        without a configured backup cannot rule (acceptance 9)."""
        state = (round_.data or {}).get("judge", {})
        if state.get("recused"):
            backup = state.get("backup")
            if not backup:
                raise ActionError(
                    "the judge is recused and no backup is configured; court is in recess"
                )
            return self._participant(round_, backup)
        return self._participant(round_, "judge")

    async def _structured_call(self, session, round_, participant: dict, template_id: str,
                               schema_cls, user_prompt: str, params: dict):
        provider = self._provider_for(participant, Operation.TEXT_GENERATION)
        parse_status = "provider_failed"
        request = response = parsed = None
        retries = 0
        for attempt in range(2):
            request = ProviderRequest(
                request_id=self._new_id("req"),
                participant_id=participant["participant_id"],
                operation=Operation.TEXT_GENERATION,
                template_id=template_id,
                template_version=self.module.manifest.prompts[template_id],
                system_prompt="You are part of a fictional comedy courtroom. "
                              "Everything here is entertainment, not real law.",
                user_prompt=user_prompt,
                params={**params, "attempt": str(attempt),
                        "persona_id": participant.get("persona_id", "")},
                timeout_seconds=participant.get("timeout_seconds", 45),
            )
            response = await provider.generate(request)
            retries = attempt
            if response.status is not RequestStatus.OK:
                parse_status = response.error_detail or "provider_failed"
                continue
            try:
                parsed = schema_cls.model_validate_json(response.text)
                parse_status = "ok"
                break
            except Exception as exc:
                parse_status = f"invalid_{template_id}: {exc}"
        self._record_ai_call(session, round_.round_id, request, response, parse_status, retries)
        return parsed, parse_status, retries, response

    # -- case building (sections 5, 10) ----------------------------------

    def build_case(self, round_id: str, actor: Actor, charge: str,
                   stipulated_facts: list[str] | None = None,
                   witnesses: list[dict] | None = None,
                   prior_precedent_ids: list[str] | None = None) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.ROUND_INTRO)
            if not round_.locked_prompt:
                raise ActionError("no locked case premise; lock a submission first")
            if (round_.data or {}).get("case"):
                raise ActionError("case record already built")

            prior_ids = prior_precedent_ids or []
            if prior_ids:
                available = {
                    r.data["precedent"]["precedent_id"]
                    for r in session.query(Round)
                    .filter(Round.show_id == round_.show_id)
                    .filter(Round.game_id == self.game_id)
                    .all()
                    if (r.data or {}).get("precedent")
                }
                unknown = [p for p in prior_ids if p not in available]
                if unknown:
                    # Precedent is scoped to this show only (acceptance 8).
                    raise ActionError(
                        f"precedent ids not found in this show: {unknown}"
                    )

            witness_models = [
                Witness(
                    id=f"w{i + 1}",
                    role=w.get("role", "witness"),
                    known_facts=list(w.get("known_facts", [])),
                    credibility=w.get("credibility", "neutral"),
                )
                for i, w in enumerate((witnesses or [])[:2])
            ]
            case = CaseRecord(
                case_id=self._new_id("case"),
                title=round_.locked_prompt[:200],
                charge=charge,
                stipulated_facts=[
                    Fact(fact_id=f"f{i + 1}", text=text)
                    for i, text in enumerate(stipulated_facts or [])
                ],
                witnesses=witness_models,
                prior_precedent_ids=prior_ids,
            )
            trial = {"stage": "opening", "turns": [], "objections": [],
                     "opening_done": [], "closing_done": [], "repair_for": None}
            self._update_data(
                session, round_,
                case=case.model_dump(), trial=trial,
                judge={"recused": False, "backup": None},
            )
            # The fictional notice is on every public frame (acceptance 1).
            self._emit(
                session, round_, "case.created",
                actor.actor_type, actor.actor_id, public=True,
                payload={"case": case.model_dump(), "fictional_notice": True},
            )
        return {"round_id": round_id, "case": case.model_dump()}

    # -- trial flow (section 6) ------------------------------------------

    def advance_stage(self, round_id: str, actor: Actor) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            trial = self._trial(round_)
            index = STAGES.index(trial["stage"])
            if trial["stage"] == "done":
                raise ActionError("trial is already complete")
            trial["stage"] = STAGES[index + 1]
            self._update_data(session, round_, trial=trial)
            self._emit(
                session, round_, "trial.stage_advanced",
                actor.actor_type, actor.actor_id, public=True,
                payload={"stage": trial["stage"]},
            )
        return {"round_id": round_id, "stage": trial["stage"]}

    def _validate_turn_role(self, trial: dict, case: CaseRecord, role: str) -> str:
        """Returns the participant id for the role; enforces stage order."""
        witness_ids = {w.id for w in case.witnesses}
        if role in LAWYER_ROLES:
            participant_id = LAWYER_ROLES[role]
        elif role in witness_ids:
            participant_id = role
        else:
            raise ActionError(f"unknown courtroom role {role!r}")

        if trial["repair_for"]:
            if role != trial["repair_for"]:
                raise ActionError(
                    f"a sustained objection requires {trial['repair_for']} to repair first"
                )
            # A repair turn replaces the struck line; stage-completion
            # checks do not apply to it.
            return participant_id
        stage = trial["stage"]
        if stage == "done":
            raise ActionError("trial is complete; no further turns")
        if stage in {"opening", "closing"}:
            done_key = f"{stage}_done"
            if role not in LAWYER_ROLES:
                raise ActionError(f"witnesses do not speak during {stage} statements")
            if role in trial[done_key]:
                raise ActionError(f"{role} already delivered a {stage} statement")
            if role == "defense" and "prosecution" not in trial[done_key]:
                raise ActionError(f"prosecution {stage.rstrip('s')}s first")
        return participant_id

    async def request_turn(self, round_id: str, actor: Actor, role: str) -> dict:
        turn_entry = None
        failure_reason = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            case = self._case(round_)
            trial = self._trial(round_)
            if any(o["status"] == "pending" for o in trial["objections"]):
                raise ActionError("an objection is pending; the line is paused until ruled")
            participant_id = self._validate_turn_role(trial, case, role)
            participant = self._participant(round_, participant_id)
            is_repair = trial["repair_for"] == role

            witness_ids = {w.id for w in case.witnesses}
            if role in witness_ids:
                # Witnesses receive only their own facts + admitted evidence.
                witness = next(w for w in case.witnesses if w.id == role)
                fact_ids = [f"{role}_k{i + 1}" for i in range(len(witness.known_facts))]
                fact_ids += [
                    e.evidence_id for e in case.evidence
                    if e.status is EvidenceStatus.ADMITTED
                ]
                role_kind = "witness"
                record_view = {
                    "your_known_facts": witness.known_facts,
                    "admitted_evidence": [
                        e.description for e in case.evidence
                        if e.status is EvidenceStatus.ADMITTED
                    ],
                }
            else:
                fact_ids = sorted(case.known_fact_ids())
                role_kind = role
                record_view = case.admitted_record()

            opposing = [
                t for t in trial["turns"]
                if t["role"] in LAWYER_ROLES and t["role"] != role
            ]
            turn, parse_status, retries, response = await self._structured_call(
                session, round_, participant, "court_turn", TurnOutput,
                user_prompt=(
                    f"Case record: {json.dumps(record_view)}\n"
                    f"Current stage: {trial['stage']}\n"
                    f"Opponent's last argument: "
                    f"{opposing[-1]['output']['spoken_line'] if opposing else 'none'}\n"
                    "Stay within admitted facts. Label new claims as argument, "
                    "not evidence. Return structured JSON."
                ),
                params={
                    "role_kind": role_kind,
                    "stage": trial["stage"],
                    "fact_ids": json.dumps(fact_ids),
                    "turn_index": str(len(trial["turns"])),
                },
            )
            if turn is None:
                self._record_failure(
                    session, round_, error_type="turn_failed", detail=parse_status,
                    provider=response.provider, model=response.model,
                    operation="text_generation", retry_count=retries,
                    recovery_action="contempt_and_one_sentence_summary",
                )
                failure_reason = f"{role} argument failed; judge may issue contempt"
            else:
                flags = {}
                unknown_facts = [f for f in turn.fact_ids_used if f not in fact_ids]
                if unknown_facts:
                    # Citing facts outside the record is flagged, visibly,
                    # not silently accepted (acceptance 3).
                    flags["cites_unrecorded_fact"] = unknown_facts
                turn_entry = {
                    "turn_id": f"turn_{len(trial['turns'])}",
                    "role": role,
                    "stage": trial["stage"],
                    "output": turn.model_dump(),
                    "flags": flags,
                    "struck": False,
                    "repair": is_repair,
                }
                trial["turns"] = trial["turns"] + [turn_entry]
                if is_repair:
                    trial["repair_for"] = None
                elif trial["stage"] in {"opening", "closing"}:
                    trial[f"{trial['stage']}_done"] = trial[f"{trial['stage']}_done"] + [role]
                self._update_data(session, round_, trial=trial)
                self._emit(
                    session, round_, "trial.turn",
                    ActorType.AI, participant_id, public=True, payload=turn_entry,
                )
        if failure_reason:
            raise ActionError(failure_reason)
        return {"round_id": round_id, **turn_entry}

    # -- objections (section 7) ------------------------------------------

    def raise_objection(self, round_id: str, actor: Actor, raised_by: str,
                        objection_type: str, target_turn_id: str, statement: str) -> dict:
        if raised_by not in LAWYER_ROLES:
            raise ActionError("only prosecution or defense may object")
        try:
            validated_type = ObjectionType(objection_type)
        except ValueError:
            raise ActionError(f"unknown objection type {objection_type!r}")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            trial = self._trial(round_)
            if not any(t["turn_id"] == target_turn_id for t in trial["turns"]):
                raise ActionError(f"no turn {target_turn_id!r} in the record")
            objection = {
                "objection_id": self._new_id("obj"),
                "raised_by": raised_by,
                "type": validated_type.value,
                "target_turn_id": target_turn_id,
                "statement": statement[:300],
                "status": "pending",
                "ruling": None,
            }
            trial["objections"] = trial["objections"] + [objection]
            self._update_data(session, round_, trial=trial)
            self._emit(
                session, round_, "objection.raised",
                actor.actor_type, actor.actor_id, public=True, payload=objection,
            )
        return {"round_id": round_id, **objection}

    async def rule_objection(self, round_id: str, actor: Actor, objection_id: str,
                             override_ruling: str | None = None,
                             override_reason: str | None = None) -> dict:
        ruling_dump = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            trial = self._trial(round_)
            objection = next(
                (o for o in trial["objections"] if o["objection_id"] == objection_id), None
            )
            if objection is None or objection["status"] != "pending":
                raise ActionError("no pending objection with that id")

            if override_ruling is not None:
                # Producer override of a malformed ruling — always logged.
                if actor.actor_type is not ActorType.PRODUCER:
                    raise ActionError("ruling override is producer-only")
                try:
                    code = RulingCode(override_ruling)
                except ValueError:
                    raise ActionError(f"invalid ruling code {override_ruling!r}")
                ruling = ObjectionRuling(
                    ruling=code,
                    explanation=override_reason or "Producer override.",
                    repair_instruction="Restate within the record." if code is RulingCode.SUSTAINED else "",
                )
                self._emit(
                    session, round_, "objection.ruling_overridden",
                    actor.actor_type, actor.actor_id, public=True,
                    payload={"objection_id": objection_id, "ruling": ruling.model_dump(),
                             "reason": override_reason or "unspecified"},
                )
            else:
                judge = self._ruling_judge(round_)
                ruling, parse_status, retries, response = await self._structured_call(
                    session, round_, judge, "court_objection_ruling", ObjectionRuling,
                    user_prompt=(
                        f"Objection ({objection['type']}): {objection['statement']}\n"
                        "Return sustained, overruled, or reserved with a short explanation."
                    ),
                    params={"objection_id": objection_id},
                )
                if ruling is None:
                    self._record_failure(
                        session, round_, error_type="objection_ruling_failed",
                        detail=parse_status, provider=response.provider,
                        model=response.model, operation="text_generation",
                        retry_count=retries, recovery_action="producer_override_available",
                    )
                    raise ActionError(
                        "the judge's ruling was malformed; the producer may override"
                    )

            objection["status"] = "ruled"
            objection["ruling"] = ruling.model_dump()
            if ruling.ruling is RulingCode.SUSTAINED:
                # Strike the line but preserve the audit trail (acceptance 5).
                for turn in trial["turns"]:
                    if turn["turn_id"] == objection["target_turn_id"]:
                        turn["struck"] = True
                trial["repair_for"] = next(
                    (t["role"] for t in trial["turns"]
                     if t["turn_id"] == objection["target_turn_id"]),
                    None,
                )
            self._update_data(session, round_, trial=trial)
            self._emit(
                session, round_, "objection.ruled",
                actor.actor_type, actor.actor_id, public=True,
                payload={"objection_id": objection_id, "ruling": ruling.model_dump()},
            )
            ruling_dump = ruling.model_dump()
        return {"round_id": round_id, "objection_id": objection_id, "ruling": ruling_dump}

    # -- evidence (section 6, acceptance 2) -------------------------------

    def propose_evidence(self, round_id: str, actor: Actor, description: str) -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("evidence proposals resolve through producer approval")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            case = self._case(round_)
            trial = self._trial(round_)
            if trial["stage"] in {"closing", "done"}:
                raise ActionError("trial is closed to new evidence")
            evidence = Evidence(
                evidence_id=self._new_id("ev"), description=description[:300]
            )
            case.evidence.append(evidence)
            self._update_data(session, round_, case=case.model_dump())
            # Pending evidence is producer-facing only until admitted.
            self._emit(
                session, round_, "evidence.proposed",
                actor.actor_type, actor.actor_id, public=False,
                payload={"evidence": evidence.model_dump()},
            )
        return {"round_id": round_id, "evidence": evidence.model_dump()}

    async def rule_evidence(self, round_id: str, actor: Actor, evidence_id: str) -> dict:
        decision = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            case = self._case(round_)
            evidence = next(
                (e for e in case.evidence if e.evidence_id == evidence_id), None
            )
            if evidence is None or evidence.status is not EvidenceStatus.PENDING:
                raise ActionError("no pending evidence with that id")
            judge = self._ruling_judge(round_)
            ruling, parse_status, retries, response = await self._structured_call(
                session, round_, judge, "court_evidence_ruling", EvidenceRuling,
                user_prompt=f"Rule on admitting: {evidence.description}",
                params={"evidence_id": evidence_id},
            )
            if ruling is None:
                self._record_failure(
                    session, round_, error_type="evidence_ruling_failed",
                    detail=parse_status, provider=response.provider,
                    model=response.model, operation="text_generation",
                    retry_count=retries, recovery_action="evidence_stays_pending",
                )
                raise ActionError("evidence ruling failed; evidence stays pending")
            evidence.status = (
                EvidenceStatus.ADMITTED if ruling.decision == "admitted"
                else EvidenceStatus.REJECTED
            )
            evidence.ruling_explanation = ruling.explanation
            self._update_data(session, round_, case=case.model_dump())
            self._emit(
                session, round_, "evidence.ruled",
                actor.actor_type, actor.actor_id, public=True,
                payload={"evidence_id": evidence_id, "decision": ruling.decision,
                         "explanation": ruling.explanation},
            )
            decision = ruling.decision
        return {"round_id": round_id, "evidence_id": evidence_id, "decision": decision}

    # -- contradictions and recusal (acceptance 6, 9) ---------------------

    def flag_contradiction(self, round_id: str, actor: Actor, turn_id: str, note: str) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            trial = self._trial(round_)
            turn = next((t for t in trial["turns"] if t["turn_id"] == turn_id), None)
            if turn is None:
                raise ActionError(f"no turn {turn_id!r} in the record")
            turn["flags"] = {**turn.get("flags", {}), "contradiction": note[:300]}
            self._update_data(session, round_, trial=trial)
            # Contradictions stay visible — never silently absorbed.
            self._emit(
                session, round_, "testimony.contradiction",
                actor.actor_type, actor.actor_id, public=True,
                payload={"turn_id": turn_id, "note": note[:300]},
            )
        return {"round_id": round_id, "turn_id": turn_id, "flagged": True}

    def recuse(self, round_id: str, actor: Actor, backup_judge_id: str | None = None) -> dict:
        if actor.actor_type is not ActorType.PRODUCER:
            raise ActionError("recusal is producer-only")
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.CREATION_ACTIVE)
            if backup_judge_id is not None:
                self._participant(round_, backup_judge_id)
            self._update_data(
                session, round_, judge={"recused": True, "backup": backup_judge_id}
            )
            self._emit(
                session, round_, "judge.recused",
                actor.actor_type, actor.actor_id, public=True,
                payload={"backup_judge_id": backup_judge_id},
            )
        return {"round_id": round_id, "recused": True, "backup": backup_judge_id}

    # -- ruling and precedent (sections 9, 10) ----------------------------

    async def final_ruling(self, round_id: str, actor: Actor) -> dict:
        ruling_dump = None
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            result = round_.result or {}
            if not result.get("official"):
                raise ActionError("calculate the jury verdict before the ruling")
            verdict = result.get("winner_id") or "legally_complicated"
            judge = self._ruling_judge(round_)
            ruling, parse_status, retries, response = await self._structured_call(
                session, round_, judge, "court_final_ruling", FinalRuling,
                user_prompt=(
                    f"The jury verdict is {verdict}. Write the fictional court's "
                    "ruling. You may disagree in commentary but the verdict stands."
                ),
                params={"verdict": verdict},
            )
            if ruling is None:
                self._record_failure(
                    session, round_, error_type="final_ruling_failed",
                    detail=parse_status, provider=response.provider,
                    model=response.model, operation="text_generation",
                    retry_count=retries, recovery_action="producer_reads_ruling",
                )
                raise ActionError("final ruling failed; producer may read a ruling")
            # The ruling never touches round_.result — the jury verdict is
            # the official outcome (acceptance 7).
            self._update_data(session, round_, final_ruling=ruling.model_dump())
            self._emit(
                session, round_, "court.final_ruling",
                ActorType.AI, judge["participant_id"], public=True,
                payload={"ruling": ruling.model_dump(), "verdict": verdict,
                         "fictional_notice": True},
            )
            ruling_dump = ruling.model_dump()
        return {"round_id": round_id, "ruling": ruling_dump}

    def create_precedent(self, round_id: str, actor: Actor, holding: str) -> dict:
        with session_scope(self._sessions) as session:
            round_ = self._get_round(session, round_id)
            self._require_phase(round_, Phase.SCORING)
            case = self._case(round_)
            precedent = Precedent(
                precedent_id=self._new_id("prec"),
                case_id=case.case_id,
                holding=holding[:300],
            )
            self._update_data(session, round_, precedent=precedent.model_dump())
            self._emit(
                session, round_, "precedent.created",
                actor.actor_type, actor.actor_id, public=True,
                payload={"precedent": precedent.model_dump()},
            )
        return {"round_id": round_id, "precedent": precedent.model_dump()}

    # -- replay -----------------------------------------------------------

    def replay(self, round_id: str) -> dict:
        replay, data = self._replay_base(round_id)
        trial = data.get("trial", {})
        return {
            **replay,
            # Fictional entertainment, on every export (acceptance 1).
            "fictional_notice": True,
            "case": data.get("case"),
            "turns": trial.get("turns", []),
            "objections": trial.get("objections", []),
            "final_ruling": data.get("final_ruling"),
            "precedent": data.get("precedent"),
        }
