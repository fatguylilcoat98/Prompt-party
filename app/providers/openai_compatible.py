"""OpenAI-compatible provider adapter (Milestone 4).

Talks to any endpoint implementing the OpenAI API shape: OpenAI itself,
Ollama, LM Studio, vLLM, LocalAI, etc. Text uses ``/chat/completions``;
images use ``/images/generations`` with base64 payloads.

Adapter rules (Master Spec section 9 + Milestone 4):
- No provider-specific logic leaves this file; callers see only
  ProviderRequest/ProviderResponse.
- Never raises to the caller — failures come back as non-OK statuses with
  technical detail in ``error_detail`` (recorded privately, never public).
- The API key lives in configuration only and is never echoed into
  responses or logs.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from time import monotonic

import httpx

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


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    return None, None


class OpenAICompatibleProvider(ProviderAdapter):
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        capabilities: frozenset[Capability],
        media_dir: Path | None = None,
        timeout_margin_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.capabilities = capabilities
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._media_dir = Path(media_dir) if media_dir else None
        self._margin = timeout_margin_seconds
        #: Injectable transport so tests run with zero network access.
        self._transport = transport

    def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=self._transport,
        )

    def _failure(self, request: ProviderRequest, status: RequestStatus,
                 detail: str, latency_ms: int = 0) -> ProviderResponse:
        return ProviderResponse(
            request_id=request.request_id,
            provider=self.name,
            model=self._model,
            status=status,
            error_detail=detail,
            latency_ms=latency_ms,
        )

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self.supports(request.operation):
            return self._failure(
                request, RequestStatus.ERROR,
                f"operation {request.operation.value} not supported by {self.name}",
            )
        if request.operation is Operation.TEXT_GENERATION:
            return await self._generate_text(request)
        if request.operation is Operation.IMAGE_GENERATION:
            return await self._generate_image(request)
        return self._failure(
            request, RequestStatus.ERROR,
            f"operation {request.operation.value} has no v1 implementation",
        )

    async def _generate_text(self, request: ProviderRequest) -> ProviderResponse:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": request.params.get("temperature", 0.8),
        }
        if request.params.get("json_response"):
            payload["response_format"] = {"type": "json_object"}

        started = monotonic()
        try:
            async with self._client(request.timeout_seconds + self._margin) as client:
                response = await client.post("/chat/completions", json=payload)
        except httpx.TimeoutException:
            return self._failure(
                request, RequestStatus.TIMEOUT,
                f"text generation timed out after {request.timeout_seconds}s",
            )
        except httpx.HTTPError as exc:
            return self._failure(request, RequestStatus.ERROR, f"transport error: {exc}")
        latency_ms = int((monotonic() - started) * 1000)

        if response.status_code != 200:
            return self._failure(
                request, RequestStatus.ERROR,
                f"HTTP {response.status_code}: {response.text[:300]}", latency_ms,
            )
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {}) or {}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return self._failure(
                request, RequestStatus.ERROR,
                f"malformed completion payload: {exc}", latency_ms,
            )
        return ProviderResponse(
            request_id=request.request_id,
            provider=self.name,
            model=body.get("model", self._model),
            status=RequestStatus.OK,
            text=text,
            latency_ms=latency_ms,
            usage={
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
            },
        )

    async def _generate_image(self, request: ProviderRequest) -> ProviderResponse:
        if self._media_dir is None:
            return self._failure(
                request, RequestStatus.ERROR, "image provider has no media_dir configured"
            )
        payload = {
            "model": self._model,
            "prompt": request.user_prompt,
            "n": 1,
            "size": request.params.get("size", "1792x1024"),
            "response_format": "b64_json",
        }
        started = monotonic()
        try:
            async with self._client(request.timeout_seconds + self._margin) as client:
                response = await client.post("/images/generations", json=payload)
        except httpx.TimeoutException:
            return self._failure(
                request, RequestStatus.TIMEOUT,
                f"image generation timed out after {request.timeout_seconds}s",
            )
        except httpx.HTTPError as exc:
            return self._failure(request, RequestStatus.ERROR, f"transport error: {exc}")
        latency_ms = int((monotonic() - started) * 1000)

        if response.status_code != 200:
            return self._failure(
                request, RequestStatus.ERROR,
                f"HTTP {response.status_code}: {response.text[:300]}", latency_ms,
            )
        try:
            import base64

            body = response.json()
            b64 = body["data"][0]["b64_json"]
            data = base64.b64decode(b64)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return self._failure(
                request, RequestStatus.ERROR,
                f"malformed image payload: {exc}", latency_ms,
            )

        digest = hashlib.sha256(data).hexdigest()
        self._media_dir.mkdir(parents=True, exist_ok=True)
        # PNG is the OpenAI-compatible default; sniff for JPEG just in case.
        if data[:3] == b"\xff\xd8\xff":
            suffix, mime = "jpg", "image/jpeg"
        else:
            suffix, mime = "png", "image/png"
        path = self._media_dir / f"{self.name}_{digest[:16]}.{suffix}"
        path.write_bytes(data)
        width, height = _png_dimensions(data)
        return ProviderResponse(
            request_id=request.request_id,
            provider=self.name,
            model=body.get("model", self._model) if isinstance(body, dict) else self._model,
            status=RequestStatus.OK,
            media=MediaResult(
                path=str(path), mime_type=mime, checksum_sha256=digest,
                width=width, height=height,
            ),
            latency_ms=latency_ms,
        )

    async def health(self) -> ProviderHealth:
        try:
            async with self._client(timeout_seconds=5) as client:
                response = await client.get("/models")
            healthy = response.status_code == 200
            detail = "" if healthy else f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            healthy, detail = False, type(exc).__name__
        return ProviderHealth(provider=self.name, healthy=healthy, detail=detail)
