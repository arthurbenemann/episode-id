"""Abstract base for subtitle/transcript providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeTranscript:
    """One episode's transcript as retrieved from a provider."""

    season: int
    episode: int
    title: str
    text: str


class SubtitleProvider(ABC):
    """Source of canonical reference dialogue, used to identify files."""

    name: str

    @abstractmethod
    def fetch_season(
        self,
        series_key: str,
        season: int,
    ) -> list[EpisodeTranscript]:
        """Return all episode transcripts for one season of one series.

        `series_key` is provider-specific. For Chakoteya it's a show slug like
        "NextGen"; for OpenSubtitles it would be the TVDB or IMDB id.
        """
