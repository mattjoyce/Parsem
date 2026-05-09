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
    client, conn = empty_app
    _insert(conn, title="ready-doc", status="ready", created_at=T0)
    _insert(conn, title="failed-doc", status="failed", created_at=T0 + timedelta(seconds=1))
    body = client.get("/library").text
    assert "library-status-ready" in body
    assert "library-status-failed" in body


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
