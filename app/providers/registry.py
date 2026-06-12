"""Provider registry — maps provider names to constructed instances.

The CLI, JSON API, and htmx views all resolve providers through here so a
new provider only needs one registration (plus its CLI/UI affordances, per
CLAUDE.md "Adding a new provider").
"""

from __future__ import annotations

from typing import Final

from app.db import TranscriptCache
from app.providers.base import SubtitleProvider
from app.providers.chakoteya import ChakoteyaProvider
from app.providers.opensubtitles import OpenSubtitlesProvider

PROVIDER_NAMES: Final = ("chakoteya", "opensubtitles")


def create_provider(
    name: str,
    *,
    cache: TranscriptCache | None = None,
) -> SubtitleProvider:
    if name == "chakoteya":
        return ChakoteyaProvider(cache=cache)
    if name == "opensubtitles":
        return OpenSubtitlesProvider(cache=cache)
    raise ValueError(f"unknown provider '{name}'. Known: {', '.join(PROVIDER_NAMES)}")
