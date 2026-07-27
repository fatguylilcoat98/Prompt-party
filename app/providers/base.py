"""Provider adapter interface.

All AI calls (text, image, audio) go through this interface. Provider-
specific logic lives only in adapter implementations, never in the shared
game engine (Milestone 4 rule). Raw provider errors are recorded privately
and never shown on public surfaces (Master Spec section 9).
"""

from __future__ import annotations

import abc
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.games.shared.schemas import Capability, new_id, utc_now


class Operation(str, Enum):
    TEXT_GENERATION = "text_generation"
    IMAGE_GENERATION = "image_generation"
    TEXT_TO_SPEECH = "text_to_speech"


class RequestStatus(str, Enum):
    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"
    UNSAFE = "unsafe"


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: new_id("req"))
    participant_id: str
    operation: Operation
    template_id: str = ""
    template_version: str = ""
    system_prompt: str = ""
    user_prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 45


class MediaResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    mime_type: str
    checksum_sha256: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    provider: str
    model: str
    status: RequestStatus
    text: str | None = None
    media: MediaResult | None = None
    latency_ms: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    # Private technical detail; recorded in the errors ledger, never public.
    error_detail: str | None = None


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    healthy: bool
    detail: str = ""
    checked_at: str = Field(default_factory=lambda: utc_now().isoformat())


class CapabilityNotSupported(Exception):
    pass


class ProviderAdapter(abc.ABC):
    """Base class for all providers. Adapters must not raise raw exceptions
    to callers: failures are returned as ProviderResponse with a non-OK
    status so the controller can apply failure-recovery rules."""

    name: str = "base"
    capabilities: frozenset[Capability] = frozenset()

    def supports(self, operation: Operation) -> bool:
        mapping = {
            Operation.TEXT_GENERATION: Capability.TEXT_GENERATION,
            Operation.IMAGE_GENERATION: Capability.IMAGE_GENERATION,
            Operation.TEXT_TO_SPEECH: Capability.TEXT_TO_SPEECH,
        }
        return mapping[operation] in self.capabilities

    @abc.abstractmethod
    async def generate(self, request: ProviderRequest) -> ProviderResponse: ...

    @abc.abstractmethod
    async def health(self) -> ProviderHealth: ...
