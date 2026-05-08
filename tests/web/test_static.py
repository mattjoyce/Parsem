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
    """`]`, `[`, `'`, `,`, `:`, `?` are deferred to future beads (no backend)."""
    for deferred_key in ("]", "[", "'"):
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


def test_reader_js_initial_settle_via_request_animation_frame(reader_js_source: str) -> None:
    assert "requestAnimationFrame" in reader_js_source


def test_reader_js_settles_on_window_resize(reader_js_source: str) -> None:
    assert "resize" in reader_js_source
