"""Web-test fixtures. Builds a fresh DB + ReaderState + TestClient per test."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.cli import RESUME_WARM_CHUNKS_DEFAULT
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_chunks_and_sections, insert_document
from parsem.web.app import create_app
from parsem.web.state import ReaderState, build_reader_state_for_document
from tests.conftest import T0, chunk_via_substrate

WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"


@pytest.fixture
def db() -> sqlite3.Connection:
    """In-memory SQLite seeded with the welcome doc + chunks + sections.
    document_id 1 is the welcome doc."""
    conn = connect(":memory:")
    migrate(conn)
    chunks, sections = chunk_via_substrate(WELCOME.read_text(encoding="utf-8"))
    document_id = insert_document(
        conn,
        title="welcome",
        original_path="data/welcome.md",
        status="ready",
        total_chunks=len(chunks),
        now=T0,
    )
    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=chunks,
        sections=sections,
        now=T0,
    )
    return conn


@pytest.fixture
def state(db: sqlite3.Connection) -> ReaderState:
    """ReaderState opened on the welcome doc (id=1), clock pinned to T0."""
    state = build_reader_state_for_document(
        db, document_id=1, warm_chunks=RESUME_WARM_CHUNKS_DEFAULT
    )
    assert state is not None
    state.clock = lambda: T0
    return state


@pytest.fixture
def client(
    state: ReaderState, db: sqlite3.Connection, tmp_path: Path
) -> Iterator[TestClient]:
    app = create_app(state, db=db, originals_dir=tmp_path / "originals")
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
