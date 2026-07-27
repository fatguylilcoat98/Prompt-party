"""AI Improv structured contracts (Packet 04 sections 5, 6)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SENTENCES = 3

UPDATE_TYPES = {"fact", "object", "relationship", "thread", "emotion", "time"}


class SceneCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_id: str
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(min_length=1, max_length=100)
    emotion: str = Field(min_length=1, max_length=60)


class SceneFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    text: str = Field(min_length=1, max_length=300)


class SceneState(BaseModel):
    """Section 5 scene-state ledger. There is deliberately no operation
    that removes an established fact (acceptance 3)."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    location: str = Field(min_length=1, max_length=120)
    genre: str = Field(min_length=1, max_length=60)
    characters: list[SceneCharacter] = Field(min_length=3, max_length=4)
    established_facts: list[SceneFact] = Field(default_factory=list)
    active_objects: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
    active_modifier: dict | None = None
    turn_index: int = 0

    def compact(self) -> dict:
        return {
            "location": self.location,
            "genre": self.genre,
            "characters": [
                {"name": c.name, "role": c.role, "emotion": c.emotion}
                for c in self.characters
            ],
            "established_facts": [f.model_dump() for f in self.established_facts],
            "active_objects": self.active_objects,
            "relationships": self.relationships,
            "unresolved_threads": self.unresolved_threads,
        }


class ProposedUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    value: str = Field(min_length=1, max_length=300)

    @field_validator("type")
    @classmethod
    def known_type(cls, value: str) -> str:
        if value not in UPDATE_TYPES:
            raise ValueError(f"unknown update type {value!r}")
        return value


class ImprovTurn(BaseModel):
    """Section 6 turn contract: 1-3 sentences, accept + add + reference."""

    model_config = ConfigDict(extra="forbid")

    spoken_line: str = Field(min_length=1)
    accepted_offer: str = ""
    new_offer: str = ""
    referenced_fact_ids: list[str] = Field(default_factory=list)
    proposed_state_updates: list[ProposedUpdate] = Field(default_factory=list)

    @field_validator("spoken_line")
    @classmethod
    def at_most_three_sentences(cls, value: str) -> str:
        sentences = [s for s in re.split(r"[.!?]+", value) if s.strip()]
        if len(sentences) > MAX_SENTENCES:
            raise ValueError(f"spoken_line exceeds {MAX_SENTENCES} sentences")
        return value


#: Yes-and checks (section 7). These produce *recommendations* for the
#: host/producer — the bell is always manual (acceptance 5).
def yes_and_recommendations(turn: ImprovTurn, has_prior_line: bool,
                            has_facts: bool) -> list[str]:
    recommendations = []
    if has_prior_line and not turn.accepted_offer.strip():
        recommendations.append("possible_denial_no_accepted_offer")
    if not turn.new_offer.strip():
        recommendations.append("no_new_playable_detail")
    if has_facts and not turn.referenced_fact_ids:
        recommendations.append("no_reference_to_established_reality")
    escape_markers = ("wake up", "it was all a dream", "the scene ends", "end scene")
    if any(marker in turn.spoken_line.lower() for marker in escape_markers):
        recommendations.append("possible_scene_escape")
    return recommendations
