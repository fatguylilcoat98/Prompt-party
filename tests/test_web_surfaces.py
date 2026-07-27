"""Phase 2 P2-P4: game catalog, show listing, vote countdowns, per-game
broadcast projections, and the static web surfaces."""

import pytest

from tests.rap_fixtures import RAP_CAST

TOKEN = {"X-Producer-Token": "change-me"}


def test_game_catalog_lists_all_five_games_with_casts(client):
    games = client.get("/api/games").json()["games"]
    by_id = {g["game_id"]: g for g in games}
    assert set(by_id) == {"art_showdown", "rap_battle", "ai_court", "improv", "roast_battle"}
    for game in games:
        assert game["default_cast"], f"{game['game_id']} needs a default cast"
        assert game["modifiers"], f"{game['game_id']} needs modifiers"
        assert len(game["phases"]) == 12
    # Default casts validate against the Participant schema via round creation.
    show = client.post("/api/shows", json={"title": "Catalog"}, headers=TOKEN).json()
    client.post(f"/api/shows/{show['show_id']}/start", headers=TOKEN)
    for game in games:
        response = client.post(
            f"/api/shows/{show['show_id']}/rounds",
            json={"game_id": game["game_id"], "cast": game["default_cast"]},
            headers=TOKEN,
        )
        assert response.status_code == 201, f"{game['game_id']}: {response.text}"
    # Catalog carries no secrets.
    text = client.get("/api/games").text
    assert "api_key" not in text and "change-me" not in text


def test_show_listing_requires_producer_token(client):
    assert client.get("/api/shows").status_code == 401
    show = client.post("/api/shows", json={"title": "Listed"}, headers=TOKEN).json()
    listing = client.get("/api/shows", headers=TOKEN).json()["shows"]
    assert any(s["show_id"] == show["show_id"] for s in listing)


@pytest.fixture
def voting_round(client):
    show = client.post(
        "/api/shows", json={"title": "Votes", "selected_games": ["rap_battle"]},
        headers=TOKEN,
    ).json()
    client.post(f"/api/shows/{show['show_id']}/start", headers=TOKEN)
    round_id = client.post(
        f"/api/shows/{show['show_id']}/rounds",
        json={"game_id": "rap_battle", "cast": RAP_CAST}, headers=TOKEN,
    ).json()["round_id"]
    for phase in ["SUBMISSION_OPEN", "SUBMISSION_REVIEW", "PROMPT_LOCKED", "ROUND_INTRO",
                  "CONTESTANT_PLANNING", "CREATION_ACTIVE", "PRE_REVEAL_COMMENTARY",
                  "REVEAL", "FINAL_JUDGING", "AUDIENCE_VOTING"]:
        client.post(f"/api/rounds/{round_id}/transition",
                    json={"new_phase": phase, "override": phase == "SUBMISSION_OPEN"},
                    headers=TOKEN)
    return {"show_id": show["show_id"], "round_id": round_id}


def test_vote_countdown_is_display_guidance_only(client, voting_round):
    opened = client.post(
        f"/api/rounds/{voting_round['round_id']}/votes/open",
        json={"choices": ["rapper_01", "rapper_02"], "countdown_seconds": 45},
        headers=TOKEN,
    ).json()
    assert opened["countdown_seconds"] == 45
    state = client.get(
        f"/api/shows/{voting_round['show_id']}/broadcast-state"
    ).json()["round"]["voting"]
    assert state["countdown_seconds"] == 45
    assert state["opened_at"]
    # The countdown never closes the ballot by itself: still open, and the
    # deterministic close remains a producer action.
    member = client.post(
        f"/api/shows/{voting_round['show_id']}/audience/join",
        json={"display_name": "late but fine"},
    ).json()
    vote = client.post(
        f"/api/rounds/{voting_round['round_id']}/votes",
        json={"session_id": member["session_id"], "choice": "rapper_01"},
    )
    assert vote.status_code == 201
    closed = client.post(
        f"/api/rounds/{voting_round['round_id']}/votes/close", headers=TOKEN
    ).json()
    assert closed["tally"]["rapper_01"] == 1


def test_broadcast_game_state_for_rap(client, voting_round):
    state = client.get(
        f"/api/shows/{voting_round['show_id']}/broadcast-state"
    ).json()["round"]
    assert state["game_id"] == "rap_battle"
    assert set(state["game_state"]) == {"order", "verses", "exchanges"}


def test_static_surfaces_served(client):
    for path, marker in [
        ("/", "PROMPT PARTY"),
        ("/broadcast/", "Broadcast"),
        ("/audience/", "Join the audience"),
        ("/producer/", "PRODUCER"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text, path
    # The producer page never embeds a token; it is user-supplied at runtime.
    assert "change-me" not in client.get("/producer/").text


def test_api_still_wins_over_static_mount(client):
    assert client.get("/api/health").json()["app"] == "prompt-party"


def test_broadcast_page_carries_tv_pacing_features(client):
    """P7: the stream view keeps its television-pacing elements."""
    page = client.get("/broadcast/").text
    for marker in [
        'id="stinger"',          # clip-moment stingers
        'id="winner-overlay"',   # winner moment
        'id="countdown"',        # audience countdown
        "updateCommentaryPool",  # rotating waiting commentary
        "countup",               # score count-up animation
        "panel canvas",          # reveal transition containers
        "OBJECTION!",            # event-driven moments wired to SSE
        "keyframes stinger",
    ]:
        assert marker in page, marker
