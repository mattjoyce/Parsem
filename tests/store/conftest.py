"""Shared fixtures for store-layer tests."""

from __future__ import annotations

import sqlite3

import pytest

from parsem.domain.materialize import Chunk, Section
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_chunks_and_sections, insert_document
from tests.conftest import T0


def _chunk(position: int) -> Chunk:
    return Chunk(
        position=position,
        source_offset_start=position * 10,
        source_offset_end=position * 10 + 9,
        text=f"chunk {position}",
        lead_token_type="paragraph",
        lead_heading_level=None,
        estimated_read_seconds=1.0,
    )


@pytest.fixture
def db_with_chunks() -> sqlite3.Connection:
    """SQLite seeded with two documents and 5 chunks each. The standard
    fixture for projection-cache tests that need the chunks table
    populated so position → chunks.id resolution succeeds."""
    conn = connect(":memory:")
    migrate(conn)
    for title in ("d1", "d2"):
        document_id = insert_document(
            conn,
            title=title,
            original_path=f"{title}.md",
            status="ready",
            total_chunks=5,
            now=T0,
        )
        insert_chunks_and_sections(
            conn,
            document_id=document_id,
            chunks=[_chunk(i) for i in range(5)],
            sections=[
                Section(
                    heading_chunk_position=None,
                    heading_level=None,
                    start_chunk_position=0,
                    end_chunk_position=4,
                )
            ],
            now=T0,
        )
    return conn
