"""Tests for POST /jump-to-pin and POST /return. Spec: parsem-spec.md §13.4;
bead Parsem-1pg."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_jump_to_pin_with_no_pins_is_noop(client: TestClient, state: ReaderState) -> None:
    pos_before = state.current_position
    response = client.post("/jump-to-pin", json={"direction": "next"})
    assert response.status_code == 200
    assert state.current_position == pos_before
    assert state.pre_jump_position is None
    assert "X-Reveal-Outcome" not in response.headers


def test_jump_to_pin_next_moves_to_next_pin(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {2: 1, 5: 1}
    state.last_active_pin_color = 1
    state.current_position = 0
    client.post("/jump-to-pin", json={"direction": "next"})
    assert state.current_position == 2


def test_jump_to_pin_prev_moves_to_previous_pin(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {2: 1, 5: 1}
    state.last_active_pin_color = 1
    state.current_position = 6
    client.post("/jump-to-pin", json={"direction": "prev"})
    assert state.current_position == 5


def test_jump_to_pin_default_color_mode_is_any(
    client: TestClient, state: ReaderState
) -> None:
    """`]` / `[` (color_mode='any', the default) ignore last_active_pin_color
    and visit every pin regardless of colour. Spec §8 keyboard table."""
    state.pin_colors = {2: 1, 5: 2, 8: 1}
    state.last_active_pin_color = 2
    state.current_position = 0
    client.post("/jump-to-pin", json={"direction": "next"})
    assert state.current_position == 2  # nearest pin of any colour


def test_jump_to_pin_with_no_active_color_uses_any(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {3: 1, 5: 2}
    state.last_active_pin_color = None
    state.current_position = 0
    client.post("/jump-to-pin", json={"direction": "next"})
    assert state.current_position == 3  # nearest of any colour


def test_jump_to_pin_same_as_current_filters_to_current_chunks_color(
    client: TestClient, state: ReaderState
) -> None:
    """`}` / `{` (color_mode='same_as_current') visit only pins matching
    the colour of the current chunk's own pin. Spec §8."""
    state.pin_colors = {2: 1, 4: 1, 5: 2, 8: 1}
    state.current_position = 4  # current chunk pinned colour 1
    state.last_active_pin_color = 2  # ignored under same_as_current
    client.post(
        "/jump-to-pin",
        json={"direction": "next", "color_mode": "same_as_current"},
    )
    assert state.current_position == 8  # next colour-1 pin, skipping the colour-2 pin at 5


def test_jump_to_pin_same_as_current_is_noop_when_current_unpinned(
    client: TestClient, state: ReaderState
) -> None:
    """When the current chunk has no pin, `}` / `{` cannot derive a colour
    to filter on, so they are an explicit no-op. Spec §8."""
    state.pin_colors = {2: 1, 5: 2}
    state.current_position = 0  # not pinned
    state.last_active_pin_color = 1
    pos_before = state.current_position
    response = client.post(
        "/jump-to-pin",
        json={"direction": "next", "color_mode": "same_as_current"},
    )
    assert state.current_position == pos_before
    assert state.pre_jump_position is None
    assert "X-Reveal-Outcome" not in response.headers


def test_jump_to_pin_same_as_current_wraps_around_within_color(
    client: TestClient, state: ReaderState
) -> None:
    """Wrap-around inside a same-colour set: from past the last colour-1
    pin, `}` returns to the first colour-1 pin."""
    state.pin_colors = {2: 1, 5: 2, 8: 1}
    state.current_position = 8  # current chunk pinned colour 1, also last colour-1 pin
    client.post(
        "/jump-to-pin",
        json={"direction": "next", "color_mode": "same_as_current"},
    )
    assert state.current_position == 2  # wraps to first colour-1 pin


def test_jump_to_pin_next_wraps_around(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {2: 1, 5: 1}
    state.last_active_pin_color = 1
    state.current_position = 6
    client.post("/jump-to-pin", json={"direction": "next"})
    assert state.current_position == 2  # wraps from past-last to first


def test_jump_to_pin_prev_wraps_around(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {2: 1, 5: 1}
    state.last_active_pin_color = 1
    state.current_position = 1
    client.post("/jump-to-pin", json={"direction": "prev"})
    assert state.current_position == 5  # wraps from before-first to last


def test_jump_to_pin_only_pin_at_current_is_noop(
    client: TestClient, state: ReaderState
) -> None:
    state.current_position = 4
    state.pin_colors = {4: 1}
    state.last_active_pin_color = 1
    response = client.post("/jump-to-pin", json={"direction": "next"})
    assert state.current_position == 4
    assert state.pre_jump_position is None
    assert "X-Reveal-Outcome" not in response.headers


def test_jump_to_pin_captures_pre_jump_position(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors = {2: 1, 5: 1}
    state.last_active_pin_color = 1
    state.current_position = 7
    client.post("/jump-to-pin", json={"direction": "prev"})
    assert state.pre_jump_position == 7


def test_jump_to_pin_logs_no_event(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {3: 1}
    state.last_active_pin_color = 1
    pre_count = len(state.event_log.events_for_document(state.document_id))
    client.post("/jump-to-pin", json={"direction": "next"})
    post_count = len(state.event_log.events_for_document(state.document_id))
    assert pre_count == post_count


def test_jump_to_pin_sets_outcome_header_advanced_free(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors = {3: 1}
    state.last_active_pin_color = 1
    response = client.post("/jump-to-pin", json={"direction": "next"})
    assert response.headers.get("X-Reveal-Outcome") == "advanced_free"


def test_jump_to_pin_updates_last_active_color_on_landing(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors = {2: 3, 5: 3}
    state.last_active_pin_color = None
    state.current_position = 0
    client.post("/jump-to-pin", json={"direction": "next"})
    assert state.last_active_pin_color == 3


def test_jump_to_pin_returns_partial_fragment(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {3: 1}
    state.last_active_pin_color = 1
    response = client.post("/jump-to-pin", json={"direction": "next"})
    assert response.text.lstrip().startswith("<main")


def test_jump_to_pin_rejects_invalid_direction(client: TestClient) -> None:
    response = client.post("/jump-to-pin", json={"direction": "sideways"})
    assert response.status_code == 422


def test_pin_set_updates_last_active_color(client: TestClient, state: ReaderState) -> None:
    client.post("/pin")  # cycles to colour 1
    assert state.last_active_pin_color == 1
    client.post("/pin")  # cycles to colour 2
    assert state.last_active_pin_color == 2


def test_pin_clear_does_not_reset_last_active_color(
    client: TestClient, state: ReaderState
) -> None:
    for _ in range(5):
        client.post("/pin")  # → c5
    assert state.last_active_pin_color == 5
    client.post("/pin")  # cycle to none; engagement memory stays
    assert state.last_active_pin_color == 5


def test_return_restores_pre_jump_position(client: TestClient, state: ReaderState) -> None:
    state.pin_colors = {2: 1, 5: 1}
    state.last_active_pin_color = 1
    state.current_position = 0
    client.post("/jump-to-pin", json={"direction": "next"})
    assert state.current_position == 2  # jumped
    response = client.post("/return")
    assert response.status_code == 200
    assert state.current_position == 0
    assert state.pre_jump_position is None


def test_return_with_no_prejump_is_noop(client: TestClient, state: ReaderState) -> None:
    pos_before = state.current_position
    response = client.post("/return")
    assert response.status_code == 200
    assert state.current_position == pos_before
    assert "X-Reveal-Outcome" not in response.headers


def test_return_sets_outcome_header_advanced_free(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors = {3: 1}
    state.last_active_pin_color = 1
    state.current_position = 0
    client.post("/jump-to-pin", json={"direction": "next"})
    response = client.post("/return")
    assert response.headers.get("X-Reveal-Outcome") == "advanced_free"


def test_return_returns_partial_fragment(client: TestClient) -> None:
    response = client.post("/return")
    assert response.text.lstrip().startswith("<main")


def test_return_consumes_prejump_so_second_return_is_noop(
    client: TestClient, state: ReaderState
) -> None:
    state.pin_colors = {3: 1}
    state.last_active_pin_color = 1
    client.post("/jump-to-pin", json={"direction": "next"})
    client.post("/return")
    pos_after_first_return = state.current_position
    client.post("/return")  # nothing to restore
    assert state.current_position == pos_after_first_return
