"""SQLite-backed reading event log. Spec: parsem-spec.md §18.1, §21.

Drop-in replacement for the Phase 1 in-memory EventLog (Parsem-v5l). The
public interface — reveal/conceal/rate_effort/pin_set/pin_clear/
open_document/close_document and events_for_document/
reveal_times_for_document — is unchanged so route handlers and existing
web tests stay intact.

`chunk_id` semantics carry over from Phase 1: it is the chunk's POSITION
within its document, not chunks.id. db.py drops the chunks-side FK on
reading_events for that reason; deletes still cascade through the
documents FK. ReadingEvent.id and event ordering are owned by the
database via AUTOINCREMENT.

Time is injected (`created_at`) so callers (the route layer and tests)
remain in control of clock semantics. Datetimes round-trip through
ISO-8601 strings; payloads round-trip through JSON.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict, cast

EventType = Literal[
    "reveal",
    "conceal",
    "rate_effort",
    "rate_clear",
    "pin_set",
    "pin_clear",
    "note_set",
    "note_clear",
    "open_document",
    "close_document",
]


class RateEffortPayload(TypedDict):
    rating: int


class PinSetPayload(TypedDict):
    color_id: int


class NoteSetPayload(TypedDict):
    note: str


EventPayload = RateEffortPayload | PinSetPayload | NoteSetPayload | None


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
    """SQLite-backed append-only event log.

    Holds a sqlite3.Connection passed in by the caller; never opens or
    closes the connection itself. Multiple EventLog instances may share
    a connection — they're stateless query wrappers.

    The optional ``on_event`` callback fires after every successful
    insert with the freshly created `ReadingEvent`. Projection caches
    (Parsem-3jd, 1na, pv8) wire themselves in here so the on-disk
    projection rows stay current with each write.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        on_event: Callable[[ReadingEvent], None] | None = None,
    ) -> None:
        self._conn = conn
        self._on_event = on_event

    def reveal(
        self, *, document_id: int, chunk_id: int, created_at: datetime
    ) -> ReadingEvent:
        return self._append("reveal", document_id, chunk_id, None, created_at)

    def conceal(
        self, *, document_id: int, chunk_id: int, created_at: datetime
    ) -> ReadingEvent:
        return self._append("conceal", document_id, chunk_id, None, created_at)

    def rate_effort(
        self, *, document_id: int, chunk_id: int, rating: int, created_at: datetime
    ) -> ReadingEvent:
        if not 1 <= rating <= 5:
            raise ValueError(f"rating must be in 1..5, got {rating}")
        payload: RateEffortPayload = {"rating": rating}
        return self._append("rate_effort", document_id, chunk_id, payload, created_at)

    def rate_clear(
        self, *, document_id: int, chunk_id: int, created_at: datetime
    ) -> ReadingEvent:
        """Wipe the rating on a chunk. Mirrors `pin_clear` — the projection
        deletes the chunk_ratings row; replay reproduces the wiped state.
        Raised by the dot-toggle UX (claude-axx.3): clicking a filled
        rating dot a second time clears the rating."""
        return self._append("rate_clear", document_id, chunk_id, None, created_at)

    def pin_set(
        self, *, document_id: int, chunk_id: int, color_id: int, created_at: datetime
    ) -> ReadingEvent:
        if not 1 <= color_id <= 5:
            raise ValueError(f"color_id must be in 1..5, got {color_id}")
        payload: PinSetPayload = {"color_id": color_id}
        return self._append("pin_set", document_id, chunk_id, payload, created_at)

    def pin_clear(
        self, *, document_id: int, chunk_id: int, created_at: datetime
    ) -> ReadingEvent:
        return self._append("pin_clear", document_id, chunk_id, None, created_at)

    def note_set(
        self, *, document_id: int, chunk_id: int, note: str, created_at: datetime
    ) -> ReadingEvent:
        """Attach (or overwrite) the reader's note on a chunk. Mirrors
        `rate_effort` — the projection upserts the chunk_notes row;
        replay reproduces the latest note. Empty/whitespace text is a
        caller error: the route maps an emptied editor to `note_clear`."""
        if not note.strip():
            raise ValueError("note text must be non-empty; use note_clear to wipe")
        payload: NoteSetPayload = {"note": note}
        return self._append("note_set", document_id, chunk_id, payload, created_at)

    def note_clear(
        self, *, document_id: int, chunk_id: int, created_at: datetime
    ) -> ReadingEvent:
        """Wipe the note on a chunk. Mirrors `rate_clear` — the
        projection deletes the chunk_notes row; replay reproduces the
        wiped state."""
        return self._append("note_clear", document_id, chunk_id, None, created_at)

    def open_document(self, *, document_id: int, created_at: datetime) -> ReadingEvent:
        return self._append("open_document", document_id, None, None, created_at)

    def close_document(self, *, document_id: int, created_at: datetime) -> ReadingEvent:
        return self._append("close_document", document_id, None, None, created_at)

    def events_for_document(self, document_id: int) -> list[ReadingEvent]:
        rows = self._conn.execute(
            "SELECT id, document_id, event_type, chunk_id, payload_json, created_at"
            " FROM reading_events WHERE document_id=? ORDER BY id",
            (document_id,),
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def reveal_times_for_document(self, document_id: int) -> list[datetime]:
        rows = self._conn.execute(
            "SELECT created_at FROM reading_events"
            " WHERE document_id=? AND event_type='reveal' ORDER BY id",
            (document_id,),
        ).fetchall()
        return [datetime.fromisoformat(row["created_at"]) for row in rows]

    def _append(
        self,
        event_type: EventType,
        document_id: int,
        chunk_id: int | None,
        payload: EventPayload,
        created_at: datetime,
    ) -> ReadingEvent:
        payload_json = json.dumps(payload) if payload is not None else None
        cur = self._conn.execute(
            "INSERT INTO reading_events"
            " (document_id, chunk_id, event_type, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (document_id, chunk_id, event_type, payload_json, created_at.isoformat()),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        event = ReadingEvent(
            id=new_id,
            document_id=document_id,
            event_type=event_type,
            chunk_id=chunk_id,
            payload=payload,
            created_at=created_at,
        )
        if self._on_event is None:
            self._conn.commit()
            return event
        # Hook present: defer commit so the projection update lands in
        # the same transaction as the event INSERT. Rollback on failure
        # so we never leave events in the log without their projection.
        try:
            self._on_event(event)
        except Exception:
            self._conn.rollback()
            raise
        return event


def rate_effort_rating(event: ReadingEvent) -> int | None:
    """Centralized payload narrowing — events.py owns the invariant
    that `rate_effort` events always carry a `RateEffortPayload`. Any
    callsite that needs the rating int from a generic ReadingEvent
    routes through here so the union narrowing lives in one place."""
    if event.event_type != "rate_effort":
        return None
    return cast(RateEffortPayload, event.payload)["rating"]


def pin_set_color(event: ReadingEvent) -> int | None:
    """Same pattern as `rate_effort_rating`: returns the color_id from
    a `pin_set` event's payload, or None for any other event type."""
    if event.event_type != "pin_set":
        return None
    return cast(PinSetPayload, event.payload)["color_id"]


def note_set_text(event: ReadingEvent) -> str | None:
    """Same pattern as `pin_set_color`: returns the note text from a
    `note_set` event's payload, or None for any other event type."""
    if event.event_type != "note_set":
        return None
    return cast(NoteSetPayload, event.payload)["note"]


def _row_to_event(row: sqlite3.Row) -> ReadingEvent:
    payload_raw = row["payload_json"]
    payload: EventPayload = json.loads(payload_raw) if payload_raw is not None else None
    return ReadingEvent(
        id=row["id"],
        document_id=row["document_id"],
        event_type=row["event_type"],
        chunk_id=row["chunk_id"],
        payload=payload,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
