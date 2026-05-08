"""Tests for pins projection cache. Spec §13, §18.1, §21; bead Parsem-pv8."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.store.events import EventLog, ReadingEvent
from parsem.store.projections_cache import (
    apply_to_pins,
    apply_to_reading_state,
    load_pins_for_document,
    make_event_log,
    rebuild_pins,
)
from tests.conftest import T0


@pytest.fixture
def db(db_with_chunks: sqlite3.Connection) -> sqlite3.Connection:
    return db_with_chunks


@pytest.fixture
def log(db: sqlite3.Connection) -> EventLog:
    return make_event_log(db)


def _pin_rows(db: sqlite3.Connection, document_id: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT chunk_id_start, chunk_id_end, word_start, word_end, color_id"
        " FROM pins WHERE document_id=? ORDER BY id",
        (document_id,),
    ).fetchall()


def test_load_returns_empty_dict_for_unpinned_document(db: sqlite3.Connection) -> None:
    assert load_pins_for_document(db, document_id=1) == {}


def test_pin_set_writes_one_row(db: sqlite3.Connection, log: EventLog) -> None:
    log.pin_set(document_id=1, chunk_id=2, color_id=3, created_at=T0)
    rows = _pin_rows(db, document_id=1)
    assert len(rows) == 1
    assert rows[0]["word_start"] == 0
    assert rows[0]["word_end"] == -1
    assert rows[0]["color_id"] == 3
    assert rows[0]["chunk_id_start"] == rows[0]["chunk_id_end"]


def test_pin_set_returns_via_load_pins_as_position_dict(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_set(document_id=1, chunk_id=2, color_id=3, created_at=T0)
    log.pin_set(document_id=1, chunk_id=4, color_id=1, created_at=T0)
    assert load_pins_for_document(db, document_id=1) == {2: 3, 4: 1}


def test_pin_set_twice_on_same_chunk_keeps_one_row_with_latest_color(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_set(document_id=1, chunk_id=2, color_id=2, created_at=T0)
    log.pin_set(document_id=1, chunk_id=2, color_id=5, created_at=T0 + timedelta(seconds=1))
    rows = _pin_rows(db, document_id=1)
    assert len(rows) == 1
    assert rows[0]["color_id"] == 5
    assert load_pins_for_document(db, document_id=1) == {2: 5}


def test_pin_clear_deletes_chunk_level_row(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_set(document_id=1, chunk_id=2, color_id=3, created_at=T0)
    log.pin_clear(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=1))
    assert _pin_rows(db, document_id=1) == []
    assert load_pins_for_document(db, document_id=1) == {}


def test_pin_clear_on_unpinned_chunk_is_no_op(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_clear(document_id=1, chunk_id=2, created_at=T0)
    assert _pin_rows(db, document_id=1) == []


def test_concurrent_documents_track_pins_independently(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_set(document_id=1, chunk_id=0, color_id=4, created_at=T0)
    log.pin_set(document_id=2, chunk_id=0, color_id=1, created_at=T0)
    log.pin_set(document_id=1, chunk_id=2, color_id=2, created_at=T0)
    assert load_pins_for_document(db, document_id=1) == {0: 4, 2: 2}
    assert load_pins_for_document(db, document_id=2) == {0: 1}


def test_non_pin_events_are_no_op_for_pins(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.reveal(document_id=1, chunk_id=2, created_at=T0)
    log.conceal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    log.rate_effort(
        document_id=1, chunk_id=2, rating=4, created_at=T0 + timedelta(seconds=2)
    )
    assert load_pins_for_document(db, document_id=1) == {}


def test_apply_silently_skips_when_position_has_no_chunk(db: sqlite3.Connection) -> None:
    log = EventLog(db)  # bare log: hook is None, event commits itself
    event = log.pin_set(
        document_id=1, chunk_id=999, color_id=2, created_at=T0
    )
    apply_to_pins(db, event)
    assert _pin_rows(db, document_id=1) == []


def test_rebuild_replaces_chunk_level_pins_for_document(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_set(document_id=1, chunk_id=0, color_id=3, created_at=T0)
    log.pin_set(document_id=1, chunk_id=2, color_id=4, created_at=T0 + timedelta(seconds=1))
    log.pin_clear(document_id=1, chunk_id=0, created_at=T0 + timedelta(seconds=2))
    log.pin_set(document_id=1, chunk_id=3, color_id=1, created_at=T0 + timedelta(seconds=3))
    rebuilt = rebuild_pins(db, document_id=1, log=EventLog(db))
    assert rebuilt == {2: 4, 3: 1}
    assert load_pins_for_document(db, document_id=1) == {2: 4, 3: 1}


def test_rebuild_drops_stale_word_level_chunk_rows(db: sqlite3.Connection) -> None:
    """rebuild only wipes chunk-level rows (word_start=0, word_end=-1).
    A future word-level pin with non-sentinel offsets must be preserved."""
    db.execute(
        "INSERT INTO pins (document_id, chunk_id_start, word_start,"
        " chunk_id_end, word_end, color_id, created_at)"
        " SELECT 1, c.id, 5, c.id, 12, 4, '2026-01-01'"
        " FROM chunks c WHERE c.document_id=1 AND c.position=2"
    )
    db.commit()
    rebuild_pins(db, document_id=1, log=EventLog(db))
    rows = db.execute(
        "SELECT word_start, word_end FROM pins WHERE document_id=1"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["word_start"] == 5
    assert rows[0]["word_end"] == 12


def test_documents_delete_cascades_to_pins(
    db: sqlite3.Connection, log: EventLog
) -> None:
    log.pin_set(document_id=1, chunk_id=0, color_id=4, created_at=T0)
    assert load_pins_for_document(db, document_id=1) == {0: 4}
    db.execute("DELETE FROM documents WHERE id=1")
    db.commit()
    assert load_pins_for_document(db, document_id=1) == {}


def test_make_event_log_writes_all_three_projections_in_one_transaction(
    db: sqlite3.Connection, log: EventLog
) -> None:
    """A single pin_set event must update reading_state, chunk_ratings
    (no-op for pin_set, but the event cursor advances), AND pins —
    all in one transaction."""
    log.pin_set(document_id=1, chunk_id=2, color_id=3, created_at=T0)
    # pins populated
    assert load_pins_for_document(db, document_id=1) == {2: 3}
    # reading_state row also created (last_event_id_applied advanced)
    rs_row = db.execute(
        "SELECT last_event_id_applied FROM reading_state WHERE document_id=1"
    ).fetchone()
    assert rs_row is not None
    assert rs_row["last_event_id_applied"] == 1


def test_failure_after_reading_state_rolls_back_event_and_reading_state(
    db: sqlite3.Connection,
) -> None:
    """A failure between projections (here: simulated by raising right
    after `apply_to_reading_state` succeeds) must roll back BOTH the
    event INSERT and the reading_state UPSERT. The pins write never
    happened, but verifying its absence proves the transaction never
    committed."""

    def _on_event(event: ReadingEvent) -> None:
        apply_to_reading_state(db, event)
        raise RuntimeError("simulated mid-fan-out failure")

    log = EventLog(db, on_event=_on_event)
    with pytest.raises(RuntimeError):
        log.pin_set(document_id=1, chunk_id=2, color_id=3, created_at=T0)
    assert log.events_for_document(1) == []
    rs_row = db.execute(
        "SELECT 1 FROM reading_state WHERE document_id=1"
    ).fetchone()
    assert rs_row is None
    assert _pin_rows(db, document_id=1) == []
