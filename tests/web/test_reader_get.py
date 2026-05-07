"""Tests for GET /reader. Spec: parsem-spec.md §22, beads Parsem-wym."""

from __future__ import annotations

from fastapi.testclient import TestClient

from parsem.web.state import ReaderState


def test_get_reader_returns_200_html(client: TestClient) -> None:
    response = client.get("/reader")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_get_reader_renders_first_chunk_text(client: TestClient, state: ReaderState) -> None:
    response = client.get("/reader")
    assert state.chunks[0].text.strip().split("\n")[0] in response.text
