"""Tests for parsem.store.events. Spec: parsem-spec.md §18.1, §21; bead Parsem-v5l."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from parsem.domain.bucket import BucketConfig, tokens_now
from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
from parsem.store.events import EventLog, ReadingEvent
from tests.conftest import T0


@pytest.fixture
def db() -> sqlite3.Connection:
    """SQLite with two documents seeded so FK constraints on
    reading_events.document_id are satisfied for tests touching multiple
    documents."""
    conn = connect(":memory:")
    migrate(conn)
    insert_document(conn, title="d1", original_path="d1.md", status="ready", now=T0)
    insert_document(conn, title="d2", original_path="d2.md", status="ready", now=T0)
    return conn


@pytest.fixture
def log(db: sqlite3.Connection) -> EventLog:
    return EventLog(db)


def test_reveal_appends_and_returns_an_event(log: EventLog) -> None:
    event = log.reveal(document_id=1, chunk_id=5, created_at=T0)
    assert isinstance(event, ReadingEvent)
    assert event.event_type == "reveal"
    assert event.document_id == 1
    assert event.chunk_id == 5
    assert event.payload is None
    assert event.created_at == T0


def test_conceal_appends_with_no_payload(log: EventLog) -> None:
    event = log.conceal(document_id=1, chunk_id=5, created_at=T0)
    assert event.event_type == "conceal"
    assert event.payload is None


def test_rate_effort_records_rating_payload(log: EventLog) -> None:
    event = log.rate_effort(document_id=1, chunk_id=5, rating=4, created_at=T0)
    assert event.event_type == "rate_effort"
    assert event.payload == {"rating": 4}


def test_rate_effort_outside_one_to_five_raises_value_error(log: EventLog) -> None:
    with pytest.raises(ValueError):
        log.rate_effort(document_id=1, chunk_id=5, rating=0, created_at=T0)
    with pytest.raises(ValueError):
        log.rate_effort(document_id=1, chunk_id=5, rating=6, created_at=T0)


def test_rate_effort_accepts_inclusive_boundary_values(log: EventLog) -> None:
    low = log.rate_effort(document_id=1, chunk_id=1, rating=1, created_at=T0)
    high = log.rate_effort(document_id=1, chunk_id=1, rating=5, created_at=T0)
    assert low.payload == {"rating": 1}
    assert high.payload == {"rating": 5}


def test_pin_set_records_color_id_payload(log: EventLog) -> None:
    event = log.pin_set(document_id=1, chunk_id=5, color_id=3, created_at=T0)
    assert event.event_type == "pin_set"
    assert event.payload == {"color_id": 3}


def test_pin_set_outside_one_to_five_raises_value_error(log: EventLog) -> None:
    with pytest.raises(ValueError):
        log.pin_set(document_id=1, chunk_id=5, color_id=0, created_at=T0)
    with pytest.raises(ValueError):
        log.pin_set(document_id=1, chunk_id=5, color_id=6, created_at=T0)


def test_pin_set_accepts_inclusive_boundary_values(log: EventLog) -> None:
    low = log.pin_set(document_id=1, chunk_id=1, color_id=1, created_at=T0)
    high = log.pin_set(document_id=1, chunk_id=1, color_id=5, created_at=T0)
    assert low.payload == {"color_id": 1}
    assert high.payload == {"color_id": 5}


def test_pin_clear_appends_with_no_payload(log: EventLog) -> None:
    event = log.pin_clear(document_id=1, chunk_id=5, created_at=T0)
    assert event.event_type == "pin_clear"
    assert event.payload is None


def test_open_document_has_no_chunk_id(log: EventLog) -> None:
    event = log.open_document(document_id=1, created_at=T0)
    assert event.event_type == "open_document"
    assert event.chunk_id is None


def test_close_document_has_no_chunk_id(log: EventLog) -> None:
    event = log.close_document(document_id=1, created_at=T0)
    assert event.event_type == "close_document"
    assert event.chunk_id is None


def test_ids_start_at_one_and_monotonically_increase(log: EventLog) -> None:
    a = log.reveal(document_id=1, chunk_id=1, created_at=T0)
    b = log.conceal(document_id=1, chunk_id=1, created_at=T0)
    c = log.pin_set(document_id=1, chunk_id=1, color_id=1, created_at=T0)
    assert a.id == 1
    assert b.id == 2
    assert c.id == 3


def test_ids_are_unique_across_event_types(log: EventLog) -> None:
    log.open_document(document_id=1, created_at=T0)
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.rate_effort(document_id=1, chunk_id=1, rating=3, created_at=T0)
    log.close_document(document_id=1, created_at=T0)
    events = log.events_for_document(1)
    ids = [e.id for e in events]
    assert ids == sorted(set(ids))


def test_events_for_document_returns_only_that_documents_events(log: EventLog) -> None:
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.reveal(document_id=2, chunk_id=10, created_at=T0)
    log.reveal(document_id=1, chunk_id=2, created_at=T0)
    events = log.events_for_document(1)
    assert len(events) == 2
    assert all(e.document_id == 1 for e in events)


def test_events_for_document_preserves_append_order(log: EventLog) -> None:
    times = [T0 + timedelta(seconds=i) for i in range(5)]
    for i, t in enumerate(times):
        log.reveal(document_id=1, chunk_id=i, created_at=t)
    events = log.events_for_document(1)
    assert [e.chunk_id for e in events] == [0, 1, 2, 3, 4]


def test_events_for_unknown_document_returns_empty_list(log: EventLog) -> None:
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    assert log.events_for_document(999) == []


def test_reveal_times_returns_only_reveal_events(log: EventLog) -> None:
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.conceal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    log.rate_effort(document_id=1, chunk_id=1, rating=3, created_at=T0 + timedelta(seconds=2))
    log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=3))
    times = log.reveal_times_for_document(1)
    assert len(times) == 2
    assert times == [T0, T0 + timedelta(seconds=3)]


def test_reveal_times_filters_by_document_id(log: EventLog) -> None:
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.reveal(document_id=2, chunk_id=1, created_at=T0)
    assert len(log.reveal_times_for_document(1)) == 1
    assert len(log.reveal_times_for_document(2)) == 1


def test_reveal_times_feeds_directly_into_tokens_now(log: EventLog) -> None:
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=1))
    log.reveal(document_id=1, chunk_id=3, created_at=T0 + timedelta(seconds=2))
    times = log.reveal_times_for_document(1)
    config = BucketConfig(capacity=3)
    assert tokens_now(times, config, T0 + timedelta(seconds=2)) == 0


def test_reading_event_is_immutable(log: EventLog) -> None:
    from dataclasses import FrozenInstanceError

    event = log.reveal(document_id=1, chunk_id=5, created_at=T0)
    with pytest.raises(FrozenInstanceError):
        event.chunk_id = 99  # type: ignore[misc]


def test_payload_round_trips_through_json(log: EventLog) -> None:
    """Set a rating, fetch the event back, payload is the original dict."""
    log.rate_effort(document_id=1, chunk_id=7, rating=4, created_at=T0)
    [event] = log.events_for_document(1)
    assert event.payload == {"rating": 4}


def test_documents_delete_cascades_to_reading_events(
    db: sqlite3.Connection, log: EventLog
) -> None:
    """Deleting the parent document removes all its events. Even though
    the chunks-side FK was dropped, the documents-side FK still cascades."""
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.conceal(document_id=1, chunk_id=1, created_at=T0)
    db.execute("DELETE FROM documents WHERE id=1")
    db.commit()
    assert log.events_for_document(1) == []


def test_events_module_does_not_import_from_web() -> None:
    import ast
    from pathlib import Path

    tree = ast.parse(Path("parsem/store/events.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("parsem.web"), (
                f"forbidden import: from {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("parsem.web"), (
                    f"forbidden import: {alias.name}"
                )
