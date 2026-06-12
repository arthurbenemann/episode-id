"""Configuration loaded from environment variables and .env files."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # File system
    library_root: Path = Path("/media/tv")
    media_root: Path = Path("/media/input")

    # Matching
    sample_start_seconds: int = 180
    """Skip the first N seconds of each file when extracting reference dialogue
    (avoids cold opens, recaps, and title sequences which vary across episodes)."""

    sample_line_count: int = 40
    """How many subtitle lines to sample after `sample_start_seconds`."""

    min_confidence: int = 70
    """Below this confidence (0-100), matches are flagged for manual review."""

    # Output naming
    rename_template: str = "{series_title} - S{season:02}E{episode:02} - {episode_title}.{ext}"
    series_folder_template: str = "{series_title} ({year}) [tvdbid-{tvdb_id}]"
    include_provider_id: bool = True

    # Providers
    opensubtitles_api_key: str | None = None
    tvdb_api_key: str | None = None

    # Transcript cache (spec: cache aggressively, OpenSubtitles rate-limits)
    database_url: str = "sqlite:///./data/episode-id.db"

    @property
    def cache_db_path(self) -> Path:
        """Filesystem path extracted from the sqlite:/// database URL."""
        return Path(self.database_url.removeprefix("sqlite:///"))

    # HTTP
    http_timeout_seconds: int = 30
    http_user_agent: str = "episode-id/0.1 (+https://github.com/yourname/episode-id)"


settings = Settings()
