"""Tests for the chunk_notes projection cache (notes-export). Mirrors
test_chunk_ratings_cache.py — same wiring, same invariants."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.store.events import EventLog, ReadingEvent
from parsem.store.projections_cache import (
    apply_to_chunk_notes,
    apply_to_reading_state,
    get_notes_for_document,
    make_event_log,
    rebuild_chunk_notes,
)
from tests.conftest import T0


@pytest.fixture
def db(db_with_chunks: sqlite3.Connection) -> sqlite3.Connection:
    return db_with_chunks


@pytest.fixture
def log(db: sqlite3.Connection) -> EventLog:
    """Production wiring — fans out to ALL projections in one transaction."""
    return make_event_log(db)


def test_get_notes_returns_empty_dict_for_unnoted_document(db: sqlite3.Connection) -> None:
    assert get_notes_for_document(db, document_id=1) == {}


def test_apply_writes_first_note_for_a_chunk(db: sqlite3.Connection, log: EventLog) -> None:
    log.note_set(document_id=1, chunk_id=2, note="margin", created_at=T0)
    assert get_notes_for_document(db, document_id=1) == {2: "margin"}


def test_latest_note_wins_for_repeated_chunk(db: sqlite3.Connection, log: EventLog) -> None:
    log.note_set(document_id=1, chunk_id=2, note="one", created_at=T0)
    log.note_set(document_id=1, chunk_id=2, note="two", created_at=T0 + timedelta(seconds=1))
    log.note_set(document_id=1, chunk_id=2, note="three", created_at=T0 + timedelta(seconds=2))
    assert get_notes_for_document(db, document_id=1) == {2: "three"}


def test_note_clear_removes_the_note(db: sqlite3.Connection, log: EventLog) -> None:
    log.note_set(document_id=1, chunk_id=2, note="bye", created_at=T0)
    log.note_clear(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=1))
    assert get_notes_for_document(db, document_id=1) == {}


def test_non_note_events_are_no_op_for_chunk_notes(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.reveal(document_id=1, chunk_id=2, created_at=T0)
    log.rate_effort(document_id=1, chunk_id=2, rating=3, created_at=T0 + timedelta(seconds=1))
    log.pin_set(document_id=1, chunk_id=2, color_id=3, created_at=T0 + timedelta(seconds=2))
    assert get_notes_for_document(db, document_id=1) == {}


def test_concurrent_documents_track_notes_independently(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.note_set(document_id=1, chunk_id=0, note="d1", created_at=T0)
    log.note_set(document_id=2, chunk_id=0, note="d2", created_at=T0)
    log.note_set(document_id=1, chunk_id=2, note="d1b", created_at=T0)
    assert get_notes_for_document(db, document_id=1) == {0: "d1", 2: "d1b"}
    assert get_notes_for_document(db, document_id=2) == {0: "d2"}


def test_apply_silently_skips_when_position_has_no_chunk(db: sqlite3.Connection) -> None:
    """Drift guard: a note_set referencing a position with no chunks row
    must not crash and must not insert a NULL chunk_id."""
    log = EventLog(db)  # no projection hook — manual apply below
    event = log.note_set(document_id=1, chunk_id=999, note="ghost", created_at=T0)
    apply_to_chunk_notes(db, event)
    assert get_notes_for_document(db, document_id=1) == {}


def test_rebuild_replaces_all_notes_for_a_document(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.note_set(document_id=1, chunk_id=0, note="a", created_at=T0)
    log.note_set(document_id=1, chunk_id=2, note="b", created_at=T0 + timedelta(seconds=1))
    log.note_set(document_id=1, chunk_id=2, note="b2", created_at=T0 + timedelta(seconds=2))
    rebuilt = rebuild_chunk_notes(db, document_id=1, log=EventLog(db))
    assert rebuilt == {0: "a", 2: "b2"}
    assert get_notes_for_document(db, document_id=1) == {0: "a", 2: "b2"}


def test_rebuild_drops_stale_rows(db: sqlite3.Connection, log: EventLog) -> None:
    log.note_set(document_id=1, chunk_id=0, note="keep", created_at=T0)
    db.execute(
        "INSERT INTO chunk_notes (chunk_id, note, updated_at)"
        " SELECT id, 'stale', '2026-01-01' FROM chunks"
        " WHERE document_id=1 AND position=4"
    )
    db.commit()
    assert 4 in get_notes_for_document(db, document_id=1)
    rebuild_chunk_notes(db, document_id=1, log=EventLog(db))
    assert get_notes_for_document(db, document_id=1) == {0: "keep"}


def test_documents_delete_cascades_to_chunk_notes(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.note_set(document_id=1, chunk_id=0, note="x", created_at=T0)
    assert get_notes_for_document(db, document_id=1) == {0: "x"}
    db.execute("DELETE FROM documents WHERE id=1")
    db.commit()
    assert get_notes_for_document(db, document_id=1) == {}


def test_make_event_log_writes_notes_projection_in_one_transaction(
    db: sqlite3.Connection, log: EventLog
) -> None:
    """A single note_set must update both chunk_notes and reading_state
    atomically (the fan-out composer commits once)."""
    log.note_set(document_id=1, chunk_id=2, note="hi", created_at=T0)
    assert get_notes_for_document(db, document_id=1) == {2: "hi"}
    row = db.execute(
        "SELECT last_event_id_applied FROM reading_state WHERE document_id=1"
    ).fetchone()
    assert row is not None
    assert row["last_event_id_applied"] == 1


def test_chunk_notes_failure_rolls_back_event_and_reading_state(
    db: sqlite3.Connection,
) -> None:
    """If a hook raises after the note INSERT, the event and the
    reading_state UPSERT both roll back — single-transaction guarantee."""

    def _on_event(event: ReadingEvent) -> None:
        apply_to_reading_state(db, event)
        raise RuntimeError("boom")

    log = EventLog(db, on_event=_on_event)
    with pytest.raises(RuntimeError):
        log.note_set(document_id=1, chunk_id=2, note="hi", created_at=T0)
    assert log.events_for_document(1) == []
    assert get_notes_for_document(db, document_id=1) == {}
