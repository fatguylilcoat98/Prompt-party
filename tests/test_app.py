"""Application factory and health surface."""


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["app"] == "prompt-party"
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    provider_names = {p["provider"] for p in body["providers"]}
    assert {"mock_text", "mock_image"} <= provider_names


def test_health_never_leaks_secrets(client, settings):
    response = client.get("/api/health")
    text = response.text
    assert settings.producer_token.get_secret_value() not in text
    assert "api_key" not in text


def test_all_five_modules_register_through_one_engine(client):
    """Master Spec section 15 acceptance gate: five game modules, one
    reusable engine."""
    app = client.app
    assert set(app.state.games) == {
        "art_showdown", "rap_battle", "ai_court", "improv", "roast_battle"
    }
    assert set(app.state.controller._modules) == set(app.state.games)
    from app.games.shared.module import GameModule

    for orchestrator in app.state.games.values():
        assert isinstance(orchestrator.module, GameModule)


def test_game_module_protocol_importable():
    # Confirms the shared engine contracts import cleanly before Milestone 2.
    from app.games.shared.module import GameModule, TransitionError  # noqa: F401
    from app.games.shared.schemas import GameManifest  # noqa: F401
