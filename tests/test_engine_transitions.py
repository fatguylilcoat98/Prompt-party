"""Phase transitions: validation, authority, pause blocking, overrides.

Proves illegal transitions are rejected (Milestone 2 exit criterion,
Master Spec section 15).
"""

import pytest

from app.controller.engine import Actor, AuthorityError, ShowStateError
from app.games.shared.module import TransitionError
from app.games.shared.phases import PHASE_ORDER, Phase
from app.games.shared.schemas import ActorType

PRODUCER = Actor(ActorType.PRODUCER, "producer_1")
CONTROLLER = Actor(ActorType.CONTROLLER, "controller")
AI = Actor(ActorType.AI, "judge_01")
AUDIENCE = Actor(ActorType.AUDIENCE, "aud_1")


@pytest.fixture
def live_round(harness):
    show_id = harness.controller.create_show("Pilot", ["art_showdown"], PRODUCER)["show_id"]
    harness.controller.start_show(show_id, PRODUCER)
    round_id = harness.controller.create_round(show_id, "art_showdown", PRODUCER)["round_id"]
    return {"show_id": show_id, "round_id": round_id}


def test_round_starts_in_lobby(harness, live_round):
    assert harness.controller.get_round(live_round["round_id"])["phase"] == "LOBBY"


def test_full_forward_walk_through_all_twelve_phases(harness, live_round):
    for phase in PHASE_ORDER[1:]:
        result = harness.controller.transition_round(
            live_round["round_id"], phase, CONTROLLER
        )
        assert result["phase"] == phase.value
    assert harness.controller.get_round(live_round["round_id"])["phase"] == "SCORING"


def test_skipping_a_phase_is_rejected(harness, live_round):
    with pytest.raises(TransitionError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.SUBMISSION_REVIEW, CONTROLLER
        )
    assert harness.controller.get_round(live_round["round_id"])["phase"] == "LOBBY"


def test_backward_transition_is_rejected(harness, live_round):
    harness.controller.transition_round(
        live_round["round_id"], Phase.SUBMISSION_OPEN, CONTROLLER
    )
    with pytest.raises(TransitionError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.LOBBY, CONTROLLER
        )


def test_self_transition_is_rejected(harness, live_round):
    with pytest.raises(TransitionError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.LOBBY, CONTROLLER
        )


@pytest.mark.parametrize("actor", [AI, AUDIENCE])
def test_ai_and_audience_cannot_advance_a_round(harness, live_round, actor):
    with pytest.raises(AuthorityError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.SUBMISSION_OPEN, actor
        )
    assert harness.controller.get_round(live_round["round_id"])["phase"] == "LOBBY"


def test_paused_show_blocks_transitions(harness, live_round):
    harness.controller.pause_show(live_round["show_id"], PRODUCER)
    with pytest.raises(ShowStateError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.SUBMISSION_OPEN, CONTROLLER
        )
    harness.controller.resume_show(live_round["show_id"], PRODUCER)
    harness.controller.transition_round(
        live_round["round_id"], Phase.SUBMISSION_OPEN, CONTROLLER
    )


def test_ended_show_blocks_transitions(harness, live_round):
    harness.controller.end_show(live_round["show_id"], PRODUCER, reason="emergency_stop")
    with pytest.raises(ShowStateError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.SUBMISSION_OPEN, CONTROLLER
        )


def test_producer_override_allows_skip_and_is_audited(harness, live_round):
    harness.controller.transition_round(
        live_round["round_id"], Phase.REVEAL, PRODUCER,
        override=True, reason="image arrived early",
    )
    assert harness.controller.get_round(live_round["round_id"])["phase"] == "REVEAL"
    stored = harness.store.list_events(live_round["show_id"])
    overridden = [s for s in stored if s.envelope.event_type == "round.phase_overridden"]
    assert len(overridden) == 1
    payload = overridden[0].envelope.payload
    assert payload == {
        "from": "LOBBY", "to": "REVEAL",
        "override": True, "reason": "image arrived early",
    }


def test_override_is_producer_only(harness, live_round):
    with pytest.raises(AuthorityError):
        harness.controller.transition_round(
            live_round["round_id"], Phase.REVEAL, CONTROLLER, override=True
        )


def test_registered_module_can_reject_transitions(harness, live_round):
    class StrictManifest:
        game_id = "art_showdown"

    class StrictModule:
        manifest = StrictManifest()

        def validate_transition(self, old, new):
            if new is Phase.SUBMISSION_OPEN:
                raise TransitionError("module says not yet")

    harness.controller.register_module(StrictModule())
    with pytest.raises(TransitionError, match="module says not yet"):
        harness.controller.transition_round(
            live_round["round_id"], Phase.SUBMISSION_OPEN, CONTROLLER
        )


def test_transition_events_are_persisted_in_order(harness, live_round):
    for phase in [Phase.SUBMISSION_OPEN, Phase.SUBMISSION_REVIEW, Phase.PROMPT_LOCKED]:
        harness.controller.transition_round(live_round["round_id"], phase, CONTROLLER)
    stored = harness.store.list_events(live_round["show_id"])
    changes = [
        s.envelope.payload["to"]
        for s in stored
        if s.envelope.event_type == "round.phase_changed"
    ]
    assert changes == ["SUBMISSION_OPEN", "SUBMISSION_REVIEW", "PROMPT_LOCKED"]
    seqs = [s.seq for s in stored]
    assert seqs == sorted(seqs)
