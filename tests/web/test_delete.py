"""Tests for POST /documents/{id}/delete. Spec §22, §21; bead Parsem-eci.

The hard-delete relies on the schema's ON DELETE CASCADE chain (§21).
Each cascade target — sections, chunks, reading_events, reading_state,
pins — gets its own assertion so a future schema change that quietly
breaks one of them surfaces here, not in production.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsem.domain.chunking import ChunkingConfig, chunk
from parsem.parse.markdown_parse import parse
from parsem.store.db import connect, migrate
from parsem.store.documents import (
    insert_chunks_and_sections,
    insert_document,
    load_document,
)
from parsem.web.app import create_app
from parsem.web.state import empty_reader_state

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _seed_doc(conn: sqlite3.Connection, *, title: str = "doc") -> int:
    """Parse a tiny markdown blob and insert document + chunks + sections.
    Returns the new document_id."""
    md = "# Heading\n\nfirst paragraph.\n\nsecond paragraph.\n"
    output = chunk(parse(md), ChunkingConfig())
    document_id = insert_document(
        conn,
        title=title,
        original_path=f"data/originals/{title}.md",
        status="ready",
        total_chunks=len(output.chunks),
        now=T0,
    )
    insert_chunks_and_sections(
        conn,
        document_id=document_id,
        chunks=output.chunks,
        sections=output.sections,
        now=T0,
    )
    return document_id


@pytest.fixture
def app_ctx(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, sqlite3.Connection, Path]]:
    conn = connect(":memory:")
    migrate(conn)
    originals = tmp_path / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    app = create_app(empty_reader_state(conn), db=conn, originals_dir=originals)
    with TestClient(app) as client:
        yield client, conn, originals


def test_delete_redirects_to_library(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    response = client.post(f"/documents/{doc_id}/delete", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/library"


def test_delete_removes_documents_row(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    client.post(f"/documents/{doc_id}/delete")
    assert load_document(conn, doc_id) is None


def test_delete_cascades_to_chunks(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchone()[0] > 0
    client.post(f"/documents/{doc_id}/delete")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_delete_cascades_to_sections(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    client.post(f"/documents/{doc_id}/delete")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM sections WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_delete_cascades_to_reading_events(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    conn.execute(
        "INSERT INTO reading_events (document_id, chunk_id, event_type,"
        " payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (doc_id, 0, "reveal", None, T0.isoformat()),
    )
    conn.commit()
    client.post(f"/documents/{doc_id}/delete")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM reading_events WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_delete_cascades_to_reading_state(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    conn.execute(
        "INSERT INTO reading_state (document_id, high_water_position,"
        " current_position, last_event_id_applied, updated_at)"
        " VALUES (?, 0, 0, NULL, ?)",
        (doc_id, T0.isoformat()),
    )
    conn.commit()
    client.post(f"/documents/{doc_id}/delete")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM reading_state WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_delete_cascades_to_pins(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn)
    chunk_row = conn.execute(
        "SELECT id FROM chunks WHERE document_id=? ORDER BY position LIMIT 1",
        (doc_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO pins (document_id, chunk_id_start, chunk_id_end,"
        " color_id, created_at) VALUES (?, ?, ?, 1, ?)",
        (doc_id, chunk_row["id"], chunk_row["id"], T0.isoformat()),
    )
    conn.commit()
    client.post(f"/documents/{doc_id}/delete")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM pins WHERE document_id=?", (doc_id,)
    ).fetchone()[0]
    assert remaining == 0


def test_delete_unlinks_original_file(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_doc(conn)
    original = originals / f"{doc_id}.md"
    original.write_text("# Hello", encoding="utf-8")
    assert original.exists()
    client.post(f"/documents/{doc_id}/delete")
    assert not original.exists()


def test_delete_is_idempotent_when_original_missing(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, originals = app_ctx
    doc_id = _seed_doc(conn)
    assert not (originals / f"{doc_id}.md").exists()
    response = client.post(f"/documents/{doc_id}/delete", follow_redirects=False)
    assert response.status_code == 302


def test_delete_unknown_id_returns_404(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, _, _ = app_ctx
    response = client.post("/documents/999/delete")
    assert response.status_code == 404


def test_library_renders_a_delete_button_per_row(
    app_ctx: tuple[TestClient, sqlite3.Connection, Path],
) -> None:
    client, conn, _ = app_ctx
    doc_id = _seed_doc(conn, title="trash-me")
    body = client.get("/library").text
    assert f'action="/documents/{doc_id}/delete"' in body
    assert "library-delete" in body
