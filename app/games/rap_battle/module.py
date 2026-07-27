"""AI Rap Battle game module (Packet 02).

Scoring (section 9): audience 60% / normalized judge 40%; tie order is
audience share, then Wordplay average, then one-bar sudden death /
audience runoff, which need a live producer decision.
"""

from __future__ import annotations

from app.games.rap_battle.schemas import MAX_RAP_TOTAL, RapScorecard
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

GAME_ID = "rap_battle"

AUDIENCE_WEIGHT = 0.6
JUDGE_WEIGHT = 0.4


MANIFEST = GameManifest(
    game_id=GAME_ID,
    display_name="AI Rap Battle",
    version="1.0",
    seat_requirements=[
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="rapper", min_count=2, max_count=2,
            required_capabilities=[Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT],
        ),
        SeatRequirement(
            seat_type=SeatType.COMMENTATOR, role="judge", min_count=3, max_count=3,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
        SeatRequirement(
            seat_type=SeatType.HOST, role="audio_renderer", min_count=0, max_count=1,
            required_capabilities=[Capability.TEXT_TO_SPEECH],
        ),
    ],
    phases={
        Phase.LOBBY: "Cast, room, provider health, game selection",
        Phase.SUBMISSION_OPEN: "Audience submits battle topics",
        Phase.SUBMISSION_REVIEW: "Screening and producer review",
        Phase.PROMPT_LOCKED: "Battle topic becomes immutable",
        Phase.ROUND_INTRO: "Framing and opening draw",
        Phase.CONTESTANT_PLANNING: "Opening positions",
        Phase.CREATION_ACTIVE: "Alternating verses",
        Phase.PRE_REVEAL_COMMENTARY: "Judges deliberate",
        Phase.REVEAL: "Completed battle stands",
        Phase.FINAL_JUDGING: "Category scores",
        Phase.AUDIENCE_VOTING: "Audience vote opens and closes",
        Phase.SCORING: "Calculate, announce, persist, reset",
    },
    actions={
        Phase.ROUND_INTRO: {"producer": ["opening_draw"]},
        Phase.CREATION_ACTIVE: {"producer": ["request_verse", "apply_modifier"], "ai": ["verse"]},
        Phase.FINAL_JUDGING: {"producer": ["request_scores"], "ai": ["score"]},
        Phase.AUDIENCE_VOTING: {"producer": ["open_votes", "close_votes"], "audience": ["vote"]},
        Phase.SCORING: {"producer": ["calculate", "override", "draw"]},
    },
    prompts={"rap_verse": "1.0", "rap_judge_scorecard": "1.0"},
    schemas={"rap_verse": "1.0", "rap_scorecard": "1.0"},
    modifiers=[
        Modifier(
            modifier_id="rhyme_robbery", game_id=GAME_ID, display_name="Rhyme Robbery",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["verse"],
            duration="next_verse",
            effect=ModifierEffect(type="forced_words", instruction="{{approved_words}}"),
            fallback={"on_omission": "regenerate_once"},
        ),
        Modifier(
            modifier_id="topic_bomb", game_id=GAME_ID, display_name="Topic Bomb",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["verse"],
            duration="next_verse",
            effect=ModifierEffect(type="secondary_topic", instruction="{{approved_topic}}"),
            fallback={"on_late": "reject_after_verse_request_sent"},
        ),
        Modifier(
            modifier_id="speed_round", game_id=GAME_ID, display_name="Speed Round",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["verse"],
            duration="next_verse",
            effect=ModifierEffect(type="bar_cap", instruction="4-6 lines"),
            fallback={"on_parser_failure": "use_normal_cap"},
        ),
        Modifier(
            modifier_id="ghostwriter", game_id=GAME_ID, display_name="Ghostwriter",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["verse"],
            duration="next_verse",
            effect=ModifierEffect(type="audience_bar", instruction="{{approved_bar}}"),
            fallback={"on_unsafe": "reject_unsafe_forced_bar"},
        ),
        Modifier(
            modifier_id="mic_feedback", game_id=GAME_ID, display_name="Mic Feedback",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["performance"],
            duration="next_verse",
            effect=ModifierEffect(type="style_swap", instruction="{{approved_style}}"),
            fallback={"rule": "no_effect_on_canonical_text"},
        ),
        Modifier(
            modifier_id="beat_switch", game_id=GAME_ID, display_name="Beat Switch",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["performance"],
            duration="next_verse",
            effect=ModifierEffect(type="flow_constraint", instruction="{{approved_flow}}"),
            fallback={"on_audio_failure": "fall_back_to_text"},
        ),
    ],
    scoring={
        "audience_weight": AUDIENCE_WEIGHT,
        "judge_weight": JUDGE_WEIGHT,
        "tie_order": [
            "audience_share", "wordplay_average", "one_bar_sudden_death", "audience_runoff",
        ],
    },
    timeouts={"verse": 45, "scorecard": 60},
    fallbacks={
        "empty_or_malformed_verse": "one_repair_then_recovery_line",
        "audio_failure": "continue_with_canonical_text",
        "unsafe_topic": "reject_before_lock",
        "forced_word_miss": "one_revision_or_producer_waiver",
    },
)


class RapBattleModule:
    manifest = MANIFEST

    def allowed_actions(self, state: RoundState) -> list[str]:
        actions = self.manifest.actions.get(state.phase, {})
        return sorted({a for group in actions.values() for a in group})

    def validate_transition(self, old: Phase, new: Phase) -> None:
        if phase_index(new) != phase_index(old) + 1:
            raise TransitionError(
                f"rap_battle: illegal transition {old.value} -> {new.value}"
            )

    def build_ai_request(self, action: GameAction, context: GameContext) -> AIRequestSpec:
        state = context.state
        return AIRequestSpec(
            operation="text_generation",
            template_id="rap_verse",
            template_version=self.manifest.prompts["rap_verse"],
            system_prompt="You are a contestant in a playful AI rap battle.",
            user_prompt=(
                f"Topic: {state.locked_prompt}\n"
                "Write 8-12 short bar-equivalent lines. Return JSON only."
            ),
            timeout_seconds=self.manifest.timeouts["verse"],
        )

    def parse_ai_response(self, action: GameAction, raw: str) -> StructuredOutput:
        from app.games.rap_battle.schemas import RapVerse

        try:
            verse = RapVerse.model_validate_json(raw)
            return StructuredOutput(schema_id="rap_verse", valid=True, data=verse.model_dump())
        except Exception as exc:
            return StructuredOutput(schema_id="rap_verse", valid=False, parse_error=str(exc))

    def apply_modifier(self, modifier: Modifier, state: RoundState) -> RoundState:
        if state.phase not in modifier.allowed_phases:
            raise ValueError(
                f"modifier {modifier.modifier_id} not allowed in phase {state.phase.value}"
            )
        return state.model_copy(update={"active_modifier_id": modifier.modifier_id})

    def calculate_score(self, round_record: RoundRecord) -> ScoreResult:
        votes = round_record.votes
        scorecards = {
            judge_id: RapScorecard.model_validate(
                {k: v for k, v in card.items() if k not in {"blind", "abstained"}}
            )
            for judge_id, card in round_record.judge_scores.items()
            if not card.get("abstained")
        }
        rapper_ids = sorted(
            {r for card in scorecards.values() for r in card.scores} | set(votes.keys())
        )
        if not rapper_ids:
            return ScoreResult(winner_id=None, is_draw=True, components={})

        total_votes = sum(votes.values())
        components: dict[str, dict] = {}
        for rapper in rapper_ids:
            share = (votes.get(rapper, 0) / total_votes) if total_votes else 0.0
            totals = [
                card.scores[rapper].total
                for card in scorecards.values() if rapper in card.scores
            ]
            judge_norm = (sum(totals) / len(totals)) / MAX_RAP_TOTAL if totals else 0.0
            wordplay = [
                card.scores[rapper].wordplay
                for card in scorecards.values() if rapper in card.scores
            ]
            components[rapper] = {
                "audience_share": round(share, 6),
                "judge_normalized": round(judge_norm, 6),
                "wordplay_average": round(sum(wordplay) / len(wordplay), 6) if wordplay else 0.0,
                "combined": round(AUDIENCE_WEIGHT * share + JUDGE_WEIGHT * judge_norm, 6),
            }

        best_combined = max(components[r]["combined"] for r in rapper_ids)
        leaders = [r for r in rapper_ids if components[r]["combined"] == best_combined]
        tie_break = None
        if len(leaders) > 1:
            best_share = max(components[r]["audience_share"] for r in leaders)
            by_share = [r for r in leaders if components[r]["audience_share"] == best_share]
            if len(by_share) == 1:
                leaders, tie_break = by_share, "audience_share"
            else:
                best_wp = max(components[r]["wordplay_average"] for r in by_share)
                by_wp = [r for r in by_share if components[r]["wordplay_average"] == best_wp]
                if len(by_wp) == 1:
                    leaders, tie_break = by_wp, "wordplay_average"
                else:
                    return ScoreResult(
                        winner_id=None, is_draw=True, components=components,
                        tie_break_used="requires_sudden_death_or_runoff",
                    )
        return ScoreResult(
            winner_id=leaders[0], is_draw=False,
            components=components, tie_break_used=tie_break,
        )

    def public_state(self, round_record: RoundRecord) -> dict:
        return {
            "game_id": GAME_ID,
            "round_id": round_record.round_id,
            "phase": round_record.phase.value,
            "locked_prompt": round_record.locked_prompt,
            "outputs": round_record.outputs,
            "votes": round_record.votes,
        }
