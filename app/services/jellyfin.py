"""Jellyfin integration — kick a library scan after a successful apply.

Both `JELLYFIN_URL` and `JELLYFIN_API_KEY` must be set; otherwise the
integration is silently off and `apply_job` reports state="skipped".

Jellyfin's `/Library/Refresh` endpoint queues a global scan. It returns
immediately (the scan runs server-side) and only re-indexes changed
directories, so the cost on a no-op call is small. Authentication is
the `X-Emby-Token` header — the canonical key/header pair both Jellyfin
and Emby accept.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JellyfinStatus:
    """Outcome of the post-apply Jellyfin call, surfaced to the API + UI."""

    # "skipped" — integration not configured (default).
    # "triggered" — server accepted the refresh request.
    # "failed" — request reached the server but it returned an error,
    #            or the network call itself failed. `detail` has the message.
    state: str
    detail: str | None = None

    @classmethod
    def skipped(cls) -> JellyfinStatus:
        return cls(state="skipped")

    @classmethod
    def triggered(cls) -> JellyfinStatus:
        return cls(state="triggered")

    @classmethod
    def failed(cls, detail: str) -> JellyfinStatus:
        return cls(state="failed", detail=detail)


class JellyfinClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: int = 10,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    def refresh_library(self) -> None:
        """Queue a global library scan. Raises on HTTP or transport failure."""
        resp = self._client.post(
            f"{self._url}/Library/Refresh",
            headers={"X-Emby-Token": self._api_key},
        )
        resp.raise_for_status()


def client_from_settings() -> JellyfinClient | None:
    """Return a configured client, or None if the integration is off."""
    if not settings.jellyfin_url or not settings.jellyfin_api_key:
        return None
    return JellyfinClient(settings.jellyfin_url, settings.jellyfin_api_key)


def trigger_rescan(client: JellyfinClient | None = None) -> JellyfinStatus:
    """Call Jellyfin's library refresh. Never raises — the move already worked."""
    client = client if client is not None else client_from_settings()
    if client is None:
        return JellyfinStatus.skipped()
    try:
        client.refresh_library()
    except Exception as exc:
        log.warning("jellyfin library refresh failed: %s", exc)
        return JellyfinStatus.failed(f"{type(exc).__name__}: {exc}")
    return JellyfinStatus.triggered()
