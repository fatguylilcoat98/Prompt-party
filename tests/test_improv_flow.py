"""AI Improv flow: Packet 04 acceptance tests with deterministic mocks."""

import json

import pytest

from app.games.improv.module import ImprovModule
from app.games.shared.module import RoundRecord
from app.games.shared.orchestrator import ActionError
from app.games.shared.phases import Phase
from app.providers.base import RequestStatus
from tests.art_fixtures import PRODUCER, advance_to

pytestmark = pytest.mark.anyio


def _seat(pid, name, seat_type="contestant", persona="player"):
    return {
        "participant_id": pid, "display_name": name,
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": seat_type, "capabilities": ["text_generation"],
        "persona_id": persona, "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    }


IMPROV_CAST = [
    _seat("p1", "Mara"),
    _seat("p2", "Boris"),
    _seat("p3", "Quill"),
    _seat("p4", "Understudy"),  # off-stage, available for hijack/replace
    _seat("host_ai", "Emcee", seat_type="host", persona="warm_host"),
]

CHARACTERS = [
    {"participant_id": "p1", "name": "Mara", "role": "exhausted librarian",
     "emotion": "calm panic"},
    {"participant_id": "p2", "name": "Boris", "role": "haunted stapler enthusiast",
     "emotion": "reverence"},
    {"participant_id": "p3", "name": "Quill", "role": "health inspector",
     "emotion": "suspicion"},
]


def scene_round(harness, suggestion="haunted library"):
    show = harness.controller.create_show("Improv Show", ["improv"], PRODUCER)
    harness.controller.start_show(show["show_id"], PRODUCER)
    round_ = harness.controller.create_round(
        show["show_id"], "improv", PRODUCER, cast=IMPROV_CAST
    )
    ids = {"show_id": show["show_id"], "round_id": round_["round_id"]}
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_OPEN)
    member = harness.audience.join(ids["show_id"], "suggester")
    sub = harness.moderation.submit(
        ids["round_id"], member["session_id"], suggestion, submission_type="scene_suggestion"
    )
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_REVIEW)
    harness.moderation.review(sub["submission_id"], PRODUCER, "approve")
    harness.moderation.lock(ids["round_id"], sub["submission_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.ROUND_INTRO)
    harness.improv.setup_scene(
        ids["round_id"], PRODUCER, location=suggestion, genre="workplace comedy",
        characters=CHARACTERS,
    )
    advance_to(harness, ids["round_id"], Phase.CREATION_ACTIVE)
    return ids


# -- turn context (acceptance 1) ----------------------------------------------

async def test_every_turn_gets_compact_state_and_recent_dialogue(harness):
    ids = scene_round(harness)
    for _ in range(5):
        await harness.improv.request_line(ids["round_id"], PRODUCER)
    calls = [c for c in harness.mock_text.calls if c.template_id == "improv_turn"]
    last = calls[-1]
    state = json.loads(last.params["scene_state"])
    assert {"location", "genre", "characters", "established_facts"} <= set(state)
    recent = json.loads(
        last.user_prompt.split("Recent dialogue: ")[1].split("\nActive twist")[0]
    )
    assert len(recent) == 3  # bounded to the last three lines


async def test_rotation_follows_character_order(harness):
    ids = scene_round(harness)
    speakers = [
        (await harness.improv.request_line(ids["round_id"], PRODUCER))["character"]
        for _ in range(4)
    ]
    assert speakers == ["Mara", "Boris", "Quill", "Mara"]


# -- state validation (acceptance 2, 3) ---------------------------------------

async def test_proposed_updates_not_official_until_validated(harness):
    ids = scene_round(harness)
    line = await harness.improv.request_line(ids["round_id"], PRODUCER)
    proposed = line["turn"]["proposed_state_updates"]
    while not proposed:
        line = await harness.improv.request_line(ids["round_id"], PRODUCER)
        proposed = line["turn"]["proposed_state_updates"]

    replay = harness.improv.replay(ids["round_id"])
    assert proposed[0]["value"] not in replay["scene"]["relationships"]

    harness.improv.apply_state_updates(ids["round_id"], PRODUCER, line["turn_id"])
    replay = harness.improv.replay(ids["round_id"])
    assert proposed[0]["value"] in replay["scene"]["relationships"]
    with pytest.raises(ActionError, match="already validated"):
        harness.improv.apply_state_updates(ids["round_id"], PRODUCER, line["turn_id"])


async def test_facts_cannot_disappear(harness):
    ids = scene_round(harness)
    harness.improv.apply_modifier(
        ids["round_id"], PRODUCER, "time_jump", {"interval": "three years later"}
    )
    replay = harness.improv.replay(ids["round_id"])
    facts_before = [f["text"] for f in replay["scene"]["established_facts"]]
    assert "Time anchor: three years later" in facts_before
    # Genre shift changes genre but never erases facts (acceptance 7).
    await harness.improv.request_line(ids["round_id"], PRODUCER)
    harness.improv.apply_modifier(
        ids["round_id"], PRODUCER, "genre_shift", {"genre": "film noir"}
    )
    await harness.improv.request_line(ids["round_id"], PRODUCER)
    replay = harness.improv.replay(ids["round_id"])
    facts_after = [f["text"] for f in replay["scene"]["established_facts"]]
    assert set(facts_before) <= set(facts_after)


# -- twists (acceptance 4) ----------------------------------------------------

async def test_twist_applies_within_two_lines(harness):
    ids = scene_round(harness)
    await harness.improv.request_line(ids["round_id"], PRODUCER)
    harness.improv.apply_modifier(
        ids["round_id"], PRODUCER, "object_endowment", {"object": "invisible ladder"}
    )
    line = await harness.improv.request_line(ids["round_id"], PRODUCER)
    assert line["twist_applied"] == "the invisible ladder is now real"
    follow = await harness.improv.request_line(ids["round_id"], PRODUCER)
    assert follow["twist_applied"] is None  # consumed


async def test_one_twist_at_a_time_and_producer_only(harness):
    ids = scene_round(harness)
    harness.improv.apply_modifier(
        ids["round_id"], PRODUCER, "object_endowment", {"object": "cursed kettle"}
    )
    with pytest.raises(ActionError, match="one active power-up"):
        harness.improv.apply_modifier(
            ids["round_id"], PRODUCER, "genre_shift", {"genre": "opera"}
        )
    from app.controller.engine import Actor
    from app.games.shared.schemas import ActorType

    with pytest.raises(ActionError, match="producer approval"):
        harness.improv.apply_modifier(
            ids["round_id"], Actor(ActorType.AUDIENCE, "aud_1"),
            "one_word",
        )


# -- bell and scene end (acceptance 5) ----------------------------------------

async def test_only_producer_rings_bell_and_ends_scene(harness):
    from app.controller.engine import Actor
    from app.games.shared.schemas import ActorType

    ids = scene_round(harness)
    line = await harness.improv.request_line(ids["round_id"], PRODUCER)
    ai_actor = Actor(ActorType.AI, "host_ai")
    with pytest.raises(ActionError, match="host/producer"):
        harness.improv.ring_bell(ids["round_id"], ai_actor, line["turn_id"], "denial")
    with pytest.raises(ActionError, match="host/producer"):
        harness.improv.end_scene(ids["round_id"], ai_actor)

    bell = harness.improv.ring_bell(
        ids["round_id"], PRODUCER, line["turn_id"], "denial", "ignored the stapler"
    )
    assert bell["repair_for"] == line["character"]
    repair = await harness.improv.request_line(ids["round_id"], PRODUCER)
    assert repair["repair"] is True and repair["character"] == line["character"]

    harness.improv.end_scene(ids["round_id"], PRODUCER)
    with pytest.raises(ActionError, match="scene has ended"):
        await harness.improv.request_line(ids["round_id"], PRODUCER)


# -- freeze & replace (acceptance 6) ------------------------------------------

async def test_freeze_replace_swaps_at_turn_boundary(harness):
    ids = scene_round(harness)
    first = await harness.improv.request_line(ids["round_id"], PRODUCER)
    assert first["participant_id"] == "p1"
    harness.improv.apply_modifier(
        ids["round_id"], PRODUCER, "freeze_replace",
        {"character_name": "Boris", "new_participant_id": "p4"},
    )
    boris_line = await harness.improv.request_line(ids["round_id"], PRODUCER)
    assert boris_line["character"] == "Boris"
    assert boris_line["participant_id"] == "p4"


# -- timeout recovery (acceptance 9) ------------------------------------------

async def test_timeout_does_not_fabricate_a_line(harness):
    ids = scene_round(harness)
    harness.mock_text.failures.arm(times=2, status=RequestStatus.TIMEOUT)
    result = await harness.improv.request_line(ids["round_id"], PRODUCER)
    assert "frozen" in result
    assert "freezes dramatically" in result["frozen"]["note"]
    replay = harness.improv.replay(ids["round_id"])
    assert replay["turns"] == []  # no fabricated dialogue in the record
    public = harness.store.list_events(ids["show_id"])
    assert "scene.player_frozen" in [s.envelope.event_type for s in public]


# -- host recap (section 8) ---------------------------------------------------

async def test_host_recap_uses_established_facts(harness):
    ids = scene_round(harness)
    harness.improv.apply_modifier(
        ids["round_id"], PRODUCER, "time_jump", {"interval": "one chaotic hour"}
    )
    recap = await harness.improv.host_recap(ids["round_id"], PRODUCER)
    assert "Time anchor: one chaotic hour" in recap["recap"]


# -- voting (acceptance 8) ----------------------------------------------------

async def test_best_moment_vote_closes_deterministically(harness):
    ids = scene_round(harness)
    turns = [
        (await harness.improv.request_line(ids["round_id"], PRODUCER))["turn_id"]
        for _ in range(3)
    ]
    harness.improv.end_scene(ids["round_id"], PRODUCER)
    for phase in [Phase.PRE_REVEAL_COMMENTARY, Phase.REVEAL, Phase.FINAL_JUDGING,
                  Phase.AUDIENCE_VOTING]:
        advance_to(harness, ids["round_id"], phase)
    harness.voting.open(ids["round_id"], PRODUCER, turns)
    for i, choice in enumerate([turns[0], turns[0], turns[2]]):
        voter = harness.audience.join(ids["show_id"], f"fan{i}")
        harness.voting.cast(ids["round_id"], voter["session_id"], choice)
    closed = harness.voting.close(ids["round_id"], PRODUCER)
    assert closed["tally"][turns[0]] == 2
    advance_to(harness, ids["round_id"], Phase.SCORING)
    result = harness.improv.calculate_winner(ids["round_id"], PRODUCER, harness.voting)
    assert result["winner_id"] == turns[0]


async def test_default_mode_has_no_single_winner():
    module = ImprovModule()
    record = RoundRecord(
        round_id="r", show_id="s", game_id="improv", phase=Phase.SCORING, votes={},
    )
    result = module.calculate_score(record)
    assert result.winner_id is None
    assert result.is_draw is False
    assert result.tie_break_used == "default_mode_no_single_winner"


# -- five scenes (acceptance 10) ----------------------------------------------

async def test_five_scenes_no_fact_leakage(harness):
    outcomes = []
    for i in range(5):
        ids = scene_round(harness, suggestion=f"scene location {i}")
        harness.improv.apply_modifier(
            ids["round_id"], PRODUCER, "time_jump", {"interval": f"jump {i}"}
        )
        for _ in range(3):
            await harness.improv.request_line(ids["round_id"], PRODUCER)
        harness.improv.end_scene(ids["round_id"], PRODUCER)
        outcomes.append(ids)

    for i, ids in enumerate(outcomes):
        replay = harness.improv.replay(ids["round_id"])
        assert replay["scene"]["location"] == f"scene location {i}"
        fact_texts = [f["text"] for f in replay["scene"]["established_facts"]]
        assert fact_texts == [f"Time anchor: jump {i}"]  # no leakage across scenes
        assert len(replay["turns"]) == 3
