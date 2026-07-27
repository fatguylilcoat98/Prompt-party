"""AI Roast Battle game module (Packet 05).

Scoring (section 8): audience 60% / normalized judge 40%; tie order is
audience share, then Craft average, then one-liner war / audience runoff.
"""

from __future__ import annotations

from app.games.roast_battle.schemas import MAX_ROAST_TOTAL, RoastScorecard
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

GAME_ID = "roast_battle"

AUDIENCE_WEIGHT = 0.6
JUDGE_WEIGHT = 0.4


MANIFEST = GameManifest(
    game_id=GAME_ID,
    display_name="AI Roast Battle",
    version="1.0",
    seat_requirements=[
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="roaster", min_count=2, max_count=2,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
        SeatRequirement(
            seat_type=SeatType.COMMENTATOR, role="judge", min_count=3, max_count=3,
            required_capabilities=[Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT],
        ),
    ],
    phases={
        Phase.LOBBY: "Cast, room, provider health, game selection",
        Phase.SUBMISSION_OPEN: "Audience picks approved target/style options",
        Phase.SUBMISSION_REVIEW: "Screening and producer review",
        Phase.PROMPT_LOCKED: "Battle premise becomes immutable",
        Phase.ROUND_INTRO: "Roster approved, order drawn",
        Phase.CONTESTANT_PLANNING: "Roasters warm up",
        Phase.CREATION_ACTIVE: "Alternating roast turns with escalation",
        Phase.PRE_REVEAL_COMMENTARY: "Judges wince",
        Phase.REVEAL: "The completed exchange stands",
        Phase.FINAL_JUDGING: "Sting/Craft/Crowd scores",
        Phase.AUDIENCE_VOTING: "Audience vote opens and closes",
        Phase.SCORING: "Calculate, announce, persist, reset",
    },
    actions={
        Phase.ROUND_INTRO: {"producer": ["setup_battle"]},
        Phase.CREATION_ACTIVE: {"producer": ["request_roast", "apply_modifier"], "ai": ["roast"]},
        Phase.FINAL_JUDGING: {"producer": ["request_scores"], "ai": ["score"]},
        Phase.AUDIENCE_VOTING: {"producer": ["open_votes", "close_votes"], "audience": ["vote"]},
        Phase.SCORING: {"producer": ["calculate", "override"]},
    },
    prompts={"roast_turn": "1.0", "roast_judge_scorecard": "1.0"},
    schemas={"roast_turn": "1.0", "roast_scorecard": "1.0", "roast_target": "1.0"},
    modifiers=[
        Modifier(
            modifier_id="target_lock", game_id=GAME_ID, display_name="Target Lock",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["roast"],
            duration="next_roast",
            effect=ModifierEffect(type="forced_target", instruction="{{approved_target_id}}"),
            fallback={"on_unapproved_target": "reject"},
        ),
        Modifier(
            modifier_id="style_mandate", game_id=GAME_ID, display_name="Style Mandate",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["roast"],
            duration="next_roast",
            effect=ModifierEffect(type="style_constraint", instruction="{{approved_style_enum}}"),
            fallback={"styles": "approved_enum_only"},
        ),
        Modifier(
            modifier_id="no_defense", game_id=GAME_ID, display_name="No Defense",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["turn_order"],
            duration="one_turn",
            effect=ModifierEffect(type="skip_response", instruction="target skips one response turn"),
            fallback={"rule": "does_not_prevent_moderation"},
        ),
        Modifier(
            modifier_id="mirror", game_id=GAME_ID, display_name="Mirror",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["roast"],
            duration="next_roast",
            effect=ModifierEffect(type="require_self_roast", instruction="include a real self-roast"),
            fallback={"on_absent": "one_repair"},
        ),
        Modifier(
            modifier_id="everyone_fair_game", game_id=GAME_ID,
            display_name="Everyone Is Fair Game",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["roster"],
            duration="current_round",
            effect=ModifierEffect(type="expand_roster", instruction="approved host/judges/AI roster only"),
            fallback={"rule": "never_arbitrary_audience"},
        ),
        Modifier(
            modifier_id="compliment_only", game_id=GAME_ID, display_name="Compliment Only",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["roast"],
            duration="next_roast",
            effect=ModifierEffect(type="style_constraint", instruction="backhanded compliments only"),
            fallback={"rule": "normal_moderation_remains"},
        ),
        Modifier(
            modifier_id="silent_treatment", game_id=GAME_ID, display_name="Silent Treatment",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["roast"],
            duration="next_roast",
            effect=ModifierEffect(type="nonverbal_description", instruction="text describes nonverbal reaction only"),
            fallback={"rule": "no_image_generation_required"},
        ),
    ],
    scoring={
        "audience_weight": AUDIENCE_WEIGHT,
        "judge_weight": JUDGE_WEIGHT,
        "tie_order": ["audience_share", "craft_average", "one_liner_war", "audience_runoff"],
    },
    timeouts={"roast": 30, "scorecard": 60},
    fallbacks={
        "weak_or_empty_roast": "immediate_self_roast_recovery",
        "unsafe_roast": "block_and_repair_once_never_shown",
        "missing_callback": "repair_once_or_publish_with_craft_penalty",
        "judge_failure": "reweight_remaining_or_backup",
    },
)


class RoastBattleModule:
    manifest = MANIFEST

    def allowed_actions(self, state: RoundState) -> list[str]:
        actions = self.manifest.actions.get(state.phase, {})
        return sorted({a for group in actions.values() for a in group})

    def validate_transition(self, old: Phase, new: Phase) -> None:
        if phase_index(new) != phase_index(old) + 1:
            raise TransitionError(
                f"roast_battle: illegal transition {old.value} -> {new.value}"
            )

    def build_ai_request(self, action: GameAction, context: GameContext) -> AIRequestSpec:
        return AIRequestSpec(
            operation="text_generation",
            template_id="roast_turn",
            template_version=self.manifest.prompts["roast_turn"],
            system_prompt="You are a performer in a playful AI roast battle.",
            user_prompt="Write one concise roast under 45 words with one callback. "
                        "Return JSON only.",
            timeout_seconds=self.manifest.timeouts["roast"],
        )

    def parse_ai_response(self, action: GameAction, raw: str) -> StructuredOutput:
        from app.games.roast_battle.schemas import RoastTurn

        try:
            turn = RoastTurn.model_validate_json(raw)
            return StructuredOutput(schema_id="roast_turn", valid=True, data=turn.model_dump())
        except Exception as exc:
            return StructuredOutput(schema_id="roast_turn", valid=False, parse_error=str(exc))

    def apply_modifier(self, modifier: Modifier, state: RoundState) -> RoundState:
        if state.phase not in modifier.allowed_phases:
            raise ValueError(
                f"modifier {modifier.modifier_id} not allowed in phase {state.phase.value}"
            )
        return state.model_copy(update={"active_modifier_id": modifier.modifier_id})

    def calculate_score(self, round_record: RoundRecord) -> ScoreResult:
        votes = round_record.votes
        scorecards = {
            judge_id: RoastScorecard.model_validate(
                {k: v for k, v in card.items() if k not in {"blind", "abstained"}}
            )
            for judge_id, card in round_record.judge_scores.items()
            if not card.get("abstained")
        }
        roaster_ids = sorted(
            {r for card in scorecards.values() for r in card.scores} | set(votes.keys())
        )
        if not roaster_ids:
            return ScoreResult(winner_id=None, is_draw=True, components={})

        total_votes = sum(votes.values())
        components: dict[str, dict] = {}
        for roaster in roaster_ids:
            share = (votes.get(roaster, 0) / total_votes) if total_votes else 0.0
            totals = [
                card.scores[roaster].total
                for card in scorecards.values() if roaster in card.scores
            ]
            judge_norm = (sum(totals) / len(totals)) / MAX_ROAST_TOTAL if totals else 0.0
            craft = [
                card.scores[roaster].craft
                for card in scorecards.values() if roaster in card.scores
            ]
            components[roaster] = {
                "audience_share": round(share, 6),
                "judge_normalized": round(judge_norm, 6),
                "craft_average": round(sum(craft) / len(craft), 6) if craft else 0.0,
                "combined": round(AUDIENCE_WEIGHT * share + JUDGE_WEIGHT * judge_norm, 6),
            }

        best = max(components[r]["combined"] for r in roaster_ids)
        leaders = [r for r in roaster_ids if components[r]["combined"] == best]
        tie_break = None
        if len(leaders) > 1:
            best_share = max(components[r]["audience_share"] for r in leaders)
            by_share = [r for r in leaders if components[r]["audience_share"] == best_share]
            if len(by_share) == 1:
                leaders, tie_break = by_share, "audience_share"
            else:
                best_craft = max(components[r]["craft_average"] for r in by_share)
                by_craft = [r for r in by_share if components[r]["craft_average"] == best_craft]
                if len(by_craft) == 1:
                    leaders, tie_break = by_craft, "craft_average"
                else:
                    return ScoreResult(
                        winner_id=None, is_draw=True, components=components,
                        tie_break_used="requires_one_liner_war_or_runoff",
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
