"""Tests for POST /pin. Spec: parsem-spec.md §7.3, §13; bead Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_pin_first_press_sets_color_one(client: TestClient, state: ReaderState) -> None:
    response = client.post("/pin")
    assert response.status_code == 200
    assert state.pin_colors[state.current_position] == 1


def test_pin_cycles_through_all_five_colours_then_back_to_none(
    client: TestClient, state: ReaderState
) -> None:
    chunk_pos = state.current_position
    for expected in (1, 2, 3, 4, 5):
        client.post("/pin")
        assert state.pin_colors.get(chunk_pos) == expected
    client.post("/pin")  # 6th press wraps to none
    assert chunk_pos not in state.pin_colors


def test_pin_logs_pin_set_on_entry_to_colour(client: TestClient, state: ReaderState) -> None:
    client.post("/pin")  # → c1
    events = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "pin_set"
    ]
    assert len(events) == 1
    assert events[0].payload == {"color_id": 1}
    assert events[0].chunk_id == state.current_position


def test_pin_logs_pin_clear_on_cycle_to_none(client: TestClient, state: ReaderState) -> None:
    for _ in range(6):
        client.post("/pin")  # cycle to none
    events = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "pin_clear"
    ]
    assert len(events) == 1


def test_pin_returns_partial_fragment_not_full_page(client: TestClient) -> None:
    response = client.post("/pin")
    assert response.text.lstrip().startswith("<main")
