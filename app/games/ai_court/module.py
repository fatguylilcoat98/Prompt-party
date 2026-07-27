"""AI Court game module (Packet 03).

The audience jury is the primary verdict (section 9): the score
calculation is a plurality over Guilty / Not Guilty / Legally Complicated.
The AI Judge writes the ruling but can never replace the vote.
"""

from __future__ import annotations

from app.games.ai_court.schemas import VERDICT_CHOICES
from app.games.shared.module import (
    AIRequestSpec,
    GameAction,
    GameContext,
    RoundRecord,
    RoundState,
    ScoreResult,
    StructuredOutput,
    TransitionError,
)
from app.games.shared.phases import Phase, phase_index
from app.games.shared.schemas import (
    Capability,
    GameManifest,
    Modifier,
    ModifierEffect,
    SeatRequirement,
    SeatType,
)

GAME_ID = "ai_court"


MANIFEST = GameManifest(
    game_id=GAME_ID,
    display_name="AI Court",
    version="1.0",
    seat_requirements=[
        SeatRequirement(
            seat_type=SeatType.COMMENTATOR, role="judge", min_count=1, max_count=1,
            required_capabilities=[Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT],
        ),
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="prosecutor", min_count=1, max_count=1,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="defense", min_count=1, max_count=1,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="witness", min_count=1, max_count=2,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
    ],
    phases={
        Phase.LOBBY: "Cast, room, provider health, game selection",
        Phase.SUBMISSION_OPEN: "Audience submits fictional case ideas",
        Phase.SUBMISSION_REVIEW: "Screening and producer review",
        Phase.PROMPT_LOCKED: "Case premise becomes immutable",
        Phase.ROUND_INTRO: "Case file built and read",
        Phase.CONTESTANT_PLANNING: "Counsel prepare theories",
        Phase.CREATION_ACTIVE: "Openings, testimony, objections, closings",
        Phase.PRE_REVEAL_COMMENTARY: "Recess and legal color",
        Phase.REVEAL: "The completed trial record stands",
        Phase.FINAL_JUDGING: "Judge summation",
        Phase.AUDIENCE_VOTING: "Jury votes the verdict",
        Phase.SCORING: "Verdict calculated, ruling delivered, precedent recorded",
    },
    actions={
        Phase.ROUND_INTRO: {"producer": ["build_case"]},
        Phase.CREATION_ACTIVE: {
            "producer": [
                "request_turn", "advance_stage", "raise_objection", "rule_objection",
                "propose_evidence", "rule_evidence", "flag_contradiction", "recuse",
            ],
            "ai": ["argue", "testify", "rule"],
        },
        Phase.AUDIENCE_VOTING: {"producer": ["open_votes", "close_votes"], "audience": ["vote"]},
        Phase.SCORING: {"producer": ["calculate", "final_ruling", "create_precedent", "override"]},
    },
    prompts={"court_turn": "1.0", "court_objection_ruling": "1.0",
             "court_evidence_ruling": "1.0", "court_final_ruling": "1.0"},
    schemas={"turn_output": "1.0", "objection_ruling": "1.0",
             "evidence_ruling": "1.0", "final_ruling": "1.0", "precedent": "1.0"},
    modifiers=[
        Modifier(
            modifier_id="new_evidence", game_id=GAME_ID, display_name="New Evidence",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["case"],
            duration="current_round",
            effect=ModifierEffect(type="pending_evidence", instruction="{{approved_evidence}}"),
            fallback={"on_trial_closed": "reject"},
        ),
        Modifier(
            modifier_id="contempt", game_id=GAME_ID, display_name="Contempt",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["participant"],
            duration="one_turn",
            effect=ModifierEffect(type="forced_apology", instruction="one-sentence apology or point loss"),
            fallback={"confirm": "producer_confirms_target"},
        ),
        Modifier(
            modifier_id="recuse", game_id=GAME_ID, display_name="Recuse",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["judge"],
            duration="current_round",
            effect=ModifierEffect(type="judge_swap", instruction="swap to configured backup"),
            fallback={"no_backup": "pause"},
        ),
        Modifier(
            modifier_id="jury_nullification", game_id=GAME_ID,
            display_name="Jury Nullification",
            allowed_phases=[Phase.AUDIENCE_VOTING], target_types=["verdict"],
            duration="once_per_show",
            effect=ModifierEffect(type="mistrial_option", instruction="producer-confirmed mistrial"),
            fallback={"limit": "once_per_show"},
        ),
        Modifier(
            modifier_id="judge_goes_rogue", game_id=GAME_ID, display_name="Judge Goes Rogue",
            allowed_phases=[Phase.FINAL_JUDGING], target_types=["ruling"],
            duration="one_turn",
            effect=ModifierEffect(type="comedy_supplemental_charge", instruction="never official"),
            fallback={"display": "comedy_only"},
        ),
        Modifier(
            modifier_id="witness_collapse", game_id=GAME_ID, display_name="Witness Collapse",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["witness"],
            duration="one_turn",
            effect=ModifierEffect(type="unrelated_confession", instruction="fictional matter only"),
            fallback={"rule": "does_not_enter_evidence"},
        ),
    ],
    scoring={
        "verdict_choices": VERDICT_CHOICES,
        "primary": "audience_jury_plurality",
        "tie": "solomonic_ruling_then_producer_runoff_or_complicated",
    },
    timeouts={"turn": 45, "ruling": 30, "final_ruling": 60},
    fallbacks={
        "incoherent_argument": "contempt_and_one_sentence_summary",
        "contradictory_testimony": "record_and_expose_to_cross",
        "missing_judge": "backup_or_producer_ruling_no_silent_auto_verdict",
        "unsafe_case_or_evidence": "reject_before_admission",
    },
)


class AiCourtModule:
    manifest = MANIFEST

    def allowed_actions(self, state: RoundState) -> list[str]:
        actions = self.manifest.actions.get(state.phase, {})
        return sorted({a for group in actions.values() for a in group})

    def validate_transition(self, old: Phase, new: Phase) -> None:
        if phase_index(new) != phase_index(old) + 1:
            raise TransitionError(
                f"ai_court: illegal transition {old.value} -> {new.value}"
            )

    def build_ai_request(self, action: GameAction, context: GameContext) -> AIRequestSpec:
        return AIRequestSpec(
            operation="text_generation",
            template_id="court_turn",
            template_version=self.manifest.prompts["court_turn"],
            system_prompt="You are counsel in a fictional comedy courtroom.",
            user_prompt="Stay within admitted facts. Return structured JSON.",
            timeout_seconds=self.manifest.timeouts["turn"],
        )

    def parse_ai_response(self, action: GameAction, raw: str) -> StructuredOutput:
        from app.games.ai_court.schemas import TurnOutput

        try:
            turn = TurnOutput.model_validate_json(raw)
            return StructuredOutput(schema_id="turn_output", valid=True, data=turn.model_dump())
        except Exception as exc:
            return StructuredOutput(schema_id="turn_output", valid=False, parse_error=str(exc))

    def apply_modifier(self, modifier: Modifier, state: RoundState) -> RoundState:
        if state.phase not in modifier.allowed_phases:
            raise ValueError(
                f"modifier {modifier.modifier_id} not allowed in phase {state.phase.value}"
            )
        return state.model_copy(update={"active_modifier_id": modifier.modifier_id})

    def calculate_score(self, round_record: RoundRecord) -> ScoreResult:
        """Verdict = jury plurality. The AI Judge has no vote here — the
        audience jury is the primary verdict (acceptance 7)."""
        votes = {choice: round_record.votes.get(choice, 0) for choice in VERDICT_CHOICES}
        total = sum(votes.values())
        components = {
            choice: {
                "votes": count,
                "share": round(count / total, 6) if total else 0.0,
            }
            for choice, count in votes.items()
        }
        if total == 0:
            return ScoreResult(
                winner_id=None, is_draw=True, components=components,
                tie_break_used="no_jury_votes_producer_decides",
            )
        best = max(votes.values())
        leaders = [c for c in VERDICT_CHOICES if votes[c] == best]
        if len(leaders) > 1:
            # Section 9: tie triggers the Solomonic ruling, then a
            # producer-selected runoff or Complicated verdict.
            return ScoreResult(
                winner_id=None, is_draw=True, components=components,
                tie_break_used="solomonic_ruling_then_producer_choice",
            )
        return ScoreResult(winner_id=leaders[0], is_draw=False, components=components)

    def public_state(self, round_record: RoundRecord) -> dict:
        return {
            "game_id": GAME_ID,
            "round_id": round_record.round_id,
            "phase": round_record.phase.value,
            "locked_prompt": round_record.locked_prompt,
            "fictional_notice": True,
            "outputs": round_record.outputs,
            "votes": round_record.votes,
        }
