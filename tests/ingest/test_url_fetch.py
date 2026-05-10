"""Tests for the URL fetcher (claude-mwx.1)."""

from __future__ import annotations

import httpx
import pytest

from parsem.ingest.url_fetch import UrlFetchError, fetch


def _client_with_handler(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_returns_bytes_and_filename_from_content_disposition() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"# Hi\n",
            headers={"content-disposition": 'attachment; filename="article.md"'},
        )

    with _client_with_handler(handler) as c:
        result = fetch("https://example.com/x", client=c)
    assert result.content == b"# Hi\n"
    assert result.suggested_filename == "article.md"


def test_fetch_falls_back_to_url_basename() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    with _client_with_handler(handler) as c:
        result = fetch("https://example.com/path/to/post.md", client=c)
    assert result.suggested_filename == "post.md"


def test_fetch_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    with _client_with_handler(handler) as c, pytest.raises(UrlFetchError):
        fetch("https://example.com/missing", client=c)


def test_fetch_raises_when_content_length_exceeds_cap() -> None:
    """Pre-emptive check: server declares size > cap → reject before
    any body bytes are read."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 200,
            headers={"content-length": "200"},
        )

    with _client_with_handler(handler) as c, pytest.raises(UrlFetchError, match="exceeds"):
        fetch("https://example.com/big", client=c, max_bytes=100)


def test_fetch_streams_and_aborts_when_content_length_unparseable() -> None:
    """Malformed Content-Length header (server lies / non-numeric) →
    pre-check falls through, streaming loop must catch the overrun
    before the buffer holds more than `max_bytes`."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 200,
            headers={"content-length": "not-a-number"},
        )

    with _client_with_handler(handler) as c, pytest.raises(UrlFetchError, match="exceeds size cap"):
        fetch("https://example.com/big", client=c, max_bytes=100)


def test_fetch_translates_network_errors_to_url_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with _client_with_handler(handler) as c, pytest.raises(UrlFetchError, match="Network"):
        fetch("https://example.com/x", client=c)


def test_fetch_uses_empty_filename_fallback() -> None:
    """URL ending in a slash → no basename → 'download' fallback."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    with _client_with_handler(handler) as c:
        result = fetch("https://example.com/", client=c)
    assert result.suggested_filename == "download"
