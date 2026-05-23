"""Tests for POST /free (Free Mode toggle). Bead Parsem-ci5.

Free Mode is the explicit escape hatch from paced reading:
  * the whole document renders settled (every chunk visible)
  * the bucket valve is suspended (Space advances without a token, never
    moves high_water)
  * rate / pin / unrate stay blocked past the frontier (view-only)
  * GET /documents/{id}/reader always resets the flag to False
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState
from tests.web.conftest import exhaust_bucket


def test_free_toggle_sets_flag_on_state(client: TestClient, state: ReaderState) -> None:
    assert state.free_mode is False
    client.post("/free")
    assert state.free_mode is True


def test_free_toggle_is_idempotent_pair(client: TestClient, state: ReaderState) -> None:
    client.post("/free")
    client.post("/free")
    assert state.free_mode is False


def test_free_response_renders_every_chunk(client: TestClient, state: ReaderState) -> None:
    # Paced: only the first chunk is visible (current = high_water = 0)
    paced = client.get(f"/documents/{state.document_id}/reader")
    paced_chunks = paced.text.count('class="chunk ')
    response = client.post("/free")
    free_chunks = response.text.count('class="chunk ')
    assert paced_chunks == 1
    assert free_chunks == len(state.chunks)


def test_free_mode_marker_attribute_flips(client: TestClient) -> None:
    """The JS dispatcher dims the bucket dots via [data-free-mode="true"];
    the attribute must travel in the partial fragment so the client-side
    apply syncs the row CSS without a full reload."""
    response_off = client.post("/free")
    assert response_off  # ON
    assert 'data-free-mode="true"' in response_off.text
    response_on = client.post("/free")
    assert 'data-free-mode="false"' in response_on.text


def test_free_badge_always_renders_visibility_via_data_attribute(client: TestClient) -> None:
    """Badge element is always in the DOM so the top-bar grid slot is
    reserved — toggling F never restructures the surrounding columns.
    Visibility is gated by [data-free-mode="false"] in CSS, asserted via
    the data attribute (see test_free_mode_marker_attribute_flips)."""
    paced = client.post("/conceal")  # cheap re-render with free_mode=False
    assert "free-badge" in paced.text
    assert 'data-free-mode="false"' in paced.text
    free = client.post("/free")
    assert "free-badge" in free.text
    assert 'data-free-mode="true"' in free.text


def test_free_returns_partial_fragment_not_full_page(client: TestClient) -> None:
    response = client.post("/free")
    assert response.text.lstrip().startswith("<main")


def test_reveal_in_free_mode_does_not_consume_token(client: TestClient, state: ReaderState) -> None:
    """Bucket valve is suspended in Free Mode. Even after exhausting
    every token in paced mode, a Free-Mode Space still advances —
    that's the whole point of the escape hatch."""
    exhaust_bucket(client, state)
    paid_before = len(state.paid_reveal_times)
    client.post("/free")
    pos_before = state.current_position
    response = client.post("/reveal")
    assert response.status_code == 200
    assert state.current_position == pos_before + 1
    assert len(state.paid_reveal_times) == paid_before


def test_reveal_in_free_mode_does_not_advance_high_water(
    client: TestClient, state: ReaderState
) -> None:
    client.post("/free")
    hw_before = state.high_water_position
    client.post("/reveal")
    assert state.high_water_position == hw_before


def test_reveal_in_free_mode_logs_no_reveal_event(client: TestClient, state: ReaderState) -> None:
    """Free Mode is browse, not reading; the event log stays quiet so
    projection rebuilds don't confuse skim-through with paced reading."""
    client.post("/free")
    client.post("/reveal")
    reveals = [
        e
        for e in state.event_log.events_for_document(state.document_id)
        if e.event_type == "reveal"
    ]
    assert reveals == []


def test_reveal_in_free_mode_outcome_header_is_advanced_free(client: TestClient) -> None:
    client.post("/free")
    response = client.post("/reveal")
    assert response.headers["X-Reveal-Outcome"] == "advanced_free"


def test_reveal_in_free_mode_at_end_emits_end_of_document(
    client: TestClient, state: ReaderState
) -> None:
    client.post("/free")
    state.current_position = len(state.chunks) - 1
    response = client.post("/reveal")
    assert response.headers["X-Reveal-Outcome"] == "end_of_document"
    assert state.current_position == len(state.chunks) - 1


def test_rate_past_frontier_in_free_mode_is_rejected(
    client: TestClient, state: ReaderState
) -> None:
    """'View-only past frontier': Free-Mode Space can park current past
    high_water, but committing a rating there violates the rule that
    ratings reflect reading. Server rejects with 422 so the JS short-
    circuits silently."""
    client.post("/free")
    state.current_position = state.high_water_position + 2
    response = client.post("/rate", json={"rating": 3})
    assert response.status_code == 422
    assert state.current_position not in state.chunk_ratings


def test_pin_past_frontier_in_free_mode_is_rejected(client: TestClient, state: ReaderState) -> None:
    client.post("/free")
    state.current_position = state.high_water_position + 1
    response = client.post("/pin")
    assert response.status_code == 422
    assert state.current_position not in state.pin_colors


def test_unrate_past_frontier_in_free_mode_is_rejected(
    client: TestClient, state: ReaderState
) -> None:
    client.post("/free")
    state.current_position = state.high_water_position + 1
    response = client.post("/unrate")
    assert response.status_code == 422


def test_rate_at_frontier_in_free_mode_still_works(client: TestClient, state: ReaderState) -> None:
    """The view-only gate is past high_water — at or before is fully
    interactive even with Free Mode on."""
    client.post("/free")
    assert state.current_position <= state.high_water_position
    response = client.post("/rate", json={"rating": 4})
    assert response.status_code == 200
    assert state.chunk_ratings[state.current_position] == 4


def test_toggle_off_clamps_current_back_to_high_water(
    client: TestClient, state: ReaderState
) -> None:
    """Free Mode lets current drift past high_water via Space. On exit,
    the user resumes paced reading at the frontier rather than past it —
    otherwise the spine they were reading vanishes from view."""
    client.post("/free")
    state.current_position = state.high_water_position + 3
    client.post("/free")  # OFF
    assert state.current_position == state.high_water_position


def test_get_reader_resets_free_mode(client: TestClient, state: ReaderState) -> None:
    """Page reload returns to paced — every GET sets the flag back to
    False, regardless of in-memory state. Free Mode never sticks."""
    client.post("/free")
    assert state.free_mode is True
    client.get(f"/documents/{state.document_id}/reader")
    assert state.free_mode is False
