"""Append-only reading event log. Spec: parsem-spec.md §18.1, §21.

Phase 1 in-memory placeholder for the Phase 2 SQLite-backed table. The
ReadingEvent dataclass mirrors the SQL row shape exactly so the swap is
mechanical: replace the underlying list with an INSERT, and the queries
with SELECT statements that respect the same field names.

Time is injected (`created_at` parameter) so the log itself is testable
without freezing or patching the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

EventType = Literal[
    "reveal",
    "conceal",
    "rate_effort",
    "pin_set",
    "pin_clear",
    "open_document",
    "close_document",
]


class RateEffortPayload(TypedDict):
    rating: int


class PinSetPayload(TypedDict):
    color_id: int


# Persisted payload shapes — explicit and versionable per standards doc and
# spec §21 (payload_json column). Reveal/conceal/pin_clear/open/close carry
# no payload (None).
EventPayload = RateEffortPayload | PinSetPayload | None


@dataclass(frozen=True)
class ReadingEvent:
    """One reader action. Mirrors spec §21 reading_events row."""

    id: int
    document_id: int
    event_type: EventType
    chunk_id: int | None
    payload: EventPayload
    created_at: datetime


class EventLog:
    """Append-only in-memory event log.

    Phase 1 placeholder; Phase 2 will replace the underlying list with a
    SQLite-backed implementation behind the same public interface, at which
    point _next_id becomes AUTOINCREMENT and _events becomes a table.

    Single-process only — _next_id is not safe under multiprocessing.
    Memory grows with session length; server restart bounds it. Both
    constraints disappear in the Phase 2 swap.
    """

    def __init__(self) -> None:
        self._events: list[ReadingEvent] = []
        self._next_id: int = 1

    def reveal(
        self,
        *,
        document_id: int,
        chunk_id: int,
        created_at: datetime,
    ) -> ReadingEvent:
        return self._append("reveal", document_id, chunk_id, None, created_at)

    def conceal(
        self,
        *,
        document_id: int,
        chunk_id: int,
        created_at: datetime,
    ) -> ReadingEvent:
        return self._append("conceal", document_id, chunk_id, None, created_at)

    def rate_effort(
        self,
        *,
        document_id: int,
        chunk_id: int,
        rating: int,
        created_at: datetime,
    ) -> ReadingEvent:
        """Record an effort rating (1=easy, 5=struggled). Raises ValueError
        if rating is outside [1, 5]."""
        if not 1 <= rating <= 5:
            raise ValueError(f"rating must be in 1..5, got {rating}")
        payload: RateEffortPayload = {"rating": rating}
        return self._append("rate_effort", document_id, chunk_id, payload, created_at)

    def pin_set(
        self,
        *,
        document_id: int,
        chunk_id: int,
        color_id: int,
        created_at: datetime,
    ) -> ReadingEvent:
        """Record a pin with one of the 5 colours (1-5). Raises ValueError
        if color_id is outside [1, 5]."""
        if not 1 <= color_id <= 5:
            raise ValueError(f"color_id must be in 1..5, got {color_id}")
        payload: PinSetPayload = {"color_id": color_id}
        return self._append("pin_set", document_id, chunk_id, payload, created_at)

    def pin_clear(
        self,
        *,
        document_id: int,
        chunk_id: int,
        created_at: datetime,
    ) -> ReadingEvent:
        return self._append("pin_clear", document_id, chunk_id, None, created_at)

    def open_document(
        self,
        *,
        document_id: int,
        created_at: datetime,
    ) -> ReadingEvent:
        return self._append("open_document", document_id, None, None, created_at)

    def close_document(
        self,
        *,
        document_id: int,
        created_at: datetime,
    ) -> ReadingEvent:
        return self._append("close_document", document_id, None, None, created_at)

    def events_for_document(self, document_id: int) -> list[ReadingEvent]:
        return [e for e in self._events if e.document_id == document_id]

    def reveal_times_for_document(self, document_id: int) -> list[datetime]:
        return [
            e.created_at
            for e in self._events
            if e.document_id == document_id and e.event_type == "reveal"
        ]

    def _append(
        self,
        event_type: EventType,
        document_id: int,
        chunk_id: int | None,
        payload: EventPayload,
        created_at: datetime,
    ) -> ReadingEvent:
        event = ReadingEvent(
            id=self._next_id,
            document_id=document_id,
            event_type=event_type,
            chunk_id=chunk_id,
            payload=payload,
            created_at=created_at,
        )
        self._events.append(event)
        self._next_id += 1
        return event
