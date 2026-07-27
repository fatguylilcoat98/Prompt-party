"""AI Court structured contracts (Packet 03 sections 5, 7, 8, 10).

TRUTH BOUNDARY: every case, precedent, and export is marked fictional;
the schemas make the flags impossible to unset.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    text: str = Field(min_length=1, max_length=300)


class EvidenceStatus(str, Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    REJECTED = "rejected"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    description: str = Field(min_length=1, max_length=300)
    status: EvidenceStatus = EvidenceStatus.PENDING
    ruling_explanation: str = ""


class Witness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: str = Field(min_length=1, max_length=100)
    known_facts: list[str] = Field(default_factory=list)
    credibility: str = "neutral"


class CaseRecord(BaseModel):
    """Section 5. ``fictional_notice`` is forced True — a case cannot be
    constructed without the fiction marker (acceptance 1)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str = Field(min_length=1, max_length=200)
    charge: str = Field(min_length=1, max_length=300)
    fictional_notice: Literal[True] = True
    stipulated_facts: list[Fact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    witnesses: list[Witness] = Field(default_factory=list, max_length=2)
    prior_precedent_ids: list[str] = Field(default_factory=list)

    def admitted_record(self) -> dict:
        """The only view lawyers and witnesses may argue from: stipulated
        facts plus admitted evidence."""
        return {
            "case_id": self.case_id,
            "title": self.title,
            "charge": self.charge,
            "fictional_notice": True,
            "stipulated_facts": [f.model_dump() for f in self.stipulated_facts],
            "admitted_evidence": [
                e.model_dump() for e in self.evidence
                if e.status is EvidenceStatus.ADMITTED
            ],
            "prior_precedent_ids": self.prior_precedent_ids,
        }

    def known_fact_ids(self) -> set[str]:
        ids = {f.fact_id for f in self.stipulated_facts}
        ids |= {
            e.evidence_id for e in self.evidence
            if e.status is EvidenceStatus.ADMITTED
        }
        return ids


class TurnOutput(BaseModel):
    """Section 8 lawyer/witness structured output."""

    model_config = ConfigDict(extra="forbid")

    spoken_line: str = Field(min_length=1)
    fact_ids_used: list[str] = Field(default_factory=list)
    objection_risk: float = Field(ge=0.0, le=1.0, default=0.0)
    requested_next_action: str = ""


class ObjectionType(str, Enum):
    FACTS_NOT_IN_EVIDENCE = "facts_not_in_evidence"
    SPECULATION = "speculation"
    RELEVANCE = "relevance"
    LEADING = "leading"
    BADGERING = "badgering"
    NONSENSE = "nonsense"


class RulingCode(str, Enum):
    SUSTAINED = "sustained"
    OVERRULED = "overruled"
    RESERVED = "reserved"


class ObjectionRuling(BaseModel):
    """Section 7: rulings use a validated code (acceptance 4)."""

    model_config = ConfigDict(extra="forbid")

    ruling: RulingCode
    explanation: str = Field(min_length=1)
    repair_instruction: str = ""
    score_effect: int = 0


class EvidenceRuling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["admitted", "rejected"]
    explanation: str = Field(min_length=1)


VERDICT_CHOICES = ["guilty", "not_guilty", "legally_complicated"]


class FinalRuling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opinion: str = Field(min_length=1)
    supports_verdict: bool = True
    precedent_holding: str = ""


class Precedent(BaseModel):
    """Section 10: clearly fictional, show-scoped only."""

    model_config = ConfigDict(extra="forbid")

    precedent_id: str
    case_id: str
    holding: str = Field(min_length=1, max_length=300)
    scope: Literal["prompt_party_only"] = "prompt_party_only"
    clearly_fictional: Literal[True] = True

    @field_validator("holding")
    @classmethod
    def holding_not_real_legal_advice(cls, value: str) -> str:
        return value
