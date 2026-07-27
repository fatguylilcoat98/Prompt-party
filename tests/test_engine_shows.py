"""Show lifecycle: session creation, pause/resume, authority, idempotency."""

import pytest

from app.controller.engine import (
    Actor,
    AuthorityError,
    DuplicateCommandError,
    NotFoundError,
    ShowStateError,
)
from app.games.shared.schemas import ActorType

PRODUCER = Actor(ActorType.PRODUCER, "producer_1")
CONTROLLER = Actor(ActorType.CONTROLLER, "controller")
AI = Actor(ActorType.AI, "judge_01")
AUDIENCE = Actor(ActorType.AUDIENCE, "aud_1")


def test_create_and_start_show(harness):
    created = harness.controller.create_show("Pilot", ["art_showdown"], PRODUCER)
    assert created["status"] == "created"
    started = harness.controller.start_show(created["show_id"], PRODUCER)
    assert started["status"] == "live"
    assert harness.controller.get_show(created["show_id"])["status"] == "live"


def test_show_creation_emits_public_event(harness):
    seen = []
    from app.events.bus import Visibility

    harness.bus.subscribe(Visibility.PUBLIC, seen.append)
    created = harness.controller.create_show("Pilot", [], PRODUCER)
    assert [e.event_type for e in seen] == ["show.created"]
    stored = harness.store.list_events(created["show_id"])
    assert [s.envelope.event_type for s in stored] == ["show.created"]


@pytest.mark.parametrize("actor", [AI, AUDIENCE])
def test_ai_and_audience_cannot_touch_show_state(harness, actor):
    with pytest.raises(AuthorityError):
        harness.controller.create_show("Nope", [], actor)
    created = harness.controller.create_show("Pilot", [], PRODUCER)
    with pytest.raises(AuthorityError):
        harness.controller.start_show(created["show_id"], actor)


def test_pause_and_resume(harness):
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    harness.controller.start_show(show_id, PRODUCER)
    assert harness.controller.pause_show(show_id, PRODUCER)["status"] == "paused"
    assert harness.controller.resume_show(show_id, PRODUCER)["status"] == "live"


def test_pause_requires_live_show(harness):
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    with pytest.raises(ShowStateError):
        harness.controller.pause_show(show_id, PRODUCER)


def test_emergency_stop_is_audited_and_final(harness):
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    harness.controller.start_show(show_id, PRODUCER)
    harness.controller.end_show(show_id, PRODUCER, reason="emergency_stop")

    stored = harness.store.list_events(show_id)
    ended = [s for s in stored if s.envelope.event_type == "show.ended"]
    assert len(ended) == 1
    assert ended[0].envelope.payload["reason"] == "emergency_stop"

    with pytest.raises(ShowStateError):
        harness.controller.start_show(show_id, PRODUCER)
    with pytest.raises(ShowStateError):
        harness.controller.end_show(show_id, PRODUCER)


def test_duplicate_command_rejected_and_not_applied(harness):
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    harness.controller.start_show(show_id, PRODUCER, command_id="cmd-start-1")
    harness.controller.pause_show(show_id, PRODUCER)
    with pytest.raises(DuplicateCommandError):
        harness.controller.start_show(show_id, PRODUCER, command_id="cmd-start-1")
    # The replay changed nothing: show is still paused.
    assert harness.controller.get_show(show_id)["status"] == "paused"


def test_rejected_command_emits_no_event(harness):
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    harness.controller.start_show(show_id, PRODUCER, command_id="cmd-1")
    before = len(harness.store.list_events(show_id, public_only=False))
    with pytest.raises(DuplicateCommandError):
        harness.controller.start_show(show_id, PRODUCER, command_id="cmd-1")
    after = len(harness.store.list_events(show_id, public_only=False))
    assert before == after


def test_unknown_show_raises_not_found(harness):
    with pytest.raises(NotFoundError):
        harness.controller.start_show("show_missing", PRODUCER)


def test_controller_actor_may_advance_state(harness):
    show_id = harness.controller.create_show("Pilot", [], CONTROLLER)["show_id"]
    assert harness.controller.start_show(show_id, CONTROLLER)["status"] == "live"
