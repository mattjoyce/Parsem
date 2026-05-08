"""Incremental + rebuild cache for projections. Spec: parsem-spec.md §18.1, §18.5, §21.

Persists the pure projections from `parsem.domain.projections` into
the SQLite tables defined in `parsem.store.db`. The per-event logic
itself lives in `domain/projections.py:apply_event` so the cache and
any future batch rebuild share exactly one source of truth.

`apply_to_reading_state` is wired into `EventLog`'s `on_event` hook so
every event write keeps the cache fresh. `rebuild_reading_state` is
the §18.5 recovery path — call it on server start when
`reading_state.last_event_id_applied < MAX(reading_events.id)` for a
document.

This module imports nothing from `parsem.web`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from functools import partial

from parsem.domain.projections import (
    ReadingState,
    apply_event,
    build_reading_state,
    empty_reading_state,
    resume_position,
)
from parsem.store.events import EventLog, ReadingEvent


def load_reading_state(
    conn: sqlite3.Connection, document_id: int
) -> ReadingState | None:
    row = conn.execute(
        "SELECT document_id, high_water_position, current_position,"
        " last_event_id_applied"
        " FROM reading_state WHERE document_id=?",
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    return ReadingState(
        document_id=row["document_id"],
        high_water_position=row["high_water_position"],
        current_position=row["current_position"],
        last_event_id_applied=row["last_event_id_applied"],
    )


def apply_to_reading_state(
    conn: sqlite3.Connection, event: ReadingEvent
) -> None:
    current = load_reading_state(conn, event.document_id) or empty_reading_state(
        event.document_id
    )
    _write(conn, apply_event(current, event))


def make_event_log(conn: sqlite3.Connection) -> EventLog:
    """EventLog wired so every write keeps the reading_state
    projection current — call sites that build a ReaderState use this
    instead of `EventLog(conn)`."""
    return EventLog(conn, on_event=partial(apply_to_reading_state, conn))


def initial_reader_positions(
    conn: sqlite3.Connection, document_id: int, *, warm_chunks: int
) -> tuple[int, int]:
    """Returns ``(current_position, high_water_position)`` for opening
    a document — applies §25.2 resume math. Both 0 if no projection
    row exists (fresh document)."""
    cached = load_reading_state(conn, document_id)
    if cached is None:
        return 0, 0
    return resume_position(cached, warm_chunks), cached.high_water_position


def rebuild_reading_state(
    conn: sqlite3.Connection, document_id: int, log: EventLog
) -> ReadingState:
    """Recompute the projection from the full event history. §18.5.

    Reuses `EventLog.events_for_document` rather than running raw SQL
    here so the read path stays consolidated.
    """
    events = log.events_for_document(document_id)
    state = build_reading_state(document_id, events)
    _write(conn, state)
    return state


def _write(conn: sqlite3.Connection, state: ReadingState) -> None:
    conn.execute(
        "INSERT INTO reading_state"
        " (document_id, high_water_position, current_position,"
        "  last_event_id_applied, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(document_id) DO UPDATE SET"
        "  high_water_position=excluded.high_water_position,"
        "  current_position=excluded.current_position,"
        "  last_event_id_applied=excluded.last_event_id_applied,"
        "  updated_at=excluded.updated_at",
        (
            state.document_id,
            state.high_water_position,
            state.current_position,
            state.last_event_id_applied,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
