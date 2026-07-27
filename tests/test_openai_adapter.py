"""OpenAI-compatible adapter (Milestone 4): correct wire format, failure
statuses instead of exceptions, config-driven registration, and proof that
a real adapter slots behind the interface with zero engine changes.

All HTTP is stubbed with httpx.MockTransport — no network access.
"""

import base64
import hashlib
import json

import httpx
import pytest

from app.config import Settings
from app.games.shared.schemas import Capability
from app.main import build_provider_registry
from app.providers.base import Operation, ProviderRequest, RequestStatus
from app.providers.mock import _minimal_png
from app.providers.openai_compatible import OpenAICompatibleProvider

pytestmark = pytest.mark.anyio

TEXT_CAPS = frozenset({Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT})
IMAGE_CAPS = frozenset({Capability.IMAGE_GENERATION})


def text_adapter(handler, **kwargs):
    return OpenAICompatibleProvider(
        name="openai_text", base_url="https://llm.home.lan/v1", api_key="sk-home-test",
        model="local-model", capabilities=TEXT_CAPS,
        transport=httpx.MockTransport(handler), **kwargs,
    )


def image_adapter(handler, media_dir):
    return OpenAICompatibleProvider(
        name="openai_image", base_url="https://img.home.lan/v1", api_key="",
        model="local-image", capabilities=IMAGE_CAPS, media_dir=media_dir,
        transport=httpx.MockTransport(handler),
    )


def _request(operation=Operation.TEXT_GENERATION, prompt="Say hi"):
    return ProviderRequest(
        participant_id="artist_01", operation=operation, user_prompt=prompt,
        system_prompt="You are a contestant.", timeout_seconds=10,
    )


async def test_text_generation_wire_format_and_parse():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "local-model-v2",
            "choices": [{"message": {"role": "assistant", "content": "hello there"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        })

    response = await text_adapter(handler).generate(_request())
    assert response.status is RequestStatus.OK
    assert response.text == "hello there"
    assert response.model == "local-model-v2"
    assert response.usage == {"input_tokens": 12, "output_tokens": 3}
    assert seen["url"] == "https://llm.home.lan/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-home-test"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "You are a contestant."}
    assert seen["body"]["messages"][1]["content"] == "Say hi"


async def test_http_error_becomes_status_not_exception():
    def handler(request):
        return httpx.Response(500, text="upstream exploded")

    response = await text_adapter(handler).generate(_request())
    assert response.status is RequestStatus.ERROR
    assert "HTTP 500" in response.error_detail


async def test_timeout_becomes_timeout_status():
    def handler(request):
        raise httpx.ConnectTimeout("connect timed out")

    response = await text_adapter(handler).generate(_request())
    assert response.status is RequestStatus.TIMEOUT


async def test_malformed_payload_is_error():
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    response = await text_adapter(handler).generate(_request())
    assert response.status is RequestStatus.ERROR
    assert "malformed" in response.error_detail


async def test_unsupported_operation_rejected_by_capability():
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no HTTP call expected")

    response = await text_adapter(handler).generate(
        _request(operation=Operation.IMAGE_GENERATION)
    )
    assert response.status is RequestStatus.ERROR
    assert "not supported" in response.error_detail


async def test_image_generation_writes_validated_file(tmp_path):
    png = _minimal_png(64, 36, (10, 200, 30))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/images/generations")
        assert body["response_format"] == "b64_json"
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(png).decode()}],
        })

    response = await image_adapter(handler, tmp_path / "media").generate(
        _request(operation=Operation.IMAGE_GENERATION, prompt="raccoon hotel")
    )
    assert response.status is RequestStatus.OK
    media = response.media
    assert media.mime_type == "image/png"
    assert media.width == 64 and media.height == 36
    from pathlib import Path

    data = Path(media.path).read_bytes()
    assert data == png
    assert hashlib.sha256(data).hexdigest() == media.checksum_sha256


async def test_health_check(tmp_path):
    def ok(request):
        return httpx.Response(200, json={"data": []})

    def down(request):
        raise httpx.ConnectError("refused")

    healthy = await text_adapter(ok).health()
    assert healthy.healthy is True
    unhealthy = await text_adapter(down).health()
    assert unhealthy.healthy is False


def test_registry_registers_real_adapters_from_settings(tmp_path):
    settings = Settings(
        _env_file=None,
        media_dir=tmp_path / "media",
        text_provider="openai_compatible",
        text_provider_base_url="https://llm.home.lan/v1",
        text_provider_model="local-model",
        image_provider="openai_compatible",
        image_provider_base_url="https://img.home.lan/v1",
        image_provider_model="local-image",
    )
    registry = build_provider_registry(settings)
    assert registry.names() == ["mock_image", "mock_text", "openai_image", "openai_text"]


def test_registry_defaults_to_mocks_only(tmp_path):
    settings = Settings(_env_file=None, media_dir=tmp_path / "media")
    registry = build_provider_registry(settings)
    assert registry.names() == ["mock_image", "mock_text"]


async def test_orchestrator_runs_on_real_adapter_without_engine_changes(harness):
    """Interface-boundary proof: swap the real adapter into the registry
    and the Art Showdown planning flow works unchanged — the engine and
    orchestrator only ever see the ProviderAdapter interface."""
    from tests.art_fixtures import PRODUCER, advance_to, locked_round
    from app.games.shared.phases import Phase

    plan_json = json.dumps({
        "concept_title": "Wire Format Победа",
        "visual_plan": "A raccoon concierge greets pigeon guests beneath a chandelier of sunflower seeds.",
        "key_details": ["raccoon concierge", "pigeon guests"],
        "composition_choice": "wide lobby scene",
        "intended_tone": "luxurious absurdity",
    })

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": plan_json}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 60},
        })

    real = OpenAICompatibleProvider(
        name="openai_text", base_url="https://llm.home.lan/v1", api_key="sk-x",
        model="local-model",
        capabilities=frozenset({Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT}),
        transport=httpx.MockTransport(handler),
    )
    harness.providers.register(real)
    cast_on_real = [
        {"participant_id": "artist_01", "display_name": "Pix", "provider": "openai_text",
         "model": "local-model", "seat_type": "contestant",
         "capabilities": ["text_generation"], "persona_id": "precision", "avatar": "",
         "enabled": True, "timeout_seconds": 45, "max_retries": 1},
        {"participant_id": "artist_02", "display_name": "Smudge", "provider": "openai_text",
         "model": "local-model", "seat_type": "contestant",
         "capabilities": ["text_generation"], "persona_id": "vibe", "avatar": "",
         "enabled": True, "timeout_seconds": 45, "max_retries": 1},
    ]
    ids = locked_round(harness, cast=cast_on_real)
    advance_to(harness, ids["round_id"], Phase.CONTESTANT_PLANNING)
    result = await harness.art.request_plans(ids["round_id"], PRODUCER)
    assert set(result["plans"]) == {"artist_01", "artist_02"}
    assert result["plans"]["artist_01"]["concept_title"] == "Wire Format Победа"
