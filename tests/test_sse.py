"""SSE event streaming (Phase 2 Priority 1): replay with ids, reconnect,
public/private separation, heartbeat, cleanup, live push."""

import json

TOKEN = {"X-Producer-Token": "change-me"}


def _make_show_with_events(client):
    show = client.post(
        "/api/shows", json={"title": "SSE Show", "selected_games": []}, headers=TOKEN
    ).json()
    show_id = show["show_id"]
    client.post(f"/api/shows/{show_id}/start", headers=TOKEN)
    client.post(f"/api/shows/{show_id}/pause", headers=TOKEN)
    client.post(f"/api/shows/{show_id}/resume", headers=TOKEN)
    # One private event via a recorded failure.
    client.app.state.controller.record_failure(
        show_id=show_id, round_id=None,
        error_type="provider_timeout", detail="secret stack trace",
    )
    return show_id


def _read_frames(response, max_frames=50):
    """Parse SSE frames (dicts) and comments (strings) from a stream."""
    frames, current = [], {}
    for line in response.iter_lines():
        if line.startswith(":"):
            frames.append(line)
        elif line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "" and current:
            frames.append(current)
            current = {}
        if len(frames) >= max_frames:
            break
    return frames


def _events_of(frames):
    return [f for f in frames if isinstance(f, dict)]


def test_stream_replays_ledger_with_seq_ids(client):
    show_id = _make_show_with_events(client)
    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream",
        params={"since": 0, "limit": 4},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = _read_frames(response)
    events = _events_of(frames)
    assert [e["event"] for e in events] == [
        "show.created", "show.started", "show.paused", "show.resumed"
    ]
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)
    assert all(e["data"]["seq"] == e["id"] for e in events)
    assert all(e["data"]["public"] for e in events)


def test_private_events_require_valid_token(client):
    show_id = _make_show_with_events(client)
    # Public stream never carries the failure event.
    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream", params={"since": 0, "limit": 4}
    ) as response:
        public_events = _events_of(_read_frames(response))
    assert "failure.recorded" not in [e["event"] for e in public_events]
    assert "secret stack trace" not in json.dumps([e["data"] for e in public_events])

    # Wrong token is rejected outright.
    bad = client.get(
        f"/api/shows/{show_id}/events/stream",
        params={"token": "wrong", "limit": 1},
    )
    assert bad.status_code == 401

    # Producer token unlocks the full feed.
    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream",
        params={"since": 0, "limit": 5, "token": "change-me"},
    ) as response:
        full_events = _events_of(_read_frames(response))
    assert "failure.recorded" in [e["event"] for e in full_events]


def test_last_event_id_reconnect_resumes_without_duplicates(client):
    show_id = _make_show_with_events(client)
    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream", params={"since": 0, "limit": 2}
    ) as response:
        first = _events_of(_read_frames(response))
    cursor = first[-1]["id"]

    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream", params={"limit": 2},
        headers={"Last-Event-ID": str(cursor)},
    ) as response:
        resumed = _events_of(_read_frames(response))
    assert {e["id"] for e in first}.isdisjoint({e["id"] for e in resumed})
    assert min(e["id"] for e in resumed) > cursor


def test_heartbeat_on_idle_stream(client):
    show_id = _make_show_with_events(client)
    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream",
        # Cursor at a high seq: no replay, stream idles immediately.
        params={"since": 10_000, "heartbeat": 0.05, "max_keepalives": 2},
    ) as response:
        keepalives = [line for line in response.iter_lines() if "keepalive" in line]
    assert len(keepalives) == 2


def test_live_events_are_pushed_to_open_streams(client):
    import threading

    from app.controller.engine import Actor
    from app.games.shared.schemas import ActorType

    show_id = _make_show_with_events(client)
    controller = client.app.state.controller
    producer = Actor(ActorType.PRODUCER, "producer_1")
    # Cursor at the current ledger tail: anything received is a live push.
    existing = client.get(
        f"/api/shows/{show_id}/events/stream", params={"since": 0, "limit": 4}
    )
    tail = max(
        int(line[4:]) for line in existing.text.splitlines() if line.startswith("id: ")
    )
    timer = threading.Timer(0.2, lambda: controller.pause_show(show_id, producer))
    timer.start()
    try:
        with client.stream(
            "GET", f"/api/shows/{show_id}/events/stream",
            params={"since": tail, "limit": 1, "heartbeat": 30},
        ) as response:
            events = _events_of(_read_frames(response))
    finally:
        timer.join()
    assert [e["event"] for e in events] == ["show.paused"]
    assert events[0]["id"] > tail


def test_stream_cleanup_and_viewer_accounting(client):
    show_id = _make_show_with_events(client)
    hub = client.app.state.stream_hub
    assert hub.total_viewers() == 0
    with client.stream(
        "GET", f"/api/shows/{show_id}/events/stream", params={"since": 0, "limit": 1}
    ) as response:
        _read_frames(response)
    # Connection closed -> waker unregistered, no leak.
    assert hub.total_viewers() == 0
    assert hub.viewer_counts() == {}


def test_unknown_show_is_404(client):
    assert client.get(
        "/api/shows/show_missing/events/stream", params={"limit": 1}
    ).status_code == 404
