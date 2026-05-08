"""Incremental + rebuild cache for projections. Spec: parsem-spec.md §18.1, §18.5, §21.

Persists the pure projections from `parsem.domain.projections` into
the SQLite tables defined in `parsem.store.db`. The per-event logic
itself lives in `domain/projections.py:apply_event` /
`apply_rating_event` so the cache and any future batch rebuild share
exactly one source of truth.

`make_event_log` wires the EventLog so every event write fans out
through ALL projection writers in a single transaction — event INSERT,
reading_state UPSERT, and chunk_ratings UPSERT either all commit or
all roll back. The `apply_to_*` functions DO NOT commit; the composer
in `make_event_log` commits once after every projection has applied.
The `rebuild_*` functions are standalone (called outside the hook)
and commit themselves.

`rebuild_reading_state` / `rebuild_chunk_ratings` are the §18.5
recovery paths — call them on server start when
`last_event_id_applied < MAX(reading_events.id)` for a document.

This module imports nothing from `parsem.web`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from parsem.domain.projections import (
    ReadingState,
    apply_event,
    build_chunk_ratings,
    build_reading_state,
    empty_reading_state,
    resume_position,
)
from parsem.store.events import EventLog, ReadingEvent, rate_effort_rating

# ─── reading_state projection (Parsem-3jd) ────────────────────────────


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
    """Fold one event into the cached reading_state row. Does NOT
    commit — the EventLog hook composer commits after fan-out."""
    current = load_reading_state(conn, event.document_id) or empty_reading_state(
        event.document_id
    )
    _write_reading_state(conn, apply_event(current, event))


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
    """§18.5 recovery: recompute reading_state from the full event log
    and commit. Standalone (not called via the EventLog hook)."""
    events = log.events_for_document(document_id)
    state = build_reading_state(document_id, events)
    _write_reading_state(conn, state)
    conn.commit()
    return state


def _write_reading_state(conn: sqlite3.Connection, state: ReadingState) -> None:
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


# ─── chunk_ratings projection (Parsem-1na) ────────────────────────────


def apply_to_chunk_ratings(
    conn: sqlite3.Connection, event: ReadingEvent
) -> None:
    """UPSERT one rate_effort event into chunk_ratings. No-op for any
    other event type. Resolves the event's POSITION-keyed chunk_id to
    the chunks.id row; silently skips if no such chunk exists (drift
    guard). Does NOT commit."""
    rating = rate_effort_rating(event)
    if rating is None or event.chunk_id is None:
        return
    chunk_db_id = _resolve_chunk_id(conn, event.document_id, event.chunk_id)
    if chunk_db_id is None:
        return
    _write_chunk_rating(conn, chunk_db_id, rating)


def get_ratings_for_document(
    conn: sqlite3.Connection, document_id: int
) -> dict[int, int]:
    """Return the persisted ratings for a document as a position→rating
    dict (UI lives in position space; chunk_ratings is keyed on
    chunks.id, so we JOIN to translate). Empty dict if no ratings yet."""
    rows = conn.execute(
        "SELECT c.position, r.rating"
        " FROM chunk_ratings r"
        " JOIN chunks c ON c.id = r.chunk_id"
        " WHERE c.document_id=?",
        (document_id,),
    ).fetchall()
    return {row["position"]: row["rating"] for row in rows}


def rebuild_chunk_ratings(
    conn: sqlite3.Connection, document_id: int, log: EventLog
) -> dict[int, int]:
    """§18.5 recovery: rewrite chunk_ratings rows for one document
    from the full event log. Wipes the document's existing ratings
    first so a rebuild can never leave stale rows behind."""
    events = log.events_for_document(document_id)
    ratings = build_chunk_ratings(document_id, events)
    conn.execute(
        "DELETE FROM chunk_ratings"
        " WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)",
        (document_id,),
    )
    for position, rating in ratings.items():
        chunk_db_id = _resolve_chunk_id(conn, document_id, position)
        if chunk_db_id is None:
            continue
        _write_chunk_rating(conn, chunk_db_id, rating)
    conn.commit()
    return ratings


def _resolve_chunk_id(
    conn: sqlite3.Connection, document_id: int, position: int
) -> int | None:
    row = conn.execute(
        "SELECT id FROM chunks WHERE document_id=? AND position=?",
        (document_id, position),
    ).fetchone()
    return row["id"] if row is not None else None


def _write_chunk_rating(
    conn: sqlite3.Connection, chunk_db_id: int, rating: int
) -> None:
    conn.execute(
        "INSERT INTO chunk_ratings (chunk_id, rating, updated_at)"
        " VALUES (?, ?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET"
        "  rating=excluded.rating,"
        "  updated_at=excluded.updated_at",
        (chunk_db_id, rating, datetime.now(UTC).isoformat()),
    )


# ─── EventLog wiring ──────────────────────────────────────────────────


def make_event_log(conn: sqlite3.Connection) -> EventLog:
    """EventLog wired so every write fans out to all projection
    writers in a single transaction. Compose every projection here so
    a failure in any one rolls back ALL of them — including the event
    INSERT itself (via EventLog._append's rollback path)."""

    def _on_event(event: ReadingEvent) -> None:
        apply_to_reading_state(conn, event)
        apply_to_chunk_ratings(conn, event)
        conn.commit()

    return EventLog(conn, on_event=_on_event)
