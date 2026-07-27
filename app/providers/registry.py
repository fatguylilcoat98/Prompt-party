"""Provider registry: name -> adapter instance.

The controller resolves participants' ``provider`` field through this
registry, so swapping mock providers for real adapters (Milestone 4) is a
configuration change, not an engine change.
"""

from __future__ import annotations

from app.providers.base import ProviderAdapter, ProviderHealth


class UnknownProviderError(KeyError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        self._providers[adapter.name] = adapter

    def get(self, name: str) -> ProviderAdapter:
        try:
            return self._providers[name]
        except KeyError:
            raise UnknownProviderError(name) from None

    def names(self) -> list[str]:
        return sorted(self._providers)

    async def health(self) -> list[ProviderHealth]:
        return [await adapter.health() for adapter in self._providers.values()]
