"""Shared data contracts (Master Spec section 5).

These pydantic models are the validated shapes for participants, the event
envelope, and controlled modifiers. Every game module and every surface
(broadcast, audience, producer, replay) speaks these types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from app.games.shared.phases import Phase


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SeatType(str, Enum):
    CONTESTANT = "contestant"
    COMMENTATOR = "commentator"
    HOST = "host"


class Capability(str, Enum):
    TEXT_GENERATION = "text_generation"
    IMAGE_GENERATION = "image_generation"
    VISION_INPUT = "vision_input"
    STRUCTURED_OUTPUT = "structured_output"
    TEXT_TO_SPEECH = "text_to_speech"
    AUDIO_GENERATION = "audio_generation"


class ActorType(str, Enum):
    AI = "ai"
    PRODUCER = "producer"
    AUDIENCE = "audience"
    CONTROLLER = "controller"
    SYSTEM = "system"


class Participant(BaseModel):
    """Master Spec 5.1."""

    model_config = ConfigDict(extra="forbid")

    participant_id: str
    display_name: str
    provider: str
    model: str
    seat_type: SeatType
    capabilities: list[Capability]
    persona_id: str
    avatar: str = ""
    enabled: bool = True
    timeout_seconds: int = 45
    max_retries: int = 1


class EventEnvelope(BaseModel):
    """Master Spec 5.2. Every broadcast/audience/producer/replay event uses
    this envelope. ``public`` controls server-side visibility filtering:
    private events never leave the producer/replay surfaces."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: str = Field(min_length=1)
    show_id: str = Field(min_length=1)
    round_id: str | None = None
    game_id: str | None = None
    phase: Phase | None = None
    actor_type: ActorType
    actor_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    public: bool
    payload: dict[str, Any] = Field(default_factory=dict)


class ModifierEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    instruction: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class Modifier(BaseModel):
    """Master Spec 5.3. Controlled modifier: audience power-ups and chaos
    cards resolve only through these server-defined objects. Audience
    choices select approved values; they never inject raw instructions
    into model prompts."""

    model_config = ConfigDict(extra="forbid")

    modifier_id: str
    game_id: str
    display_name: str
    allowed_phases: list[Phase]
    target_types: list[str]
    duration: str = "current_phase"
    requires_producer_approval: bool = True
    audience_selectable: bool = True
    effect: ModifierEffect
    fallback: dict[str, str] = Field(default_factory=dict)


class SeatRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seat_type: SeatType
    role: str
    min_count: int = 1
    max_count: int = 1
    required_capabilities: list[Capability] = Field(default_factory=list)
    optional_capabilities: list[Capability] = Field(default_factory=list)


class GameManifest(BaseModel):
    """Master Spec section 6 manifest fields."""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    display_name: str
    version: str
    seat_requirements: list[SeatRequirement]
    phases: dict[Phase, str] = Field(
        default_factory=dict,
        description="Shared phase -> game-specific meaning",
    )
    actions: dict[Phase, dict[str, list[str]]] = Field(
        default_factory=dict,
        description="Phase -> {producer|ai|audience: [action ids]}",
    )
    prompts: dict[str, str] = Field(
        default_factory=dict, description="Template id -> version"
    )
    schemas: dict[str, str] = Field(
        default_factory=dict, description="Output schema id -> version"
    )
    modifiers: list[Modifier] = Field(default_factory=list)
    scoring: dict[str, Any] = Field(default_factory=dict)
    timeouts: dict[str, int] = Field(default_factory=dict)
    fallbacks: dict[str, str] = Field(default_factory=dict)
