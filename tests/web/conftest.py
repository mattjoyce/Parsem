"""Web-test fixtures. Builds a fresh ReaderState + TestClient per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.domain.bucket import BucketConfig
from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.events import EventLog
from parsem.web.app import create_app
from parsem.web.state import ReaderState
from tests.conftest import T0

WELCOME = Path(__file__).resolve().parents[2] / "data" / "welcome.md"


@pytest.fixture
def state() -> ReaderState:
    """Fresh ReaderState anchored at T0; tests reassign `clock` to bump time."""
    blocks = parse(WELCOME.read_text(encoding="utf-8"))
    output = chunk(blocks, ChunkingConfig())
    return ReaderState(
        chunks=output.chunks,
        sections=output.sections,
        event_log=EventLog(),
        bucket_config=BucketConfig(),
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
