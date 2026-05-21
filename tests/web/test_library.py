"""Tests for the library route. Spec §9.1, §22; bead Parsem-3z8.

A standalone fixture (no welcome doc seeded) is used so each test can
control exactly which documents and reading_state rows exist — the
ordering tests rely on knowing precisely what's in the DB.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.store.db import connect, migrate
from parsem.store.documents import insert_document
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


def _insert(conn: sqlite3.Connection, *, title: str, status: str, created_at: datetime) -> int:
    return insert_document(
        conn,
        title=title,
        original_path=f"data/originals/{title}.md",
        status=status,
        now=created_at,
    )


def _set_last_opened(conn: sqlite3.Connection, document_id: int, when: datetime) -> None:
    conn.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, last_event_id_applied, updated_at)"
        " VALUES (?, 0, 0, NULL, ?)",
        (document_id, when.isoformat()),
    )
    conn.commit()


def test_root_redirects_to_library(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/library"


def test_get_library_returns_html_page(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.get("/library")
    assert response.status_code == 200
    assert "<html" in response.text


def test_empty_library_renders_empty_state(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.get("/library")
    assert response.status_code == 200
    assert "library-empty" in response.text


def test_library_lists_every_document(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    _insert(conn, title="alpha", status="ready", created_at=T0)
    _insert(conn, title="beta", status="processing", created_at=T0 + timedelta(seconds=1))
    _insert(conn, title="gamma", status="failed", created_at=T0 + timedelta(seconds=2))
    body = client.get("/library").text
    assert "alpha" in body
    assert "beta" in body
    assert "gamma" in body


def test_library_renders_status_per_row(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """v2 tile: non-ready docs render a `library-status-<status>` div;
    ready docs render no status badge (the absence IS the signal —
    ADR 0005). The tile itself carries the ready state via the
    title + slug + silhouette."""
    client, conn = empty_app
    ready_id = _insert(conn, title="ready-doc", status="ready", created_at=T0)
    failed_id = _insert(
        conn, title="failed-doc", status="failed",
        created_at=T0 + timedelta(seconds=1),
    )
    body = client.get("/library").text
    # Failed status renders as a labelled badge in the tile.
    assert "library-status-failed" in body
    # Ready tile has the failure modifier absent and shows no status div.
    assert f"library-tile-{ready_id}" in body
    assert f"library-tile-{failed_id}" in body
    assert "library-tile--failed" in body


def test_library_title_links_to_reader(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _insert(conn, title="onlydoc", status="ready", created_at=T0)
    body = client.get("/library").text
    assert f'href="/documents/{doc_id}/reader"' in body


def test_library_orders_by_last_opened_desc_with_created_at_fallback(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Three docs:
       - 'oldest-created'  created at T0,        last_opened None  → COALESCE = T0
       - 'middle-created'  created at T0+1m,     last_opened None  → COALESCE = T0+1m
       - 'recently-opened' created at T0,        last_opened T0+1h → COALESCE = T0+1h

    Expected order: recently-opened, middle-created, oldest-created.
    """
    client, conn = empty_app
    oldest = _insert(conn, title="oldest-created", status="ready", created_at=T0)
    middle = _insert(
        conn,
        title="middle-created",
        status="ready",
        created_at=T0 + timedelta(minutes=1),
    )
    recent = _insert(conn, title="recently-opened", status="ready", created_at=T0)
    _set_last_opened(conn, recent, T0 + timedelta(hours=1))

    body = client.get("/library").text
    # Slice on the document-id-bearing href which is unique per doc.
    pos_oldest = body.index(f"/documents/{oldest}/reader")
    pos_middle = body.index(f"/documents/{middle}/reader")
    pos_recent = body.index(f"/documents/{recent}/reader")
    assert pos_recent < pos_middle < pos_oldest


def test_library_ties_break_alphabetically_by_title(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    # Identical created_at, never opened → COALESCE ties → sort by title ASC.
    a = _insert(conn, title="apple", status="ready", created_at=T0)
    b = _insert(conn, title="banana", status="ready", created_at=T0)
    c = _insert(conn, title="cherry", status="ready", created_at=T0)
    body = client.get("/library").text
    pos_a = body.index(f"/documents/{a}/reader")
    pos_b = body.index(f"/documents/{b}/reader")
    pos_c = body.index(f"/documents/{c}/reader")
    assert pos_a < pos_b < pos_c


# Parsem-5oi — Library progress % column


def _set_position(
    conn: sqlite3.Connection,
    document_id: int,
    *,
    current: int,
    total: int,
) -> None:
    conn.execute(
        "UPDATE documents SET total_chunks=? WHERE id=?",
        (total, document_id),
    )
    conn.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, last_event_id_applied, updated_at)"
        " VALUES (?, ?, ?, NULL, ?)",
        (document_id, current, current, T0.isoformat()),
    )
    conn.commit()


def test_library_renders_progress_percent_for_never_opened_doc(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Never opened == no reading_state row → 0%."""
    client, conn = empty_app
    doc_id = _insert(conn, title="never-opened", status="ready", created_at=T0)
    conn.execute("UPDATE documents SET total_chunks=10 WHERE id=?", (doc_id,))
    conn.commit()
    body = client.get("/library").text
    assert "0%" in body


def test_library_progress_at_position_zero_with_ten_chunks_is_ten_percent(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """current_position 0 with 10 chunks → 100*(0+1)/10 = 10%."""
    client, conn = empty_app
    doc_id = _insert(conn, title="open-zero", status="ready", created_at=T0)
    _set_position(conn, doc_id, current=0, total=10)
    body = client.get("/library").text
    assert "10%" in body


def test_library_progress_at_last_chunk_is_one_hundred_percent(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    doc_id = _insert(conn, title="finished", status="ready", created_at=T0)
    _set_position(conn, doc_id, current=9, total=10)
    body = client.get("/library").text
    assert "100%" in body


def test_library_progress_clamps_to_one_hundred_when_position_overflows(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Defensive clamp: if a future event-replay drift overshoots
    total_chunks, the displayed percent should still cap at 100%."""
    client, conn = empty_app
    doc_id = _insert(conn, title="overshoot", status="ready", created_at=T0)
    _set_position(conn, doc_id, current=15, total=10)
    body = client.get("/library").text
    assert "100%" in body
    assert "160%" not in body


def test_library_progress_omitted_for_failed_doc_with_no_total_chunks(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """status='failed' or 'processing' docs may have total_chunks=NULL.
    v2 tile (ADR 0005, Parsem-7wu.2): percent text is hidden for docs
    with no chunks — the failed badge carries the at-glance signal.
    The tile must still render without crashing."""
    client, conn = empty_app
    doc_id = _insert(conn, title="failed-doc", status="failed", created_at=T0)
    body = client.get("/library").text
    assert f"library-tile-{doc_id}" in body
    assert "library-tile__percent" not in body
    assert "library-tile--failed" in body


# ─── Library v2 tile silhouette (ADR 0005, bd Parsem-7wu.2) ──────────


def _seed_doc_with_chunks_and_ratings(
    conn: sqlite3.Connection,
    *,
    title: str,
    chunk_count: int,
    ratings: dict[int, int],
    high_water: int = 0,
) -> int:
    """Insert a document with `chunk_count` chunks, per-position ratings,
    and an optional reading_state row at `high_water`. Used to verify
    the v2 tile silhouette renders the right cell kinds for each
    reading + rating combination."""
    doc_id = insert_document(
        conn,
        title=title,
        original_path=f"data/originals/{title}.md",
        status="ready",
        total_chunks=chunk_count,
        now=T0,
    )
    chunk_ids: list[int] = []
    for pos in range(chunk_count):
        cur = conn.execute(
            "INSERT INTO chunks (document_id, position, source_offset_start,"
            " source_offset_end, text, lead_token_type, estimated_read_seconds,"
            " created_at) VALUES (?, ?, ?, ?, ?, 'paragraph', 1.0, ?)",
            (doc_id, pos, pos * 10, pos * 10 + 5, f"c{pos}", T0.isoformat()),
        )
        cid = cur.lastrowid
        assert cid is not None
        chunk_ids.append(cid)
    for pos, rating in ratings.items():
        conn.execute(
            "INSERT INTO chunk_ratings (chunk_id, rating, updated_at)"
            " VALUES (?, ?, ?)",
            (chunk_ids[pos], rating, T0.isoformat()),
        )
    if high_water > 0:
        conn.execute(
            "INSERT INTO reading_state (document_id, high_water_position,"
            " current_position, updated_at) VALUES (?, ?, ?, ?)",
            (doc_id, high_water, high_water, T0.isoformat()),
        )
    conn.commit()
    return doc_id


def test_library_silhouette_always_renders_25_cells(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """The 5x5 tile silhouette is always 25 cells — the down-sampler
    spreads chunks across a fixed grid regardless of total_chunks.
    ADR 0005, Q3."""
    client, conn = empty_app
    _seed_doc_with_chunks_and_ratings(
        conn, title="any", chunk_count=5, ratings={}
    )
    body = client.get("/library").text
    assert body.count('class="library-tile__cell ') == 25


def test_library_silhouette_tints_read_and_rated_cell(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """A bucket whose chunks are read AND rated takes the rating-coloured
    modifier (library-tile__cell--rated-N) so CSS can paint it from
    the --rating-N palette. Reading-state precedence: rating-coloured
    only fires when the bucket has settled chunks."""
    client, conn = empty_app
    # 3 chunks rated 4, fully read → all populated buckets render rated-4.
    _seed_doc_with_chunks_and_ratings(
        conn, title="all-rated", chunk_count=3, ratings={0: 4, 1: 4, 2: 4},
        high_water=3,
    )
    body = client.get("/library").text
    assert "library-tile__cell--rated-4" in body


def test_library_silhouette_unread_for_rated_but_unread_chunks(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Reading-state takes precedence over rating in the silhouette:
    a chunk rated but never read renders as 'unread' (faint outline),
    not as a rated cell. The mark says 'how it felt' only for territory
    you actually visited."""
    client, conn = empty_app
    _seed_doc_with_chunks_and_ratings(
        conn, title="rated-unread", chunk_count=3, ratings={0: 5},
        high_water=0,  # never opened
    )
    body = client.get("/library").text
    assert "library-tile__cell--unread" in body
    assert "library-tile__cell--rated-5" not in body


def test_library_silhouette_omits_rating_modifier_for_read_unrated_buckets(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Read-but-not-rated buckets render the neutral 'read_unrated'
    modifier — not a rating-coloured one."""
    client, conn = empty_app
    _seed_doc_with_chunks_and_ratings(
        conn, title="read-unrated", chunk_count=3, ratings={},
        high_water=3,  # fully read, no ratings
    )
    body = client.get("/library").text
    assert "library-tile__cell--read_unrated" in body
    for r in range(1, 6):
        assert f"library-tile__cell--rated-{r}" not in body


def test_library_silhouette_renders_for_doc_with_no_chunks(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Failed / processing docs (total_chunks=NULL) still render the
    25-cell silhouette — all cells are 'unread' since there's nothing
    to bucket. The tile reads as a quiet empty mark."""
    client, conn = empty_app
    _insert(conn, title="failed-doc", status="failed", created_at=T0)
    body = client.get("/library").text
    assert 'class="library-tile__silhouette"' in body
    assert body.count('class="library-tile__cell ') == 25


def test_library_silhouette_renders_in_tile_via_rename_route(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """The rename route re-renders the tile partial; the silhouette
    must survive that re-render so the tile stays consistent after a
    title edit. ADR 0005, bd Parsem-7wu.2."""
    client, conn = empty_app
    doc_id = _seed_doc_with_chunks_and_ratings(
        conn, title="rated", chunk_count=3, ratings={1: 5}, high_water=3,
    )
    response = client.post(
        f"/documents/{doc_id}/rename", json={"title": "renamed"}
    )
    assert response.status_code == 200
    assert "library-tile__cell--rated-5" in response.text


def test_progress_percent_pure_function_clamps_and_rounds() -> None:
    """Direct unit test of the pure formula — keeps rounding/clamping
    behaviour isolated from the SQL layer."""
    from parsem.store.documents import progress_percent

    assert progress_percent(None, None) == 0
    assert progress_percent(10, None) == 0
    assert progress_percent(None, 0) == 0
    assert progress_percent(0, 0) == 0
    assert progress_percent(10, 0) == 10
    assert progress_percent(10, 4) == 50
    assert progress_percent(10, 9) == 100
    assert progress_percent(10, 100) == 100
    assert progress_percent(3, 0) == 33  # round(33.33...) == 33
    assert progress_percent(3, 1) == 67  # round(66.66...) == 67
