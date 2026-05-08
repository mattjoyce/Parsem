"""ReaderState — the in-memory holder the FastAPI app reads and mutates.

Holds chunked document, event log, configuration, current/high-water
positions, and the per-session pin_colors hot-read cache (seeded from
`load_pins_for_document` on open by every ReaderState construction
site — Parsem-pv8). `last_active_pin_color` and `pre_jump_position`
are session-scoped and not persisted.

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
    pin_colors: dict[int, int]  # caller seeds via load_pins_for_document
    document_id: int = 1
    current_position: int = 0
    high_water_position: int = 0
    paid_reveal_times: list[datetime] = field(default_factory=list)
    last_active_pin_color: int | None = None
    pre_jump_position: int | None = None
    clock: Callable[[], datetime] = field(default=_utcnow)
