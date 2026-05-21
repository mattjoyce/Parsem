"""Tests for the library v2 control strip — segments + sort.
ADR 0005, bd Parsem-7wu.4.

Covers:
- list_library_rows() filter SQL for each segment.
- list_library_rows() sort SQL for each order.
- GET /library accepts query params and falls back on invalid values.
- The strip renders with the active segment marked.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.store.db import connect, migrate
from parsem.store.documents import (
    DEFAULT_SEGMENT,
    DEFAULT_SORT,
    insert_document,
    list_library_rows,
)
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    migrate(conn)
    return conn


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


def _doc(
    conn: sqlite3.Connection,
    *,
    title: str,
    total: int | None,
    high_water: int = 0,
    created_at: datetime = T0,
    opened_at: datetime | None = None,
) -> int:
    """Insert a document with optional reading_state. `total=None`
    represents a failed/processing doc with NULL total_chunks."""
    doc_id = insert_document(
        conn,
        title=title,
        original_path=f"data/originals/{title}.md",
        status="ready" if total else "failed",
        total_chunks=total,
        now=created_at,
    )
    if high_water > 0 or opened_at is not None:
        conn.execute(
            "INSERT INTO reading_state (document_id, high_water_position,"
            " current_position, updated_at) VALUES (?, ?, ?, ?)",
            (doc_id, high_water, high_water,
             (opened_at or created_at).isoformat()),
        )
        conn.commit()
    return doc_id


# === Segment filter SQL ================================================


def test_segment_all_returns_every_doc(db: sqlite3.Connection) -> None:
    _doc(db, title="a", total=10)
    _doc(db, title="b", total=10, high_water=5)
    _doc(db, title="c", total=10, high_water=10)
    rows = list_library_rows(db, segment="all")
    assert {r.document.title for r in rows} == {"a", "b", "c"}


def test_segment_unread_returns_high_water_zero_docs(
    db: sqlite3.Connection,
) -> None:
    _doc(db, title="never-opened", total=10)
    _doc(db, title="in-progress", total=10, high_water=5)
    _doc(db, title="done", total=10, high_water=10)
    rows = list_library_rows(db, segment="unread")
    titles = {r.document.title for r in rows}
    assert titles == {"never-opened"}


def test_segment_unread_includes_failed_doc(db: sqlite3.Connection) -> None:
    """A failed doc has high_water=0 by default and shows in unread —
    the user can still open the drawer to retry."""
    _doc(db, title="failed", total=None)
    rows = list_library_rows(db, segment="unread")
    assert {r.document.title for r in rows} == {"failed"}


def test_segment_in_progress_excludes_unread_and_finished(
    db: sqlite3.Connection,
) -> None:
    _doc(db, title="unread", total=10)
    _doc(db, title="halfway", total=10, high_water=5)
    _doc(db, title="finished", total=10, high_water=10)
    rows = list_library_rows(db, segment="in_progress")
    assert {r.document.title for r in rows} == {"halfway"}


def test_segment_finished_uses_95_percent_cutoff(
    db: sqlite3.Connection,
) -> None:
    """A doc that's read >=95% of chunks counts as finished. Below
    that, it's still in progress."""
    _doc(db, title="under-95", total=100, high_water=94)
    _doc(db, title="at-95", total=100, high_water=95)
    _doc(db, title="fully-done", total=10, high_water=10)
    rows = list_library_rows(db, segment="finished")
    assert {r.document.title for r in rows} == {"at-95", "fully-done"}


def test_segment_finished_excludes_failed_docs(
    db: sqlite3.Connection,
) -> None:
    """Failed/processing docs have NULL total_chunks → NULLIF returns
    NULL → comparison returns NULL → row excluded from finished."""
    _doc(db, title="failed", total=None)
    rows = list_library_rows(db, segment="finished")
    assert rows == []


def test_unknown_segment_falls_back_to_default(
    db: sqlite3.Connection,
) -> None:
    """Defensive: an unknown segment string yields the default segment
    (in_progress). Route validates before calling but the store stays
    safe."""
    _doc(db, title="halfway", total=10, high_water=5)
    rows = list_library_rows(db, segment="garbage")
    assert {r.document.title for r in rows} == {"halfway"}


# === Sort SQL =========================================================


def test_sort_last_opened_orders_by_reading_state_updated_desc(
    db: sqlite3.Connection,
) -> None:
    _doc(db, title="oldest", total=10, created_at=T0)
    _doc(
        db, title="recently-opened", total=10, high_water=2,
        created_at=T0, opened_at=T0 + timedelta(hours=2),
    )
    _doc(db, title="middle-created", total=10,
         created_at=T0 + timedelta(minutes=10))
    rows = list_library_rows(db, segment="all", sort="last_opened")
    titles = [r.document.title for r in rows]
    assert titles[0] == "recently-opened"
    assert titles == ["recently-opened", "middle-created", "oldest"]


def test_sort_recently_added_orders_by_created_at_desc(
    db: sqlite3.Connection,
) -> None:
    _doc(db, title="oldest", total=10, created_at=T0)
    _doc(db, title="middle", total=10,
         created_at=T0 + timedelta(minutes=5))
    _doc(db, title="newest", total=10,
         created_at=T0 + timedelta(minutes=10))
    rows = list_library_rows(db, segment="all", sort="recently_added")
    titles = [r.document.title for r in rows]
    assert titles == ["newest", "middle", "oldest"]


def test_sort_title_az_orders_alphabetically(
    db: sqlite3.Connection,
) -> None:
    _doc(db, title="charlie", total=10)
    _doc(db, title="alpha", total=10)
    _doc(db, title="bravo", total=10)
    rows = list_library_rows(db, segment="all", sort="title_az")
    titles = [r.document.title for r in rows]
    assert titles == ["alpha", "bravo", "charlie"]


def test_sort_longest_orders_by_chunk_count_with_nulls_last(
    db: sqlite3.Connection,
) -> None:
    _doc(db, title="short", total=5)
    _doc(db, title="long", total=100)
    _doc(db, title="medium", total=20)
    _doc(db, title="failed", total=None)
    rows = list_library_rows(db, segment="all", sort="longest")
    titles = [r.document.title for r in rows]
    assert titles == ["long", "medium", "short", "failed"]


def test_unknown_sort_falls_back_to_default(db: sqlite3.Connection) -> None:
    _doc(db, title="z", total=10)
    _doc(db, title="a", total=10)
    rows = list_library_rows(db, segment="all", sort="invented")
    # Default is last_opened; both never opened → falls to created_at
    # then title asc; both same created_at → 'a' < 'z'.
    titles = [r.document.title for r in rows]
    assert titles == ["a", "z"]


# === Route: query-param plumbing ======================================


def test_route_defaults_to_in_progress_and_last_opened(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.get("/library")
    assert response.status_code == 200
    body = response.text
    # The "In progress" segment has the active class on default load.
    assert 'aria-selected="true"' in body
    assert "library-segment--active" in body
    # Sort dropdown has last_opened selected.
    assert 'value="last_opened" selected' in body


def test_route_accepts_segment_query_param(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, conn = empty_app
    _doc(conn, title="never", total=10)
    response = client.get("/library?segment=unread")
    assert response.status_code == 200
    body = response.text
    assert "library-tile-" in body  # the never-opened doc renders
    # The Unread chip is marked active.
    assert 'data-segment="unread"' in body
    assert 'aria-selected="true"' in body


def test_route_accepts_sort_query_param(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _ = empty_app
    response = client.get("/library?segment=all&sort=title_az")
    assert response.status_code == 200
    assert 'value="title_az" selected' in response.text


def test_route_silently_falls_back_on_invalid_query(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """Bookmarked URL from a future schema, or a typo, mustn't 500."""
    client, _ = empty_app
    response = client.get("/library?segment=garbage&sort=garbage")
    assert response.status_code == 200
    # Falls back to defaults.
    assert 'value="last_opened" selected' in response.text


# === Empty-state behaviour =============================================


def test_empty_segment_shows_show_all_link(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """A non-`all` segment with no matches surfaces a 'show all' link
    so the user can recover."""
    client, _ = empty_app
    response = client.get("/library?segment=in_progress")
    body = response.text
    assert "library-empty" in body
    assert "show all" in body
    assert "/library?segment=all" in body


def test_empty_library_keeps_existing_copy_for_all_segment(
    empty_app: tuple[TestClient, sqlite3.Connection],
) -> None:
    """When there are zero docs anywhere AND the user is on 'all',
    show the original 'No documents yet' nudge — not the 'show all'
    link (we're already on it)."""
    client, _ = empty_app
    response = client.get("/library?segment=all")
    body = response.text
    assert "No documents yet" in body


# === Default constants exposed for the route ==========================


def test_default_constants() -> None:
    """Sanity: the route imports these to coerce invalid input."""
    assert DEFAULT_SEGMENT == "in_progress"
    assert DEFAULT_SORT == "last_opened"
