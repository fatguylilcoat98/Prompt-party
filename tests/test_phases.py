"""Shared 12-phase lifecycle (Master Spec section 4)."""

from app.games.shared.phases import PHASE_ORDER, Phase, phase_index

SPEC_ORDER = [
    "LOBBY",
    "SUBMISSION_OPEN",
    "SUBMISSION_REVIEW",
    "PROMPT_LOCKED",
    "ROUND_INTRO",
    "CONTESTANT_PLANNING",
    "CREATION_ACTIVE",
    "PRE_REVEAL_COMMENTARY",
    "REVEAL",
    "FINAL_JUDGING",
    "AUDIENCE_VOTING",
    "SCORING",
]


def test_exactly_twelve_phases():
    assert len(Phase) == 12


def test_phase_order_matches_master_spec_table():
    assert [p.value for p in PHASE_ORDER] == SPEC_ORDER


def test_phase_index_is_monotonic():
    indices = [phase_index(p) for p in PHASE_ORDER]
    assert indices == sorted(indices)
    assert phase_index(Phase.LOBBY) == 0
    assert phase_index(Phase.SCORING) == 11
