"""Server-Sent Events endpoint (Master Spec section 10: SSE preferred).

GET /api/shows/{show_id}/events/stream

- ``since`` or the ``Last-Event-ID`` header resumes from a ledger seq.
- ``token`` (query, because EventSource cannot set headers) unlocks the
  private feed for the producer console; without it the stream is
  public-only, filtered server-side.
- ``limit`` closes the stream after N events (test/automation hook).
- Heartbeat comments keep intermediaries from timing out idle streams.
"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.events.stream import format_sse
from app.persistence.db import session_scope
from app.persistence.models import Show

router = APIRouter()

READ_BATCH = 200


@router.get("/shows/{show_id}/events/stream")
async def stream_events(
    show_id: str,
    request: Request,
    since: int | None = None,
    token: str | None = None,
    limit: int | None = None,
    heartbeat: float = 15.0,
    max_keepalives: int | None = None,
):
    with session_scope(request.app.state.session_factory) as session:
        if session.get(Show, show_id) is None:
            raise HTTPException(status_code=404, detail="show not found")

    include_private = False
    if token is not None:
        expected = request.app.state.settings.producer_token.get_secret_value()
        if not expected or not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="invalid producer token")
        include_private = True

    last_event_id = request.headers.get("last-event-id", "")
    if since is not None:
        start_seq = since
    elif last_event_id.isdigit():
        start_seq = int(last_event_id)
    else:
        start_seq = 0

    store = request.app.state.event_store
    hub = request.app.state.stream_hub
    heartbeat = max(0.05, min(heartbeat, 60.0))

    async def generator():
        entry = hub.register(show_id)
        _, waker = entry
        last_seq = start_seq
        sent = 0
        keepalives = 0
        try:
            yield "retry: 3000\n: connected\n\n"
            while True:
                while True:
                    batch = await asyncio.to_thread(
                        store.list_events, show_id,
                        public_only=not include_private,
                        since_seq=last_seq, limit=READ_BATCH,
                    )
                    for stored in batch:
                        yield format_sse(stored)
                        last_seq = stored.seq
                        sent += 1
                        if limit is not None and sent >= limit:
                            return
                    if len(batch) < READ_BATCH:
                        break
                waker.clear()
                try:
                    await asyncio.wait_for(waker.wait(), timeout=heartbeat)
                except (asyncio.TimeoutError, TimeoutError):
                    yield ": keepalive\n\n"
                    keepalives += 1
                    # Bound idle lifetime; clients auto-reconnect via
                    # Last-Event-ID, so reaping costs nothing.
                    if max_keepalives is not None and keepalives >= max_keepalives:
                        return
                if await request.is_disconnected():
                    return
        finally:
            hub.unregister(show_id, entry)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
