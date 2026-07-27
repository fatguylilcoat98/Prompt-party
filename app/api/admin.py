"""Operational dashboard API (Phase 2 Priority 6). Producer-only."""

from __future__ import annotations

import resource
import sys
from pathlib import Path
from time import monotonic, time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func

from app import __version__
from app.controller.engine import Actor
from app.persistence.db import session_scope
from app.persistence.models import (
    AIRequestRecord,
    AudienceMember,
    ErrorRecord,
    EventRecord,
    Round,
    Show,
    Submission,
    Vote,
)
from app.producer.auth import require_producer

router = APIRouter()


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


@router.get("/admin/status")
async def admin_status(request: Request, actor: Actor = Depends(require_producer)):
    app_state = request.app.state
    settings = app_state.settings

    # -- providers with measured health latency --------------------------
    providers = []
    for name in app_state.providers.names():
        adapter = app_state.providers.get(name)
        started = monotonic()
        health = await adapter.health()
        providers.append({
            "provider": name,
            "healthy": health.healthy,
            "detail": health.detail,
            "health_latency_ms": int((monotonic() - started) * 1000),
        })

    # -- persistence-derived metrics --------------------------------------
    with session_scope(app_state.session_factory) as session:
        shows_by_status = dict(
            session.query(Show.status, func.count()).group_by(Show.status).all()
        )
        live_shows = [
            {"show_id": s.show_id, "title": s.title}
            for s in session.query(Show).filter(Show.status.in_(["live", "paused"])).all()
        ]
        rounds_by_game = dict(
            session.query(Round.game_id, func.count()).group_by(Round.game_id).all()
        )
        winners = (
            session.query(func.count()).select_from(Round)
            .filter(Round.result.isnot(None)).scalar()
        )
        metrics = {
            "shows_by_status": shows_by_status,
            "rounds_by_game": rounds_by_game,
            "rounds_with_results": winners,
            "events_total": session.query(func.count()).select_from(EventRecord).scalar(),
            "votes_total": session.query(func.count()).select_from(Vote).scalar(),
            "submissions_total": session.query(func.count()).select_from(Submission).scalar(),
            "audience_members_total": session.query(func.count()).select_from(AudienceMember).scalar(),
            "ai_requests_total": session.query(func.count()).select_from(AIRequestRecord).scalar(),
        }
        avg_latency = (
            session.query(AIRequestRecord.provider, func.avg(AIRequestRecord.latency_ms))
            .filter(AIRequestRecord.status == "ok")
            .group_by(AIRequestRecord.provider)
            .all()
        )
        provider_latency = {name: round(avg or 0, 1) for name, avg in avg_latency}
        incidents = [
            {
                "error_id": e.error_id,
                "error_type": e.error_type,
                "operation": e.operation,
                "provider": e.provider,
                "round_id": e.round_id,
                "recovery_action": e.recovery_action,
                "detail": (e.detail or "")[:200],
                "at": e.created_at.isoformat(),
            }
            for e in session.query(ErrorRecord)
            .order_by(ErrorRecord.created_at.desc()).limit(10).all()
        ]

    # -- process and storage ----------------------------------------------
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":  # ru_maxrss is bytes on macOS
        rss_kb //= 1024
    db_path = settings.database_url.removeprefix("sqlite:///")
    storage = {
        "database_bytes": Path(db_path).stat().st_size if Path(db_path).exists() else 0,
        "media_bytes": _dir_size(Path(settings.media_dir)),
        "exports_bytes": _dir_size(Path(settings.exports_dir)),
    }

    hub = app_state.stream_hub
    return {
        "app": "prompt-party",
        "version": __version__,
        "environment": settings.environment,
        "uptime_seconds": int(time() - hub.started_at),
        "memory_rss_mb": round(rss_kb / 1024, 1),
        "viewers": {
            "total": hub.total_viewers(),
            "by_show": hub.viewer_counts(),
        },
        "live_shows": live_shows,
        "providers": providers,
        "provider_avg_request_latency_ms": provider_latency,
        "metrics": metrics,
        "storage": storage,
        "incidents": incidents,
    }
