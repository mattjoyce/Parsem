"""Outbound HTTP helper to the ductile gateway. ADR 0003.

Per-request `httpx.Client` instantiation. No module-scope clients, no
background calls, no retries beyond what httpx does natively. The rule
from ADR 0003: every outbound call here must be inside the lifecycle of
a user-initiated request handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import httpx

from parsem.config import DuctileSettings

DEFAULT_TIMEOUT_SECONDS = 5.0
FIRECRAWL_PLUGIN_PATH = "/plugin/firecrawl/handle"

DuctileErrorKind = Literal["config", "transport", "response"]


class DuctileError(Exception):
    """Raised on any ductile call failure. `kind` classifies the failure
    so callers can decide retry / surface semantics without parsing the
    reason string."""

    def __init__(
        self,
        reason: str,
        *,
        kind: DuctileErrorKind,
        ductile_status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.ductile_status = ductile_status


def submit_firecrawl_scrape(
    *,
    url: str,
    doc_id: str,
    output_dir: Path,
    settings: DuctileSettings,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Submit a URL scrape job to ductile's firecrawl plugin.

    Raises `DuctileError` on any failure (unreachable, timeout, 4xx, 5xx,
    unexpected status). On success returns None — the scrape itself is
    asynchronous on the plugin side; the completion signal is the
    markdown file appearing in `output_dir` (filewatch ingests it via
    the existing `/ingest/converted-arrived` endpoint).
    """
    if not settings.base_url:
        raise DuctileError("ductile.base_url not configured", kind="config")

    body = {
        "payload": {
            "url": url,
            "doc_id": doc_id,
            "output_dir": str(output_dir),
        }
    }
    headers = {"Content-Type": "application/json"}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"

    submit_url = settings.base_url.rstrip("/") + FIRECRAWL_PLUGIN_PATH

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(submit_url, json=body, headers=headers)
    except httpx.ConnectError as exc:
        raise DuctileError(f"ductile unreachable: {exc}", kind="transport") from exc
    except httpx.TimeoutException as exc:
        raise DuctileError(f"ductile timeout: {exc}", kind="transport") from exc
    except httpx.HTTPError as exc:
        raise DuctileError(f"ductile transport error: {exc}", kind="transport") from exc

    code = response.status_code
    if code >= 300:
        label = "5xx" if code >= 500 else "4xx" if code >= 400 else "unexpected status"
        raise DuctileError(
            f"ductile {label}: {code}",
            kind="response",
            ductile_status=code,
        )
