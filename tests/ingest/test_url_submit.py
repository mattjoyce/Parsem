"""Tests for parsem.ingest.url_submit (claude-5fp).

The shared core that backs both `POST /ingest/url` and the CLI's
`parsem add <url>`. Inserts a `converting` row, calls the firecrawl
plugin via ductile, rolls back the row on failure.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from parsem.config import DuctileSettings
from parsem.ingest.ductile_client import DuctileError
from parsem.ingest.url_submit import (
    SubmitResult,
    UrlSubmitError,
    submit_url,
)
from parsem.store.db import connect, migrate


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


def _settings() -> DuctileSettings:
    return DuctileSettings(base_url="http://gateway:8888", api_token="")


def test_happy_path_returns_result_with_converting_row(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    with patch(
        "parsem.ingest.url_submit.submit_firecrawl_scrape", return_value=None
    ) as mock_submit:
        result = submit_url(
            "https://example.com/article",
            conn=conn,
            settings=_settings(),
            inbound_converted_dir=tmp_path,
        )
    assert isinstance(result, SubmitResult)
    assert result.doc_id == str(result.document_id)
    # Plugin was called with the doc_id we just minted.
    mock_submit.assert_called_once()
    assert mock_submit.call_args.kwargs["doc_id"] == result.doc_id
    assert mock_submit.call_args.kwargs["url"] == "https://example.com/article"
    assert mock_submit.call_args.kwargs["output_dir"] == tmp_path
    # Row exists with status=converting, source_type=url, original_path=url.
    row = conn.execute(
        "SELECT status, source_type, original_path FROM documents WHERE id = ?",
        (result.document_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "converting"
    assert row["source_type"] == "url"
    assert row["original_path"] == "https://example.com/article"


def test_empty_url_raises_bad_input(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(UrlSubmitError) as exc_info:
        submit_url("", conn=conn, settings=_settings(), inbound_converted_dir=tmp_path)
    assert exc_info.value.status == 400
    assert exc_info.value.kind == "bad_input"
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


def test_whitespace_url_raises_bad_input(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    with pytest.raises(UrlSubmitError):
        submit_url(
            "   \n", conn=conn, settings=_settings(), inbound_converted_dir=tmp_path
        )


def test_bad_scheme_raises_bad_input(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    for bad in ("file:///etc/passwd", "ftp://x", "javascript:alert(1)", "data:foo"):
        with pytest.raises(UrlSubmitError) as exc_info:
            submit_url(
                bad, conn=conn, settings=_settings(), inbound_converted_dir=tmp_path
            )
        assert exc_info.value.kind == "bad_input"
        assert exc_info.value.status == 400


def test_url_missing_hostname_raises(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    with pytest.raises(UrlSubmitError) as exc_info:
        submit_url(
            "https://", conn=conn, settings=_settings(), inbound_converted_dir=tmp_path
        )
    assert exc_info.value.kind == "bad_input"


def test_ductile_failure_rolls_back_row(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """When the ductile call fails, the converting row must be deleted
    so the user doesn't see a permanently-stuck library entry. This is
    the central reliability property of `submit_url`."""
    with patch(
        "parsem.ingest.url_submit.submit_firecrawl_scrape",
        side_effect=DuctileError("ductile 5xx: 503", kind="response", ductile_status=503),
    ):
        with pytest.raises(UrlSubmitError) as exc_info:
            submit_url(
                "https://example.com",
                conn=conn,
                settings=_settings(),
                inbound_converted_dir=tmp_path,
            )
    assert exc_info.value.status == 502
    assert exc_info.value.kind == "ductile"
    (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert count == 0


def test_config_error_classified_as_config_kind(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A 'not configured' DuctileError → UrlSubmitError(kind='config').
    Lets the caller distinguish 'fix your config' from 'ductile crashed'."""
    with patch(
        "parsem.ingest.url_submit.submit_firecrawl_scrape",
        side_effect=DuctileError("ductile.base_url not configured", kind="config"),
    ):
        with pytest.raises(UrlSubmitError) as exc_info:
            submit_url(
                "https://example.com",
                conn=conn,
                settings=_settings(),
                inbound_converted_dir=tmp_path,
            )
    assert exc_info.value.kind == "config"
    assert exc_info.value.status == 502


def test_title_derived_from_last_path_component(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    with patch("parsem.ingest.url_submit.submit_firecrawl_scrape", return_value=None):
        result = submit_url(
            "https://example.gov/regulations/title-42.html",
            conn=conn,
            settings=_settings(),
            inbound_converted_dir=tmp_path,
        )
    title = conn.execute(
        "SELECT title FROM documents WHERE id = ?", (result.document_id,)
    ).fetchone()["title"]
    assert "title 42.html" == title


def test_title_falls_back_to_hostname(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    with patch("parsem.ingest.url_submit.submit_firecrawl_scrape", return_value=None):
        result = submit_url(
            "https://example.com",
            conn=conn,
            settings=_settings(),
            inbound_converted_dir=tmp_path,
        )
    title = conn.execute(
        "SELECT title FROM documents WHERE id = ?", (result.document_id,)
    ).fetchone()["title"]
    assert title == "example.com"
