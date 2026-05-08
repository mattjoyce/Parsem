"""Resume-on-open integration test. Spec §25.2; bead Parsem-3jd."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
from parsem.store.projections_cache import initial_reader_positions, make_event_log
from tests.conftest import T0


@pytest.fixture
def db() -> tuple[sqlite3.Connection, int]:
    """Bare migrated SQLite with one documents row — chunks aren't
    needed; resume math reads the projection only."""
    conn = connect(":memory:")
    migrate(conn)
    document_id = insert_document(
        conn, title="d1", original_path="d1.md", status="ready", now=T0
    )
    return conn, document_id


def _seed_reveals(conn: sqlite3.Connection, document_id: int, last_position: int) -> None:
    log = make_event_log(conn)
    for i in range(last_position + 1):
        log.reveal(
            document_id=document_id,
            chunk_id=i,
            created_at=T0 + timedelta(seconds=i),
        )


def test_resume_lands_at_high_water_minus_warm_chunks(
    db: tuple[sqlite3.Connection, int],
) -> None:
    conn, document_id = db
    _seed_reveals(conn, document_id, last_position=5)
    current, high_water = initial_reader_positions(conn, document_id, warm_chunks=2)
    assert high_water == 5
    assert current == 3


def test_resume_clamps_to_zero_for_shallow_high_water(
    db: tuple[sqlite3.Connection, int],
) -> None:
    conn, document_id = db
    _seed_reveals(conn, document_id, last_position=1)
    current, high_water = initial_reader_positions(conn, document_id, warm_chunks=2)
    assert high_water == 1
    assert current == 0


def test_fresh_document_with_no_events_starts_at_zero(
    db: tuple[sqlite3.Connection, int],
) -> None:
    conn, document_id = db
    current, high_water = initial_reader_positions(conn, document_id, warm_chunks=2)
    assert (current, high_water) == (0, 0)


def test_resume_warm_chunks_zero_lands_exactly_at_high_water(
    db: tuple[sqlite3.Connection, int],
) -> None:
    conn, document_id = db
    _seed_reveals(conn, document_id, last_position=4)
    current, high_water = initial_reader_positions(conn, document_id, warm_chunks=0)
    assert (current, high_water) == (4, 4)
