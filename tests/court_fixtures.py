"""Cast and flow helpers for AI Court tests (Packet 03 section 2).

Cast convention: participant ids are the courtroom roles.
"""

from __future__ import annotations

from app.games.shared.phases import Phase
from tests.art_fixtures import PRODUCER, advance_to

def _seat(pid, name, seat_type, persona):
    return {
        "participant_id": pid, "display_name": name,
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": seat_type,
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": persona, "avatar": "", "enabled": True,
        "timeout_seconds": 45, "max_retries": 1,
    }


COURT_CAST = [
    _seat("judge", "Judge Gavelsworth", "commentator", "dry_procedural"),
    _seat("backup_judge", "Judge Backup", "commentator", "chaotic_backup"),
    _seat("prosecutor", "Prosecutor Zeal", "contestant", "overzealous"),
    _seat("defense", "Counsel Chill", "contestant", "unbothered"),
    _seat("w1", "Kitchen Spoon", "contestant", "chaotic_witness"),
]

CASE_SETUP = {
    "charge": "Intentional breakfast sabotage",
    "stipulated_facts": [
        "A bagel was burned",
        "The toaster was connected to power",
    ],
    "witnesses": [
        {"role": "Kitchen Spoon", "known_facts": ["Was present on counter"],
         "credibility": "chaotic"},
    ],
}


def make_court_round(harness) -> dict:
    show = harness.controller.create_show("Court Show", ["ai_court"], PRODUCER)
    harness.controller.start_show(show["show_id"], PRODUCER)
    round_ = harness.controller.create_round(
        show["show_id"], "ai_court", PRODUCER, cast=COURT_CAST
    )
    return {"show_id": show["show_id"], "round_id": round_["round_id"]}


def case_built_round(harness, premise="The People v. The Toaster") -> dict:
    ids = make_court_round(harness)
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_OPEN)
    member = harness.audience.join(ids["show_id"], "case_donor")
    submission = harness.moderation.submit(
        ids["round_id"], member["session_id"], premise, submission_type="case_premise"
    )
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_REVIEW)
    harness.moderation.review(submission["submission_id"], PRODUCER, "approve")
    harness.moderation.lock(ids["round_id"], submission["submission_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.ROUND_INTRO)
    case = harness.court.build_case(ids["round_id"], PRODUCER, **CASE_SETUP)
    advance_to(harness, ids["round_id"], Phase.CONTESTANT_PLANNING)
    advance_to(harness, ids["round_id"], Phase.CREATION_ACTIVE)
    return {**ids, "case": case["case"], "premise": premise}


async def run_trial(harness, ids) -> None:
    """Openings, one witness exam, closings."""
    await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")
    harness.court.advance_stage(ids["round_id"], PRODUCER)  # -> examination
    await harness.court.request_turn(ids["round_id"], PRODUCER, "w1")
    await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    harness.court.advance_stage(ids["round_id"], PRODUCER)  # -> closing
    await harness.court.request_turn(ids["round_id"], PRODUCER, "prosecution")
    await harness.court.request_turn(ids["round_id"], PRODUCER, "defense")
    harness.court.advance_stage(ids["round_id"], PRODUCER)  # -> done


async def to_jury(harness, ids, votes=("guilty", "guilty", "not_guilty")) -> None:
    await run_trial(harness, ids)
    advance_to(harness, ids["round_id"], Phase.AUDIENCE_VOTING)
    harness.voting.open(
        ids["round_id"], PRODUCER, ["guilty", "not_guilty", "legally_complicated"]
    )
    for i, choice in enumerate(votes):
        juror = harness.audience.join(ids["show_id"], f"juror{i}")
        harness.voting.cast(ids["round_id"], juror["session_id"], choice)
    harness.voting.close(ids["round_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.SCORING)
