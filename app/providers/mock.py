"""Deterministic mock providers.

The whole platform must be testable end to end without external API calls
(Milestone 3 rule). Mocks are deterministic: identical requests produce
identical output. Failure injection lets tests exercise the
failure-as-content contract without real outages.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from app.games.shared.schemas import Capability
from app.providers.base import (
    MediaResult,
    Operation,
    ProviderAdapter,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    RequestStatus,
)


def _digest(request: ProviderRequest) -> str:
    material = "|".join(
        [
            request.operation.value,
            request.system_prompt,
            request.user_prompt,
            json.dumps(request.params, sort_keys=True),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()


class FailureScript:
    """Makes the next N calls fail with the given status, then recover."""

    def __init__(self) -> None:
        self.remaining = 0
        self.status = RequestStatus.ERROR

    def arm(self, times: int, status: RequestStatus = RequestStatus.ERROR) -> None:
        self.remaining = times
        self.status = status

    def take(self) -> RequestStatus | None:
        if self.remaining > 0:
            self.remaining -= 1
            return self.status
        return None


class MockTextProvider(ProviderAdapter):
    name = "mock_text"
    capabilities = frozenset(
        {
            Capability.TEXT_GENERATION,
            Capability.STRUCTURED_OUTPUT,
            Capability.VISION_INPUT,
        }
    )

    def __init__(self, canned: dict[str, str] | None = None) -> None:
        #: template_id -> canned JSON/text response, for game-level tests.
        self.canned = canned or {}
        self.failures = FailureScript()
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        forced = self.failures.take()
        if forced is not None:
            return ProviderResponse(
                request_id=request.request_id,
                provider=self.name,
                model="mock-text-1",
                status=forced,
                error_detail=f"injected {forced.value} for testing",
            )
        if not self.supports(request.operation):
            return ProviderResponse(
                request_id=request.request_id,
                provider=self.name,
                model="mock-text-1",
                status=RequestStatus.ERROR,
                error_detail=f"operation {request.operation.value} not supported",
            )
        text = self.canned.get(
            request.template_id,
            f"[mock:{_digest(request)[:16]}] {request.user_prompt[:80]}",
        )
        return ProviderResponse(
            request_id=request.request_id,
            provider=self.name,
            model="mock-text-1",
            status=RequestStatus.OK,
            text=text,
            latency_ms=1,
            usage={"input_tokens": len(request.user_prompt) // 4, "output_tokens": len(text) // 4},
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True, detail="mock")


def _minimal_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Build a tiny valid PNG in pure Python (no imaging dependency)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return (
        header
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


class MockImageProvider(ProviderAdapter):
    name = "mock_image"
    capabilities = frozenset({Capability.IMAGE_GENERATION})

    def __init__(self, media_dir: Path, width: int = 64, height: int = 36) -> None:
        self.media_dir = Path(media_dir)
        self.width = width
        self.height = height
        self.failures = FailureScript()
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        forced = self.failures.take()
        if forced is not None:
            return ProviderResponse(
                request_id=request.request_id,
                provider=self.name,
                model="mock-image-1",
                status=forced,
                error_detail=f"injected {forced.value} for testing",
            )
        if not self.supports(request.operation):
            return ProviderResponse(
                request_id=request.request_id,
                provider=self.name,
                model="mock-image-1",
                status=RequestStatus.ERROR,
                error_detail=f"operation {request.operation.value} not supported",
            )
        digest = _digest(request)
        rgb = tuple(int(digest[i : i + 2], 16) for i in (0, 2, 4))
        data = _minimal_png(self.width, self.height, rgb)  # type: ignore[arg-type]
        self.media_dir.mkdir(parents=True, exist_ok=True)
        path = self.media_dir / f"mock_{digest[:16]}.png"
        path.write_bytes(data)
        return ProviderResponse(
            request_id=request.request_id,
            provider=self.name,
            model="mock-image-1",
            status=RequestStatus.OK,
            media=MediaResult(
                path=str(path),
                mime_type="image/png",
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                width=self.width,
                height=self.height,
            ),
            latency_ms=1,
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True, detail="mock")
