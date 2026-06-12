"""SQLite cache for provider transcripts.

The spec calls for aggressive caching of reference transcripts —
OpenSubtitles caps downloads at a small daily quota, and chakoteya.net is
slow enough that re-fetching a whole season on every scan is wasteful.
Rows are keyed by (provider, series_key, season, episode) so different
providers and shows never collide.

Plain stdlib sqlite3: a single upsert table doesn't justify an ORM. Each
operation opens its own short-lived connection, which keeps the class safe
to share between the API thread and BackgroundTasks workers.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from app.providers.base import EpisodeTranscript

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    provider   TEXT    NOT NULL,
    series_key TEXT    NOT NULL,
    season     INTEGER NOT NULL,
    episode    INTEGER NOT NULL,
    title      TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    fetched_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider, series_key, season, episode)
)
"""


class TranscriptCache:
    """Persistent transcript store.

    Construction is free — the database file and schema are created lazily
    on first use, so instantiating the cache in a code path that never
    fetches (e.g. tests with a mocked provider) leaves no file behind.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute(_SCHEMA)
        return conn

    def get(
        self,
        provider: str,
        series_key: str,
        season: int,
        episode: int,
    ) -> EpisodeTranscript | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT season, episode, title, text FROM transcripts"
                " WHERE provider = ? AND series_key = ? AND season = ? AND episode = ?",
                (provider, series_key, season, episode),
            ).fetchone()
        if row is None:
            return None
        return EpisodeTranscript(season=row[0], episode=row[1], title=row[2], text=row[3])

    def get_season(
        self,
        provider: str,
        series_key: str,
        season: int,
    ) -> list[EpisodeTranscript]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT season, episode, title, text FROM transcripts"
                " WHERE provider = ? AND series_key = ? AND season = ?"
                " ORDER BY episode",
                (provider, series_key, season),
            ).fetchall()
        return [EpisodeTranscript(season=r[0], episode=r[1], title=r[2], text=r[3]) for r in rows]

    def put(self, provider: str, series_key: str, transcript: EpisodeTranscript) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO transcripts (provider, series_key, season, episode, title, text)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (provider, series_key, season, episode)"
                " DO UPDATE SET title = excluded.title, text = excluded.text,"
                "               fetched_at = datetime('now')",
                (
                    provider,
                    series_key,
                    transcript.season,
                    transcript.episode,
                    transcript.title,
                    transcript.text,
                ),
            )
