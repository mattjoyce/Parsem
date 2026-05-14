"""URL submission core. Shared between web route and CLI.

Inserts a `converting` documents row, submits to ductile's firecrawl
plugin, rolls back on failure. ADR 0003.

This is the only Parsem path that initiates outbound HTTP — and it
does so within a single user-initiated call (synchronous, request-
bounded). No background work, no retries that outlive the call.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from parsem.config import DuctileSettings
from parsem.ingest.ductile_client import DuctileError, submit_firecrawl_scrape
from parsem.store.documents import delete_document, insert_document

UrlSubmitErrorKind = Literal["bad_input", "config", "ductile"]


class UrlSubmitError(Exception):
    """Raised on any submit failure. `kind` classifies the failure;
    `status` is derived via the class-level map so the route layer never
    has to invent HTTP codes."""

    _STATUS_BY_KIND: dict[str, int] = {
        "bad_input": 400,
        "config": 502,
        "ductile": 502,
    }

    def __init__(self, reason: str, *, kind: UrlSubmitErrorKind) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind: UrlSubmitErrorKind = kind

    @property
    def status(self) -> int:
        return self._STATUS_BY_KIND[self.kind]


@dataclass(frozen=True)
class SubmitResult:
    document_id: int
    doc_id: str


def _validate_url(url: str) -> str:
    if not url or not url.strip():
        raise UrlSubmitError("url is required", kind="bad_input")
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise UrlSubmitError(
            f"url scheme must be http or https (got {parsed.scheme!r})",
            kind="bad_input",
        )
    if not parsed.netloc:
        raise UrlSubmitError("url missing hostname", kind="bad_input")
    return cleaned


def _derive_title(url: str) -> str:
    """Initial title from URL; replaced when content arrives via
    /ingest/converted-arrived. Use last non-empty path component or
    the hostname."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if parts:
        return parts[-1].replace("-", " ").replace("_", " ")
    return parsed.netloc or url


def submit_url(
    url: str,
    *,
    conn: sqlite3.Connection,
    settings: DuctileSettings,
    inbound_converted_dir: Path,
) -> SubmitResult:
    """Validate URL, insert a `converting` row, submit to ductile, roll
    back the row on submit failure. Returns the SubmitResult on success.

    Raises `UrlSubmitError` on any failure with `status` set to the
    appropriate HTTP code (400 for bad input, 502 for config/ductile).

    The rollback is best-effort: if the DELETE itself fails after a
    ductile error, we swallow it — the user already has a 502, and a
    stuck-converting row is benign (visible in the library, retryable
    later via re-ingest UI).
    """
    cleaned_url = _validate_url(url)
    now = datetime.now(UTC)

    document_id = insert_document(
        conn,
        title=_derive_title(cleaned_url),
        source_type="url",
        original_path=cleaned_url,
        status="converting",
        now=now,
    )
    conn.commit()
    doc_id = str(document_id)

    try:
        submit_firecrawl_scrape(
            url=cleaned_url,
            doc_id=doc_id,
            output_dir=inbound_converted_dir,
            settings=settings,
        )
    except DuctileError as exc:
        try:
            delete_document(conn, document_id)
            conn.commit()
        except sqlite3.Error:
            pass
        submit_kind: UrlSubmitErrorKind = "config" if exc.kind == "config" else "ductile"
        raise UrlSubmitError(exc.reason, kind=submit_kind) from exc

    return SubmitResult(document_id=document_id, doc_id=doc_id)
