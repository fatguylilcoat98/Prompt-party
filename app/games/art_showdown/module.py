"""AI Art Showdown game module (Packet 01).

Implements the shared GameModule protocol: manifest, transition
validation, scoring (section 11: 60% audience / 40% normalized judges,
spec tie order), and the public-state projection.
"""

from __future__ import annotations

from app.games.shared.module import (
    GameAction,
    GameContext,
    AIRequestSpec,
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
from app.games.art_showdown.schemas import (
    MAX_CATEGORY_TOTAL,
    ArtistPlan,
    JudgeScorecard,
)

GAME_ID = "art_showdown"

AUDIENCE_WEIGHT = 0.6
JUDGE_WEIGHT = 0.4


MANIFEST = GameManifest(
    game_id=GAME_ID,
    display_name="AI Art Showdown",
    version="1.0",
    seat_requirements=[
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="artist", min_count=2, max_count=3,
            required_capabilities=[Capability.IMAGE_GENERATION],
            optional_capabilities=[Capability.TEXT_GENERATION],
        ),
        SeatRequirement(
            seat_type=SeatType.COMMENTATOR, role="judge", min_count=3, max_count=3,
            required_capabilities=[Capability.TEXT_GENERATION],
            optional_capabilities=[Capability.VISION_INPUT],
        ),
        SeatRequirement(
            seat_type=SeatType.HOST, role="host", min_count=0, max_count=1,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
    ],
    phases={
        Phase.LOBBY: "Cast, room, provider health, game selection",
        Phase.SUBMISSION_OPEN: "Audience submits scene prompts",
        Phase.SUBMISSION_REVIEW: "Screening and producer review",
        Phase.PROMPT_LOCKED: "Challenge becomes immutable",
        Phase.ROUND_INTRO: "Broadcast framing and artist assignment",
        Phase.CONTESTANT_PLANNING: "<=60-word visual plans",
        Phase.CREATION_ACTIVE: "Image generation",
        Phase.PRE_REVEAL_COMMENTARY: "Judges discuss prompt and plans only",
        Phase.REVEAL: "Validated images shown",
        Phase.FINAL_JUDGING: "Vision-grounded category scores",
        Phase.AUDIENCE_VOTING: "Audience vote opens and closes",
        Phase.SCORING: "Calculate, announce, persist, reset",
    },
    actions={
        Phase.SUBMISSION_OPEN: {"producer": ["open_queue", "close_queue"], "audience": ["submit"]},
        Phase.SUBMISSION_REVIEW: {"producer": ["approve", "edit", "reject", "lock"]},
        Phase.CONTESTANT_PLANNING: {"producer": ["request_plans"], "ai": ["plan"], "audience": ["select_modifier"]},
        Phase.CREATION_ACTIVE: {"producer": ["start_generation", "retry", "replace"], "ai": ["generate"]},
        Phase.PRE_REVEAL_COMMENTARY: {"producer": ["request_commentary", "pause_comments"], "ai": ["comment"]},
        Phase.REVEAL: {"producer": ["reveal"]},
        Phase.FINAL_JUDGING: {"producer": ["request_scores"], "ai": ["score"]},
        Phase.AUDIENCE_VOTING: {"producer": ["open_votes", "close_votes"], "audience": ["vote"]},
        Phase.SCORING: {"producer": ["calculate", "override", "draw"]},
    },
    prompts={"artist_plan": "1.0", "judge_commentary": "1.0", "judge_scorecard": "1.0"},
    schemas={"artist_plan": "1.0", "judge_comment": "1.0", "judge_scorecard": "1.0"},
    modifiers=[
        Modifier(
            modifier_id="glitch_brush", game_id=GAME_ID, display_name="Glitch Brush",
            allowed_phases=[Phase.CONTESTANT_PLANNING], target_types=["artist"],
            duration="current_round",
            effect=ModifierEffect(type="aesthetic_constraint", instruction="corrupted aesthetic"),
            fallback={"on_invalid_phase": "reject_after_generation_starts"},
        ),
        Modifier(
            modifier_id="theme_swap", game_id=GAME_ID, display_name="Theme Swap",
            allowed_phases=[Phase.CONTESTANT_PLANNING], target_types=["round"],
            duration="current_round",
            effect=ModifierEffect(type="mandatory_theme", instruction="{{approved_theme}}"),
            fallback={"on_invalid_phase": "queue_next_round_or_regenerate"},
        ),
        Modifier(
            modifier_id="judge_blindfold", game_id=GAME_ID, display_name="Judge Blindfold",
            allowed_phases=[Phase.FINAL_JUDGING], target_types=["judge"],
            duration="current_round",
            effect=ModifierEffect(type="blind_judging", instruction="score plans/descriptions only"),
            fallback={"on_invalid_phase": "reject"},
        ),
        Modifier(
            modifier_id="double_exposure", game_id=GAME_ID, display_name="Double Exposure",
            allowed_phases=[Phase.CONTESTANT_PLANNING], target_types=["round"],
            duration="current_round",
            effect=ModifierEffect(type="shared_detail", instruction="adopt one approved opponent detail"),
            fallback={"on_missing_plans": "reject"},
        ),
        Modifier(
            modifier_id="gallery_floods", game_id=GAME_ID, display_name="Gallery Floods",
            allowed_phases=[Phase.SCORING], target_types=["display"],
            duration="temporary_display",
            effect=ModifierEffect(type="display_only_inversion", instruction="invert displayed judge totals, then restore"),
            fallback={"rule": "never_alter_stored_official_score"},
        ),
        Modifier(
            modifier_id="critic_meltdown", game_id=GAME_ID, display_name="Critic Meltdown",
            allowed_phases=[Phase.FINAL_JUDGING], target_types=["judge"],
            duration="current_round",
            effect=ModifierEffect(type="judge_abstains", instruction="monologue and abstain"),
            fallback={"scoring": "reweight_remaining_valid_judges"},
        ),
    ],
    scoring={
        "audience_weight": AUDIENCE_WEIGHT,
        "judge_weight": JUDGE_WEIGHT,
        "tie_order": [
            "audience_share", "comedy_average", "sudden_death_sketch",
            "audience_runoff", "producer_draw",
        ],
    },
    timeouts={"plan": 45, "generation": 120, "comment": 30, "scorecard": 60},
    fallbacks={
        "image_timeout": "retry_backup_reveal_other_or_exhibition",
        "invalid_file": "quarantine_and_retry",
        "judge_vision_failure": "retry_or_blind_judge",
        "unsafe_output": "block_reveal_and_replace",
    },
)


class ArtShowdownModule:
    manifest = MANIFEST

    def allowed_actions(self, state: RoundState) -> list[str]:
        actions = self.manifest.actions.get(state.phase, {})
        return sorted({a for group in actions.values() for a in group})

    def validate_transition(self, old: Phase, new: Phase) -> None:
        if phase_index(new) != phase_index(old) + 1:
            raise TransitionError(
                f"art_showdown: illegal transition {old.value} -> {new.value}"
            )

    def build_ai_request(self, action: GameAction, context: GameContext) -> AIRequestSpec:
        # The orchestrator builds requests directly for v1; this protocol
        # method mirrors the plan request as the canonical example.
        state = context.state
        return AIRequestSpec(
            operation="text_generation",
            template_id="artist_plan",
            template_version=self.manifest.prompts["artist_plan"],
            system_prompt="You are an AI art contestant in a live comedy game show.",
            user_prompt=(
                f"CHALLENGE: {state.locked_prompt}\n"
                f"PERSONA: {context.persona_id}\n"
                f"ACTIVE MODIFIER: {state.active_modifier_id or 'none'}\n"
                "Return JSON only. Describe the intended image in no more than "
                "60 words. Include concept_title, visual_plan, key_details[1..4], "
                "composition_choice, intended_tone. Do not claim that an image "
                "already exists."
            ),
            timeout_seconds=self.manifest.timeouts["plan"],
        )

    def parse_ai_response(self, action: GameAction, raw: str) -> StructuredOutput:
        try:
            plan = ArtistPlan.model_validate_json(raw)
            return StructuredOutput(schema_id="artist_plan", valid=True, data=plan.model_dump())
        except Exception as exc:
            return StructuredOutput(
                schema_id="artist_plan", valid=False, parse_error=str(exc)
            )

    def apply_modifier(self, modifier: Modifier, state: RoundState) -> RoundState:
        if state.phase not in modifier.allowed_phases:
            raise ValueError(
                f"modifier {modifier.modifier_id} not allowed in phase {state.phase.value}"
            )
        return state.model_copy(update={"active_modifier_id": modifier.modifier_id})

    # -- scoring (Packet 01 section 11) ----------------------------------

    def calculate_score(self, round_record: RoundRecord) -> ScoreResult:
        votes = round_record.votes
        scorecards = {
            judge_id: JudgeScorecard.model_validate(
                {k: v for k, v in card.items() if k not in {"blind", "abstained"}}
            )
            for judge_id, card in round_record.judge_scores.items()
            if not card.get("abstained")
        }
        artist_ids = sorted(
            {a for card in scorecards.values() for a in card.scores}
            | set(votes.keys())
        )
        if not artist_ids:
            return ScoreResult(winner_id=None, is_draw=True, components={})

        total_votes = sum(votes.values())
        components: dict[str, dict] = {}
        for artist in artist_ids:
            share = (votes.get(artist, 0) / total_votes) if total_votes else 0.0
            totals = [
                card.scores[artist].total
                for card in scorecards.values()
                if artist in card.scores
            ]
            judge_norm = (
                (sum(totals) / len(totals)) / MAX_CATEGORY_TOTAL if totals else 0.0
            )
            comedy = [
                card.scores[artist].comedy
                for card in scorecards.values()
                if artist in card.scores
            ]
            components[artist] = {
                "audience_share": round(share, 6),
                "judge_normalized": round(judge_norm, 6),
                "comedy_average": round(sum(comedy) / len(comedy), 6) if comedy else 0.0,
                "combined": round(AUDIENCE_WEIGHT * share + JUDGE_WEIGHT * judge_norm, 6),
            }

        def ranked(key: str) -> list[str]:
            best = max(components[a][key] for a in artist_ids)
            return [a for a in artist_ids if components[a][key] == best]

        leaders = ranked("combined")
        tie_break = None
        if len(leaders) > 1:
            # Tie order: higher audience share, then higher comedy average.
            by_share = [a for a in leaders if components[a]["audience_share"]
                        == max(components[x]["audience_share"] for x in leaders)]
            if len(by_share) == 1:
                leaders, tie_break = by_share, "audience_share"
            else:
                by_comedy = [a for a in by_share if components[a]["comedy_average"]
                             == max(components[x]["comedy_average"] for x in by_share)]
                if len(by_comedy) == 1:
                    leaders, tie_break = by_comedy, "comedy_average"
                else:
                    # Sudden death / runoff / producer draw need a live
                    # decision; report the unresolved tie.
                    return ScoreResult(
                        winner_id=None, is_draw=True, components=components,
                        tie_break_used="requires_sudden_death_or_producer",
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
