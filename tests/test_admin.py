"""Phase 2 P6: operational dashboard API."""

TOKEN = {"X-Producer-Token": "change-me"}


def test_admin_status_requires_producer_token(client):
    assert client.get("/api/admin/status").status_code == 401
    assert client.get(
        "/api/admin/status", headers={"X-Producer-Token": "wrong"}
    ).status_code == 401


def test_admin_status_shape_and_metrics(client):
    show = client.post(
        "/api/shows", json={"title": "Ops Show", "selected_games": ["rap_battle"]},
        headers=TOKEN,
    ).json()
    client.post(f"/api/shows/{show['show_id']}/start", headers=TOKEN)
    client.app.state.controller.record_failure(
        show_id=show["show_id"], round_id=None,
        error_type="provider_timeout", detail="x" * 500,
        recovery_action="retry",
    )

    status = client.get("/api/admin/status", headers=TOKEN).json()
    assert status["app"] == "prompt-party"
    assert status["uptime_seconds"] >= 0
    assert status["memory_rss_mb"] > 0
    assert status["viewers"] == {"total": 0, "by_show": {}}
    assert any(s["show_id"] == show["show_id"] for s in status["live_shows"])
    providers = {p["provider"]: p for p in status["providers"]}
    assert providers["mock_text"]["healthy"] is True
    assert providers["mock_text"]["health_latency_ms"] >= 0
    assert status["metrics"]["shows_by_status"]["live"] >= 1
    assert status["metrics"]["events_total"] >= 2
    assert status["storage"]["database_bytes"] > 0
    incident = status["incidents"][0]
    assert incident["error_type"] == "provider_timeout"
    assert incident["recovery_action"] == "retry"
    assert len(incident["detail"]) <= 200  # truncated for the dashboard


def test_admin_page_served(client):
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "ADMIN" in response.text
