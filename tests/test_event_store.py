"""Event persistence: ordering, public/private separation, atomicity,
failure recording."""

import pytest

from app.controller.engine import Actor
from app.events.bus import Visibility
from app.games.shared.schemas import ActorType
from app.persistence.db import session_scope
from app.persistence.models import ErrorRecord

PRODUCER = Actor(ActorType.PRODUCER, "producer_1")


@pytest.fixture
def show_id(harness):
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    harness.controller.start_show(show_id, PRODUCER)
    return show_id


def test_private_events_hidden_from_public_reads(harness, show_id):
    harness.controller.record_failure(
        show_id=show_id, round_id=None,
        error_type="provider_timeout", detail="raw stack trace here",
        provider="mock_text", operation="text_generation",
    )
    public = harness.store.list_events(show_id, public_only=True)
    full = harness.store.list_events(show_id, public_only=False)
    assert all(s.envelope.public for s in public)
    assert "failure.recorded" not in [s.envelope.event_type for s in public]
    assert "failure.recorded" in [s.envelope.event_type for s in full]
    # Raw technical detail never appears in any public event payload.
    assert "raw stack trace" not in str([s.envelope.payload for s in public])


def test_private_events_never_reach_public_bus_subscribers(harness, show_id):
    public_seen, all_seen = [], []
    harness.bus.subscribe(Visibility.PUBLIC, public_seen.append)
    harness.bus.subscribe(Visibility.ALL, all_seen.append)
    harness.controller.record_failure(
        show_id=show_id, round_id=None, error_type="x", detail="private",
    )
    assert "failure.recorded" not in [e.event_type for e in public_seen]
    assert "failure.recorded" in [e.event_type for e in all_seen]


def test_since_seq_pagination(harness, show_id):
    harness.controller.pause_show(show_id, PRODUCER)
    harness.controller.resume_show(show_id, PRODUCER)
    all_events = harness.store.list_events(show_id)
    cutoff = all_events[1].seq
    newer = harness.store.list_events(show_id, since_seq=cutoff)
    assert [s.seq for s in newer] == [s.seq for s in all_events if s.seq > cutoff]


def test_events_publish_only_after_commit(harness):
    """A rolled-back state change must not fan out its events."""
    seen = []
    harness.bus.subscribe(Visibility.ALL, seen.append)

    original_claim = harness.controller._claim_command

    def exploding_claim(session, command_id, action, actor, show_id):
        original_claim(session, command_id, action, actor, show_id)
        if action == "start":
            raise RuntimeError("crash after event staged")

    harness.controller._claim_command = exploding_claim
    show_id = harness.controller.create_show("Pilot", [], PRODUCER)["show_id"]
    with pytest.raises(RuntimeError):
        harness.controller.start_show(show_id, PRODUCER, command_id="cmd-crash")
    harness.controller._claim_command = original_claim

    assert [e.event_type for e in seen] == ["show.created"]
    stored = harness.store.list_events(show_id, public_only=False)
    assert [s.envelope.event_type for s in stored] == ["show.created"]


def test_record_failure_writes_error_ledger(harness, show_id):
    error_id = harness.controller.record_failure(
        show_id=show_id, round_id=None,
        error_type="provider_timeout", detail="socket timeout after 45s",
        provider="mock_text", model="mock-text-1",
        operation="text_generation", retry_count=1, recovery_action="retry",
    )
    with session_scope(harness.session_factory) as session:
        record = session.get(ErrorRecord, error_id)
        assert record is not None
        assert record.error_type == "provider_timeout"
        assert record.detail == "socket timeout after 45s"
        assert record.retry_count == 1
        assert record.recovery_action == "retry"


def test_deterministic_fixture_ids_and_timestamps(harness):
    """The harness produces fully reproducible ids and timestamps."""
    created = harness.controller.create_show("Pilot", [], PRODUCER)
    assert created["show_id"] == "show_000001"
    stored = harness.store.list_events(created["show_id"], public_only=False)
    assert stored[0].envelope.event_id == "evt_000002"
    assert stored[0].envelope.timestamp.isoformat() == "2026-07-27T17:00:00+00:00"
