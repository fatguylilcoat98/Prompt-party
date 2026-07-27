"""Cast and flow helpers for AI Art Showdown tests (Packet 01 section 2)."""

from __future__ import annotations

from app.controller.engine import Actor, CONTROLLER_ACTOR
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType

PRODUCER = Actor(ActorType.PRODUCER, "producer_1")

ART_CAST = [
    {
        "participant_id": "artist_01", "display_name": "Pix",
        "provider": "mock_image", "model": "mock-image-1",
        "seat_type": "contestant",
        "capabilities": ["image_generation", "text_generation"],
        "persona_id": "precision", "avatar": "", "enabled": True,
        "timeout_seconds": 45, "max_retries": 1,
    },
    {
        "participant_id": "artist_02", "display_name": "Smudge",
        "provider": "mock_image", "model": "mock-image-1",
        "seat_type": "contestant",
        "capabilities": ["image_generation", "text_generation"],
        "persona_id": "vibe", "avatar": "", "enabled": True,
        "timeout_seconds": 45, "max_retries": 1,
    },
    {
        "participant_id": "judge_01", "display_name": "Verdict",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "commentator",
        "capabilities": ["text_generation", "vision_input", "structured_output"],
        "persona_id": "formalist", "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    },
    {
        "participant_id": "judge_02", "display_name": "Litera",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "commentator",
        "capabilities": ["text_generation", "vision_input", "structured_output"],
        "persona_id": "literalist", "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    },
    {
        # Deliberately no vision_input: this judge must be labeled blind.
        "participant_id": "judge_03", "display_name": "Wreck",
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": "commentator",
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": "chaos_critic", "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    },
]

#: Artist plans depend on the mock text provider, so artists also need a
#: text provider entry — the orchestrator uses provider from the cast for
#: image generation; plans go through the same participant. For the mock
#: setup we point the plan calls at mock_text via a dedicated cast.
PLANNING_CAST = [
    {**ART_CAST[0], "provider": "mock_text"},
    {**ART_CAST[1], "provider": "mock_text"},
    *ART_CAST[2:],
]


def advance_to(harness, round_id: str, target: Phase) -> None:
    """Walk the round forward one legal phase at a time."""
    from app.games.shared.phases import PHASE_ORDER, phase_index

    current = Phase(harness.controller.get_round(round_id)["phase"])
    for phase in PHASE_ORDER[phase_index(current) + 1 : phase_index(target) + 1]:
        harness.controller.transition_round(round_id, phase, CONTROLLER_ACTOR)


def make_round(harness, cast=None) -> dict:
    show = harness.controller.create_show("Test Show", ["art_showdown"], PRODUCER)
    harness.controller.start_show(show["show_id"], PRODUCER)
    round_ = harness.controller.create_round(
        show["show_id"], "art_showdown", PRODUCER, cast=cast or ART_CAST
    )
    return {"show_id": show["show_id"], "round_id": round_["round_id"]}


def locked_round(harness, cast=None, prompt_text="A raccoon runs a luxury hotel for pigeons") -> dict:
    ids = make_round(harness, cast)
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_OPEN)
    member = harness.audience.join(ids["show_id"], "seat_filler")
    submission = harness.moderation.submit(ids["round_id"], member["session_id"], prompt_text)
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_REVIEW)
    harness.moderation.review(submission["submission_id"], PRODUCER, "approve")
    harness.moderation.lock(ids["round_id"], submission["submission_id"], PRODUCER)
    return {**ids, "member": member, "submission_id": submission["submission_id"],
            "prompt": prompt_text}
