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


def test_reader_js_binds_every_contract_key(client: TestClient) -> None:
    source = client.get("/static/reader.js").text
    for key, url in EXPECTED_BINDINGS.items():
        # Both the key literal AND the url must appear in the source.
        assert f'"{key}"' in source, f"missing key literal {key!r} in reader.js"
        assert url in source, f"missing url {url!r} in reader.js"


def test_reader_js_binds_review_mode_toggle(client: TestClient) -> None:
    source = client.get("/static/reader.js").text
    assert "ArrowUp" in source
    assert "shiftKey" in source
    assert "review-mode" in source


def test_reader_js_binds_escape_to_exit_review_mode(client: TestClient) -> None:
    source = client.get("/static/reader.js").text
    assert "Escape" in source


def test_reader_js_runs_countdown_interval(client: TestClient) -> None:
    source = client.get("/static/reader.js").text
    assert "setInterval" in source
    assert "data-seconds" in source


def test_reader_js_does_not_wire_deferred_keys(client: TestClient) -> None:
    """`]`, `[`, `'`, `,`, `:`, `?` are deferred to future beads (no backend)."""
    source = client.get("/static/reader.js").text
    for deferred_key in ("]", "[", "'"):
        # crude but matches the contract: those literals as quoted keys
        # should not appear as ACTIONS entries
        assert f'"{deferred_key}":' not in source, f"unexpected handler for {deferred_key!r}"
