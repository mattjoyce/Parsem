"""URL → bytes downloader for the ingest pipeline.

Spec: ADR docs/adr/0001-nas-ingest-pipeline.md.

Used by the `POST /ingest` route and the `parsem add <url>` CLI when
the input is a URL. Downloads to memory (size-capped) and returns a
suggested filename so the caller can write it to `inbound/raw/`.

Single-user, internal-network app: any HTTP(S) host is allowed. Size
cap and timeout are the defenses against pathological URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class UrlFetchError(Exception):
    """Raised on any failure during URL fetch — network, HTTP status,
    or size cap. Carries a human-readable reason for the UI."""


@dataclass(frozen=True)
class FetchedFile:
    """Result of a successful URL fetch — bytes plus a suggested
    filename derived from Content-Disposition or the URL path."""

    content: bytes
    suggested_filename: str


def fetch(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    client: httpx.Client | None = None,
) -> FetchedFile:
    """Download `url` synchronously, capping size and applying a
    timeout. Raises `UrlFetchError` on network errors, HTTP 4xx/5xx,
    or response size > max_bytes. Pass `client` in tests to inject a
    mock transport.

    Stream-aborts on size — we never materialize a body larger than
    the cap, even if the server lies in Content-Length or doesn't
    send one. A pre-emptive Content-Length check rejects oversize
    early without spending bandwidth.
    """
    owned_client = client is None
    c = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)
    try:
        try:
            with c.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise UrlFetchError(
                        f"HTTP {response.status_code} from {url}"
                    )
                content_length_header = response.headers.get("content-length")
                if content_length_header:
                    try:
                        if int(content_length_header) > max_bytes:
                            raise UrlFetchError(
                                f"Response from {url} declares "
                                f"{content_length_header} bytes, exceeds "
                                f"{max_bytes} cap"
                            )
                    except ValueError:
                        pass  # malformed header — fall through to streaming check
                buffer = bytearray()
                for chunk in response.iter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        raise UrlFetchError(
                            f"Response from {url} exceeds size cap "
                            f"({max_bytes} bytes)"
                        )
                filename = _filename_from_response(response, url)
                return FetchedFile(
                    content=bytes(buffer), suggested_filename=filename
                )
        except httpx.HTTPError as exc:
            raise UrlFetchError(f"Network error fetching {url}: {exc}") from exc
    finally:
        if owned_client:
            c.close()


def _filename_from_response(response: httpx.Response, url: str) -> str:
    """Prefer Content-Disposition's filename; fall back to URL path
    basename; final fallback is `download` so the inbound dir always
    gets a name."""
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r'filename="?([^";]+)"?', disposition)
    if match:
        return match.group(1).strip()
    parsed = urlparse(url)
    basename = unquote(parsed.path).rsplit("/", 1)[-1]
    return basename or "download"
