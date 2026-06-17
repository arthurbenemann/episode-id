"""Tests for the Jellyfin rescan integration."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services import jellyfin


def _client(handler) -> jellyfin.JellyfinClient:
    return jellyfin.JellyfinClient(
        url="http://jellyfin.test:8096/",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_refresh_library_posts_with_token_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    _client(handler).refresh_library()

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "http://jellyfin.test:8096/Library/Refresh"
    assert seen[0].headers["X-Emby-Token"] == "secret-key"


def test_refresh_library_raises_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad api key"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).refresh_library()


def test_trigger_rescan_returns_skipped_when_unconfigured() -> None:
    with patch.object(jellyfin.settings, "jellyfin_url", None):
        result = jellyfin.trigger_rescan()
    assert result.state == "skipped"
    assert result.detail is None


def test_trigger_rescan_returns_triggered_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    result = jellyfin.trigger_rescan(_client(handler))

    assert result.state == "triggered"
    assert result.detail is None


def test_trigger_rescan_returns_failed_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    result = jellyfin.trigger_rescan(_client(handler))

    assert result.state == "failed"
    assert result.detail is not None
    assert "500" in result.detail or "Internal Server Error" in result.detail


def test_trigger_rescan_swallows_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = jellyfin.trigger_rescan(_client(handler))

    assert result.state == "failed"
    assert "connection refused" in (result.detail or "").lower()


def test_client_from_settings_returns_none_when_missing() -> None:
    with (
        patch.object(jellyfin.settings, "jellyfin_url", None),
        patch.object(jellyfin.settings, "jellyfin_api_key", "x"),
    ):
        assert jellyfin.client_from_settings() is None

    with (
        patch.object(jellyfin.settings, "jellyfin_url", "http://x"),
        patch.object(jellyfin.settings, "jellyfin_api_key", None),
    ):
        assert jellyfin.client_from_settings() is None


def test_client_from_settings_returns_client_when_configured() -> None:
    with (
        patch.object(jellyfin.settings, "jellyfin_url", "http://j:8096"),
        patch.object(jellyfin.settings, "jellyfin_api_key", "k"),
    ):
        client = jellyfin.client_from_settings()
    assert isinstance(client, jellyfin.JellyfinClient)
