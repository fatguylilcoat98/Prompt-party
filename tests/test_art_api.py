"""AI Art Showdown end to end through the HTTP API, including five
consecutive rounds without state corruption (Packet 01 acceptance 10)."""

import pytest

from tests.art_fixtures import ART_CAST

TOKEN = {"X-Producer-Token": "change-me"}

PHASES_TO_PLANNING = ["SUBMISSION_OPEN", "SUBMISSION_REVIEW"]


def _transition(client, round_id, phase):
    response = client.post(
        f"/api/rounds/{round_id}/transition", json={"new_phase": phase}, headers=TOKEN
    )
    assert response.status_code == 200, response.text
    return response.json()


def run_full_round(client, show_id: str, prompt_text: str) -> dict:
    """Drive one complete Art Showdown round through the public API."""
    round_id = client.post(
        f"/api/shows/{show_id}/rounds",
        json={"game_id": "art_showdown", "cast": ART_CAST},
        headers=TOKEN,
    ).json()["round_id"]

    _transition(client, round_id, "SUBMISSION_OPEN")
    member = client.post(
        f"/api/shows/{show_id}/audience/join", json={"display_name": "fan"}
    ).json()
    submission = client.post(
        f"/api/rounds/{round_id}/submissions",
        json={"session_id": member["session_id"], "text": prompt_text},
    ).json()

    _transition(client, round_id, "SUBMISSION_REVIEW")
    assert client.post(
        f"/api/submissions/{submission['submission_id']}/review",
        json={"decision": "approve"}, headers=TOKEN,
    ).status_code == 200
    locked = client.post(
        f"/api/rounds/{round_id}/lock",
        json={"submission_id": submission["submission_id"]}, headers=TOKEN,
    ).json()
    assert locked["locked_prompt"] == prompt_text

    for phase in ["PROMPT_LOCKED", "ROUND_INTRO", "CONTESTANT_PLANNING"]:
        _transition(client, round_id, phase)
    plans = client.post(
        f"/api/rounds/{round_id}/actions/request_plans", headers=TOKEN
    ).json()["plans"]
    assert set(plans) == {"artist_01", "artist_02"}

    _transition(client, round_id, "CREATION_ACTIVE")
    generation = client.post(
        f"/api/rounds/{round_id}/actions/start_generation", headers=TOKEN
    ).json()["generation"]
    assert all(g["status"] == "complete" for g in generation.values())

    _transition(client, round_id, "PRE_REVEAL_COMMENTARY")
    for judge in ["judge_01", "judge_02"]:
        comment = client.post(
            f"/api/rounds/{round_id}/actions/request_commentary",
            json={"judge_id": judge}, headers=TOKEN,
        ).json()
        assert comment["comment"]["comment"]

    _transition(client, round_id, "REVEAL")
    revealed = client.post(
        f"/api/rounds/{round_id}/actions/reveal", json={}, headers=TOKEN
    ).json()["revealed"]
    assert len(revealed) == 2
    media = client.get(revealed[0]["url"])
    assert media.status_code == 200
    assert media.headers["content-type"] == "image/png"

    _transition(client, round_id, "FINAL_JUDGING")
    blind_flags = {}
    for judge in ["judge_01", "judge_02", "judge_03"]:
        card = client.post(
            f"/api/rounds/{round_id}/actions/request_scores",
            json={"judge_id": judge}, headers=TOKEN,
        ).json()
        blind_flags[judge] = card["blind"]
    assert blind_flags == {"judge_01": False, "judge_02": False, "judge_03": True}

    _transition(client, round_id, "AUDIENCE_VOTING")
    assert client.post(
        f"/api/rounds/{round_id}/votes/open",
        json={"choices": ["artist_01", "artist_02"]}, headers=TOKEN,
    ).status_code == 200
    voters = [
        client.post(
            f"/api/shows/{show_id}/audience/join", json={"display_name": f"voter{i}"}
        ).json()
        for i in range(3)
    ]
    for voter, choice in zip(voters, ["artist_01", "artist_01", "artist_02"]):
        assert client.post(
            f"/api/rounds/{round_id}/votes",
            json={"session_id": voter["session_id"], "choice": choice},
        ).status_code == 201
    tally = client.post(
        f"/api/rounds/{round_id}/votes/close", headers=TOKEN
    ).json()["tally"]
    assert tally == {"artist_01": 2, "artist_02": 1}

    # Late vote rejected after deterministic close.
    late = client.post(
        f"/api/rounds/{round_id}/votes",
        json={"session_id": voters[0]["session_id"], "choice": "artist_02"},
    )
    assert late.status_code == 422

    _transition(client, round_id, "SCORING")
    result = client.post(
        f"/api/rounds/{round_id}/winner/calculate", headers=TOKEN
    ).json()
    assert result["winner_id"] in {"artist_01", "artist_02"}

    replay = client.get(f"/api/rounds/{round_id}/replay").json()
    assert replay["locked_prompt"] == prompt_text
    assert replay["votes"] == tally
    return {"round_id": round_id, "result": result, "replay": replay}


@pytest.fixture
def show_id(client):
    show = client.post(
        "/api/shows", json={"title": "E2E", "selected_games": ["art_showdown"]},
        headers=TOKEN,
    ).json()
    client.post(f"/api/shows/{show['show_id']}/start", headers=TOKEN)
    return show["show_id"]


def test_complete_game_end_to_end(client, show_id):
    outcome = run_full_round(client, show_id, "A raccoon runs a luxury hotel for pigeons")

    state = client.get(f"/api/shows/{show_id}/broadcast-state").json()
    round_state = state["round"]
    assert round_state["round_id"] == outcome["round_id"]
    assert round_state["phase"] == "SCORING"
    assert round_state["result"]["winner_id"] == outcome["result"]["winner_id"]
    assert len(round_state["revealed"]) == 2
    # Broadcast surface never exposes provider internals or secrets.
    text = str(state)
    assert "api_key" not in text and "change-me" not in text and "error_detail" not in text


def test_audience_routes_have_no_producer_power(client, show_id):
    round_id = client.post(
        f"/api/shows/{show_id}/rounds",
        json={"game_id": "art_showdown", "cast": ART_CAST},
        headers=TOKEN,
    ).json()["round_id"]
    # No token: every producer action must be rejected.
    assert client.post(f"/api/rounds/{round_id}/actions/request_plans").status_code == 401
    assert client.post(
        f"/api/rounds/{round_id}/votes/open", json={"choices": ["a", "b"]}
    ).status_code == 401
    assert client.post(
        f"/api/rounds/{round_id}/transition", json={"new_phase": "SUBMISSION_OPEN"}
    ).status_code == 401


def test_five_consecutive_rounds_no_state_corruption(client, show_id):
    outcomes = []
    for i in range(5):
        prompt_text = f"Round {i}: an octopus hosts a cooking show, take {i}"
        outcomes.append(run_full_round(client, show_id, prompt_text))

    # Each round kept its own prompt, votes, and result — no leakage.
    round_ids = {o["round_id"] for o in outcomes}
    assert len(round_ids) == 5
    for i, outcome in enumerate(outcomes):
        replay = client.get(f"/api/rounds/{outcome['round_id']}/replay").json()
        assert replay["locked_prompt"] == f"Round {i}: an octopus hosts a cooking show, take {i}"
        assert replay["votes"] == {"artist_01": 2, "artist_02": 1}
        assert replay["result"]["winner_id"] == outcome["result"]["winner_id"]
        assert replay["phase"] == "SCORING"
