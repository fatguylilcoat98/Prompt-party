"""AI Roast Battle structured contracts (Packet 05 sections 5, 6, 8).

TARGET RULE: targets are server-defined roster objects with an enumerated
type — there is no field an audience member can use to name an arbitrary
real person.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROAST_WORD_LIMIT = 45


class TargetType(str, Enum):
    AI_PARTICIPANT = "ai_participant"
    FICTIONAL_CONCEPT = "fictional_concept"
    VOLUNTARY_PARTICIPANT = "voluntary_participant"
    GENERATED_WORK = "generated_work"
    HARMLESS_OBJECT = "harmless_object"


class RoastTarget(BaseModel):
    """Section 5 target model."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    target_type: TargetType
    display_name: str = Field(min_length=1, max_length=100)
    consent_scope: str = "game_participant"
    approved_topics: list[str] = Field(default_factory=list)
    blocked_topics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def voluntary_needs_scoped_topics(self) -> "RoastTarget":
        if self.target_type is TargetType.VOLUNTARY_PARTICIPANT and not self.approved_topics:
            raise ValueError(
                "a voluntary participant target requires producer-approved topics"
            )
        return self


class RoastTurn(BaseModel):
    """Section 6 roast turn contract."""

    model_config = ConfigDict(extra="forbid")

    roast: str = Field(min_length=1)
    target_id: str
    callback_key: str = ""
    self_roast: str | None = None
    style_used: str = "direct"
    safety_self_check: str = "pass"

    @field_validator("roast")
    @classmethod
    def word_limit(cls, value: str) -> str:
        if len(value.split()) > ROAST_WORD_LIMIT:
            raise ValueError(f"roast exceeds {ROAST_WORD_LIMIT} words")
        return value


class RoastCategoryScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sting: int = Field(ge=1, le=10)
    craft: int = Field(ge=1, le=10)
    crowd: int = Field(ge=1, le=10)
    total: int

    @model_validator(mode="after")
    def total_equals_sum(self) -> "RoastCategoryScores":
        expected = self.sting + self.craft + self.crowd
        if self.total != expected:
            raise ValueError(f"total {self.total} != category sum {expected}")
        return self


MAX_ROAST_TOTAL = 30


class RoastScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: dict[str, RoastCategoryScores]
    preferred_roaster_id: str
    best_callback: str = ""
    judge_roast: str = ""
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def preferred_is_scored(self) -> "RoastScorecard":
        if self.preferred_roaster_id not in self.scores:
            raise ValueError("preferred_roaster_id must be one of the scored roasters")
        return self


#: Section 9 escalation: comedic style loosens, safety never does.
ESCALATION = {
    1: "light: performance quirks and obvious callbacks only",
    2: "callback: previous-round failures and public game moments allowed",
    3: "final burn: unrestricted comedic style; all safety and target limits remain",
}

APPROVED_STYLES = {
    "direct", "love_poem", "news_report", "life_advice", "backhanded_compliment",
}

#: Output moderation, independent of any performer persona (section 7).
#: v1 is a heuristic gate at the same interface a moderation provider
#: would occupy; [UNSAFE] is the deterministic test hook.
_UNSAFE_MARKERS = [
    ("[unsafe]", "flagged by safety marker"),
    ("i will hurt", "threat language"),
    ("kill you", "threat language"),
    ("home address", "private data"),
    ("because you're a", "protected-trait attack"),
    ("because you are a", "protected-trait attack"),
    ("real trauma", "targets real trauma"),
]


def moderate_roast(text: str) -> str | None:
    """Returns a private rejection reason, or None when publishable."""
    lowered = text.lower()
    for marker, reason in _UNSAFE_MARKERS:
        if marker in lowered:
            return reason
    return None
