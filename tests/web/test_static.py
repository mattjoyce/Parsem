"""Tests for static file serving (Parsem-gx3)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_static_css_is_served(client: TestClient) -> None:
    response = client.get("/static/reader.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_static_js_is_served(client: TestClient) -> None:
    response = client.get("/static/reader.js")
    assert response.status_code == 200
    # Content-type for JS varies (text/javascript or application/javascript).
    assert "javascript" in response.headers["content-type"]


# The bead description IS the contract — these key→URL pairs must appear
# in reader.js. Testing the contract via grep is legitimate; testing the
# implementation (switch vs Map, regex shape, etc.) would not be.
EXPECTED_BINDINGS: dict[str, str] = {
    " ": "/reveal",
    "Backspace": "/conceal",
    "1": "/rate",
    "2": "/rate",
    "3": "/rate",
    "4": "/rate",
    "5": "/rate",
    "p": "/pin",
    "P": "/pin",
    "]": "/jump-to-pin",
    "[": "/jump-to-pin",
    "'": "/return",
}


def test_reader_js_binds_every_contract_key(reader_js_source: str) -> None:
    for key, url in EXPECTED_BINDINGS.items():
        assert f'"{key}"' in reader_js_source, f"missing key literal {key!r}"
        assert url in reader_js_source, f"missing url {url!r}"


def test_reader_js_binds_review_mode_toggle(reader_js_source: str) -> None:
    assert "ArrowUp" in reader_js_source
    assert "shiftKey" in reader_js_source
    assert "review-mode" in reader_js_source


def test_reader_js_binds_escape_to_exit_review_mode(reader_js_source: str) -> None:
    assert "Escape" in reader_js_source


def test_reader_js_does_not_wire_deferred_keys(reader_js_source: str) -> None:
    """`,`, `:`, `?` are deferred to future beads (no backend yet). Pin
    navigation keys (`]`, `[`, `'`) are now wired (Parsem-1pg)."""
    for deferred_key in (",", ":", "?"):
        assert f'"{deferred_key}":' not in reader_js_source, (
            f"unexpected handler for {deferred_key!r}"
        )


def test_reader_js_defines_settle_at_current(reader_js_source: str) -> None:
    assert "settleAtCurrent" in reader_js_source


def test_reader_js_uses_smooth_scroll(reader_js_source: str) -> None:
    assert 'behavior: "smooth"' in reader_js_source or "behavior:'smooth'" in reader_js_source


def test_reader_js_reads_outcome_header(reader_js_source: str) -> None:
    assert "X-Reveal-Outcome" in reader_js_source


def test_reader_js_implements_canonical_check(reader_js_source: str) -> None:
    assert "isAtCanonical" in reader_js_source


def test_reader_js_applies_rejecting_class(reader_js_source: str) -> None:
    assert "rejecting" in reader_js_source


def test_reader_js_binds_chunk_body_click(reader_js_source: str) -> None:
    """claude-axx.3 / spec §8a.2: chunk-body click POSTs
    /set-current-position with the clicked chunk's position. The
    handler must be wired in reader.js, not optimistic about being
    swapped in later."""
    assert "/set-current-position" in reader_js_source
    assert ".chunk" in reader_js_source


def test_reader_js_binds_rating_dot_click(reader_js_source: str) -> None:
    """claude-axx.3 / spec §8a.2 / §7.4: clicking a rating dot POSTs
    /rate (set) or /unrate (clear) based on the dot's data-active
    state. Pointer-mode peer of the 1-5 keypress."""
    assert "rating-dot" in reader_js_source
    assert "/rate" in reader_js_source
    assert "/unrate" in reader_js_source


def test_reader_js_handles_space_resume(reader_js_source: str) -> None:
    """claude-axx.3 / spec §8.2: Space when current < high_water
    must route to /set-current-position(high_water) instead of
    /reveal. The JS reads positions from the #reader-main data
    attrs the server stamps."""
    assert "highWaterPosition" in reader_js_source
    assert "spaceActionForState" in reader_js_source or "current < highWater" in reader_js_source


def test_reader_js_initial_settle_via_request_animation_frame(reader_js_source: str) -> None:
    assert "requestAnimationFrame" in reader_js_source


def test_reader_js_settles_on_window_resize(reader_js_source: str) -> None:
    assert "resize" in reader_js_source


# Parsem-bwz — pin navigation moved off the server to pure client-side scroll.


def test_reader_pins_js_is_served(client: TestClient) -> None:
    response = client.get("/static/reader_pins.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_reader_pins_css_is_served(client: TestClient) -> None:
    response = client.get("/static/reader_pins.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_reader_pins_js_handles_all_four_pin_keys(client: TestClient) -> None:
    src = client.get("/static/reader_pins.js").text
    for key in ("[", "]", "{", "}"):
        assert f'"{key}"' in src, f"reader_pins.js missing key literal {key!r}"


def test_reader_pins_js_uses_capture_phase(client: TestClient) -> None:
    """Capture-phase listener guarantees pin keys are handled before
    reader.js's bubble-phase listener fires (which would POST to the
    obsolete jump-to-pin route)."""
    src = client.get("/static/reader_pins.js").text
    assert "stopImmediatePropagation" in src
    # The third arg `true` to addEventListener (or `{ capture: true }`) flips
    # the listener to capture phase. Either form satisfies the contract.
    assert "true, // capture" in src or "{ capture: true }" in src


def test_reader_pins_js_does_not_post_to_server(client: TestClient) -> None:
    """The whole point of Parsem-bwz: jumps are pure scroll, no server
    round-trip. fetch / POST should not appear anywhere in this module."""
    src = client.get("/static/reader_pins.js").text
    assert "fetch(" not in src
    assert "/jump-to-pin" not in src
    assert "/return" not in src


def test_reader_pins_js_filters_by_color_for_curly_keys(client: TestClient) -> None:
    """{ and } take an extra `sameColorOnly` flag so the filter logic
    is testable from this contract grep."""
    src = client.get("/static/reader_pins.js").text
    assert "data-pin-color" in src
    assert "sameColorOnly" in src


def test_reader_html_links_pin_assets(client: TestClient, state) -> None:  # type: ignore[no-untyped-def]
    body = client.get(f"/documents/{state.document_id}/reader").text
    assert "/static/reader_pins.css" in body
    assert "/static/reader_pins.js" in body


def test_reader_pins_css_targets_current_chunk_dot(client: TestClient) -> None:
    src = client.get("/static/reader_pins.css").text
    assert ".chunk--current" in src
    assert ".pin-dot" in src
    assert "left:" in src
