"""Event persistence: the ordered public/private event ledger.

Envelopes are written to the ``events`` table inside the caller's
transaction (so a state change and its event commit atomically), then
published to the in-process bus after commit. Public/private filtering for
readers happens here on the server (Master Spec section 12), never in
front-end code.
"""

from __future__ import annotations

from datetime import timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from app.events.bus import EventBus
from app.games.shared.schemas import EventEnvelope
from app.persistence.models import EventRecord


class StoredEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    envelope: EventEnvelope


def _to_record(envelope: EventEnvelope) -> EventRecord:
    return EventRecord(
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        show_id=envelope.show_id,
        round_id=envelope.round_id,
        game_id=envelope.game_id,
        phase=envelope.phase.value if envelope.phase else None,
        actor_type=envelope.actor_type.value,
        actor_id=envelope.actor_id,
        public=envelope.public,
        payload=envelope.payload,
        timestamp=envelope.timestamp,
    )


def _to_envelope(record: EventRecord) -> EventEnvelope:
    # SQLite returns naive datetimes; all ledger timestamps are written in
    # UTC, so restore the timezone on the way out.
    timestamp = record.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return EventEnvelope(
        event_id=record.event_id,
        event_type=record.event_type,
        show_id=record.show_id,
        round_id=record.round_id,
        game_id=record.game_id,
        phase=record.phase,
        actor_type=record.actor_type,
        actor_id=record.actor_id,
        timestamp=timestamp,
        public=record.public,
        payload=record.payload or {},
    )


class EventStore:
    def __init__(self, session_factory: sessionmaker[Session], bus: EventBus) -> None:
        self._sessions = session_factory
        self._bus = bus

    def append(self, session: Session, envelope: EventEnvelope) -> None:
        """Stage the event in the caller's transaction; publish to the bus
        only once that transaction commits. A rolled-back state change must
        never fan out its events."""
        session.add(_to_record(envelope))

        def publish_after_commit(_session: Session) -> None:
            self._bus.publish(envelope)

        sa_event.listen(session, "after_commit", publish_after_commit, once=True)

    def list_events(
        self,
        show_id: str,
        *,
        public_only: bool = True,
        since_seq: int = 0,
        limit: int = 500,
    ) -> list[StoredEvent]:
        session = self._sessions()
        try:
            query = (
                session.query(EventRecord)
                .filter(EventRecord.show_id == show_id)
                .filter(EventRecord.seq > since_seq)
            )
            if public_only:
                query = query.filter(EventRecord.public.is_(True))
            records = query.order_by(EventRecord.seq).limit(limit).all()
            return [
                StoredEvent(seq=r.seq, envelope=_to_envelope(r)) for r in records
            ]
        finally:
            session.close()
