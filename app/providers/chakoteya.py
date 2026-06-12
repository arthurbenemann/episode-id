"""Chakoteya provider — Star Trek episode transcripts from chakoteya.net.

The site has a per-show index page (e.g. /NextGen/episodes.htm) with one table
per season. Each row links to a per-episode transcript page. The order of rows
within a season is broadcast/aired order, which matches TheTVDB's airdate
order and is what Jellyfin uses for `SxxEyy`.

Caveats:
    - The TNG pilot "Encounter at Farpoint" is one transcript that covers two
      aired episodes (101 + 102). We surface it as episode 1; matching the
      second half against episode 2's file should still work because the
      transcript covers both halves.
    - The site uses British English spellings in titles ("Honour" vs "Honor"),
      which sometimes differ from TheTVDB. The matcher doesn't care because
      we're matching transcripts, not titles.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

import httpx
from bs4 import BeautifulSoup, Tag

from app.db import TranscriptCache
from app.providers.base import EpisodeTranscript, SubtitleProvider

log = logging.getLogger(__name__)


CHAKOTEYA_BASE: Final = "http://www.chakoteya.net"

# Map a friendly key to the path segment used by chakoteya for each series.
SERIES_PATHS: Final[dict[str, str]] = {
    "tng": "NextGen",
    "ds9": "DS9",
    "voy": "Voyager",
    "ent": "Enterprise",
    "tos": "StarTrek",
    "tas": "TAS",
    "dis": "Discovery",
}


# Season names in the index page headers. Chakoteya uses spelled-out ordinals.
ORDINALS: Final[dict[str, int]] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}


@dataclass(frozen=True)
class IndexEntry:
    """One row in the season table on the index page."""

    season: int
    episode: int  # 1-based, in broadcast order
    title: str
    transcript_url: str


class ChakoteyaProvider(SubtitleProvider):
    """Scrape episode transcripts from chakoteya.net."""

    name = "chakoteya"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: int = 30,
        cache: TranscriptCache | None = None,
    ) -> None:
        self._cache = cache
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={
                # chakoteya rejects bot-looking User-Agents with 403, so
                # present as a regular browser.
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )

    def fetch_season(
        self,
        series_key: str,
        season: int,
    ) -> list[EpisodeTranscript]:
        path = SERIES_PATHS.get(series_key.lower())
        if path is None:
            raise ValueError(f"unknown series '{series_key}'. Known: {sorted(SERIES_PATHS)}")

        index = self._fetch_index(path)
        season_entries = [e for e in index if e.season == season]
        if not season_entries:
            raise ValueError(f"no episodes found for {series_key} season {season}")

        transcripts: list[EpisodeTranscript] = []
        for entry in season_entries:
            if self._cache is not None:
                cached = self._cache.get(self.name, series_key, entry.season, entry.episode)
                if cached is not None:
                    transcripts.append(cached)
                    continue
            text = self._fetch_transcript(entry.transcript_url)
            transcript = EpisodeTranscript(
                season=entry.season,
                episode=entry.episode,
                title=entry.title,
                text=text,
            )
            if self._cache is not None:
                self._cache.put(self.name, series_key, transcript)
            transcripts.append(transcript)
        return transcripts

    # ---- internals ----

    def _fetch_index(self, series_path: str) -> list[IndexEntry]:
        url = f"{CHAKOTEYA_BASE}/{series_path}/episodes.htm"
        log.info("fetching index: %s", url)
        resp = self._client.get(url)
        resp.raise_for_status()
        return list(self._parse_index(resp.text, series_path))

    def _parse_index(
        self,
        html: str,
        series_path: str,
    ) -> list[IndexEntry]:
        """Walk the index page, attaching each table row to its enclosing season.

        The page is roughly:
            <h?>Season One</h?>
            <table>...rows...</table>
            <h?>Season Two</h?>
            <table>...rows...</table>
        """
        soup = BeautifulSoup(html, "lxml")
        entries: list[IndexEntry] = []
        current_season: int | None = None

        for element in soup.body.descendants if soup.body else []:
            if not isinstance(element, Tag):
                continue

            # Heading text like "Season One" tells us which season the next
            # table belongs to.
            if element.name in {"h1", "h2", "h3", "h4", "p", "b", "strong"}:
                text = element.get_text(" ", strip=True)
                match = re.match(r"^Season\s+(\w+)", text, re.IGNORECASE)
                if match:
                    word = match.group(1).lower()
                    season_num = ORDINALS.get(word)
                    if season_num is None:
                        # Try numeric form too, just in case.
                        try:
                            season_num = int(word)
                        except ValueError:
                            season_num = None
                    if season_num is not None:
                        current_season = season_num
                continue

            if element.name != "table" or current_season is None:
                continue

            ep_num_in_season = 0
            for row in element.find_all("tr"):
                link = row.find("a")
                if link is None or not link.get("href"):
                    continue
                title = link.get_text(" ", strip=True)
                if not title:
                    continue
                ep_num_in_season += 1
                href = link["href"]
                if href.startswith("http"):
                    url = href
                else:
                    url = f"{CHAKOTEYA_BASE}/{series_path}/{href.lstrip('/')}"
                entries.append(
                    IndexEntry(
                        season=current_season,
                        episode=ep_num_in_season,
                        title=title,
                        transcript_url=url,
                    )
                )

        return entries

    def _fetch_transcript(self, url: str) -> str:
        log.debug("fetching transcript: %s", url)
        resp = self._client.get(url)
        resp.raise_for_status()
        return self._extract_dialogue(resp.text)

    @staticmethod
    def _extract_dialogue(html: str) -> str:
        """Pull plain-text dialogue out of a transcript page.

        Chakoteya pages are simple HTML with the transcript living mostly in
        one big table cell. We just take all visible text from the body and
        let the fuzzy matcher do its job; speaker tags like `PICARD:` add no
        noise that affects token_set_ratio meaningfully.
        """
        soup = BeautifulSoup(html, "lxml")
        if soup.body is None:
            return ""
        text = soup.body.get_text(" ", strip=True)
        # Collapse whitespace runs.
        return re.sub(r"\s+", " ", text)
