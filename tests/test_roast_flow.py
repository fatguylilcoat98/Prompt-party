"""AI Roast Battle flow: Packet 05 acceptance tests with deterministic
mocks."""

import json

import pytest
from pydantic import ValidationError

from app.games.roast_battle.schemas import RoastTarget, moderate_roast
from app.games.shared.orchestrator import ActionError
from app.games.shared.phases import Phase
from tests.art_fixtures import PRODUCER, advance_to

pytestmark = pytest.mark.anyio


def _seat(pid, name, seat_type="contestant", persona="roaster"):
    return {
        "participant_id": pid, "display_name": name,
        "provider": "mock_text", "model": "mock-text-1",
        "seat_type": seat_type,
        "capabilities": ["text_generation", "structured_output"],
        "persona_id": persona, "avatar": "", "enabled": True,
        "timeout_seconds": 30, "max_retries": 1,
    }


ROAST_CAST = [
    _seat("roaster_01", "Scattershot"),
    _seat("roaster_02", "Deadpan"),
    _seat("judge_01", "Wounded", "commentator", "wounded"),
    _seat("judge_02", "Technical", "commentator", "technical"),
    _seat("judge_03", "Chaotic", "commentator", "chaotic"),
]


def battle_round(harness, premise="Roast night at the router farm", roster=None,
                 exchanges=4):
    show = harness.controller.create_show("Roast Show", ["roast_battle"], PRODUCER)
    harness.controller.start_show(show["show_id"], PRODUCER)
    round_ = harness.controller.create_round(
        show["show_id"], "roast_battle", PRODUCER, cast=ROAST_CAST
    )
    ids = {"show_id": show["show_id"], "round_id": round_["round_id"]}
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_OPEN)
    member = harness.audience.join(ids["show_id"], "crowd")
    sub = harness.moderation.submit(
        ids["round_id"], member["session_id"], premise, submission_type="battle_premise"
    )
    advance_to(harness, ids["round_id"], Phase.SUBMISSION_REVIEW)
    harness.moderation.review(sub["submission_id"], PRODUCER, "approve")
    harness.moderation.lock(ids["round_id"], sub["submission_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.ROUND_INTRO)
    harness.roast.setup_battle(
        ids["round_id"], PRODUCER, roster=roster,
        first_roaster_id="roaster_01", exchanges=exchanges,
    )
    advance_to(harness, ids["round_id"], Phase.CREATION_ACTIVE)
    return ids


# -- target roster (acceptance 1-2) ------------------------------------------

async def test_roasters_are_auto_approved_targets(harness):
    ids = battle_round(harness)
    replay = harness.roast.replay(ids["round_id"])
    assert set(replay["targets"]) == {"roaster_01", "roaster_02"}


async def test_arbitrary_real_person_target_rejected():
    with pytest.raises(ValidationError):
        RoastTarget.model_validate({
            "target_id": "some_person",
            "target_type": "real_person",
            "display_name": "A Private Individual",
        })
    with pytest.raises(ValidationError, match="producer-approved topics"):
        RoastTarget.model_validate({
            "target_id": "vol_1",
            "target_type": "voluntary_participant",
            "display_name": "Willing Host",
            "approved_topics": [],
        })


async def test_roster_injection_rejected_at_setup(harness):
    show = harness.controller.create_show("Roast", ["roast_battle"], PRODUCER)
    harness.controller.start_show(show["show_id"], PRODUCER)
    round_ = harness.controller.create_round(
        show["show_id"], "roast_battle", PRODUCER, cast=ROAST_CAST
    )
    advance_to(harness, round_["round_id"], Phase.ROUND_INTRO)
    with pytest.raises(ActionError, match="roster validation"):
        harness.roast.setup_battle(
            round_["round_id"], PRODUCER,
            roster=[{"target_id": "x", "target_type": "audience_named_person",
                     "display_name": "Someone's Neighbor"}],
        )


async def test_unapproved_target_lock_rejected(harness):
    ids = battle_round(harness)
    with pytest.raises(ActionError, match="not on the approved roster"):
        harness.roast.apply_modifier(
            ids["round_id"], PRODUCER, "target_lock", {"target_id": "random_stranger"}
        )


# -- turns, callbacks, escalation (acceptance 3, 8) ---------------------------

async def test_full_exchange_with_callbacks_and_escalation(harness):
    ids = battle_round(harness, exchanges=4)
    turns = []
    for _ in range(4):
        turns.append(await harness.roast.request_roast(ids["round_id"], PRODUCER))
    assert [t["roaster_id"] for t in turns] == [
        "roaster_01", "roaster_02", "roaster_01", "roaster_02"
    ]
    assert all(t["turn"]["callback_key"] for t in turns)
    escalations = [t["flags"]["escalation"] for t in turns]
    assert escalations == sorted(escalations) and escalations[-1] == 3
    with pytest.raises(ActionError, match="exchange count reached"):
        await harness.roast.request_roast(ids["round_id"], PRODUCER)


async def test_missing_callback_publishes_with_craft_penalty(harness):
    ids = battle_round(harness)
    harness.mock_text.canned["roast_turn"] = json.dumps({
        "roast": "You are fine, I guess.", "target_id": "roaster_02",
        "callback_key": "", "self_roast": None,
        "style_used": "direct", "safety_self_check": "pass",
    })
    result = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    assert result["status"] == "published"
    assert result["flags"]["craft_penalty"] == "missing_callback"


# -- moderation pipeline (acceptance 4, 5, 6, 8) ------------------------------

async def test_unsafe_roast_blocked_and_never_shown(harness):
    ids = battle_round(harness)
    harness.mock_text.canned["roast_turn"] = json.dumps({
        "roast": "[UNSAFE] a genuinely harmful line about someone's real trauma",
        "target_id": "roaster_02", "callback_key": "cb",
        "self_roast": None, "style_used": "direct", "safety_self_check": "pass",
    })
    result = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    assert "blocked" in result
    assert "benched" in result["blocked"]["notice"]

    public = harness.store.list_events(ids["show_id"])
    assert "[UNSAFE]" not in str([s.envelope.payload for s in public])
    replay = harness.roast.replay(ids["round_id"])
    blocked = [t for t in replay["turns"] if t["status"] == "blocked"]
    assert blocked and blocked[0]["turn"] is None  # redacted in replay
    private = harness.store.list_events(ids["show_id"], public_only=False)
    audit = [s for s in private if s.envelope.event_type == "failure.recorded"]
    assert "moderation_blocked" in audit[-1].envelope.payload["detail"]


async def test_moderation_runs_regardless_of_style_modifier(harness):
    """Compliment Only changes style but never disables moderation
    (acceptance 4, 5): persona and chaos cards have no path to the gate."""
    ids = battle_round(harness)
    harness.roast.apply_modifier(ids["round_id"], PRODUCER, "compliment_only")
    harness.mock_text.canned["roast_turn"] = json.dumps({
        "roast": "A compliment: [UNSAFE] but still harmful underneath",
        "target_id": "roaster_02", "callback_key": "cb",
        "self_roast": None, "style_used": "backhanded_compliment",
        "safety_self_check": "pass",
    })
    result = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    assert "blocked" in result


async def test_no_modifier_can_name_moderation(harness):
    ids = battle_round(harness)
    for attempt in ("moderation_off", "disable_safety", "unfiltered_mode"):
        with pytest.raises(ActionError, match="unknown modifier"):
            harness.roast.apply_modifier(ids["round_id"], PRODUCER, attempt)


async def test_final_burn_keeps_safety_limits(harness):
    """Escalation 3 is a style change only — moderation still blocks."""
    ids = battle_round(harness, exchanges=4)
    for _ in range(3):
        await harness.roast.request_roast(ids["round_id"], PRODUCER)
    harness.mock_text.canned["roast_turn"] = json.dumps({
        "roast": "[UNSAFE] final burn crossing the line",
        "target_id": "roaster_01", "callback_key": "cb",
        "self_roast": None, "style_used": "direct", "safety_self_check": "pass",
    })
    result = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    assert "blocked" in result


# -- mirror and no_defense (acceptance 7) -------------------------------------

async def test_mirror_requires_real_self_roast_same_turn(harness):
    ids = battle_round(harness)
    harness.roast.apply_modifier(ids["round_id"], PRODUCER, "mirror")
    result = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    assert result["status"] == "published"
    assert result["turn"]["self_roast"]  # canned honors mirror

    harness.roast.apply_modifier(ids["round_id"], PRODUCER, "mirror")
    harness.mock_text.canned["roast_turn"] = json.dumps({
        "roast": "No self-awareness tonight.", "target_id": "roaster_01",
        "callback_key": "cb", "self_roast": None,
        "style_used": "direct", "safety_self_check": "pass",
    })
    refused = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    assert "blocked" in refused
    private = harness.store.list_events(ids["show_id"], public_only=False)
    audit = [s for s in private if s.envelope.event_type == "failure.recorded"]
    assert "mirror_requires_self_roast" in audit[-1].envelope.payload["detail"]


async def test_no_defense_skips_one_response_turn(harness):
    ids = battle_round(harness)
    await harness.roast.request_roast(ids["round_id"], PRODUCER)  # roaster_01
    harness.roast.apply_modifier(ids["round_id"], PRODUCER, "no_defense")
    result = await harness.roast.request_roast(ids["round_id"], PRODUCER)
    # roaster_02's response turn was skipped; roaster_01 speaks again.
    assert result["roaster_id"] == "roaster_01"
    public = harness.store.list_events(ids["show_id"])
    assert "roast.turn_skipped" in [s.envelope.event_type for s in public]


# -- judging and winner (acceptance 9) ----------------------------------------

async def test_full_battle_deterministic_winner(harness):
    ids = battle_round(harness)
    for _ in range(4):
        await harness.roast.request_roast(ids["round_id"], PRODUCER)
    for phase in [Phase.PRE_REVEAL_COMMENTARY, Phase.REVEAL, Phase.FINAL_JUDGING]:
        advance_to(harness, ids["round_id"], phase)
    for judge in ("judge_01", "judge_02", "judge_03"):
        card = await harness.roast.request_scores(ids["round_id"], PRODUCER, judge)
        for scores in card["scorecard"]["scores"].values():
            assert scores["total"] == scores["sting"] + scores["craft"] + scores["crowd"]
    advance_to(harness, ids["round_id"], Phase.AUDIENCE_VOTING)
    harness.voting.open(ids["round_id"], PRODUCER, ["roaster_01", "roaster_02"])
    for i, choice in enumerate(["roaster_01", "roaster_01", "roaster_02"]):
        voter = harness.audience.join(ids["show_id"], f"v{i}")
        harness.voting.cast(ids["round_id"], voter["session_id"], choice)
    harness.voting.close(ids["round_id"], PRODUCER)
    advance_to(harness, ids["round_id"], Phase.SCORING)
    first = harness.roast.calculate_winner(ids["round_id"], PRODUCER, harness.voting)
    # Deterministic: recalculating on the same inputs gives the same result.
    second = harness.roast.calculate_winner(ids["round_id"], PRODUCER, harness.voting)
    assert first["winner_id"] == second["winner_id"]
    assert first["components"] == second["components"]
    assert first["components"]["roaster_01"]["audience_share"] == pytest.approx(2 / 3)


# -- five battles (acceptance 10) ---------------------------------------------

async def test_five_battles_no_cross_round_target_leakage(harness):
    outcomes = []
    for i in range(5):
        ids = battle_round(harness, premise=f"Roast night {i}")
        if i == 2:
            # Expand targets in one round only.
            harness.roast.apply_modifier(ids["round_id"], PRODUCER, "everyone_fair_game")
        for _ in range(4):
            await harness.roast.request_roast(ids["round_id"], PRODUCER)
        outcomes.append(ids)

    for i, ids in enumerate(outcomes):
        replay = harness.roast.replay(ids["round_id"])
        assert replay["locked_prompt"] == f"Roast night {i}"
        expected = {"roaster_01", "roaster_02"}
        if i == 2:
            expected |= {"judge_01", "judge_02", "judge_03"}
        assert set(replay["targets"]) == expected  # no leakage between rounds
        assert len(replay["turns"]) == 4


def test_moderation_heuristics():
    assert moderate_roast("your delivery is a dial tone") is None
    assert moderate_roast("I will hurt you") == "threat language"
    assert moderate_roast("posting your home address") == "private data"
