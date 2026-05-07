"""Tests for POST /conceal. Spec: parsem-spec.md §7.2; bead Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_conceal_retreats_one_chunk(client: TestClient, state: ReaderState) -> None:
    state.current_position = 3
    state.high_water_position = 3
    response = client.post("/conceal")
    assert response.status_code == 200
    assert state.current_position == 2


def test_conceal_at_zero_stays_at_zero(client: TestClient, state: ReaderState) -> None:
    assert state.current_position == 0
    client.post("/conceal")
    assert state.current_position == 0


def test_conceal_logs_event_with_chunk_id(client: TestClient, state: ReaderState) -> None:
    state.current_position = 5
    state.high_water_position = 5
    client.post("/conceal")
    events = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "conceal"
    ]
    assert len(events) == 1
    assert events[0].chunk_id == 4
