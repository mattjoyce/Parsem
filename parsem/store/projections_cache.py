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
    build_pins,
    build_reading_state,
    empty_reading_state,
    resume_position,
)
from parsem.domain.reanchor import best_chunk_by_jaccard
from parsem.store.events import (
    EventLog,
    ReadingEvent,
    pin_set_color,
    rate_effort_rating,
)

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
    """Persist one rate_effort or rate_clear event into chunk_ratings.
    Resolves the event's POSITION-keyed chunk_id to the chunks.id row;
    silently skips if no such chunk exists (drift guard). Does NOT
    commit."""
    if event.chunk_id is None:
        return
    if event.event_type == "rate_clear":
        chunk_db_id = _resolve_chunk_id(conn, event.document_id, event.chunk_id)
        if chunk_db_id is None:
            return
        conn.execute(
            "DELETE FROM chunk_ratings WHERE chunk_id=?", (chunk_db_id,)
        )
        return
    rating = rate_effort_rating(event)
    if rating is None:
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


# ─── pins projection (Parsem-pv8) ─────────────────────────────────────


def apply_to_pins(conn: sqlite3.Connection, event: ReadingEvent) -> None:
    """Persist one pin_set or pin_clear event into the `pins` table.
    Phase 2 chunk-level: enforces "at most one chunk-level pin per
    chunk" via DELETE-then-INSERT (table has no schema-level uniqueness
    on the chunk-level slot). Resolves position → chunks.id; silently
    skips on resolution miss (drift guard). Does NOT commit.

    Validates payload BEFORE the DELETE so a malformed `pin_set`
    cannot silently empty an existing slot — keeps the cache aligned
    with `apply_pin_event`'s no-op-on-missing-color contract."""
    if event.event_type == "pin_clear":
        color: int | None = None
    elif event.event_type == "pin_set":
        color = pin_set_color(event)
        if color is None:
            return
    else:
        return
    if event.chunk_id is None:
        return
    chunk_db_id = _resolve_chunk_id(conn, event.document_id, event.chunk_id)
    if chunk_db_id is None:
        return
    _delete_chunk_level_pin(conn, event.document_id, chunk_db_id)
    if color is not None:
        _insert_chunk_level_pin(
            conn, event.document_id, chunk_db_id, color, event.created_at
        )


def load_pins_for_document(
    conn: sqlite3.Connection, document_id: int
) -> dict[int, int]:
    """Return the persisted chunk-level pins as a position→color_id
    dict (UI lives in position space; pins.chunk_id_start FKs to
    chunks.id). Phase 2 only loads chunk-level rows; word-level
    selection is post-MVP."""
    rows = conn.execute(
        "SELECT c.position, p.color_id"
        " FROM pins p JOIN chunks c ON c.id = p.chunk_id_start"
        " WHERE p.document_id=? AND p.word_start=0 AND p.word_end=-1",
        (document_id,),
    ).fetchall()
    return {row["position"]: row["color_id"] for row in rows}


def rebuild_pins(
    conn: sqlite3.Connection, document_id: int, log: EventLog
) -> dict[int, int]:
    """§18.5 recovery: rewrite chunk-level pin rows for one document
    from the full event log. Wipes existing chunk-level rows first so
    a rebuild can never leave stale pins behind."""
    events = log.events_for_document(document_id)
    pins = build_pins(document_id, events)
    conn.execute(
        "DELETE FROM pins"
        " WHERE document_id=? AND word_start=0 AND word_end=-1",
        (document_id,),
    )
    for position, color in pins.items():
        chunk_db_id = _resolve_chunk_id(conn, document_id, position)
        if chunk_db_id is None:
            continue
        _insert_chunk_level_pin(
            conn, document_id, chunk_db_id, color, datetime.now(UTC)
        )
    conn.commit()
    return pins


def _delete_chunk_level_pin(
    conn: sqlite3.Connection, document_id: int, chunk_db_id: int
) -> None:
    conn.execute(
        "DELETE FROM pins"
        " WHERE document_id=? AND chunk_id_start=? AND chunk_id_end=?"
        "   AND word_start=0 AND word_end=-1",
        (document_id, chunk_db_id, chunk_db_id),
    )


def _insert_chunk_level_pin(
    conn: sqlite3.Connection,
    document_id: int,
    chunk_db_id: int,
    color: int,
    created_at: datetime,
) -> None:
    conn.execute(
        "INSERT INTO pins"
        " (document_id, chunk_id_start, word_start,"
        "  chunk_id_end, word_end, color_id, created_at)"
        " VALUES (?, ?, 0, ?, -1, ?, ?)",
        (document_id, chunk_db_id, chunk_db_id, color, created_at.isoformat()),
    )


# ─── Reading-state re-anchor on new chunking_run (claude-jtu) ─────────


def get_chunk_piece_hashes_for_document(
    conn: sqlite3.Connection, document_id: int
) -> list[frozenset[str]]:
    """Return each chunk's piece text-hash set, in position order, for
    the LATEST chunking_run of `document_id`. Used by re-anchor logic
    on re-chunk to map old positions to new positions (claude-jtu).

    Returns an empty list when the document has no chunking_run yet
    (fresh upload that hasn't been parsed). The list is dense — index
    i is the chunk at position i. Empty inner sets are possible but
    unusual (a chunk with no pieces is malformed)."""
    run_row = conn.execute(
        "SELECT cr.id FROM chunking_runs cr"
        " JOIN document_revisions dr ON dr.id = cr.revision_id"
        " WHERE dr.document_id = ? ORDER BY cr.id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if run_row is None:
        return []
    rows = conn.execute(
        "SELECT c.position, ap.text_hash"
        " FROM chunks c"
        " JOIN chunk_pieces cp ON cp.chunk_id = c.id"
        " JOIN atomic_pieces ap ON ap.id = cp.piece_id"
        " WHERE c.chunking_run_id = ?"
        " ORDER BY c.position",
        (run_row["id"],),
    ).fetchall()
    by_pos: dict[int, set[str]] = {}
    for row in rows:
        by_pos.setdefault(row["position"], set()).add(row["text_hash"])
    if not by_pos:
        return []
    max_pos = max(by_pos.keys())
    return [frozenset(by_pos.get(i, set())) for i in range(max_pos + 1)]


def reanchor_reading_state(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    old_chunks_piece_hashes: list[frozenset[str]],
    now: datetime,
) -> None:
    """Re-anchor `reading_state.current_position` and `high_water_position`
    after a new chunking_run for this document. Maps each old position
    onto the new run via Jaccard against piece-hash sets (claude-z99).

    Falls back to 0 when an old position has no anchor in the new run
    (content vanished or moved beyond recognition). Clamps current to
    high_water on order inversion — chunks normally stay in source
    order across re-chunks, but a defensive clamp protects against
    pathological strategy changes.

    No-op when no `reading_state` row exists for this document (fresh
    parse with no prior reading), or when the new run produced no
    chunks (caller should have caught that earlier; defensive).

    Caller commits — this function does NOT commit.
    """
    row = conn.execute(
        "SELECT current_position, high_water_position FROM reading_state"
        " WHERE document_id=?",
        (document_id,),
    ).fetchone()
    if row is None:
        return
    new_chunks_piece_hashes = get_chunk_piece_hashes_for_document(conn, document_id)
    if not new_chunks_piece_hashes:
        return

    def reanchor_position(old_pos: int) -> int:
        if old_pos < 0 or old_pos >= len(old_chunks_piece_hashes):
            return 0
        target = best_chunk_by_jaccard(
            old_chunks_piece_hashes[old_pos], new_chunks_piece_hashes
        )
        return target if target is not None else 0

    new_current = reanchor_position(row["current_position"])
    new_hw = reanchor_position(row["high_water_position"])
    # Defensive order clamp — chunks normally stay in source order
    # across re-chunks, but pathological strategy changes could invert.
    if new_current > new_hw:
        new_current = new_hw

    conn.execute(
        "UPDATE reading_state SET current_position=?, high_water_position=?,"
        " updated_at=? WHERE document_id=?",
        (new_current, new_hw, now.isoformat(), document_id),
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
        apply_to_pins(conn, event)
        conn.commit()

    return EventLog(conn, on_event=_on_event)
