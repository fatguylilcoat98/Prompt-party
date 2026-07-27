"""Shared 12-phase game lifecycle (Master Spec section 4).

Every game module maps its behavior onto these shared phases. The final
"SCORING / WINNER / POST-ROUND" table row is a single phase named SCORING,
covering calculate, announce, banter, persist, and reset.
"""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    LOBBY = "LOBBY"
    SUBMISSION_OPEN = "SUBMISSION_OPEN"
    SUBMISSION_REVIEW = "SUBMISSION_REVIEW"
    PROMPT_LOCKED = "PROMPT_LOCKED"
    ROUND_INTRO = "ROUND_INTRO"
    CONTESTANT_PLANNING = "CONTESTANT_PLANNING"
    CREATION_ACTIVE = "CREATION_ACTIVE"
    PRE_REVEAL_COMMENTARY = "PRE_REVEAL_COMMENTARY"
    REVEAL = "REVEAL"
    FINAL_JUDGING = "FINAL_JUDGING"
    AUDIENCE_VOTING = "AUDIENCE_VOTING"
    SCORING = "SCORING"


#: Canonical forward order of the lifecycle.
PHASE_ORDER: tuple[Phase, ...] = tuple(Phase)


def phase_index(phase: Phase) -> int:
    return PHASE_ORDER.index(phase)
