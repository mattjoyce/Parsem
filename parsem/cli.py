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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from parsem.domain.bucket import BucketConfig
from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_chunks_and_sections, insert_document
from parsem.store.projections_cache import initial_reader_positions, make_event_log
from parsem.web.app import create_app
from parsem.web.state import ReaderState

# Spec §20: resume.warm_chunks default. Phase 2 settings.py will read
# this from the settings table; until then it's a module-level default.
RESUME_WARM_CHUNKS_DEFAULT = 2

_WELCOME = Path(__file__).resolve().parents[1] / "data" / "welcome.md"


def build_app() -> FastAPI:
    """Load the welcome corpus, chunk it, and return the configured app.

    Phase 1.5 wiring (Parsem-v5l): runs against an in-memory SQLite for
    now. The EventLog is SQLite-backed; chunks and sections are seeded
    so FK constraints hold. Phase 2's library/upload beads will replace
    this with a file-backed DB and on-startup migration.
    """
    text = _WELCOME.read_text(encoding="utf-8")
    output = chunk(parse(text), ChunkingConfig())
    now = datetime.now(UTC)
    conn = connect(":memory:")
    migrate(conn)
    document_id = insert_document(
        conn,
        title="welcome",
        original_path="data/welcome.md",
        status="ready",
        total_chunks=len(output.chunks),
        now=now,
    )
    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=output.chunks,
        sections=output.sections,
        now=now,
    )
    current, high_water = initial_reader_positions(
        conn, document_id, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    state = ReaderState(
        chunks=output.chunks,
        sections=output.sections,
        event_log=make_event_log(conn),
        bucket_config=BucketConfig(),
        document_id=document_id,
        current_position=current,
        high_water_position=high_water,
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
