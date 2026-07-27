"""Shared data contracts: participant (5.1), modifier (5.3), manifest (6)."""

import pytest
from pydantic import ValidationError

from app.games.shared.phases import Phase
from app.games.shared.schemas import (
    GameManifest,
    Modifier,
    Participant,
    SeatRequirement,
    SeatType,
)

PARTICIPANT_SPEC_EXAMPLE = {
    "participant_id": "judge_01",
    "display_name": "Verdict",
    "provider": "openai_compatible",
    "model": "configured-model",
    "seat_type": "commentator",
    "capabilities": ["text_generation", "vision_input", "structured_output"],
    "persona_id": "brutal_critic",
    "avatar": "/assets/avatars/verdict.webp",
    "enabled": True,
    "timeout_seconds": 45,
    "max_retries": 1,
}

MODIFIER_SPEC_EXAMPLE = {
    "modifier_id": "genre_shift",
    "game_id": "improv",
    "display_name": "Genre Shift",
    "allowed_phases": ["CREATION_ACTIVE"],
    "target_types": ["scene"],
    "duration": "next_two_turns",
    "requires_producer_approval": True,
    "audience_selectable": True,
    "effect": {"type": "scene_constraint", "instruction": "{{approved_genre}}"},
    "fallback": {"on_invalid_phase": "reject"},
}


def test_participant_spec_example_validates():
    p = Participant.model_validate(PARTICIPANT_SPEC_EXAMPLE)
    assert p.seat_type is SeatType.COMMENTATOR
    assert p.timeout_seconds == 45


def test_participant_rejects_unknown_capability():
    data = {**PARTICIPANT_SPEC_EXAMPLE, "capabilities": ["mind_reading"]}
    with pytest.raises(ValidationError):
        Participant.model_validate(data)


def test_modifier_spec_example_validates():
    m = Modifier.model_validate(MODIFIER_SPEC_EXAMPLE)
    assert m.allowed_phases == [Phase.CREATION_ACTIVE]
    assert m.requires_producer_approval is True


def test_modifier_rejects_unknown_phase():
    data = {**MODIFIER_SPEC_EXAMPLE, "allowed_phases": ["FREESTYLE_TIME"]}
    with pytest.raises(ValidationError):
        Modifier.model_validate(data)


def test_manifest_minimal():
    manifest = GameManifest(
        game_id="art_showdown",
        display_name="AI Art Showdown",
        version="1.0",
        seat_requirements=[
            SeatRequirement(
                seat_type=SeatType.CONTESTANT,
                role="artist",
                min_count=2,
                max_count=3,
                required_capabilities=["image_generation"],
            ),
            SeatRequirement(
                seat_type=SeatType.COMMENTATOR,
                role="judge",
                min_count=3,
                max_count=3,
                required_capabilities=["text_generation"],
            ),
        ],
        phases={Phase.LOBBY: "Cast, room, provider health, game selection"},
        modifiers=[Modifier.model_validate(MODIFIER_SPEC_EXAMPLE)],
        scoring={"audience_weight": 0.6, "judge_weight": 0.4},
    )
    assert manifest.scoring["audience_weight"] == 0.6


def test_manifest_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        GameManifest(
            game_id="x",
            display_name="X",
            version="1.0",
            seat_requirements=[],
            surprise_field=True,
        )
