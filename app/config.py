"""Configuration system.

All settings come from environment variables with the ``PROMPT_PARTY_``
prefix (or a local ``.env`` file), keeping this deployment fully separate
from Team Talk (Master Spec sections 1 and 12).

Secrets are held as ``SecretStr`` and are masked in ``masked_dump()``,
which is the only representation that may be shown in the Producer UI or
logs (Master Spec section 12).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROMPT_PARTY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8710
    log_level: str = "INFO"

    # Persistence — separate database and directories from Team Talk.
    database_url: str = "sqlite:///./data/prompt_party.db"
    media_dir: Path = Path("./media")
    exports_dir: Path = Path("./exports")

    # Producer authority
    producer_token: SecretStr = SecretStr("change-me")

    # Provider adapters (Milestone 4). "mock" runs the show without any
    # external API call.
    text_provider: str = "mock"
    text_provider_base_url: str = ""
    text_provider_api_key: SecretStr = SecretStr("")
    text_provider_model: str = ""

    image_provider: str = "mock"
    image_provider_base_url: str = ""
    image_provider_api_key: SecretStr = SecretStr("")
    image_provider_model: str = ""

    def masked_dump(self) -> dict:
        """Settings as a dict with every secret masked. Safe for logs and
        the Producer UI; never expose raw secret values anywhere public."""
        data = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                data[name] = "********" if value.get_secret_value() else ""
            elif isinstance(value, Path):
                data[name] = str(value)
            else:
                data[name] = value
        return data


def get_settings(**overrides) -> Settings:
    return Settings(**overrides)
