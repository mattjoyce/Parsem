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
    """SQLite-backed append-only event log.

    Holds a sqlite3.Connection passed in by the caller; never opens or
    closes the connection itself. Multiple EventLog instances may share
    a connection — they're stateless query wrappers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

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
        self._conn.commit()
        return ReadingEvent(
            id=new_id,
            document_id=document_id,
            event_type=event_type,
            chunk_id=chunk_id,
            payload=payload,
            created_at=created_at,
        )


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
