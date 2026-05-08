"""Pure projection builders. Spec: parsem-spec.md §18.1, §18.5, §21, §25.2.

Projections are caches of the event log (§18.1). The pure functions in
this module are the source of truth for the projection logic; the
incremental cache in `parsem/store/projections_cache.py` and any future
batch rebuild both delegate to ``apply_event`` here so there is exactly
one place where event semantics live.

`chunk_id` on ReadingEvent is the chunk's POSITION within the document
(Phase 1 carryover; see parsem/store/events.py docstring), so the
projection works in position space.

This module imports nothing from `parsem.web` or `parsem.store` —
domain code never depends on transport or persistence (§18.1).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import reduce

from parsem.store.events import ReadingEvent


@dataclass(frozen=True)
class ReadingState:
    """Spec §21 reading_state row, lifted into a value type.

    `last_event_id_applied` is None for an empty event log; once any
    event has been folded in it is the id of the most-recently applied
    event, regardless of event type. This is the cursor §18.5 uses to
    detect projection drift (`< MAX(reading_events.id)`).
    """

    document_id: int
    high_water_position: int
    current_position: int
    last_event_id_applied: int | None


def empty_reading_state(document_id: int) -> ReadingState:
    """Zero-state for a fresh document — no events folded in yet."""
    return ReadingState(
        document_id=document_id,
        high_water_position=0,
        current_position=0,
        last_event_id_applied=None,
    )


def apply_event(state: ReadingState, event: ReadingEvent) -> ReadingState:
    """Fold one event into the running reading_state.

    Free re-reveals (chunk_id ≤ current high_water) MUST leave
    high_water untouched. `last_event_id_applied` advances on EVERY
    event regardless of type so §18.5 drift detection stays accurate.
    """
    new_high = state.high_water_position
    new_current = state.current_position
    if event.event_type == "reveal" and event.chunk_id is not None:
        new_high = max(new_high, event.chunk_id)
        new_current = event.chunk_id
    elif event.event_type == "conceal" and event.chunk_id is not None:
        new_current = event.chunk_id
    return replace(
        state,
        high_water_position=new_high,
        current_position=new_current,
        last_event_id_applied=event.id,
    )


def build_reading_state(
    document_id: int, events: list[ReadingEvent]
) -> ReadingState:
    """`document_id` is required because an empty event list carries
    no document identity."""
    return reduce(apply_event, events, empty_reading_state(document_id))


def resume_position(state: ReadingState, warm_chunks: int) -> int:
    """Spec §25.2 / line 209: reopen at ``high_water - warm_chunks``,
    clamped at 0. The N warm chunks are paid territory — re-reading
    them is free."""
    return max(0, state.high_water_position - warm_chunks)
