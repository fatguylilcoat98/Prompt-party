"""Phase 2 P5: the deployment kit stays consistent with the app."""

import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_production_env_example_matches_settings(monkeypatch):
    """Every variable in .env.production.example must be understood by
    Settings (catches drift between docs and config)."""
    text = (ROOT / ".env.production.example").read_text()
    pairs = re.findall(r"^(PROMPT_PARTY_[A-Z_]+)=(.*)$", text, re.MULTILINE)
    assert pairs, "env example has no variables?"
    known_fields = {f"PROMPT_PARTY_{name.upper()}" for name in Settings.model_fields}
    for key, _ in pairs:
        assert key in known_fields, f"{key} is not a Settings field"
    for key, value in pairs:
        monkeypatch.setenv(key, value or "")
    settings = Settings(_env_file=None)
    assert settings.environment == "production"
    assert settings.port == 8710


def test_deployment_files_exist_and_enforce_single_worker():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"--workers", "1"' in dockerfile
    unit = (ROOT / "deploy" / "prompt-party.service").read_text()
    assert "--workers 1" in unit
    assert "EnvironmentFile=" in unit
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "./data:/app/data" in compose and "max-size" in compose
    caddy = (ROOT / "deploy" / "Caddyfile").read_text()
    assert "flush_interval -1" in caddy  # SSE must not be buffered
    for doc in ("DEPLOY.md", "OPERATIONS.md", "RECOVERY.md"):
        assert (ROOT / "docs" / doc).exists(), doc


def test_backup_script_is_executable():
    script = ROOT / "scripts" / "backup.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111, "backup.sh must be executable"
