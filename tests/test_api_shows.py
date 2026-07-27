"""HTTP surface: producer authority, transition rejection, event feed
filtering — end to end through the API."""

import pytest

TOKEN = {"X-Producer-Token": "change-me"}


@pytest.fixture
def show_id(client):
    response = client.post(
        "/api/shows", json={"title": "Pilot", "selected_games": ["art_showdown"]},
        headers=TOKEN,
    )
    assert response.status_code == 201
    show_id = response.json()["show_id"]
    assert client.post(f"/api/shows/{show_id}/start", headers=TOKEN).status_code == 200
    return show_id


@pytest.fixture
def round_id(client, show_id):
    response = client.post(
        f"/api/shows/{show_id}/rounds", json={"game_id": "art_showdown"}, headers=TOKEN
    )
    assert response.status_code == 201
    return response.json()["round_id"]


def test_mutations_require_producer_token(client):
    assert client.post("/api/shows", json={"title": "Pilot"}).status_code == 401
    assert (
        client.post(
            "/api/shows", json={"title": "Pilot"},
            headers={"X-Producer-Token": "wrong-token"},
        ).status_code
        == 401
    )


def test_legal_transition_via_api(client, round_id):
    response = client.post(
        f"/api/rounds/{round_id}/transition",
        json={"new_phase": "SUBMISSION_OPEN"},
        headers=TOKEN,
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "SUBMISSION_OPEN"


def test_illegal_transition_rejected_via_api(client, round_id):
    response = client.post(
        f"/api/rounds/{round_id}/transition",
        json={"new_phase": "REVEAL"},
        headers=TOKEN,
    )
    assert response.status_code == 422
    assert "illegal transition" in response.json()["detail"]
    assert client.get(f"/api/rounds/{round_id}").json()["phase"] == "LOBBY"


def test_unknown_phase_rejected_by_schema(client, round_id):
    response = client.post(
        f"/api/rounds/{round_id}/transition",
        json={"new_phase": "PARTY_TIME"},
        headers=TOKEN,
    )
    assert response.status_code == 422


def test_override_requires_reasoned_producer_call(client, show_id, round_id):
    response = client.post(
        f"/api/rounds/{round_id}/transition",
        json={"new_phase": "REVEAL", "override": True, "reason": "tech check"},
        headers=TOKEN,
    )
    assert response.status_code == 200
    events = client.get(f"/api/shows/{show_id}/events").json()["events"]
    overridden = [e for e in events if e["event_type"] == "round.phase_overridden"]
    assert overridden and overridden[0]["payload"]["reason"] == "tech check"


def test_pause_blocks_transition_via_api(client, show_id, round_id):
    assert client.post(f"/api/shows/{show_id}/pause", headers=TOKEN).status_code == 200
    response = client.post(
        f"/api/rounds/{round_id}/transition",
        json={"new_phase": "SUBMISSION_OPEN"},
        headers=TOKEN,
    )
    assert response.status_code == 422


def test_duplicate_command_id_returns_409(client, show_id):
    headers = {**TOKEN, "X-Command-Id": "cmd-pause-1"}
    assert client.post(f"/api/shows/{show_id}/pause", headers=headers).status_code == 200
    assert client.post(f"/api/shows/{show_id}/resume", headers=TOKEN).status_code == 200
    assert client.post(f"/api/shows/{show_id}/pause", headers=headers).status_code == 409


def test_event_feed_is_public_only_without_token(client, show_id):
    # Create a private event via a recorded failure.
    app = client.app
    app.state.controller.record_failure(
        show_id=show_id, round_id=None,
        error_type="provider_timeout", detail="secret stack trace",
    )
    public = client.get(f"/api/shows/{show_id}/events").json()["events"]
    assert all(e["public"] for e in public)
    assert "secret stack trace" not in str(public)

    denied = client.get(f"/api/shows/{show_id}/events", params={"include_private": True})
    assert denied.status_code == 401

    full = client.get(
        f"/api/shows/{show_id}/events",
        params={"include_private": True},
        headers=TOKEN,
    ).json()["events"]
    assert "failure.recorded" in [e["event_type"] for e in full]


def test_events_are_ordered_and_seq_paginated(client, show_id, round_id):
    events = client.get(f"/api/shows/{show_id}/events").json()["events"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    tail = client.get(
        f"/api/shows/{show_id}/events", params={"since": seqs[0]}
    ).json()["events"]
    assert [e["seq"] for e in tail] == seqs[1:]
