"""Tests for POST /documents/{id}/rename. Spec §22; beads Parsem-kwq,
Parsem-7wu.2.

The route returns just the tile partial (<article class="library-tile">)
so the JS inline-edit can outerHTML-swap one tile in place. Validation:
trim whitespace, then reject empty / >200 chars with 422; unknown id is
404. Markup migrated from row to tile in Parsem-7wu.2 (ADR 0005).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document, load_document
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def empty_app(tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection]]:
    conn = connect(":memory:")
    migrate(conn)
    app = create_app(empty_reader_state(conn), db=conn, originals_dir=tmp_path / "originals")
    with TestClient(app) as client:
        yield client, conn


def _seed(conn: sqlite3.Connection, *, title: str = "old name") -> int:
    return insert_document(
        conn,
        title=title,
        original_path="data/originals/x.md",
        status="ready",
        now=T0,
    )


def test_rename_persists_new_title(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed(conn)
    client.post(f"/documents/{doc_id}/rename", json={"title": "new name"})
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.title == "new name"


def test_rename_returns_partial_tile_fragment(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Response body must be the tile partial (an <article>), not the
    full library page — the v2 JS inline-edit relies on outerHTML-
    swapping one tile. ADR 0005, bd Parsem-7wu.2."""
    client, conn = empty_app
    doc_id = _seed(conn)
    response = client.post(f"/documents/{doc_id}/rename", json={"title": "renamed"})
    assert response.status_code == 200
    body = response.text
    assert "<article" in body
    assert f'id="library-tile-{doc_id}"' in body
    assert "library-tile" in body
    assert "renamed" in body
    # The tile silhouette and slug must render — the partial expects
    # the full extended LibraryRow payload (Parsem-7wu.1 fields).
    assert "library-tile__silhouette" in body
    assert "library-tile__slug" in body
    # Sanity: this is a fragment, not a full HTML doc.
    assert "<html" not in body.lower()


def test_rename_trims_leading_and_trailing_whitespace(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed(conn)
    client.post(f"/documents/{doc_id}/rename", json={"title": "   spaced   "})
    doc = load_document(conn, doc_id)
    assert doc is not None
    assert doc.title == "spaced"


def test_rename_rejects_empty_title_with_422(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed(conn)
    response = client.post(f"/documents/{doc_id}/rename", json={"title": ""})
    assert response.status_code == 422


def test_rename_rejects_whitespace_only_title_with_422(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """`"   "` trims to empty — same path as fully empty."""
    client, conn = empty_app
    doc_id = _seed(conn)
    response = client.post(f"/documents/{doc_id}/rename", json={"title": "      "})
    assert response.status_code == 422


def test_rename_rejects_title_exceeding_200_chars_with_422(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed(conn)
    too_long = "x" * 201
    response = client.post(f"/documents/{doc_id}/rename", json={"title": too_long})
    assert response.status_code == 422


def test_rename_accepts_exactly_200_chars(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Boundary: 200 is OK, 201 is not."""
    client, conn = empty_app
    doc_id = _seed(conn)
    boundary = "x" * 200
    response = client.post(f"/documents/{doc_id}/rename", json={"title": boundary})
    assert response.status_code == 200


def test_rename_unknown_id_returns_404(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.post("/documents/999/rename", json={"title": "anything"})
    assert response.status_code == 404


def test_library_renders_a_rename_button_per_row(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed(conn, title="alpha")
    body = client.get("/library?segment=all").text
    assert f'data-doc-id="{doc_id}"' in body
    assert "library-rename" in body


def test_library_template_uses_tile_partial(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """The full library page should render tiles from `_library_tile.html`
    so the rename response and the page render the same markup. ADR 0005."""
    client, conn = empty_app
    doc_id = _seed(conn, title="alpha")
    body = client.get("/library?segment=all").text
    assert f'id="library-tile-{doc_id}"' in body
    assert "library-tile" in body


def test_library_js_is_served(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.get("/static/library.js")
    assert response.status_code == 200
    assert "library-rename" in response.text
    assert "/rename" in response.text
