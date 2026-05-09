"""Tests for POST /rate. Spec: parsem-spec.md §7.4, §14; bead Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_rate_within_bounds_logs_rate_effort_event(client: TestClient, state: ReaderState) -> None:
    response = client.post("/rate", json={"rating": 4})
    assert response.status_code == 200
    events = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "rate_effort"
    ]
    assert len(events) == 1
    assert events[0].chunk_id == state.current_position
    assert events[0].payload == {"rating": 4}


def test_rate_at_lower_bound_one_is_accepted(client: TestClient, state: ReaderState) -> None:
    response = client.post("/rate", json={"rating": 1})
    assert response.status_code == 200


def test_rate_at_upper_bound_five_is_accepted(client: TestClient, state: ReaderState) -> None:
    response = client.post("/rate", json={"rating": 5})
    assert response.status_code == 200


def test_rate_below_one_is_rejected(client: TestClient, state: ReaderState) -> None:
    response = client.post("/rate", json={"rating": 0})
    assert response.status_code >= 400


def test_rate_above_five_is_rejected(client: TestClient, state: ReaderState) -> None:
    response = client.post("/rate", json={"rating": 6})
    assert response.status_code >= 400


def test_rate_does_not_advance_position(client: TestClient, state: ReaderState) -> None:
    pos_before = state.current_position
    client.post("/rate", json={"rating": 3})
    assert state.current_position == pos_before


def test_rate_returns_partial_fragment_not_full_page(client: TestClient) -> None:
    response = client.post("/rate", json={"rating": 3})
    assert response.text.lstrip().startswith("<main")


def test_rate_updates_state_chunk_ratings(
    client: TestClient, state: ReaderState
) -> None:
    """The dot-toggle UI relies on `state.chunk_ratings` being live —
    the next render shows the active dot. The /rate route must keep
    state in sync with the event log (claude-axx.3)."""
    client.post("/rate", json={"rating": 4})
    assert state.chunk_ratings[state.current_position] == 4


def test_unrate_clears_an_existing_rating(
    client: TestClient, state: ReaderState
) -> None:
    """Click on the filled rating dot routes here. Logs a rate_clear
    event AND removes the entry from state.chunk_ratings."""
    client.post("/rate", json={"rating": 4})
    response = client.post("/unrate")
    assert response.status_code == 200
    assert state.current_position not in state.chunk_ratings


def test_unrate_logs_rate_clear_event(
    client: TestClient, state: ReaderState
) -> None:
    client.post("/rate", json={"rating": 4})
    client.post("/unrate")
    events = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "rate_clear"
    ]
    assert len(events) == 1
    assert events[0].chunk_id == state.current_position


def test_unrate_when_unrated_is_silent_noop(
    client: TestClient, state: ReaderState
) -> None:
    """Defends against a stale-DOM click: if the dot looked active to
    JS but the chunk had no rating server-side, /unrate must NOT log
    a spurious rate_clear event."""
    response = client.post("/unrate")
    assert response.status_code == 200
    events = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "rate_clear"
    ]
    assert events == []


def test_unrate_returns_partial_fragment(client: TestClient) -> None:
    response = client.post("/unrate")
    assert response.text.lstrip().startswith("<main")
