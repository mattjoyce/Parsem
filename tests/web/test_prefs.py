"""Tests for the "Aa" presentation layer (claude-rdk, spec §15.3).

Contract-level: the no-FOUC bootstrap and the appearance modal must be
present in the full-page renders (reader + library), wired from the
server's config defaults; the #reader-main partial render must carry
the "Aa" trigger but NOT the page-level panel/bootstrap.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_prefs_js_is_served(client: TestClient) -> None:
    response = client.get("/static/prefs.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "parsem_prefs" in response.text


def test_library_page_carries_bootstrap_and_panel(client: TestClient) -> None:
    html = client.get("/library?segment=all").text
    # No-FOUC bootstrap: reads the localStorage key, sets the html attrs.
    assert "parsem_prefs" in html
    assert "data-theme" in html
    assert "--prose-font" in html
    # The appearance modal + its trigger.
    assert 'id="prefs-overlay"' in html
    assert "Appearance" in html
    assert "prefs-open" in html
    # Font picker rendered from config defaults.
    assert "Charter" in html
    # Page wires prefs.js.
    assert "/static/prefs.js" in html


def test_reader_page_carries_bootstrap_and_panel(client: TestClient) -> None:
    html = client.get("/documents/1/reader").text
    assert "parsem_prefs" in html
    assert 'id="prefs-overlay"' in html
    assert "Appearance" in html
    assert "prefs-open" in html
    assert "/static/prefs.js" in html


def test_reader_partial_has_trigger_but_not_panel(client: TestClient) -> None:
    # POST /reveal returns the #reader-main partial — it must include the
    # top-bar "Aa" trigger but not the page-level overlay/bootstrap
    # (those live in reader.html and survive the swap).
    html = client.post("/reveal").text
    assert "prefs-open" in html
    assert 'id="prefs-overlay"' not in html
    assert "parsem_prefs" not in html
