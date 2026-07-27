from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.engine_fixtures import EngineHarness


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Deterministic test settings: throwaway database and media dirs,
    no .env file influence."""
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path}/test.db",
        media_dir=tmp_path / "media",
        exports_dir=tmp_path / "exports",
    )


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def harness(tmp_path) -> EngineHarness:
    h = EngineHarness(f"sqlite:///{tmp_path}/engine.db")
    yield h
    h.close()
