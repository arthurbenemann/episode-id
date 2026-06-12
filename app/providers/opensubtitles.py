"""OpenSubtitles provider — reference subtitles from the opensubtitles.com REST API.

Works for any show OpenSubtitles knows about, keyed by TVDB id — unlike the
Chakoteya provider, which is Star Trek only. Requires an API key
(opensubtitles.com/consumers); no account login is used.

Quota notes: searches are cheap but downloads are capped per day, so every
downloaded transcript goes through the TranscriptCache and cache hits skip
the download entirely. A failed download (e.g. quota exhausted mid-season)
skips that episode rather than failing the whole scan — the matcher can
still work against a partial season.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx
import pysubs2

from app.config import settings
from app.db import TranscriptCache
from app.providers.base import EpisodeTranscript, SubtitleProvider

log = logging.getLogger(__name__)

OPENSUBTITLES_API: Final = "https://api.opensubtitles.com/api/v1"

# Safety cap — a season of any sane show fits well within this.
_MAX_SEARCH_PAGES: Final = 10


@dataclass(frozen=True)
class _Candidate:
    """The best downloadable subtitle found for one episode."""

    episode: int
    title: str
    file_id: int
    from_trusted: bool
    download_count: int


class OpenSubtitlesProvider(SubtitleProvider):
    """Fetch season transcripts from opensubtitles.com."""

    name = "opensubtitles"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        cache: TranscriptCache | None = None,
        timeout: int = 30,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.opensubtitles_api_key
        self._cache = cache
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def fetch_season(
        self,
        series_key: str,
        season: int,
    ) -> list[EpisodeTranscript]:
        if not self._api_key:
            raise ValueError(
                "the opensubtitles provider requires OPENSUBTITLES_API_KEY "
                "(get one at opensubtitles.com/consumers)"
            )
        try:
            tvdb_id = int(series_key)
        except ValueError as exc:
            raise ValueError(
                f"the opensubtitles provider keys series by TVDB id, got {series_key!r}"
            ) from exc

        candidates = self._search_season(tvdb_id, season)
        if not candidates:
            raise ValueError(
                f"no English subtitles on opensubtitles for tvdb-{tvdb_id} season {season}"
            )

        transcripts: list[EpisodeTranscript] = []
        for episode in sorted(candidates):
            cand = candidates[episode]
            if self._cache is not None:
                cached = self._cache.get(self.name, series_key, season, episode)
                if cached is not None:
                    transcripts.append(cached)
                    continue
            try:
                text = self._download_transcript(cand.file_id)
            except Exception as exc:
                log.warning("S%02dE%02d download failed: %s", season, episode, exc)
                continue
            transcript = EpisodeTranscript(
                season=season,
                episode=episode,
                title=cand.title,
                text=text,
            )
            if self._cache is not None:
                self._cache.put(self.name, series_key, transcript)
            transcripts.append(transcript)

        if not transcripts:
            raise ValueError("all subtitle downloads failed (daily download quota exhausted?)")
        return transcripts

    # ---- internals ----

    def _headers(self) -> dict[str, str]:
        return {
            "Api-Key": self._api_key or "",
            "User-Agent": settings.http_user_agent,
            "Accept": "application/json",
        }

    def _search_season(self, tvdb_id: int, season: int) -> dict[int, _Candidate]:
        """Search all English subs for the season, keeping the best per episode."""
        best: dict[int, _Candidate] = {}
        page = 1
        total_pages = 1
        while page <= min(total_pages, _MAX_SEARCH_PAGES):
            resp = self._client.get(
                f"{OPENSUBTITLES_API}/subtitles",
                params={
                    "parent_tvdb_id": tvdb_id,
                    "season_number": season,
                    "languages": "en",
                    "page": page,
                },
                headers=self._headers(),
            )
            resp.raise_for_status()
            payload = resp.json()
            total_pages = int(payload.get("total_pages") or 1)
            for item in payload.get("data", []):
                cand = self._parse_candidate(item)
                if cand is None:
                    continue
                current = best.get(cand.episode)
                if current is None or self._rank(cand) > self._rank(current):
                    best[cand.episode] = cand
            page += 1
        return best

    @staticmethod
    def _rank(cand: _Candidate) -> tuple[bool, int]:
        return (cand.from_trusted, cand.download_count)

    @staticmethod
    def _parse_candidate(item: dict[str, Any]) -> _Candidate | None:
        """Extract a usable candidate from one search result, or None.

        AI/machine translations make poor reference dialogue, and
        foreign-parts-only subs cover almost none of the episode.
        """
        attrs = item.get("attributes") or {}
        if (
            attrs.get("ai_translated")
            or attrs.get("machine_translated")
            or attrs.get("foreign_parts_only")
        ):
            return None
        feature = attrs.get("feature_details") or {}
        episode = feature.get("episode_number")
        files = attrs.get("files") or []
        if not episode or not files or not files[0].get("file_id"):
            return None
        episode = int(episode)
        return _Candidate(
            episode=episode,
            title=str(feature.get("title") or f"Episode {episode:02d}"),
            file_id=int(files[0]["file_id"]),
            from_trusted=bool(attrs.get("from_trusted")),
            download_count=int(attrs.get("download_count") or 0),
        )

    def _download_transcript(self, file_id: int) -> str:
        resp = self._client.post(
            f"{OPENSUBTITLES_API}/download",
            json={"file_id": file_id},
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        link = resp.json().get("link")
        if not link:
            raise RuntimeError(f"no download link returned for file {file_id}")
        sub = self._client.get(link)
        sub.raise_for_status()
        return self._srt_to_text(sub.content)

    @staticmethod
    def _srt_to_text(raw: bytes) -> str:
        """Convert downloaded subtitle bytes to one whitespace-collapsed string."""
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        subs = pysubs2.SSAFile.from_string(text)
        lines = [ev.plaintext.strip() for ev in subs.events if ev.plaintext.strip()]
        return re.sub(r"\s+", " ", " ".join(lines))
