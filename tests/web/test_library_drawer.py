"""Tests for the library v2 side-drawer markup. ADR 0005, bd Parsem-7wu.3.

The drawer is rendered server-side as a sibling of each tile, hidden
by default. library.js toggles `[hidden]` on tile click. These tests
verify the server-rendered markup is correct; drawer-open/close
interaction is exercised by manual UAT (no JS runtime in pytest).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
from parsem.store.tags import add_tag
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def empty_app(tmp_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection]]:
    conn = connect(":memory:")
    migrate(conn)
    app = create_app(
        empty_reader_state(conn), db=conn,
        originals_dir=tmp_path / "originals",
    )
    with TestClient(app) as client:
        yield client, conn


def _seed_md(
    conn: sqlite3.Connection, title: str = "doc", chunks: int = 0
) -> int:
    return insert_document(
        conn,
        title=title,
        original_path=f"data/originals/{title}.md",
        status="ready",
        total_chunks=chunks if chunks else None,
        source_type="markdown",
        now=T0,
    )


# === Drawer markup is present and hidden by default ====================


def test_drawer_rendered_per_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Every doc gets its own pre-rendered drawer at page-load time."""
    client, conn = empty_app
    d1 = _seed_md(conn, title="alpha")
    d2 = _seed_md(conn, title="beta")
    body = client.get("/library?segment=all").text
    assert f'id="library-drawer-{d1}"' in body
    assert f'id="library-drawer-{d2}"' in body


def test_drawer_hidden_by_default(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Drawers ship with [hidden] — the JS layer drops it on tile click."""
    client, conn = empty_app
    _seed_md(conn, title="alpha")
    body = client.get("/library?segment=all").text
    # The drawer aside tag carries `hidden` as a boolean attribute.
    assert '<aside id="library-drawer-' in body
    drawer_open = body.index('<aside id="library-drawer-')
    drawer_close_gt = body.index(">", drawer_open)
    tag = body[drawer_open:drawer_close_gt]
    assert " hidden" in tag, f"drawer must be hidden by default: {tag!r}"


def test_overlay_element_present_and_hidden(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """One shared backdrop overlay, hidden until a drawer opens."""
    client, conn = empty_app
    _seed_md(conn, title="x")
    body = client.get("/library?segment=all").text
    assert 'class="library-drawer-overlay"' in body
    # The first overlay tag should carry `hidden`.
    open_idx = body.index('class="library-drawer-overlay"')
    close_idx = body.index(">", open_idx)
    assert " hidden" in body[open_idx:close_idx]


# === Drawer content =====================================================


def test_drawer_carries_full_title_and_open_button(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed_md(conn, title="my-doc")
    body = client.get("/library?segment=all").text
    # Drawer title contains the full text inside .library-drawer__title-text.
    assert 'class="library-drawer__title-text">my-doc<' in body
    # Open button is an <a> to /documents/{id}/reader inside the drawer.
    assert 'class="library-drawer__open" href="/documents/' in body
    assert f'/documents/{doc_id}/reader' in body


def test_tile_does_not_link_directly_to_reader(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """v2: the title is no longer an <a>. Click-anywhere on the tile
    opens the drawer; the drawer's Open button is the only path to
    the reader. ADR 0005."""
    client, conn = empty_app
    doc_id = _seed_md(conn, title="alpha")
    body = client.get("/library?segment=all").text
    # No anchor wrapping the tile title. The tile is an article with
    # role=button; the title is just a span.
    assert 'class="library-tile__title-text">alpha<' in body
    # The only /documents/{id}/reader link is the Drawer Open button.
    occurrences = body.count(f'/documents/{doc_id}/reader')
    assert occurrences == 1


def test_drawer_actions_present_for_ready_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    _seed_md(conn, title="alpha", chunks=3)
    body = client.get("/library?segment=all").text
    drawer_start = body.index('<aside id="library-drawer-')
    drawer_end = body.index("</aside>", drawer_start)
    drawer = body[drawer_start:drawer_end]
    assert "library-rename" in drawer
    assert "library-rechunk" in drawer
    assert "library-delete" in drawer
    assert "library-retry" not in drawer  # ready, not failed


def test_drawer_carries_retry_button_for_failed_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    insert_document(
        conn, title="boom", original_path="data/originals/boom.md",
        status="failed", failure_reason="some reason", now=T0,
    )
    body = client.get("/library?segment=all").text
    drawer_start = body.index('<aside id="library-drawer-')
    drawer_end = body.index("</aside>", drawer_start)
    drawer = body[drawer_start:drawer_end]
    assert "library-retry" in drawer
    assert "library-rename" not in drawer
    assert "library-rechunk" not in drawer


def test_drawer_renders_section_aware_heatmap_for_ready_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """A ready doc with chunks and sections renders a heatmap with
    one cell per chunk grouped by section."""
    client, conn = empty_app
    doc_id = _seed_md(conn, title="alpha", chunks=3)
    # Insert 3 chunks and a section spanning them.
    chunk_ids = []
    for pos in range(3):
        cur = conn.execute(
            "INSERT INTO chunks (document_id, position, source_offset_start,"
            " source_offset_end, text, lead_token_type, estimated_read_seconds,"
            " created_at) VALUES (?, ?, ?, ?, ?, 'paragraph', 1.0, ?)",
            (doc_id, pos, pos * 10, pos * 10 + 5,
             "## Title" if pos == 0 else f"c{pos}", T0.isoformat()),
        )
        chunk_ids.append(cur.lastrowid)
    conn.execute(
        "INSERT INTO sections (document_id, heading_chunk_id, heading_level,"
        " start_chunk_position, end_chunk_position) VALUES (?, ?, 2, 0, 2)",
        (doc_id, chunk_ids[0]),
    )
    conn.commit()

    body = client.get("/library?segment=all").text
    drawer_start = body.index('<aside id="library-drawer-')
    drawer_end = body.index("</aside>", drawer_start)
    drawer = body[drawer_start:drawer_end]
    assert "library-drawer__heatmap" in drawer
    # 3 chunks → 3 cells, all unread (no reading_state row).
    assert drawer.count("library-drawer__cell--unread") == 3


def test_drawer_heatmap_uses_full_rating_palette_for_rated_cells(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed_md(conn, title="rated", chunks=3)
    chunk_ids = []
    for pos in range(3):
        cur = conn.execute(
            "INSERT INTO chunks (document_id, position, source_offset_start,"
            " source_offset_end, text, lead_token_type, estimated_read_seconds,"
            " created_at) VALUES (?, ?, ?, ?, ?, 'paragraph', 1.0, ?)",
            (doc_id, pos, pos * 10, pos * 10 + 5, f"c{pos}", T0.isoformat()),
        )
        chunk_ids.append(cur.lastrowid)
    # Single section, all three chunks. Rate chunk 1 = 4.
    conn.execute(
        "INSERT INTO sections (document_id, heading_chunk_id, heading_level,"
        " start_chunk_position, end_chunk_position) VALUES (?, ?, 2, 0, 2)",
        (doc_id, chunk_ids[0]),
    )
    conn.execute(
        "INSERT INTO chunk_ratings (chunk_id, rating, updated_at)"
        " VALUES (?, 4, ?)",
        (chunk_ids[1], T0.isoformat()),
    )
    # Mark all chunks read.
    conn.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, updated_at) VALUES (?, 3, 2, ?)",
        (doc_id, T0.isoformat()),
    )
    conn.commit()

    body = client.get("/library?segment=all").text
    drawer_start = body.index('<aside id="library-drawer-')
    drawer_end = body.index("</aside>", drawer_start)
    drawer = body[drawer_start:drawer_end]
    assert "library-drawer__cell--rated-4" in drawer
    # Chunks 0 and 2 are read but unrated.
    assert drawer.count("library-drawer__cell--read_unrated") == 2


def test_drawer_source_url_present_only_for_url_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    insert_document(
        conn, title="post", original_path="https://stratechery.com/post",
        status="ready", total_chunks=3, source_type="url", now=T0,
    )
    body = client.get("/library?segment=all").text
    assert "library-drawer__source-url" in body
    assert 'href="https://stratechery.com/post"' in body


def test_drawer_source_url_absent_for_file_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    _seed_md(conn, title="md-doc")
    body = client.get("/library?segment=all").text
    assert "library-drawer__source-url" not in body


def test_drawer_renders_tags_when_present(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _seed_md(conn, title="alpha")
    add_tag(conn, doc_id, "wisdom", now=T0)
    add_tag(conn, doc_id, "brick", now=T0)
    body = client.get("/library?segment=all").text
    assert "library-drawer__chip" in body
    assert ">brick<" in body
    assert ">wisdom<" in body


def test_drawer_omits_tags_block_when_no_tags(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    _seed_md(conn, title="alpha")
    body = client.get("/library?segment=all").text
    assert "library-drawer__chip" not in body


def test_drawer_stats_show_chunk_position_when_read(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Stats line includes 'chunk X of N' when the doc has a
    reading_state row."""
    client, conn = empty_app
    doc_id = _seed_md(conn, title="alpha", chunks=10)
    conn.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, updated_at) VALUES (?, 4, 4, ?)",
        (doc_id, T0.isoformat()),
    )
    conn.commit()
    body = client.get("/library?segment=all").text
    assert "chunk 5 of 10" in body  # current_position + 1


def test_drawer_stats_pin_count_always_present(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    _seed_md(conn, title="alpha", chunks=3)
    body = client.get("/library?segment=all").text
    # Pins row is part of the stats dl.
    assert "<dt>Pins</dt><dd>0</dd>" in body
