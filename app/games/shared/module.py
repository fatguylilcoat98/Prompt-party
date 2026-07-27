"""Game module interface (Master Spec section 6).

All five games plug into the shared engine through this Protocol. The
engine owns state; a module only describes what is allowed and how to
build/parse AI requests. AI output never advances a round by itself.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.games.shared.phases import Phase
from app.games.shared.schemas import GameManifest, Modifier


class RoundState(BaseModel):
    """Compact controller-owned view of a round used for module decisions."""

    model_config = ConfigDict(extra="forbid")

    round_id: str
    show_id: str
    game_id: str
    phase: Phase
    locked_prompt: str | None = None
    active_modifier_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class GameAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    actor_type: str
    actor_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class GameContext(BaseModel):
    """Bounded context handed to a module when building an AI request."""

    model_config = ConfigDict(extra="forbid")

    state: RoundState
    participant_id: str
    persona_id: str
    recent_public_events: list[dict[str, Any]] = Field(default_factory=list)


class AIRequestSpec(BaseModel):
    """Module-produced request description; the engine turns this into a
    provider call. Modules never call providers directly."""

    model_config = ConfigDict(extra="forbid")

    operation: str
    template_id: str
    template_version: str
    system_prompt: str
    user_prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 45


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str
    valid: bool
    data: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None


class RoundRecord(BaseModel):
    """Replay/audit view of a completed or in-progress round."""

    model_config = ConfigDict(extra="forbid")

    round_id: str
    show_id: str
    game_id: str
    phase: Phase
    locked_prompt: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    votes: dict[str, int] = Field(default_factory=dict)
    judge_scores: dict[str, Any] = Field(default_factory=dict)
    failures: list[dict[str, Any]] = Field(default_factory=list)


class ScoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner_id: str | None
    is_draw: bool = False
    components: dict[str, Any] = Field(default_factory=dict)
    tie_break_used: str | None = None


class TransitionError(Exception):
    """Raised by a module (or the engine) when a phase transition is illegal."""


@runtime_checkable
class GameModule(Protocol):
    manifest: GameManifest

    def allowed_actions(self, state: RoundState) -> list[str]: ...

    def validate_transition(self, old: Phase, new: Phase) -> None:
        """Raise TransitionError if the transition is not legal."""
        ...

    def build_ai_request(
        self, action: GameAction, context: GameContext
    ) -> AIRequestSpec: ...

    def parse_ai_response(self, action: GameAction, raw: str) -> StructuredOutput: ...

    def apply_modifier(self, modifier: Modifier, state: RoundState) -> RoundState: ...

    def calculate_score(self, round_record: RoundRecord) -> ScoreResult: ...

    def public_state(self, round_record: RoundRecord) -> dict: ...
