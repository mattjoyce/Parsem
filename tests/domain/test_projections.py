"""Tests for parsem.domain.projections. Spec: §18.1, §18.5, §21, §25.2; bead Parsem-3jd."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

from parsem.domain.projections import (
    ReadingState,
    apply_event,
    apply_rating_event,
    build_chunk_ratings,
    build_reading_state,
    empty_reading_state,
    resume_position,
)
from parsem.store.events import ReadingEvent
from tests.conftest import T0


def _ev(
    *,
    id: int,
    event_type: str,
    chunk_id: int | None = None,
    document_id: int = 1,
    payload: object = None,
    offset_seconds: int = 0,
) -> ReadingEvent:
    return ReadingEvent(
        id=id,
        document_id=document_id,
        event_type=event_type,  # type: ignore[arg-type]
        chunk_id=chunk_id,
        payload=payload,  # type: ignore[arg-type]
        created_at=T0 + timedelta(seconds=offset_seconds),
    )


def test_empty_reading_state_has_zero_positions_and_no_event_cursor() -> None:
    state = empty_reading_state(document_id=42)
    assert state == ReadingState(
        document_id=42,
        high_water_position=0,
        current_position=0,
        last_event_id_applied=None,
    )


def test_build_reading_state_with_no_events_returns_zero_state() -> None:
    state = build_reading_state(document_id=42, events=[])
    assert state.document_id == 42
    assert state.high_water_position == 0
    assert state.current_position == 0
    assert state.last_event_id_applied is None


def test_single_reveal_raises_high_water() -> None:
    state = build_reading_state(document_id=1, events=[_ev(id=1, event_type="reveal", chunk_id=5)])
    assert state.high_water_position == 5


def test_single_reveal_sets_current_position() -> None:
    state = build_reading_state(document_id=1, events=[_ev(id=1, event_type="reveal", chunk_id=5)])
    assert state.current_position == 5


def test_interleaved_reveal_conceal_tracks_last_event_chunk_id() -> None:
    """Spec §21 current_position = position of last reveal/conceal."""
    events = [
        _ev(id=1, event_type="reveal", chunk_id=0),
        _ev(id=2, event_type="reveal", chunk_id=1),
        _ev(id=3, event_type="reveal", chunk_id=2),
        _ev(id=4, event_type="conceal", chunk_id=1),
    ]
    state = build_reading_state(document_id=1, events=events)
    assert state.current_position == 1


def test_free_re_reveal_does_not_raise_high_water() -> None:
    """A reveal at chunk_id ≤ existing high_water is a free re-read.
    high_water stays put; only current_position moves."""
    events = [
        _ev(id=1, event_type="reveal", chunk_id=0),
        _ev(id=2, event_type="reveal", chunk_id=1),
        _ev(id=3, event_type="reveal", chunk_id=2),  # high_water = 2
        _ev(id=4, event_type="conceal", chunk_id=1),
        _ev(id=5, event_type="conceal", chunk_id=0),
        _ev(id=6, event_type="reveal", chunk_id=1),  # free re-reveal
    ]
    state = build_reading_state(document_id=1, events=events)
    assert state.high_water_position == 2
    assert state.current_position == 1


def test_non_positional_events_do_not_change_positions() -> None:
    """rate_effort / pin_set / pin_clear / open_document / close_document
    advance only last_event_id_applied."""
    events = [
        _ev(id=1, event_type="reveal", chunk_id=3),
        _ev(id=2, event_type="rate_effort", chunk_id=3, payload={"rating": 4}),
        _ev(id=3, event_type="pin_set", chunk_id=3, payload={"color_id": 2}),
        _ev(id=4, event_type="pin_clear", chunk_id=3),
        _ev(id=5, event_type="open_document"),
        _ev(id=6, event_type="close_document"),
    ]
    state = build_reading_state(document_id=1, events=events)
    assert state.high_water_position == 3
    assert state.current_position == 3


def test_last_event_id_applied_advances_on_every_event() -> None:
    """§18.5 drift detection requires this — even non-positional events
    must move the cursor."""
    events = [
        _ev(id=1, event_type="reveal", chunk_id=0),
        _ev(id=2, event_type="rate_effort", chunk_id=0, payload={"rating": 3}),
        _ev(id=3, event_type="open_document"),
        _ev(id=4, event_type="pin_clear", chunk_id=0),
    ]
    state = build_reading_state(document_id=1, events=events)
    assert state.last_event_id_applied == 4


def test_apply_event_is_pure() -> None:
    """Folding does not mutate the input state."""
    seed = empty_reading_state(document_id=1)
    next_state = apply_event(seed, _ev(id=1, event_type="reveal", chunk_id=4))
    assert seed.high_water_position == 0
    assert next_state.high_water_position == 4


def test_resume_position_clamps_at_zero_when_high_water_below_warm_chunks() -> None:
    state = ReadingState(
        document_id=1, high_water_position=1, current_position=1, last_event_id_applied=10
    )
    assert resume_position(state, warm_chunks=2) == 0


def test_resume_position_subtracts_warm_chunks_when_safely_above() -> None:
    state = ReadingState(
        document_id=1, high_water_position=5, current_position=5, last_event_id_applied=10
    )
    assert resume_position(state, warm_chunks=2) == 3


def test_resume_position_with_warm_chunks_zero_returns_high_water() -> None:
    state = ReadingState(
        document_id=1, high_water_position=7, current_position=4, last_event_id_applied=10
    )
    assert resume_position(state, warm_chunks=0) == 7


# ─── chunk_ratings projection (Parsem-1na) ────────────────────────────


def test_build_chunk_ratings_with_no_events_returns_empty_dict() -> None:
    assert build_chunk_ratings(document_id=1, events=[]) == {}


def test_single_rate_effort_event_records_position_to_rating() -> None:
    events = [_ev(id=1, event_type="rate_effort", chunk_id=5, payload={"rating": 4})]
    assert build_chunk_ratings(document_id=1, events=events) == {5: 4}


def test_multiple_ratings_on_same_position_keeps_only_latest() -> None:
    events = [
        _ev(id=1, event_type="rate_effort", chunk_id=5, payload={"rating": 2}),
        _ev(id=2, event_type="rate_effort", chunk_id=5, payload={"rating": 5}),
        _ev(id=3, event_type="rate_effort", chunk_id=5, payload={"rating": 3}),
    ]
    assert build_chunk_ratings(document_id=1, events=events) == {5: 3}


def test_non_rate_effort_events_do_not_appear_in_ratings_dict() -> None:
    events = [
        _ev(id=1, event_type="reveal", chunk_id=5),
        _ev(id=2, event_type="conceal", chunk_id=4),
        _ev(id=3, event_type="pin_set", chunk_id=5, payload={"color_id": 2}),
        _ev(id=4, event_type="pin_clear", chunk_id=5),
        _ev(id=5, event_type="open_document"),
        _ev(id=6, event_type="close_document"),
    ]
    assert build_chunk_ratings(document_id=1, events=events) == {}


def test_events_from_other_documents_are_filtered_out() -> None:
    events = [
        _ev(id=1, document_id=1, event_type="rate_effort", chunk_id=5, payload={"rating": 4}),
        _ev(id=2, document_id=2, event_type="rate_effort", chunk_id=5, payload={"rating": 1}),
        _ev(id=3, document_id=1, event_type="rate_effort", chunk_id=7, payload={"rating": 5}),
    ]
    assert build_chunk_ratings(document_id=1, events=events) == {5: 4, 7: 5}


def test_apply_rating_event_is_pure() -> None:
    seed: dict[int, int] = {3: 2}
    next_state = apply_rating_event(
        seed, _ev(id=1, event_type="rate_effort", chunk_id=3, payload={"rating": 5})
    )
    assert seed == {3: 2}
    assert next_state == {3: 5}


def test_projections_module_does_not_import_from_web_or_store_internals() -> None:
    """Spec §18.1: domain doesn't depend on web. Only the event value
    types (`ReadingEvent` and its payload TypedDicts) are imported from
    store — the projection has to know what an event looks like."""
    tree = ast.parse(Path("parsem/domain/projections.py").read_text(encoding="utf-8"))
    allowed_from_store = {
        "ReadingEvent",
        "RateEffortPayload",
        "PinSetPayload",
        "rate_effort_rating",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("parsem.web"), (
                f"forbidden import: from {node.module}"
            )
            if node.module.startswith("parsem.store"):
                imported_names = {alias.name for alias in node.names}
                assert imported_names <= allowed_from_store, (
                    f"projections.py may only import event value types from "
                    f"parsem.store, got {imported_names}"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("parsem.web"), (
                    f"forbidden import: {alias.name}"
                )
