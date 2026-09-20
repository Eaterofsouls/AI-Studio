"""
Configuration module for AI Cinema Studio Engine.
Loads settings from environment variables and .env files using Pydantic Settings.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core environment
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    api_prefix: str = "/api/v1"

    # Storage
    base_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    storage_dir: Path = Field(default_factory=lambda: Path("./storage"))

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./cinema_studio.db",
        validation_alias="DATABASE_URL",
    )

    # Vector DB
    qdrant_url: str = Field(default="http://localhost:6333", validation_alias="QDRANT_URL")
    qdrant_api_key: Optional[str] = Field(default=None, validation_alias="QDRANT_API_KEY")

    # API Keys for Video / Media Providers
    fal_key: Optional[str] = Field(default=None, validation_alias="FAL_KEY")
    replicate_api_token: Optional[str] = Field(default=None, validation_alias="REPLICATE_API_TOKEN")
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    elevenlabs_api_key: Optional[str] = Field(default=None, validation_alias="ELEVENLABS_API_KEY")
    synclabs_api_key: Optional[str] = Field(default=None, validation_alias="SYNCLABS_API_KEY")
    muapi_api_key: Optional[str] = Field(default=None, validation_alias="MUAPI_API_KEY")
    heygen_api_key: Optional[str] = Field(default=None, validation_alias="HEYGEN_API_KEY")

    # FFmpeg executable path (auto-discovered if None)
    ffmpeg_path: Optional[str] = Field(default=None, validation_alias="FFMPEG_PATH")

    def ensure_directories(self) -> None:
        """Ensure necessary storage directories exist."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "graded").mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "audio").mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "composited").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    settings = Settings()
    settings.ensure_directories()
    return settings
