"""AI Improv game module (Packet 04).

Default mode has no single winner (section 9); votes celebrate Best
Moment / Best Recovery. Tournament mode uses an audience MVP plurality —
the Host recommendation is advisory only.
"""

from __future__ import annotations

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

GAME_ID = "improv"


MANIFEST = GameManifest(
    game_id=GAME_ID,
    display_name="AI Improv",
    version="1.0",
    seat_requirements=[
        SeatRequirement(
            seat_type=SeatType.CONTESTANT, role="player", min_count=3, max_count=4,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
        SeatRequirement(
            seat_type=SeatType.HOST, role="host", min_count=0, max_count=1,
            required_capabilities=[Capability.TEXT_GENERATION],
        ),
    ],
    phases={
        Phase.LOBBY: "Cast, room, provider health, game selection",
        Phase.SUBMISSION_OPEN: "Audience submits location/object/relationship/emotion",
        Phase.SUBMISSION_REVIEW: "Screening and producer review",
        Phase.PROMPT_LOCKED: "Scene setup becomes immutable",
        Phase.ROUND_INTRO: "Scene and character assignments",
        Phase.CONTESTANT_PLANNING: "Players receive characters",
        Phase.CREATION_ACTIVE: "Scene play with twists and bells",
        Phase.PRE_REVEAL_COMMENTARY: "Host color during breaks",
        Phase.REVEAL: "The completed scene stands",
        Phase.FINAL_JUDGING: "Host recap",
        Phase.AUDIENCE_VOTING: "Best Moment / Best Recovery / MVP votes",
        Phase.SCORING: "Results, recap, persist, reset",
    },
    actions={
        Phase.ROUND_INTRO: {"producer": ["setup_scene"]},
        Phase.CREATION_ACTIVE: {
            "producer": ["request_line", "apply_state_updates", "apply_modifier",
                         "ring_bell", "end_scene", "host_recap"],
            "ai": ["perform"],
        },
        Phase.AUDIENCE_VOTING: {"producer": ["open_votes", "close_votes"], "audience": ["vote"]},
        Phase.SCORING: {"producer": ["calculate", "override"]},
    },
    prompts={"improv_turn": "1.0", "improv_recap": "1.0"},
    schemas={"improv_turn": "1.0", "scene_state": "1.0"},
    modifiers=[
        Modifier(
            modifier_id="freeze_replace", game_id=GAME_ID, display_name="Freeze & Replace",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["character"],
            duration="permanent",
            effect=ModifierEffect(type="character_swap", instruction="swap at next turn boundary"),
            fallback={"on_scene_ending": "reject"},
        ),
        Modifier(
            modifier_id="emotion_injection", game_id=GAME_ID, display_name="Emotion Injection",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["character"],
            duration="two_turns",
            effect=ModifierEffect(type="emotion_change", instruction="{{approved_emotion}}"),
            fallback={"rule": "keep_character_identity"},
        ),
        Modifier(
            modifier_id="time_jump", game_id=GAME_ID, display_name="Time Jump",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["scene"],
            duration="permanent",
            effect=ModifierEffect(type="time_anchor", instruction="{{approved_interval}}"),
            fallback={"record": "new_time_anchor"},
        ),
        Modifier(
            modifier_id="object_endowment", game_id=GAME_ID, display_name="Object Endowment",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["scene"],
            duration="permanent",
            effect=ModifierEffect(type="add_object", instruction="{{approved_object}}"),
            fallback={"record": "add_to_state_ledger"},
        ),
        Modifier(
            modifier_id="scene_hijack", game_id=GAME_ID, display_name="Scene Hijack",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["cast"],
            duration="permanent",
            effect=ModifierEffect(type="new_character", instruction="bring off-stage player in"),
            fallback={"require": "available_seat"},
        ),
        Modifier(
            modifier_id="genre_shift", game_id=GAME_ID, display_name="Genre Shift",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["scene"],
            duration="next_two_turns",
            effect=ModifierEffect(type="scene_constraint", instruction="{{approved_genre}}"),
            fallback={"rule": "state_facts_remain_true"},
        ),
        Modifier(
            modifier_id="one_word", game_id=GAME_ID, display_name="One Word",
            allowed_phases=[Phase.CREATION_ACTIVE], target_types=["scene"],
            duration="configured",
            effect=ModifierEffect(type="line_length_cap", instruction="one word per line"),
            fallback={"host": "may_end_early"},
        ),
    ],
    scoring={
        "votes": ["best_moment", "best_recovery", "mvp"],
        "default_mode": "no_single_winner",
        "tournament": "audience_mvp_plurality_host_advisory",
    },
    timeouts={"line": 30, "recap": 30},
    fallbacks={
        "non_sequitur": "host_bell_and_justification_turn",
        "denial": "one_constrained_revision",
        "state_extraction_failure": "keep_line_producer_confirms_updates",
        "player_timeout": "skip_replace_or_host_narrates_absence",
    },
)


class ImprovModule:
    manifest = MANIFEST

    def allowed_actions(self, state: RoundState) -> list[str]:
        actions = self.manifest.actions.get(state.phase, {})
        return sorted({a for group in actions.values() for a in group})

    def validate_transition(self, old: Phase, new: Phase) -> None:
        if phase_index(new) != phase_index(old) + 1:
            raise TransitionError(f"improv: illegal transition {old.value} -> {new.value}")

    def build_ai_request(self, action: GameAction, context: GameContext) -> AIRequestSpec:
        return AIRequestSpec(
            operation="text_generation",
            template_id="improv_turn",
            template_version=self.manifest.prompts["improv_turn"],
            system_prompt="You are a character in an AI improv scene.",
            user_prompt="Accept, add one playable detail, reference established "
                        "reality. Return JSON.",
            timeout_seconds=self.manifest.timeouts["line"],
        )

    def parse_ai_response(self, action: GameAction, raw: str) -> StructuredOutput:
        from app.games.improv.schemas import ImprovTurn

        try:
            turn = ImprovTurn.model_validate_json(raw)
            return StructuredOutput(schema_id="improv_turn", valid=True, data=turn.model_dump())
        except Exception as exc:
            return StructuredOutput(schema_id="improv_turn", valid=False, parse_error=str(exc))

    def apply_modifier(self, modifier: Modifier, state: RoundState) -> RoundState:
        if state.phase not in modifier.allowed_phases:
            raise ValueError(
                f"modifier {modifier.modifier_id} not allowed in phase {state.phase.value}"
            )
        return state.model_copy(update={"active_modifier_id": modifier.modifier_id})

    def calculate_score(self, round_record: RoundRecord) -> ScoreResult:
        """Votes are a plurality over the opened ballot (turn ids for Best
        Moment/Recovery, player ids for MVP). Default improv mode does not
        require a single winner; an empty ballot reports that mode."""
        votes = round_record.votes
        total = sum(votes.values())
        components = {
            choice: {
                "votes": count,
                "share": round(count / total, 6) if total else 0.0,
            }
            for choice, count in sorted(votes.items())
        }
        if total == 0:
            return ScoreResult(
                winner_id=None, is_draw=False, components=components,
                tie_break_used="default_mode_no_single_winner",
            )
        best = max(votes.values())
        leaders = sorted(c for c, v in votes.items() if v == best)
        if len(leaders) > 1:
            return ScoreResult(
                winner_id=None, is_draw=True, components=components,
                tie_break_used="host_advisory_then_producer",
            )
        return ScoreResult(winner_id=leaders[0], is_draw=False, components=components)

    def public_state(self, round_record: RoundRecord) -> dict:
        return {
            "game_id": GAME_ID,
            "round_id": round_record.round_id,
            "phase": round_record.phase.value,
            "locked_prompt": round_record.locked_prompt,
            "outputs": round_record.outputs,
            "votes": round_record.votes,
        }
