"""AI Rap Battle structured output schemas (Packet 02 sections 7, 9)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Absolute schema bounds; the configured per-battle cap (8-12 normal,
#: 4-6 speed round) is enforced by the orchestrator which knows the
#: active modifier.
MAX_BARS_ABSOLUTE = 16

DEFAULT_BAR_RANGE = (8, 12)
SPEED_ROUND_BAR_RANGE = (4, 6)


class RapVerse(BaseModel):
    """Section 7: bars as an array, never one unbounded blob."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    bars: list[str] = Field(min_length=1, max_length=MAX_BARS_ABSOLUTE)
    response_to: str = ""
    callbacks: list[str] = Field(default_factory=list)
    forced_words_used: list[str] = Field(default_factory=list)
    delivery_notes: str = ""
    safety_self_check: str = "pass"

    @model_validator(mode="after")
    def bars_not_blank(self) -> "RapVerse":
        if any(not bar.strip() for bar in self.bars):
            raise ValueError("bars may not contain blank lines")
        return self


class RapperCategoryScores(BaseModel):
    """Section 9: integer categories 1-10; total must equal the sum."""

    model_config = ConfigDict(extra="forbid")

    wordplay: int = Field(ge=1, le=10)
    aggression: int = Field(ge=1, le=10)
    entertainment: int = Field(ge=1, le=10)
    total: int

    @model_validator(mode="after")
    def total_equals_sum(self) -> "RapperCategoryScores":
        expected = self.wordplay + self.aggression + self.entertainment
        if self.total != expected:
            raise ValueError(f"total {self.total} != category sum {expected}")
        return self


MAX_RAP_TOTAL = 30  # three categories x 10


class RapScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: dict[str, RapperCategoryScores]  # rapper_id -> categories
    preferred_rapper_id: str
    best_bar_reference: str = ""
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def preferred_is_scored(self) -> "RapScorecard":
        if self.preferred_rapper_id not in self.scores:
            raise ValueError("preferred_rapper_id must be one of the scored rappers")
        return self


def normalize_word(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalnum())


def forced_words_missing(bars: list[str], forced_words: list[str]) -> list[str]:
    """Case-insensitive, normalized comparison (section 7)."""
    text = " ".join(normalize_word(w) for bar in bars for w in bar.split())
    haystack = f" {text} "
    return [w for w in forced_words if f" {normalize_word(w)} " not in haystack]
