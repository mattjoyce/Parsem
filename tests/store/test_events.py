"""Tests for parsem.store.events. Spec: parsem-spec.md §18.1, §21."""

from __future__ import annotations

from datetime import timedelta

import pytest

from parsem.domain.bucket import BucketConfig, tokens_now
from parsem.store.events import EventLog, ReadingEvent
from tests.conftest import T0


def test_reveal_appends_and_returns_an_event() -> None:
    log = EventLog()
    event = log.reveal(document_id=1, chunk_id=5, created_at=T0)
    assert isinstance(event, ReadingEvent)
    assert event.event_type == "reveal"
    assert event.document_id == 1
    assert event.chunk_id == 5
    assert event.payload is None
    assert event.created_at == T0


def test_conceal_appends_with_no_payload() -> None:
    log = EventLog()
    event = log.conceal(document_id=1, chunk_id=5, created_at=T0)
    assert event.event_type == "conceal"
    assert event.payload is None


def test_rate_effort_records_rating_payload() -> None:
    log = EventLog()
    event = log.rate_effort(document_id=1, chunk_id=5, rating=4, created_at=T0)
    assert event.event_type == "rate_effort"
    assert event.payload == {"rating": 4}


def test_rate_effort_outside_one_to_five_raises_value_error() -> None:
    log = EventLog()
    with pytest.raises(ValueError):
        log.rate_effort(document_id=1, chunk_id=5, rating=0, created_at=T0)
    with pytest.raises(ValueError):
        log.rate_effort(document_id=1, chunk_id=5, rating=6, created_at=T0)


def test_rate_effort_accepts_inclusive_boundary_values() -> None:
    log = EventLog()
    low = log.rate_effort(document_id=1, chunk_id=1, rating=1, created_at=T0)
    high = log.rate_effort(document_id=1, chunk_id=1, rating=5, created_at=T0)
    assert low.payload == {"rating": 1}
    assert high.payload == {"rating": 5}


def test_pin_set_records_color_id_payload() -> None:
    log = EventLog()
    event = log.pin_set(document_id=1, chunk_id=5, color_id=3, created_at=T0)
    assert event.event_type == "pin_set"
    assert event.payload == {"color_id": 3}


def test_pin_set_outside_one_to_five_raises_value_error() -> None:
    log = EventLog()
    with pytest.raises(ValueError):
        log.pin_set(document_id=1, chunk_id=5, color_id=0, created_at=T0)
    with pytest.raises(ValueError):
        log.pin_set(document_id=1, chunk_id=5, color_id=6, created_at=T0)


def test_pin_set_accepts_inclusive_boundary_values() -> None:
    log = EventLog()
    low = log.pin_set(document_id=1, chunk_id=1, color_id=1, created_at=T0)
    high = log.pin_set(document_id=1, chunk_id=1, color_id=5, created_at=T0)
    assert low.payload == {"color_id": 1}
    assert high.payload == {"color_id": 5}


def test_pin_clear_appends_with_no_payload() -> None:
    log = EventLog()
    event = log.pin_clear(document_id=1, chunk_id=5, created_at=T0)
    assert event.event_type == "pin_clear"
    assert event.payload is None


def test_open_document_has_no_chunk_id() -> None:
    log = EventLog()
    event = log.open_document(document_id=1, created_at=T0)
    assert event.event_type == "open_document"
    assert event.chunk_id is None


def test_close_document_has_no_chunk_id() -> None:
    log = EventLog()
    event = log.close_document(document_id=1, created_at=T0)
    assert event.event_type == "close_document"
    assert event.chunk_id is None


def test_ids_start_at_one_and_monotonically_increase() -> None:
    log = EventLog()
    a = log.reveal(document_id=1, chunk_id=1, created_at=T0)
    b = log.conceal(document_id=1, chunk_id=1, created_at=T0)
    c = log.pin_set(document_id=1, chunk_id=1, color_id=1, created_at=T0)
    assert a.id == 1
    assert b.id == 2
    assert c.id == 3


def test_ids_are_unique_across_event_types() -> None:
    log = EventLog()
    log.open_document(document_id=1, created_at=T0)
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.rate_effort(document_id=1, chunk_id=1, rating=3, created_at=T0)
    log.close_document(document_id=1, created_at=T0)
    events = log.events_for_document(1)
    ids = [e.id for e in events]
    assert ids == sorted(set(ids))


def test_events_for_document_returns_only_that_documents_events() -> None:
    log = EventLog()
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.reveal(document_id=2, chunk_id=10, created_at=T0)
    log.reveal(document_id=1, chunk_id=2, created_at=T0)
    events = log.events_for_document(1)
    assert len(events) == 2
    assert all(e.document_id == 1 for e in events)


def test_events_for_document_preserves_append_order() -> None:
    log = EventLog()
    times = [T0 + timedelta(seconds=i) for i in range(5)]
    for i, t in enumerate(times):
        log.reveal(document_id=1, chunk_id=i, created_at=t)
    events = log.events_for_document(1)
    assert [e.chunk_id for e in events] == [0, 1, 2, 3, 4]


def test_events_for_unknown_document_returns_empty_list() -> None:
    log = EventLog()
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    assert log.events_for_document(999) == []


def test_reveal_times_returns_only_reveal_events() -> None:
    log = EventLog()
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.conceal(document_id=1, chunk_id=1, created_at=T0 + timedelta(seconds=1))
    log.rate_effort(document_id=1, chunk_id=1, rating=3, created_at=T0 + timedelta(seconds=2))
    log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=3))
    times = log.reveal_times_for_document(1)
    assert len(times) == 2
    assert times == [T0, T0 + timedelta(seconds=3)]


def test_reveal_times_filters_by_document_id() -> None:
    log = EventLog()
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.reveal(document_id=2, chunk_id=1, created_at=T0)
    assert len(log.reveal_times_for_document(1)) == 1
    assert len(log.reveal_times_for_document(2)) == 1


def test_reveal_times_feeds_directly_into_tokens_now() -> None:
    # Pinned to capacity=3 to verify the integration regardless of the
    # production default (spec §12.1: fixed at 5).
    log = EventLog()
    log.reveal(document_id=1, chunk_id=1, created_at=T0)
    log.reveal(document_id=1, chunk_id=2, created_at=T0 + timedelta(seconds=1))
    log.reveal(document_id=1, chunk_id=3, created_at=T0 + timedelta(seconds=2))
    times = log.reveal_times_for_document(1)
    config = BucketConfig(capacity=3)
    assert tokens_now(times, config, T0 + timedelta(seconds=2)) == 0


def test_reading_event_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    log = EventLog()
    event = log.reveal(document_id=1, chunk_id=5, created_at=T0)
    with pytest.raises(FrozenInstanceError):
        event.chunk_id = 99  # type: ignore[misc]
