"""Rap Battle end to end through the generic action API (Master Spec
section 10 action dispatch), including vote revision and late rejection."""

import pytest

from tests.rap_fixtures import RAP_CAST

TOKEN = {"X-Producer-Token": "change-me"}


def _transition(client, round_id, phase):
    response = client.post(
        f"/api/rounds/{round_id}/transition", json={"new_phase": phase}, headers=TOKEN
    )
    assert response.status_code == 200, response.text
    return response.json()


def _action(client, round_id, action, body=None, expect=200):
    response = client.post(
        f"/api/rounds/{round_id}/actions/{action}", json=body or {}, headers=TOKEN
    )
    assert response.status_code == expect, response.text
    return response.json()


@pytest.fixture
def show_id(client):
    show = client.post(
        "/api/shows", json={"title": "Rap E2E", "selected_games": ["rap_battle"]},
        headers=TOKEN,
    ).json()
    client.post(f"/api/shows/{show['show_id']}/start", headers=TOKEN)
    return show["show_id"]


def test_full_battle_end_to_end(client, show_id):
    round_id = client.post(
        f"/api/shows/{show_id}/rounds",
        json={"game_id": "rap_battle", "cast": RAP_CAST},
        headers=TOKEN,
    ).json()["round_id"]

    _transition(client, round_id, "SUBMISSION_OPEN")
    member = client.post(
        f"/api/shows/{show_id}/audience/join", json={"display_name": "topic_fan"}
    ).json()
    submission = client.post(
        f"/api/rounds/{round_id}/submissions",
        json={"session_id": member["session_id"], "text": "Dial-up vs fiber",
              "submission_type": "battle_topic"},
    ).json()
    _transition(client, round_id, "SUBMISSION_REVIEW")
    client.post(
        f"/api/submissions/{submission['submission_id']}/review",
        json={"decision": "approve"}, headers=TOKEN,
    )
    client.post(
        f"/api/rounds/{round_id}/lock",
        json={"submission_id": submission["submission_id"]}, headers=TOKEN,
    )
    _transition(client, round_id, "PROMPT_LOCKED")
    _transition(client, round_id, "ROUND_INTRO")
    draw = _action(client, round_id, "opening_draw", {"first_rapper_id": "rapper_01"})
    assert draw["order"] == ["rapper_01", "rapper_02"]

    _transition(client, round_id, "CONTESTANT_PLANNING")
    _transition(client, round_id, "CREATION_ACTIVE")

    # Weapon mid-battle through the same generic dispatch.
    _action(client, round_id, "apply_modifier",
            {"modifier_id": "rhyme_robbery", "params": {"words": ["modem"]}})

    speakers, first_bars = [], None
    for _ in range(4):
        verse = _action(client, round_id, "request_verse")
        speakers.append(verse["rapper_id"])
        assert verse["verse"]["bars"]
        if first_bars is None:
            first_bars = verse["verse"]["bars"]
    assert speakers == ["rapper_01", "rapper_02", "rapper_01", "rapper_02"]
    assert "modem" in " ".join(first_bars).lower()  # armed weapon honored

    # Unknown action names are rejected by the dispatcher.
    _action(client, round_id, "become_admin", expect=404)

    _transition(client, round_id, "PRE_REVEAL_COMMENTARY")
    _transition(client, round_id, "REVEAL")
    _transition(client, round_id, "FINAL_JUDGING")
    for judge in ["judge_01", "judge_02", "judge_03"]:
        card = _action(client, round_id, "request_scores", {"judge_id": judge})
        totals = [s["total"] for s in card["scorecard"]["scores"].values()]
        assert all(isinstance(t, int) for t in totals)

    _transition(client, round_id, "AUDIENCE_VOTING")
    client.post(
        f"/api/rounds/{round_id}/votes/open",
        json={"choices": ["rapper_01", "rapper_02"]}, headers=TOKEN,
    )
    voters = [
        client.post(
            f"/api/shows/{show_id}/audience/join", json={"display_name": f"v{i}"}
        ).json()
        for i in range(3)
    ]
    for voter, choice in zip(voters, ["rapper_01", "rapper_02", "rapper_02"]):
        client.post(
            f"/api/rounds/{round_id}/votes",
            json={"session_id": voter["session_id"], "choice": choice},
        )
    # One voter revises before close (acceptance 8).
    revised = client.post(
        f"/api/rounds/{round_id}/votes",
        json={"session_id": voters[1]["session_id"], "choice": "rapper_01"},
    ).json()
    assert revised["revised"] is True
    tally = client.post(
        f"/api/rounds/{round_id}/votes/close", headers=TOKEN
    ).json()["tally"]
    assert tally == {"rapper_01": 2, "rapper_02": 1}
    # Late vote rejected (acceptance 9).
    late = client.post(
        f"/api/rounds/{round_id}/votes",
        json={"session_id": voters[0]["session_id"], "choice": "rapper_02"},
    )
    assert late.status_code == 422

    _transition(client, round_id, "SCORING")
    result = client.post(
        f"/api/rounds/{round_id}/winner/calculate", headers=TOKEN
    ).json()
    assert result["winner_id"] in {"rapper_01", "rapper_02"}

    replay = client.get(f"/api/rounds/{round_id}/replay").json()
    assert replay["game_id"] == "rap_battle"
    assert len(replay["verses"]) == 4
    assert replay["votes"] == tally
