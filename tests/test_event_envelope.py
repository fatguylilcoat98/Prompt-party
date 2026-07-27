"""Event envelope contract (Master Spec 5.2) and bus visibility filtering."""

import pytest
from pydantic import ValidationError

from app.events.bus import EventBus, Visibility
from app.games.shared.phases import Phase
from app.games.shared.schemas import ActorType, EventEnvelope

SPEC_EXAMPLE = {
    "event_id": "evt_01J0000000000000000000000",
    "event_type": "judge.commentary_created",
    "show_id": "show_01J0000000000000000000000",
    "round_id": "round_01J0000000000000000000000",
    "game_id": "art_showdown",
    "phase": "PRE_REVEAL_COMMENTARY",
    "actor_type": "ai",
    "actor_id": "judge_01",
    "timestamp": "2026-07-27T17:00:00Z",
    "public": True,
    "payload": {},
}


def test_spec_example_envelope_validates():
    event = EventEnvelope.model_validate(SPEC_EXAMPLE)
    assert event.phase is Phase.PRE_REVEAL_COMMENTARY
    assert event.actor_type is ActorType.AI
    assert event.public is True


def test_defaults_generate_id_and_timestamp():
    event = EventEnvelope(
        event_type="show.created",
        show_id="show_1",
        actor_type=ActorType.PRODUCER,
        actor_id="producer_1",
        public=False,
    )
    assert event.event_id.startswith("evt_")
    assert event.timestamp.tzinfo is not None


def test_visibility_flag_is_mandatory():
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type="show.created",
            show_id="show_1",
            actor_type=ActorType.PRODUCER,
            actor_id="producer_1",
        )


@pytest.mark.parametrize(
    "bad_field", [{"actor_type": "hacker"}, {"event_type": ""}, {"phase": "NOT_A_PHASE"}]
)
def test_invalid_values_rejected(bad_field):
    data = {**SPEC_EXAMPLE, **bad_field}
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(data)


def _event(public: bool) -> EventEnvelope:
    return EventEnvelope(
        event_type="moderation.note",
        show_id="show_1",
        actor_type=ActorType.CONTROLLER,
        actor_id="controller",
        public=public,
    )


def test_public_subscribers_never_receive_private_events():
    bus = EventBus()
    public_seen, all_seen = [], []
    bus.subscribe(Visibility.PUBLIC, public_seen.append)
    bus.subscribe(Visibility.ALL, all_seen.append)

    bus.publish(_event(public=True))
    bus.publish(_event(public=False))

    assert [e.public for e in public_seen] == [True]
    assert [e.public for e in all_seen] == [True, False]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen = []
    unsubscribe = bus.subscribe(Visibility.ALL, seen.append)
    bus.publish(_event(public=True))
    unsubscribe()
    bus.publish(_event(public=True))
    assert len(seen) == 1
