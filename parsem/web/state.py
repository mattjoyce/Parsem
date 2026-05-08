"""ReaderState — the in-memory holder the FastAPI app reads and mutates.

Holds chunked document, event log, configuration, current/high-water
positions, and the per-session pin_colors hot-read cache (seeded from
`load_pins_for_document` on open by every ReaderState construction
site — Parsem-pv8). `last_active_pin_color` and `pre_jump_position`
are session-scoped and not persisted.

The clock is injected so route handlers can be tested with a pinned time
without monkey-patching.

`build_reader_state_for_document` is the canonical factory — it loads
chunks/sections/projections/pins for one document and assembles a
fresh ReaderState. Used by `parsem.cli.build_app` on startup and by
`GET /documents/{id}/reader` when switching documents.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from parsem.domain.bucket import BucketConfig
from parsem.domain.chunking import Chunk, Section
from parsem.store.documents import (
    load_chunks_for_document,
    load_document,
    load_sections_for_document,
)
from parsem.store.events import EventLog
from parsem.store.projections_cache import (
    initial_reader_positions,
    load_pins_for_document,
    make_event_log,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ReaderState:
    chunks: list[Chunk]
    sections: list[Section]
    event_log: EventLog
    bucket_config: BucketConfig
    pin_colors: dict[int, int]  # caller seeds via load_pins_for_document
    document_id: int = 1
    current_position: int = 0
    high_water_position: int = 0
    paid_reveal_times: list[datetime] = field(default_factory=list)
    last_active_pin_color: int | None = None
    pre_jump_position: int | None = None
    clock: Callable[[], datetime] = field(default=_utcnow)


def empty_reader_state(conn: sqlite3.Connection) -> ReaderState:
    """Placeholder ReaderState for an app instance with no document
    open yet (e.g. before first GET /documents/{id}/reader). The
    sentinel `document_id=-1` ensures the next doc-open visit always
    triggers a rebuild from DB."""
    return ReaderState(
        chunks=[],
        sections=[],
        event_log=EventLog(conn),
        bucket_config=BucketConfig(),
        pin_colors={},
        document_id=-1,
    )


def build_reader_state_for_document(
    conn: sqlite3.Connection, document_id: int, *, warm_chunks: int
) -> ReaderState | None:
    """Load a document's chunks/sections/projections/pins from the DB
    and assemble a fresh ReaderState. Returns None if the document
    does not exist — caller raises 404."""
    if load_document(conn, document_id) is None:
        return None
    chunks = load_chunks_for_document(conn, document_id)
    sections = load_sections_for_document(conn, document_id)
    current, high_water = initial_reader_positions(
        conn, document_id, warm_chunks=warm_chunks
    )
    return ReaderState(
        chunks=chunks,
        sections=sections,
        event_log=make_event_log(conn),
        bucket_config=BucketConfig(),
        pin_colors=load_pins_for_document(conn, document_id),
        document_id=document_id,
        current_position=current,
        high_water_position=high_water,
    )
