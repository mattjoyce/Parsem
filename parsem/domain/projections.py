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

from parsem.store.events import ReadingEvent, pin_set_color, rate_effort_rating


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
    no document identity. Events from other documents are filtered
    out so the function is safe to call with a mixed-document event
    stream — symmetry with `build_chunk_ratings` and `build_pins`."""
    scoped = [e for e in events if e.document_id == document_id]
    return reduce(apply_event, scoped, empty_reading_state(document_id))


def resume_position(state: ReadingState, warm_chunks: int) -> int:
    """Spec §25.2 / line 209: reopen at ``high_water - warm_chunks``,
    clamped at 0. The N warm chunks are paid territory — re-reading
    them is free."""
    return max(0, state.high_water_position - warm_chunks)


# ─── chunk_ratings projection (Parsem-1na) ────────────────────────────


def apply_rating_event(
    ratings: dict[int, int], event: ReadingEvent
) -> dict[int, int]:
    """Latest-wins fold of one event into a position→rating dict.

    Ignores any event type that isn't `rate_effort`. Event ids are
    monotonic via AUTOINCREMENT, so plain dict overwrite gives the
    latest-rating-wins semantics §21 requires.
    """
    rating = rate_effort_rating(event)
    if rating is None or event.chunk_id is None:
        return ratings
    return {**ratings, event.chunk_id: rating}


def build_chunk_ratings(
    document_id: int, events: list[ReadingEvent]
) -> dict[int, int]:
    """Project rate_effort events into a position-keyed ratings dict
    for one document. Events from other documents are filtered out."""
    scoped = [e for e in events if e.document_id == document_id]
    return reduce(apply_rating_event, scoped, {})


# ─── pins projection (Parsem-pv8) ─────────────────────────────────────


def apply_pin_event(
    pins: dict[int, int], event: ReadingEvent
) -> dict[int, int]:
    """Fold one pin event into a position→color_id dict. `pin_set`
    sets/overwrites; `pin_clear` removes; everything else is a no-op."""
    if event.chunk_id is None:
        return pins
    if event.event_type == "pin_clear":
        next_pins = dict(pins)
        next_pins.pop(event.chunk_id, None)
        return next_pins
    color = pin_set_color(event)
    if color is None:
        return pins
    return {**pins, event.chunk_id: color}


def build_pins(
    document_id: int, events: list[ReadingEvent]
) -> dict[int, int]:
    """Project pin_set/pin_clear events into a position→color_id dict
    for one document. Events from other documents are filtered out."""
    scoped = [e for e in events if e.document_id == document_id]
    return reduce(apply_pin_event, scoped, {})
