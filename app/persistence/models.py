"""Database models (Master Spec section 11).

One table per required record type: shows, rounds, participants,
audience_members, submissions, ai_requests, media_assets, votes, events,
errors, exports. Secrets are never stored in these records (Master Spec
section 12); provider credentials live only in configuration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Show(Base):
    __tablename__ = "shows"

    show_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="created")
    selected_games: Mapped[list] = mapped_column(JSON, default=list)
    producer_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Round(Base):
    __tablename__ = "rounds"

    round_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    show_id: Mapped[str] = mapped_column(ForeignKey("shows.show_id"), index=True)
    game_id: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(64), default="LOBBY")
    locked_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    cast: Mapped[list] = mapped_column(JSON, default=list)
    active_modifier: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ParticipantRecord(Base):
    __tablename__ = "participants"

    participant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    show_id: Mapped[str] = mapped_column(ForeignKey("shows.show_id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(120))
    seat_type: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    persona_id: Mapped[str] = mapped_column(String(64))
    avatar: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=45)
    max_retries: Mapped[int] = mapped_column(Integer, default=1)


class AudienceMember(Base):
    __tablename__ = "audience_members"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    show_id: Mapped[str] = mapped_column(ForeignKey("shows.show_id"), index=True)
    display_name: Mapped[str] = mapped_column(String(60))
    room_code: Mapped[str] = mapped_column(String(16))
    rate_limit: Mapped[dict] = mapped_column(JSON, default=dict)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Submission(Base):
    __tablename__ = "submissions"

    submission_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str] = mapped_column(ForeignKey("rounds.round_id"), index=True)
    audience_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submission_type: Mapped[str] = mapped_column(String(64))
    original_text: Mapped[str] = mapped_column(Text)
    moderation_status: Mapped[str] = mapped_column(String(32), default="pending")
    moderation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_locked_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AIRequestRecord(Base):
    """One row per AI request with its response outcome inline
    (Master Spec 11 "ai_requests/responses")."""

    __tablename__ = "ai_requests"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    participant_id: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(32))
    template_id: Mapped[str] = mapped_column(String(120), default="")
    template_version: Mapped[str] = mapped_column(String(32), default="")
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    parse_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    media_type: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(100))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    moderation_status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Vote(Base):
    __tablename__ = "votes"

    vote_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str] = mapped_column(ForeignKey("rounds.round_id"), index=True)
    voter_session_id: Mapped[str] = mapped_column(String(64), index=True)
    choice: Mapped[str] = mapped_column(String(64))
    replaced_vote_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EventRecord(Base):
    """Ordered public/private event ledger. ``seq`` provides a strict
    total order for replay; ``public`` drives server-side filtering."""

    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    show_id: Mapped[str] = mapped_column(String(64), index=True)
    round_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    game_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    public: Mapped[bool] = mapped_column(Boolean, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ErrorRecord(Base):
    """Private technical failure ledger (Master Spec section 9). Raw
    provider details live here only; public surfaces get themed states."""

    __tablename__ = "errors"

    error_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    show_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    round_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    recovery_action: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExportRecord(Base):
    __tablename__ = "exports"

    export_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    show_id: Mapped[str] = mapped_column(String(64), index=True)
    round_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    export_type: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
