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
