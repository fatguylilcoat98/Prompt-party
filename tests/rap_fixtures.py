"""Cast and flow helpers for AI Rap Battle tests (Packet 02 section 2)."""

from __future__ import annotations

from app.games.shared.phases import Phase
from tests.art_fixtures import PRODUCER, advance_to, make_round as _make_round

RAP_CAST = [
    {
        "participant_id": "rapper_01", "display_name": "Packet Loss",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "contestant",
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": "technical", "avatar": "", "enabled": True,
        "timeout_seconds": 45, "max_retries": 1,
    },
    {
        "participant_id": "rapper_02", "display_name": "Sir Loops-a-Lot",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "contestant",
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": "absurdist", "avatar": "", "enabled": True,
        "timeout_seconds": 45, "max_retries": 1,
    },
    {
        "participant_id": "judge_01", "display_name": "Purist",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "commentator",
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": "purist", "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    },
    {
        "participant_id": "judge_02", "display_name": "Hype",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "commentator",
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": "hype_person", "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    },
    {
        "participant_id": "judge_03", "display_name": "Snark",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "commentator",
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": "snarker", "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    },
]


def make_rap_round(harness) -> dict:
    show = harness.controller.create_show("Rap Show", ["rap_battle"], PRODUCER)
    harness.controller.start_show(show["show_id"], PRODUCER)
    round_ = harness.controller.create_round(
        show["show_id"], "rap_battle", PRODUCER, cast=RAP_CAST
    )
    return {"show_id": show["show_id"], "round_id": round_["round_id"]}


def locked_rap_round(harness, topic="Dial-up internet versus fiber") -> dict:
    ids = make_rap_round(harness)
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_OPEN)
    member = harness.audience.join(ids["show_id"], "topic_donor")
    submission = harness.moderation.submit(
        ids["round_id"], member["session_id"], topic, submission_type="battle_topic"
    )
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_REVIEW)
    harness.moderation.review(submission["submission_id"], PRODUCER, "approve")
    harness.moderation.lock(ids["round_id"], submission["submission_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.ROUND_INTRO)
    return {**ids, "topic": topic}
