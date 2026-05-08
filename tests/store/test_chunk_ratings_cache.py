"""Tests for chunk_ratings projection cache. Spec §18.1, §21; bead Parsem-1na."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.store.events import EventLog, ReadingEvent
from parsem.store.projections_cache import (
    apply_to_chunk_ratings,
    apply_to_reading_state,
    get_ratings_for_document,
    make_event_log,
    rebuild_chunk_ratings,
)
from tests.conftest import T0


@pytest.fixture
def db(db_with_chunks: sqlite3.Connection) -> sqlite3.Connection:
    return db_with_chunks


@pytest.fixture
def log(db: sqlite3.Connection) -> EventLog:
    """Production wiring — fans out to ALL projections in one transaction."""
    return make_event_log(db)


def test_get_ratings_returns_empty_dict_for_unrated_document(db: sqlite3.Connection) -> None:
    assert get_ratings_for_document(db, document_id=1) == {}


def test_apply_writes_first_rating_for_a_chunk(db: sqlite3.Connection, log: EventLog) -> None:
    log.rate_effort(document_id=1, chunk_id=2, rating=4, created_at=T0)
    assert get_ratings_for_document(db, document_id=1) == {2: 4}


def test_latest_rating_wins_for_repeated_chunk(db: sqlite3.Connection, log: EventLog) -> None:
    log.rate_effort(document_id=1, chunk_id=2, rating=2, created_at=T0)
    log.rate_effort(document_id=1, chunk_id=2, rating=5, created_at=T0 + timedelta(seconds=1))
    log.rate_effort(document_id=1, chunk_id=2, rating=3, created_at=T0 + timedelta(seconds=2))
    assert get_ratings_for_document(db, document_id=1) == {2: 3}


def test_non_rate_effort_events_are_no_op_for_chunk_ratings(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.reveal(document_id=1, chunk_id=2, created_at=T0)
    log.conceal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    log.pin_set(
        document_id=1, chunk_id=2, color_id=3, created_at=T0 + timedelta(seconds=2)
    )
    log.pin_clear(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=3))
    assert get_ratings_for_document(db, document_id=1) == {}


def test_concurrent_documents_track_ratings_independently(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.rate_effort(document_id=1, chunk_id=0, rating=4, created_at=T0)
    log.rate_effort(document_id=2, chunk_id=0, rating=1, created_at=T0)
    log.rate_effort(document_id=1, chunk_id=2, rating=5, created_at=T0)
    assert get_ratings_for_document(db, document_id=1) == {0: 4, 2: 5}
    assert get_ratings_for_document(db, document_id=2) == {0: 1}


def test_apply_silently_skips_when_position_has_no_chunk(db: sqlite3.Connection) -> None:
    """Drift guard: a rate_effort referencing a position with no
    chunks row must not crash and must not insert a NULL chunk_id."""
    log = EventLog(db)  # no projection hook — manual apply below
    event = log.rate_effort(
        document_id=1, chunk_id=999, rating=3, created_at=T0
    )
    apply_to_chunk_ratings(db, event)
    assert get_ratings_for_document(db, document_id=1) == {}


def test_rebuild_replaces_all_ratings_for_a_document(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.rate_effort(document_id=1, chunk_id=0, rating=2, created_at=T0)
    log.rate_effort(document_id=1, chunk_id=2, rating=4, created_at=T0 + timedelta(seconds=1))
    log.rate_effort(document_id=1, chunk_id=2, rating=5, created_at=T0 + timedelta(seconds=2))
    rebuilt = rebuild_chunk_ratings(db, document_id=1, log=EventLog(db))
    assert rebuilt == {0: 2, 2: 5}
    assert get_ratings_for_document(db, document_id=1) == {0: 2, 2: 5}


def test_rebuild_drops_stale_rows(db: sqlite3.Connection, log: EventLog) -> None:
    """If the cache is desynced (e.g. a row was written by a now-removed
    event), rebuild must wipe it. Simulate by hand-inserting a stale row."""
    log.rate_effort(document_id=1, chunk_id=0, rating=3, created_at=T0)
    db.execute(
        "INSERT INTO chunk_ratings (chunk_id, rating, updated_at)"
        " SELECT id, 99, '2026-01-01' FROM chunks"
        " WHERE document_id=1 AND position=4"
    )
    db.commit()
    assert 4 in get_ratings_for_document(db, document_id=1)
    rebuild_chunk_ratings(db, document_id=1, log=EventLog(db))
    assert get_ratings_for_document(db, document_id=1) == {0: 3}


def test_documents_delete_cascades_to_chunk_ratings(
    db: sqlite3.Connection, log: EventLog
) -> None:
    """The chunks→chunk_ratings FK is ON DELETE CASCADE; deleting a
    document removes the chunks, which then cascades into chunk_ratings."""
    log.rate_effort(document_id=1, chunk_id=0, rating=4, created_at=T0)
    assert get_ratings_for_document(db, document_id=1) == {0: 4}
    db.execute("DELETE FROM documents WHERE id=1")
    db.commit()
    assert get_ratings_for_document(db, document_id=1) == {}


def test_make_event_log_writes_BOTH_projections_in_one_transaction(
    db: sqlite3.Connection, log: EventLog
) -> None:
    """A single rate_effort event must update BOTH reading_state and
    chunk_ratings in one atomic step. (Reveal would also update
    reading_state; here we use rate_effort to assert chunk_ratings
    fan-out specifically.)"""
    log.rate_effort(document_id=1, chunk_id=2, rating=4, created_at=T0)
    # chunk_ratings populated
    assert get_ratings_for_document(db, document_id=1) == {2: 4}
    # reading_state row also created (last_event_id_applied advanced)
    row = db.execute(
        "SELECT last_event_id_applied FROM reading_state WHERE document_id=1"
    ).fetchone()
    assert row is not None
    assert row["last_event_id_applied"] == 1


def test_chunk_ratings_failure_rolls_back_event_and_reading_state(
    db: sqlite3.Connection,
) -> None:
    """If the chunk_ratings hook raises, the event INSERT and the
    reading_state UPSERT must BOTH roll back — single transaction
    guarantee extended to the second projection."""

    def _on_event(event: ReadingEvent) -> None:
        apply_to_reading_state(db, event)
        raise RuntimeError("chunk_ratings boom")

    log = EventLog(db, on_event=_on_event)
    with pytest.raises(RuntimeError):
        log.reveal(document_id=1, chunk_id=2, created_at=T0)
    # No event committed:
    assert log.events_for_document(1) == []
    # No reading_state row committed:
    row = db.execute(
        "SELECT 1 FROM reading_state WHERE document_id=1"
    ).fetchone()
    assert row is None
