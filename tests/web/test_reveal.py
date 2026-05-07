"""Tests for POST /reveal. Spec: parsem-spec.md §7.1, §12; bead Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState
from tests.conftest import T0


def test_reveal_advances_current_position(client: TestClient, state: ReaderState) -> None:
    assert state.current_position == 0
    response = client.post("/reveal")
    assert response.status_code == 200
    assert state.current_position == 1


def test_reveal_returns_partial_fragment_not_full_page(client: TestClient) -> None:
    response = client.post("/reveal")
    assert response.text.lstrip().startswith("<main")


def test_reveal_logs_event_with_chunk_id_and_clock_time(
    client: TestClient, state: ReaderState
) -> None:
    client.post("/reveal")
    events = state.event_log.events_for_document(state.document_id)
    reveals = [e for e in events if e.event_type == "reveal"]
    assert len(reveals) == 1
    assert reveals[0].chunk_id == 1
    assert reveals[0].created_at == T0


def test_reveal_updates_high_water_on_new_territory(client: TestClient, state: ReaderState) -> None:
    assert state.high_water_position == 0
    client.post("/reveal")
    assert state.high_water_position == 1


def _exhaust_bucket(client: TestClient, state: ReaderState) -> None:
    """Spend every token at the same instant — bucket reaches zero."""
    for _ in range(state.bucket_config.capacity):
        client.post("/reveal")


def test_reveal_when_bucket_empty_returns_countdown_fragment(
    client: TestClient, state: ReaderState
) -> None:
    _exhaust_bucket(client, state)
    response = client.post("/reveal")
    assert response.status_code == 200
    assert "Next reveal in" in response.text


def test_reveal_when_bucket_empty_does_not_advance(client: TestClient, state: ReaderState) -> None:
    _exhaust_bucket(client, state)
    pos_before = state.current_position
    client.post("/reveal")
    assert state.current_position == pos_before


def test_reveal_into_paid_territory_does_not_consume_token(
    client: TestClient, state: ReaderState
) -> None:
    _exhaust_bucket(client, state)  # high_water=3, paid_reveal_times has 3 entries
    state.current_position = 1  # simulate conceal back into paid territory
    paid_count_before = len(state.paid_reveal_times)
    response = client.post("/reveal")
    assert response.status_code == 200
    assert state.current_position == 2
    assert len(state.paid_reveal_times) == paid_count_before  # no token spent
