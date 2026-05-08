"""Web-test fixtures. Builds a fresh ReaderState + TestClient per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.domain.bucket import BucketConfig
from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_chunks_and_sections, insert_document
from parsem.store.events import EventLog
from parsem.web.app import create_app
from parsem.web.state import ReaderState
from tests.conftest import T0

WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"


@pytest.fixture
def state() -> ReaderState:
    """Fresh ReaderState anchored at T0; tests reassign `clock` to bump time.

    Phase 2 (Parsem-v5l): the EventLog is SQLite-backed, so the fixture
    opens an in-memory SQLite, migrates, and seeds the welcome document
    + its chunks + sections. The FK from reading_events.document_id to
    documents.id is then satisfied for every event the routes emit.
    """
    blocks = parse(WELCOME.read_text(encoding="utf-8"))
    output = chunk(blocks, ChunkingConfig())
    conn = connect(":memory:")
    migrate(conn)
    document_id = insert_document(
        conn,
        title="welcome",
        original_path="data/welcome.md",
        status="ready",
        total_chunks=len(output.chunks),
        now=T0,
    )
    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=output.chunks,
        sections=output.sections,
        now=T0,
    )
    return ReaderState(
        chunks=output.chunks,
        sections=output.sections,
        event_log=EventLog(conn),
        bucket_config=BucketConfig(),
        document_id=document_id,
        clock=lambda: T0,
    )


@pytest.fixture
def client(state: ReaderState) -> Iterator[TestClient]:
    app = create_app(state)
    with TestClient(app) as c:
        yield c


def exhaust_bucket(client: TestClient, state: ReaderState) -> None:
    """Spend every token at the same instant — bucket reaches zero. Shared
    helper between the route tests and the GET-content tests."""
    for _ in range(state.bucket_config.capacity):
        client.post("/reveal")


@pytest.fixture
def reader_js_source(client: TestClient) -> str:
    """The served reader.js as a string. Avoids re-fetching across the
    contract-grep suite in tests/web/test_static.py."""
    return client.get("/static/reader.js").text
