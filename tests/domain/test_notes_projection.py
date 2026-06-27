"""Pure note-projection tests (notes-export). Mirrors the pin/rating
projection tests in test_projections.py."""

from __future__ import annotations

from parsem.domain.projections import apply_note_event, build_notes
from parsem.store.events import ReadingEvent
from tests.conftest import T0


def _event(
    event_type: str, chunk_id: int, payload: object, *, doc: int = 1, eid: int = 1
) -> ReadingEvent:
    return ReadingEvent(
        id=eid,
        document_id=doc,
        event_type=event_type,  # type: ignore[arg-type]
        chunk_id=chunk_id,
        payload=payload,  # type: ignore[arg-type]
        created_at=T0,
    )


def test_apply_note_set_adds_note() -> None:
    notes = apply_note_event({}, _event("note_set", 2, {"note": "hi"}))
    assert notes == {2: "hi"}


def test_apply_note_set_overwrites() -> None:
    notes = apply_note_event({2: "old"}, _event("note_set", 2, {"note": "new"}))
    assert notes == {2: "new"}


def test_apply_note_clear_removes() -> None:
    notes = apply_note_event({2: "hi"}, _event("note_clear", 2, None))
    assert notes == {}


def test_apply_note_clear_on_absent_is_noop() -> None:
    before = {3: "hi"}
    after = apply_note_event(before, _event("note_clear", 2, None))
    assert after == before


def test_other_event_types_are_noop() -> None:
    notes = apply_note_event({}, _event("rate_effort", 2, {"rating": 4}))
    assert notes == {}


def test_build_notes_folds_and_filters_by_document() -> None:
    events = [
        _event("note_set", 0, {"note": "a"}, doc=1, eid=1),
        _event("note_set", 1, {"note": "b"}, doc=2, eid=2),
        _event("note_set", 0, {"note": "a2"}, doc=1, eid=3),
        _event("note_clear", 1, None, doc=1, eid=4),
    ]
    assert build_notes(1, events) == {0: "a2"}
    assert build_notes(2, events) == {1: "b"}
