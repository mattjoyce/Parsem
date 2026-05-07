"""Phase 1 entry point. Spec: parsem-spec.md §25.1; bead Parsem-4bt.

`build_app()` is pure construction — loads the bundled welcome corpus from
disk, runs the chunker, builds ReaderState, and returns a FastAPI app.
Tests target it directly via TestClient. `main()` is the process
orchestrator that hands the built app to uvicorn.

Default host is 127.0.0.1 to avoid the spec §23 footgun ("I-bound-to-
0.0.0.0-by-accident"). Override the runner via the `_runner` kwarg in
tests so no port is opened.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from parsem.domain.bucket import BucketConfig
from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.events import EventLog
from parsem.web.app import create_app
from parsem.web.state import ReaderState

_WELCOME = Path(__file__).resolve().parents[1] / "data" / "welcome.md"


def build_app() -> FastAPI:
    """Load the welcome corpus, chunk it, and return the configured app."""
    text = _WELCOME.read_text(encoding="utf-8")
    output = chunk(parse(text), ChunkingConfig())
    state = ReaderState(
        chunks=output.chunks,
        sections=output.sections,
        event_log=EventLog(),
        bucket_config=BucketConfig(),
    )
    return create_app(state)


def main(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    _runner: Callable[..., Any] = uvicorn.run,
) -> None:
    """Build the app and hand it to uvicorn. The `_runner` kwarg is for
    tests — production code uses the default `uvicorn.run`."""
    _runner(build_app(), host=host, port=port)
