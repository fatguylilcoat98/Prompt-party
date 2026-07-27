"""Provider interface and deterministic mocks."""

import hashlib
from pathlib import Path

import pytest

from app.providers.base import Operation, ProviderRequest, RequestStatus
from app.providers.mock import MockImageProvider, MockTextProvider
from app.providers.registry import ProviderRegistry, UnknownProviderError


def _text_request(prompt: str = "Describe a raccoon hotel.") -> ProviderRequest:
    return ProviderRequest(
        participant_id="artist_01",
        operation=Operation.TEXT_GENERATION,
        user_prompt=prompt,
    )


def _image_request() -> ProviderRequest:
    return ProviderRequest(
        participant_id="artist_01",
        operation=Operation.IMAGE_GENERATION,
        user_prompt="A raccoon hotel lobby",
    )


@pytest.mark.anyio
async def test_mock_text_is_deterministic():
    provider = MockTextProvider()
    a = await provider.generate(_text_request())
    b = await provider.generate(_text_request())
    assert a.status is RequestStatus.OK
    assert a.text == b.text


@pytest.mark.anyio
async def test_mock_text_canned_responses_by_template():
    provider = MockTextProvider(canned={"artist_plan_v1": '{"concept_title": "X"}'})
    request = _text_request()
    request = request.model_copy(update={"template_id": "artist_plan_v1"})
    response = await provider.generate(request)
    assert response.text == '{"concept_title": "X"}'


@pytest.mark.anyio
async def test_failure_injection_returns_status_not_exception():
    provider = MockTextProvider()
    provider.failures.arm(times=1, status=RequestStatus.TIMEOUT)
    first = await provider.generate(_text_request())
    second = await provider.generate(_text_request())
    assert first.status is RequestStatus.TIMEOUT
    assert first.error_detail is not None
    assert second.status is RequestStatus.OK


@pytest.mark.anyio
async def test_unsupported_operation_is_error_status():
    provider = MockTextProvider()
    request = ProviderRequest(
        participant_id="artist_01",
        operation=Operation.IMAGE_GENERATION,
        user_prompt="paint me",
    )
    response = await provider.generate(request)
    assert response.status is RequestStatus.ERROR


@pytest.mark.anyio
async def test_mock_image_writes_valid_file_with_checksum(tmp_path):
    provider = MockImageProvider(media_dir=tmp_path / "media")
    response = await provider.generate(_image_request())
    assert response.status is RequestStatus.OK
    media = response.media
    path = Path(media.path)
    assert path.exists()
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG")
    assert hashlib.sha256(data).hexdigest() == media.checksum_sha256
    assert media.mime_type == "image/png"


@pytest.mark.anyio
async def test_mock_image_deterministic_for_same_prompt(tmp_path):
    provider = MockImageProvider(media_dir=tmp_path / "media")
    a = await provider.generate(_image_request())
    b = await provider.generate(_image_request())
    assert a.media.checksum_sha256 == b.media.checksum_sha256


@pytest.mark.anyio
async def test_registry_lookup_and_health(tmp_path):
    registry = ProviderRegistry()
    registry.register(MockTextProvider())
    registry.register(MockImageProvider(media_dir=tmp_path))
    assert registry.names() == ["mock_image", "mock_text"]
    assert registry.get("mock_text").name == "mock_text"
    health = await registry.health()
    assert all(h.healthy for h in health)
    with pytest.raises(UnknownProviderError):
        registry.get("real_gpu_cluster")
