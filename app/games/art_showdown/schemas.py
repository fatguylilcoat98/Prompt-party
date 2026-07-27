"""AI Art Showdown structured output schemas (Packet 01 sections 7, 9, 10)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLAN_WORD_LIMIT = 60
COMMENT_WORD_LIMIT = 45

#: Phrases that would claim sight of an unrevealed image (Packet 01
#: section 9: "Before reveal, never claim to see an image").
IMAGE_CLAIM_MARKERS = (
    "i can see", "i see the image", "looking at the image", "the image shows",
    "in the image", "this image", "the finished piece shows",
)


class ArtistPlan(BaseModel):
    """Section 7: <=60-word plan; never claims an image already exists."""

    model_config = ConfigDict(extra="forbid")

    concept_title: str = Field(min_length=1, max_length=120)
    visual_plan: str = Field(min_length=1)
    key_details: list[str] = Field(min_length=1, max_length=4)
    composition_choice: str = Field(min_length=1, max_length=120)
    intended_tone: str = Field(min_length=1, max_length=120)

    @field_validator("visual_plan")
    @classmethod
    def enforce_word_limit(cls, value: str) -> str:
        if len(value.split()) > PLAN_WORD_LIMIT:
            raise ValueError(f"visual_plan exceeds {PLAN_WORD_LIMIT} words")
        return value

    @field_validator("visual_plan")
    @classmethod
    def no_existing_image_claim(cls, value: str) -> str:
        lowered = value.lower()
        if any(marker in lowered for marker in IMAGE_CLAIM_MARKERS):
            raise ValueError("plan may not claim an image already exists")
        return value


class JudgeComment(BaseModel):
    """Section 9 commentary contract."""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1)
    target_type: str = Field(min_length=1)  # prompt | plan | judge | artist
    target_id: str = ""
    tone: str = ""
    callback_key: str = ""

    @field_validator("comment")
    @classmethod
    def enforce_word_limit(cls, value: str) -> str:
        if len(value.split()) > COMMENT_WORD_LIMIT:
            raise ValueError(f"comment exceeds {COMMENT_WORD_LIMIT} words")
        return value


def comment_claims_image(comment: str) -> bool:
    lowered = comment.lower()
    return any(marker in lowered for marker in IMAGE_CLAIM_MARKERS)


class ArtistCategoryScores(BaseModel):
    """Section 10: integer categories 1-10; total must equal the sum."""

    model_config = ConfigDict(extra="forbid")

    prompt_accuracy: int = Field(ge=1, le=10)
    creativity: int = Field(ge=1, le=10)
    visual_quality: int = Field(ge=1, le=10)
    comedy: int = Field(ge=1, le=10)
    total: int

    @model_validator(mode="after")
    def total_equals_sum(self) -> "ArtistCategoryScores":
        expected = (
            self.prompt_accuracy + self.creativity + self.visual_quality + self.comedy
        )
        if self.total != expected:
            raise ValueError(f"total {self.total} != category sum {expected}")
        return self


MAX_CATEGORY_TOTAL = 40  # four categories x 10


class JudgeScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: dict[str, ArtistCategoryScores]  # artist_id -> categories
    preferred_artist_id: str
    critique: str = Field(min_length=1)
    best_visible_detail: str = ""
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def preferred_is_scored(self) -> "JudgeScorecard":
        if self.preferred_artist_id not in self.scores:
            raise ValueError("preferred_artist_id must be one of the scored artists")
        return self
