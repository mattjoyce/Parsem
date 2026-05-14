"""Tests for parsem.ingest.ductile_client (claude-5fp).

The outbound HTTP helper Parsem uses to submit URL scrape jobs to
ductile's firecrawl plugin. Per ADR 0003, calls are user-initiated and
request-bounded; this module's job is to surface every transport-level
failure as a typed `DuctileError` the route can map to a 502.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from parsem.config import DuctileSettings
from parsem.ingest.ductile_client import DuctileError, submit_firecrawl_scrape


def _settings(*, base_url: str = "http://gateway:8888", api_token: str = "") -> DuctileSettings:
    return DuctileSettings(base_url=base_url, api_token=api_token)


def _ok_response(status_code: int = 202) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    return response


def test_happy_path_returns_none(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(202)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        result = submit_firecrawl_scrape(
            url="https://example.com",
            doc_id="42",
            output_dir=tmp_path,
            settings=_settings(),
        )
    assert result is None


def test_unconfigured_base_url_raises_immediately(tmp_path: Path) -> None:
    """An empty base_url means URL ingest is disabled. The helper
    must short-circuit BEFORE making any HTTP call."""
    with patch("parsem.ingest.ductile_client.httpx.Client") as mock_factory:
        with pytest.raises(DuctileError, match="not configured"):
            submit_firecrawl_scrape(
                url="https://example.com",
                doc_id="42",
                output_dir=tmp_path,
                settings=_settings(base_url=""),
            )
    mock_factory.assert_not_called()


def test_connect_error_becomes_ductile_error(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.side_effect = httpx.ConnectError("nope")
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        with pytest.raises(DuctileError, match="unreachable"):
            submit_firecrawl_scrape(
                url="https://example.com",
                doc_id="42",
                output_dir=tmp_path,
                settings=_settings(),
            )


def test_timeout_becomes_ductile_error(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.side_effect = httpx.ReadTimeout("slow")
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        with pytest.raises(DuctileError, match="timeout"):
            submit_firecrawl_scrape(
                url="https://example.com",
                doc_id="42",
                output_dir=tmp_path,
                settings=_settings(),
            )


def test_5xx_response_raises_with_status(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(503)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        with pytest.raises(DuctileError) as exc_info:
            submit_firecrawl_scrape(
                url="https://example.com",
                doc_id="42",
                output_dir=tmp_path,
                settings=_settings(),
            )
    assert exc_info.value.ductile_status == 503
    assert "5xx" in exc_info.value.reason


def test_4xx_response_raises_with_status(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(401)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        with pytest.raises(DuctileError) as exc_info:
            submit_firecrawl_scrape(
                url="https://example.com",
                doc_id="42",
                output_dir=tmp_path,
                settings=_settings(),
            )
    assert exc_info.value.ductile_status == 401
    assert "4xx" in exc_info.value.reason


def test_unexpected_3xx_raises(tmp_path: Path) -> None:
    """We follow no redirects — a 3xx from the gateway is unexpected
    and surfaces as a DuctileError."""
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(301)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        with pytest.raises(DuctileError, match="unexpected"):
            submit_firecrawl_scrape(
                url="https://example.com",
                doc_id="42",
                output_dir=tmp_path,
                settings=_settings(),
            )


def test_api_token_sent_as_bearer(tmp_path: Path) -> None:
    """When `ductile.api_token` is non-empty, it must travel as a
    Bearer header on the outbound request."""
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(202)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        submit_firecrawl_scrape(
            url="https://example.com",
            doc_id="42",
            output_dir=tmp_path,
            settings=_settings(api_token="t0ken"),
        )
    call_kwargs = mock_client.post.call_args.kwargs
    headers = call_kwargs["headers"]
    assert headers["Authorization"] == "Bearer t0ken"


def test_no_auth_header_when_token_empty(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(202)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        submit_firecrawl_scrape(
            url="https://example.com",
            doc_id="42",
            output_dir=tmp_path,
            settings=_settings(api_token=""),
        )
    headers = mock_client.post.call_args.kwargs["headers"]
    assert "Authorization" not in headers


def test_request_targets_firecrawl_scrape_path(tmp_path: Path) -> None:
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.post.return_value = _ok_response(202)
    with patch("parsem.ingest.ductile_client.httpx.Client", return_value=mock_client):
        submit_firecrawl_scrape(
            url="https://example.com/article",
            doc_id="7",
            output_dir=tmp_path,
            settings=_settings(base_url="http://gateway:8888"),
        )
    call_args = mock_client.post.call_args
    url_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
    assert url_arg == "http://gateway:8888/plugin/firecrawl/scrape"
    body = call_args.kwargs["json"]
    assert body == {
        "payload": {
            "url": "https://example.com/article",
            "doc_id": "7",
            "output_dir": str(tmp_path),
        }
    }
