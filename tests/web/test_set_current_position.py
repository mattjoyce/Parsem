"""Tests for POST /set-current-position. Spec: parsem-spec.md §8a;
beads claude-axx.3, claude-axx.4."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_set_current_position_moves_backward(
    client: TestClient, state: ReaderState
) -> None:
    state.high_water_position = 8
    state.current_position = 8
    response = client.post("/set-current-position", json={"position": 3})
    assert response.status_code == 200
    assert state.current_position == 3


def test_set_current_position_captures_pre_jump_when_null(
    client: TestClient, state: ReaderState
) -> None:
    """First back-click captures pre_jump_position so `'` / Esc / Space-
    resume can return to the keyboard-anchored frontier (§8a.3)."""
    state.high_water_position = 8
    state.current_position = 8
    state.pre_jump_position = None
    client.post("/set-current-position", json={"position": 3})
    assert state.pre_jump_position == 8


def test_set_current_position_does_not_overwrite_pre_jump(
    client: TestClient, state: ReaderState
) -> None:
    """Multiple back-clicks must not overwrite pre_jump — the spine is
    the original keyboard-anchored position, not the last review hop
    (§8a.3 'preserves the original spine across multiple back-clicks')."""
    state.high_water_position = 8
    state.current_position = 5
    state.pre_jump_position = 8  # captured by an earlier click
    client.post("/set-current-position", json={"position": 2})
    assert state.current_position == 2
    assert state.pre_jump_position == 8  # unchanged


def test_set_current_position_to_high_water_clears_pre_jump(
    client: TestClient, state: ReaderState
) -> None:
    """Resuming to the frontier (Space when behind) lands the reader
    back on the spine — there is no further `'`/Esc return that makes
    sense from the frontier, so pre_jump must clear."""
    state.high_water_position = 8
    state.current_position = 3
    state.pre_jump_position = 8
    client.post("/set-current-position", json={"position": 8})
    assert state.current_position == 8
    assert state.pre_jump_position is None


def test_set_current_position_past_high_water_returns_422(
    client: TestClient, state: ReaderState
) -> None:
    """Pointer never advances past the frontier (§8a.1). Even if the
    JS layer somehow sent a forward click, the server hard-rejects."""
    state.high_water_position = 5
    state.current_position = 5
    response = client.post("/set-current-position", json={"position": 6})
    assert response.status_code == 422
    assert state.current_position == 5


def test_set_current_position_negative_returns_422(
    client: TestClient, state: ReaderState
) -> None:
    response = client.post("/set-current-position", json={"position": -1})
    assert response.status_code == 422


def test_set_current_position_at_or_past_total_returns_422(
    client: TestClient, state: ReaderState
) -> None:
    """Out-of-range guard catches positions >= chunks_total even when
    high_water happens to permit them (it shouldn't, but defence in depth)."""
    response = client.post(
        "/set-current-position", json={"position": len(state.chunks)}
    )
    assert response.status_code == 422


def test_set_current_position_to_current_is_noop(
    client: TestClient, state: ReaderState
) -> None:
    state.high_water_position = 5
    state.current_position = 3
    state.pre_jump_position = 5
    response = client.post("/set-current-position", json={"position": 3})
    assert response.status_code == 200
    assert state.current_position == 3
    assert state.pre_jump_position == 5  # unchanged — no-op


def test_set_current_position_writes_no_event(
    client: TestClient, state: ReaderState
) -> None:
    """Pointer-only navigation does not write to the event log
    (§8a.3 'No event-log entries for pointer navigation'). Mirrors
    /jump-to-pin which also does not log."""
    state.high_water_position = 5
    state.current_position = 5
    events_before = len(state.event_log.events_for_document(state.document_id))
    client.post("/set-current-position", json={"position": 2})
    events_after = state.event_log.events_for_document(state.document_id)
    assert len(events_after) == events_before


def test_set_current_position_returns_partial_fragment(
    client: TestClient, state: ReaderState
) -> None:
    state.high_water_position = 4
    state.current_position = 4
    response = client.post("/set-current-position", json={"position": 1})
    assert response.text.lstrip().startswith("<main")


def test_set_current_position_marks_advanced_free_outcome(
    client: TestClient, state: ReaderState
) -> None:
    """Set-current-position is a free advance in the same outcome class
    as a `]` jump or a `'` return. The X-Reveal-Outcome header lets
    the JS layer skip the rejection-motion path."""
    state.high_water_position = 4
    state.current_position = 4
    response = client.post("/set-current-position", json={"position": 1})
    assert response.headers.get("X-Reveal-Outcome") == "advanced_free"


def test_set_current_position_noop_omits_outcome_header(
    client: TestClient, state: ReaderState
) -> None:
    """No-op responses must not advertise advanced_free — there was
    no advance. JS treats absence of the header as 'no motion'."""
    state.high_water_position = 4
    state.current_position = 2
    response = client.post("/set-current-position", json={"position": 2})
    assert "X-Reveal-Outcome" not in response.headers
