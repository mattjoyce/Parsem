"""ReaderState — the in-memory holder the FastAPI app reads and mutates.

Phase 1 placeholder for the Phase 2 SQLite-backed state. Holds chunked
document, event log, configuration, current/high-water positions, and
caches (`pin_colors`, `paid_reveal_times`) that Phase 2 will rebuild as
projections from the event log.

The clock is injected so route handlers can be tested with a pinned time
without monkey-patching.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from parsem.domain.bucket import BucketConfig
from parsem.domain.chunking import Chunk, Section
from parsem.store.events import EventLog


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ReaderState:
    chunks: list[Chunk]
    sections: list[Section]
    event_log: EventLog
    bucket_config: BucketConfig
    document_id: int = 1
    current_position: int = 0
    high_water_position: int = 0
    pin_colors: dict[int, int] = field(default_factory=dict)
    paid_reveal_times: list[datetime] = field(default_factory=list)
    clock: Callable[[], datetime] = field(default=_utcnow)
