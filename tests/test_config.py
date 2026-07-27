"""Configuration system: env-prefixed loading, separation, secret masking."""

from app.config import Settings


def test_defaults_are_standalone_prompt_party_values():
    s = Settings(_env_file=None)
    assert s.port == 8710
    assert "prompt_party" in s.database_url
    assert s.text_provider == "mock"
    assert s.image_provider == "mock"


def test_env_prefix_is_prompt_party(monkeypatch):
    monkeypatch.setenv("PROMPT_PARTY_PORT", "9001")
    monkeypatch.setenv("PROMPT_PARTY_ENVIRONMENT", "production")
    s = Settings(_env_file=None)
    assert s.port == 9001
    assert s.environment == "production"


def test_unprefixed_env_vars_are_ignored(monkeypatch):
    monkeypatch.setenv("PORT", "1234")
    monkeypatch.setenv("DATABASE_URL", "postgres://other-app")
    s = Settings(_env_file=None)
    assert s.port == 8710
    assert "prompt_party" in s.database_url


def test_masked_dump_never_contains_secret_values(monkeypatch):
    monkeypatch.setenv("PROMPT_PARTY_PRODUCER_TOKEN", "super-secret-token")
    monkeypatch.setenv("PROMPT_PARTY_TEXT_PROVIDER_API_KEY", "sk-abc123")
    s = Settings(_env_file=None)
    dump = str(s.masked_dump())
    assert "super-secret-token" not in dump
    assert "sk-abc123" not in dump
    assert s.masked_dump()["producer_token"] == "********"


def test_repr_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("PROMPT_PARTY_PRODUCER_TOKEN", "super-secret-token")
    s = Settings(_env_file=None)
    assert "super-secret-token" not in repr(s)
